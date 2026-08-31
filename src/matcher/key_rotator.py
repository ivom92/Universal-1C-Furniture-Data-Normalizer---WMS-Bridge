"""Thread-safe Gemini API key pool with round-robin rotation and quota cooldown."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

from src.utils.logger import get_logger

logger = get_logger()

_DEFAULT_COOLDOWN_SECONDS = 60
_DEFAULT_PING_TIMEOUT = 10.0


@dataclass(frozen=True)
class KeyPoolPingResult:
    """Result of probing every key in the pool against the Gemini models API."""

    total: int
    active: int
    ok: bool
    message: str


def parse_gemini_api_keys(
    *,
    keys_env: Optional[str] = None,
    single_key_env: Optional[str] = None,
    explicit_key: Optional[str] = None,
) -> list[str]:
    """Parse comma-separated ``GEMINI_API_KEYS`` or fallback ``GEMINI_API_KEY``."""
    if explicit_key and explicit_key.strip():
        return [part.strip() for part in explicit_key.split(",") if part.strip()]

    if keys_env is not None:
        raw = keys_env.strip()
    elif single_key_env is not None:
        raw = single_key_env.strip()
    else:
        from src.config import get_config

        return get_config().gemini_api_keys

    if not raw:
        return []

    return [part.strip() for part in raw.split(",") if part.strip()]


class KeyPool:
    """Round-robin pool of Gemini API keys with temporary cooldown after quota errors."""

    def __init__(
        self,
        keys: Optional[list[str]] = None,
        *,
        cooldown_seconds: int = _DEFAULT_COOLDOWN_SECONDS,
    ) -> None:
        self._keys = list(keys or [])
        self._cooldown_seconds = cooldown_seconds
        self._lock = threading.Lock()
        self._index = 0
        self._cooldown_until: dict[str, float] = {}

    @classmethod
    def from_env(cls, explicit_key: Optional[str] = None, **kwargs) -> KeyPool:
        """Build a pool from environment variables or an explicit override key."""
        parsed = parse_gemini_api_keys(explicit_key=explicit_key)
        return cls(parsed, **kwargs)

    @property
    def is_available(self) -> bool:
        """Return True when at least one API key is configured."""
        return bool(self._keys)

    @property
    def key_count(self) -> int:
        return len(self._keys)

    @property
    def keys(self) -> list[str]:
        return list(self._keys)

    def _is_on_cooldown(self, key: str, *, now: Optional[float] = None) -> bool:
        deadline = self._cooldown_until.get(key)
        if deadline is None:
            return False
        current = now if now is not None else time.monotonic()
        if current >= deadline:
            self._cooldown_until.pop(key, None)
            return False
        return True

    def get_next_key(self) -> Optional[str]:
        """Return the next active key using round-robin, skipping keys on cooldown."""
        if not self._keys:
            return None

        with self._lock:
            now = time.monotonic()
            total = len(self._keys)
            for _ in range(total):
                key = self._keys[self._index % total]
                self._index = (self._index + 1) % total
                if not self._is_on_cooldown(key, now=now):
                    return key
            return None

    def mark_exhausted(self, key: str, cooldown_seconds: Optional[int] = None) -> None:
        """Mark a key as temporarily exhausted after quota or auth errors."""
        seconds = self._cooldown_seconds if cooldown_seconds is None else cooldown_seconds
        with self._lock:
            self._cooldown_until[key] = time.monotonic() + seconds
        logger.warning(
            "[KeyPool] Ключ ...%s исчерпал лимит (429). Временный cooldown на %sс",
            key[-4:],
            seconds,
        )

    def test_connection(
        self,
        *,
        base_url: Optional[str] = None,
        timeout: float = _DEFAULT_PING_TIMEOUT,
    ) -> KeyPoolPingResult:
        """Ping each configured key via ``models.list`` (same probe as health scripts)."""
        total = len(self._keys)
        if total == 0:
            return KeyPoolPingResult(
                total=0,
                active=0,
                ok=False,
                message="GEMINI_API_KEYS / GEMINI_API_KEY не заданы",
            )

        from src.llm.gemini_client import probe_gemini_models_list

        active = 0
        for key in self._keys:
            try:
                response = probe_gemini_models_list(
                    key,
                    base_url=base_url,
                    timeout=timeout,
                )
                if response.status_code < 400:
                    active += 1
            except Exception:
                continue

        if active == total:
            return KeyPoolPingResult(
                total=total,
                active=active,
                ok=True,
                message=f"✅ Все {total} ключей активны",
            )
        if active > 0:
            return KeyPoolPingResult(
                total=total,
                active=active,
                ok=False,
                message=f"⚠️ Активны {active} из {total} ключей",
            )
        return KeyPoolPingResult(
            total=total,
            active=0,
            ok=False,
            message="❌ Ни один ключ не отвечает — проверьте .env и прокси",
        )
