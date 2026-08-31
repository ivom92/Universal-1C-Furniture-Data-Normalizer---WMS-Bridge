"""PIN authentication utilities for the WMS Bridge web interface.

Provides:
- ``verify_pin``       — timing-safe PIN comparison via hmac.compare_digest.
- ``is_auth_required`` — checks whether WAREHOUSE_PIN is configured.
- ``BruteForceProtector`` — session-level counter that enforces a 30-second
  lockout after 3 consecutive failed attempts.

The module has NO Streamlit dependency; session state wiring lives in app_ui.py.
"""

from __future__ import annotations

import hmac
import time

from src.config import get_config

_DEFAULT_MAX_ATTEMPTS: int = 3
_DEFAULT_LOCKOUT_SECONDS: int = 30


def verify_pin(input_pin: str, target_pin: str) -> bool:
    """Return True when *input_pin* matches *target_pin* (whitespace stripped).

    Uses :func:`hmac.compare_digest` for constant-time comparison to prevent
    timing-based side-channel attacks.
    """
    return hmac.compare_digest(input_pin.strip(), target_pin.strip())


def is_auth_required() -> bool:
    """Return True when ``WAREHOUSE_PIN`` is set to a non-empty value."""
    return bool(get_config().warehouse_pin)


class BruteForceProtector:
    """Track consecutive failed PIN attempts and enforce a temporary lockout.

    Instances are meant to be stored in ``st.session_state`` so they persist
    across Streamlit reruns within the same browser session.

    Parameters
    ----------
    max_attempts:
        Number of consecutive failures before a lockout is triggered (default 3).
    lockout_seconds:
        Duration of the lockout period in seconds (default 30).
    """

    def __init__(
        self,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
        lockout_seconds: int = _DEFAULT_LOCKOUT_SECONDS,
    ) -> None:
        self._max_attempts = max_attempts
        self._lockout_seconds = lockout_seconds
        self._failed_attempts: int = 0
        self._lockout_until: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_failure(self) -> None:
        """Increment the failure counter; activate lockout when threshold is hit."""
        self._failed_attempts += 1
        if self._failed_attempts >= self._max_attempts:
            self._lockout_until = time.monotonic() + self._lockout_seconds

    def record_success(self) -> None:
        """Reset the failure counter and clear any active lockout."""
        self._failed_attempts = 0
        self._lockout_until = 0.0

    def is_locked_out(self) -> bool:
        """Return True while an active lockout period is in effect."""
        return time.monotonic() < self._lockout_until

    def seconds_remaining(self) -> int:
        """Return the number of full seconds left in the current lockout (0 if none)."""
        remaining = self._lockout_until - time.monotonic()
        return max(0, int(remaining))

    @property
    def failed_attempts(self) -> int:
        """Number of consecutive failures since the last successful attempt."""
        return self._failed_attempts

    @property
    def max_attempts(self) -> int:
        """Maximum allowed consecutive failures before lockout."""
        return self._max_attempts
