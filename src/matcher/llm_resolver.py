"""Dual-engine LLM resolver: Google Gemini (dev) and Ollama (prod/offline)."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional, TypeVar

import httpx

from src.config import get_config
from src.llm.gemini_client import (
    build_gemini_client,
    gemini_models_list_url,
    resolve_gemini_base_url,
)
from src.matcher.key_rotator import KeyPool, parse_gemini_api_keys
from src.models import (
    ExtractedFeatures,
    LLMResolutionResponse,
    MatchCandidate,
    RawOrderBlock,
)
from src.utils.logger import get_logger

logger = get_logger()

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

_UNAVAILABLE_REASONING = "LLM Fallback unavaliable"
_TIMEOUT_REASONING = "LLM request timeout"
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?", re.IGNORECASE | re.MULTILINE)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_OLLAMA_HEALTHCHECK_TIMEOUT = 2.0
_DEFAULT_REQUEST_TIMEOUT = 25.0
_DEFAULT_GEMINI_MODEL = "gemini-2.5-flash-lite"
_GEMINI_FALLBACK_MODELS = (
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash",
)
_LLM_MAX_WORKERS = 8
_RETRY_DELAY_SECONDS = 1.5
_RETRYABLE_STATUS_CODES = frozenset({429, 504})
_RETRYABLE_MARKERS = (
    "504",
    "429",
    "DEADLINE_EXCEEDED",
    "RESOURCE_EXHAUSTED",
    "TOO MANY REQUESTS",
)

T = TypeVar("T")


def sanitize_json_text(text: str) -> str:
    """Strip Markdown fences and extract the first JSON object from LLM output."""
    cleaned = text.strip()
    cleaned = _JSON_FENCE_RE.sub("", cleaned)
    cleaned = cleaned.removesuffix("```").strip()

    match = _JSON_OBJECT_RE.search(cleaned)
    if match:
        return match.group(0)
    return cleaned


def parse_llm_json_response(text: str) -> LLMResolutionResponse:
    """Parse raw LLM text into a validated resolution response."""
    json_text = sanitize_json_text(text)
    payload = json.loads(json_text)
    return LLMResolutionResponse.model_validate(payload)


class LLMResolver:
    """Resolve ambiguous catalog matches via Gemini or local Ollama."""

    def __init__(
        self,
        provider: Optional[str] = None,
        gemini_api_key: Optional[str] = None,
        gemini_model: Optional[str] = None,
        ollama_base_url: Optional[str] = None,
        ollama_model: Optional[str] = None,
        timeout: float = _DEFAULT_REQUEST_TIMEOUT,
        max_workers: int = _LLM_MAX_WORKERS,
    ) -> None:
        self._provider = (provider or get_config().llm_provider).strip().lower()
        self._key_pool = KeyPool.from_env(explicit_key=gemini_api_key)
        self._gemini_api_key = self._key_pool.keys[0] if self._key_pool.is_available else None
        self._gemini_model = gemini_model or get_config().gemini_model
        self._gemini_base_url = resolve_gemini_base_url()
        self._ollama_base_url = (
            ollama_base_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        ).rstrip("/")
        self._ollama_model = ollama_model or os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
        self._timeout = timeout
        self._max_workers = max(1, int(max_workers))
        self._cache: dict[tuple, LLMResolutionResponse] = {}
        self._cache_lock = threading.Lock()
        self._thread_local = threading.local()

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def ollama_model(self) -> str:
        return self._ollama_model

    @property
    def ollama_base_url(self) -> str:
        return self._ollama_base_url

    @property
    def gemini_model(self) -> str:
        return self._gemini_model

    @property
    def gemini_base_url(self) -> Optional[str]:
        return self._gemini_base_url

    @property
    def timeout(self) -> float:
        return self._timeout

    @property
    def max_workers(self) -> int:
        return self._max_workers

    def is_available(self) -> bool:
        """Return True when the Ollama HTTP API responds within 2 seconds."""
        try:
            with httpx.Client(timeout=_OLLAMA_HEALTHCHECK_TIMEOUT) as client:
                response = client.get(f"{self._ollama_base_url}/api/tags")
                response.raise_for_status()
                return True
        except Exception:
            return False

    def has_ollama_model(self, model_name: Optional[str] = None) -> bool:
        """Return True when the requested model is present in Ollama /api/tags."""
        target = (model_name or self._ollama_model).strip()
        try:
            with httpx.Client(timeout=_OLLAMA_HEALTHCHECK_TIMEOUT) as client:
                response = client.get(f"{self._ollama_base_url}/api/tags")
                response.raise_for_status()
                models = response.json().get("models", [])
        except Exception:
            return False

        for entry in models:
            name = str(entry.get("name", "")).strip()
            if name == target:
                return True
        return False

    def resolve(
        self,
        block: RawOrderBlock,
        features: ExtractedFeatures,
        candidates: list[MatchCandidate],
    ) -> LLMResolutionResponse:
        if not candidates:
            return LLMResolutionResponse(
                selected_nomenclature_code=None,
                confidence=0.0,
                reasoning="No candidates available",
            )

        cache_key = _resolution_cache_key(block, features, candidates)
        with self._cache_lock:
            cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        prompt = self._build_prompt(block, features, candidates)
        logger.debug(
            "[LLM] Prompt for line #%s:\n%s",
            block.line_number,
            prompt,
        )
        try:
            if self._provider == "ollama":
                result = call_with_retry(lambda: self._resolve_ollama(prompt))
            else:
                result = call_with_retry(lambda: self._resolve_gemini(prompt))
        except TimeoutError as exc:
            logger.warning("LLM resolution timed out (%s): %s", self._provider, exc)
            result = LLMResolutionResponse(
                selected_nomenclature_code=None,
                confidence=0.0,
                reasoning=_TIMEOUT_REASONING,
            )
        except httpx.TimeoutException as exc:
            logger.warning("LLM HTTP timeout (%s): %s", self._provider, exc)
            result = LLMResolutionResponse(
                selected_nomenclature_code=None,
                confidence=0.0,
                reasoning=_TIMEOUT_REASONING,
            )
        except Exception as exc:
            logger.error(
                "[LLM] Ошибка вызова Gemini API: %s - %s",
                type(exc).__name__,
                exc,
            )
            result = LLMResolutionResponse(
                selected_nomenclature_code=None,
                confidence=0.0,
                reasoning=_UNAVAILABLE_REASONING,
            )

        logger.debug(
            "[LLM] Response for line #%s: SKU=%s Conf=%.2f Reason='%s'",
            block.line_number,
            result.selected_nomenclature_code,
            result.confidence,
            result.reasoning,
        )
        with self._cache_lock:
            self._cache[cache_key] = result
        return result

    def resolve_candidates_batch(
        self,
        jobs: list[tuple[RawOrderBlock, ExtractedFeatures, list[MatchCandidate]]],
    ) -> list[LLMResolutionResponse]:
        """Resolve many NEEDS_LLM rows, reusing the in-memory cache across workers."""
        if not jobs:
            return []

        max_workers = min(self._max_workers, len(jobs))
        if max_workers <= 1:
            return [self.resolve(block, features, candidates) for block, features, candidates in jobs]

        results: list[Optional[LLMResolutionResponse]] = [None] * len(jobs)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_index = {
                executor.submit(self.resolve, block, features, candidates): index
                for index, (block, features, candidates) in enumerate(jobs)
            }
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                try:
                    results[index] = future.result()
                except Exception as exc:
                    logger.error(
                        "[LLM] Ошибка вызова Gemini API: %s - %s",
                        type(exc).__name__,
                        exc,
                    )
                    results[index] = LLMResolutionResponse(
                        selected_nomenclature_code=None,
                        confidence=0.0,
                        reasoning=_UNAVAILABLE_REASONING,
                    )
        return [result if result is not None else LLMResolutionResponse(
            selected_nomenclature_code=None,
            confidence=0.0,
            reasoning=_UNAVAILABLE_REASONING,
        ) for result in results]

    def _build_prompt(
        self,
        block: RawOrderBlock,
        features: ExtractedFeatures,
        candidates: list[MatchCandidate],
    ) -> str:
        dimensions = ", ".join(features.dimensions) if features.dimensions else "не указаны"
        packaging = features.package_ratio or "не указана"

        candidate_lines: list[str] = []
        for index, candidate in enumerate(candidates[:5], start=1):
            entity = candidate.catalog_entity
            candidate_lines.append(
                f"[{index}] Код: {entity.nomenclature_code} | "
                f"Номенклатура: {entity.nomenclature} | "
                f"Модуль: {entity.module or '-'} | "
                f"Цвет: {entity.color or '-'} | "
                f"Начинка: {entity.filling or '-'} | "
                f"Упаковка: {entity.packaging or '-'} | "
                f"Скор сходства: {candidate.similarity_score:.2f}"
            )

        candidates_text = "\n".join(candidate_lines)
        return (
            "Ты эксперт мебельного склада. Выбери точный nomenclature_code кандидата, "
            "который соответствует заказу с учётом модели, цвета, размеров и упаковки. "
            "Если это составное стекло (IMP ст), выбери стекло подходящей толщины и габаритов. "
            "Упаковка места (1/3, 2/2 и т.д.) должна совпадать точно: "
            "нельзя выбирать Ун1/1 или 1/1 вместо многоместной 1/3. "
            "Корпус кухни нельзя заменять ящиком или направляющими. "
            "Если ни один кандидат не подходит, верни selected_nomenclature_code = null.\n\n"
            "Данные строки 1С 7.7:\n"
            f"- Клиентское наименование: {block.client_description}\n"
            f"- Фабричный алиас: {block.factory_alias}\n"
            f"- Тип позиции: {block.item_type}\n"
            f"- Габариты: {dimensions}\n"
            f"- Упаковка: {packaging}\n\n"
            "Кандидаты из каталога 1С v8 (ТОП-5):\n"
            f"{candidates_text}\n\n"
            "Ответь строго JSON с полями: "
            "selected_nomenclature_code (string или null), confidence (0.0-1.0), reasoning (кратко на русском)."
        )

    def _gemini_generate_config(self):
        from google.genai import types

        timeout_ms = max(1, int(self._timeout * 1000))
        http_kwargs: dict[str, object] = {"timeout": timeout_ms}
        if self._gemini_base_url:
            http_kwargs["base_url"] = self._gemini_base_url
        return types.GenerateContentConfig(
            temperature=0.0,
            response_mime_type="application/json",
            response_schema=LLMResolutionResponse,
            tools=[],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            http_options=types.HttpOptions(**http_kwargs),
        )

    def _gemini_client(self, api_key: str):
        clients = getattr(self._thread_local, "gemini_clients", None)
        if clients is None:
            clients = {}
            self._thread_local.gemini_clients = clients

        client = clients.get(api_key)
        if client is not None:
            return client

        client = build_gemini_client(
            api_key,
            timeout=self._timeout,
            base_url=self._gemini_base_url,
        )
        clients[api_key] = client
        return client

    def _generate_gemini(
        self,
        model_name: str,
        prompt: str,
        *,
        api_key: str,
    ) -> LLMResolutionResponse:
        client = self._gemini_client(api_key)
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=self._gemini_generate_config(),
        )

        if response.parsed is not None:
            return LLMResolutionResponse.model_validate(response.parsed)

        if response.text:
            payload = json.loads(response.text)
            return LLMResolutionResponse.model_validate(payload)

        raise ValueError("Gemini returned empty response")

    def _resolve_gemini_with_key(self, api_key: str, prompt: str) -> LLMResolutionResponse:
        models_to_try = [self._gemini_model]
        for fallback in _GEMINI_FALLBACK_MODELS:
            if fallback not in models_to_try:
                models_to_try.append(fallback)

        last_exc: Exception | None = None
        for index, model_name in enumerate(models_to_try):
            try:
                result = self._generate_gemini(model_name, prompt, api_key=api_key)
                if model_name != self._gemini_model:
                    logger.warning(
                        "Gemini model %s unavailable, resolved via %s",
                        self._gemini_model,
                        model_name,
                    )
                    self._gemini_model = model_name
                return result
            except Exception as exc:
                last_exc = exc
                if is_key_failover_error(exc):
                    raise
                if not is_gemini_model_not_found(exc):
                    logger.error(
                        "[LLM] Ошибка вызова Gemini API: %s - %s",
                        type(exc).__name__,
                        exc,
                    )
                    raise
                if index < len(models_to_try) - 1:
                    logger.warning(
                        "Gemini model %s returned 404 NOT_FOUND, retrying with %s: %s",
                        model_name,
                        models_to_try[index + 1],
                        exc,
                    )
                    continue
                logger.error(
                    "[LLM] Ошибка вызова Gemini API: %s - %s",
                    type(exc).__name__,
                    exc,
                )
                raise

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Gemini resolution failed without exception")

    def _resolve_gemini(self, prompt: str) -> LLMResolutionResponse:
        if not self._key_pool.is_available:
            logger.warning("[LLM] GEMINI_API_KEY не задан в .env, вызов LLM пропущен")
            raise ValueError("GEMINI_API_KEY is not configured")

        max_attempts = max(1, self._key_pool.key_count)
        last_exc: Exception | None = None

        for _ in range(max_attempts):
            api_key = self._key_pool.get_next_key()
            if api_key is None:
                break
            try:
                return call_with_retry(
                    lambda key=api_key: self._resolve_gemini_with_key(key, prompt),
                    retry_on=is_retryable_llm_error_without_failover,
                )
            except Exception as exc:
                last_exc = exc
                if is_key_failover_error(exc):
                    self._key_pool.mark_exhausted(api_key)
                    logger.warning(
                        "[LLM] Ошибка %s на ключе ...%s. Переключение на резервный ключ...",
                        exc,
                        api_key[-4:],
                    )
                    continue
                raise

        logger.error("[LLM] Все ключи в пуле исчерпаны")
        if last_exc is not None:
            raise last_exc
        raise ValueError("All Gemini API keys are exhausted")

    def _resolve_ollama(self, prompt: str) -> LLMResolutionResponse:
        payload = {
            "model": self._ollama_model,
            "prompt": prompt,
            "format": "json",
            "stream": False,
            "options": {
                "temperature": 0.0,
            },
        }
        with httpx.Client(timeout=self._timeout) as client:
            response = client.post(f"{self._ollama_base_url}/api/generate", json=payload)
            response.raise_for_status()
            data = response.json()

        raw_json = data.get("response", "")
        if not raw_json:
            raise ValueError("Ollama returned empty response")

        return parse_llm_json_response(raw_json)


def _resolution_cache_key(
    block: RawOrderBlock,
    features: ExtractedFeatures,
    candidates: list[MatchCandidate],
) -> tuple:
    codes = tuple(candidate.catalog_entity.nomenclature_code for candidate in candidates)
    return (
        block.client_description.strip().lower(),
        (block.factory_alias or "").strip().lower(),
        block.item_type.strip().lower(),
        features.package_ratio,
        tuple(features.dimensions),
        codes,
    )


def is_gemini_model_not_found(exc: BaseException) -> bool:
    """Return True when Google GenAI reports 404 NOT_FOUND for the model id."""
    status = getattr(exc, "status_code", None)
    if status == 404:
        return True
    code = getattr(exc, "code", None)
    if code == 404 or str(code).upper() == "NOT_FOUND":
        return True
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) == 404:
        return True
    text = f"{type(exc).__name__} {exc}".upper()
    return "404" in text and ("NOT_FOUND" in text or "NOT FOUND" in text)


def is_key_failover_error(exc: BaseException) -> bool:
    """Return True when the caller should rotate to the next Gemini API key."""
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return True

    status = getattr(exc, "status_code", None)
    if status in {401, 403, 429}:
        return True

    code = getattr(exc, "code", None)
    if code in {401, 403, 429}:
        return True
    if str(code).upper() in {"UNAUTHENTICATED", "PERMISSION_DENIED", "RESOURCE_EXHAUSTED"}:
        return True

    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    if response_status in {401, 403, 429}:
        return True

    text = f"{type(exc).__name__} {exc}".upper()
    failover_markers = (
        "429",
        "401",
        "403",
        "RESOURCE_EXHAUSTED",
        "QUOTA",
        "UNAUTHENTICATED",
        "PERMISSION_DENIED",
        "FORBIDDEN",
        "TOO MANY REQUESTS",
    )
    return any(marker in text for marker in failover_markers)


def is_retryable_llm_error(exc: BaseException) -> bool:
    """Return True for 504 DEADLINE_EXCEEDED, 429, and equivalent timeouts."""
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return True

    status = getattr(exc, "status_code", None)
    if status in _RETRYABLE_STATUS_CODES:
        return True

    code = getattr(exc, "code", None)
    if code in _RETRYABLE_STATUS_CODES:
        return True

    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    if response_status in _RETRYABLE_STATUS_CODES:
        return True

    text = f"{type(exc).__name__} {exc}".upper()
    return any(marker in text for marker in _RETRYABLE_MARKERS)


def is_retryable_llm_error_without_failover(exc: BaseException) -> bool:
    """Retry only transient errors that should not trigger key rotation."""
    if is_key_failover_error(exc):
        return False
    return is_retryable_llm_error(exc)


def call_with_retry(
    func: Callable[[], T],
    *,
    delay_seconds: float = _RETRY_DELAY_SECONDS,
    retry_on: Callable[[BaseException], bool] | None = None,
) -> T:
    """Run ``func`` once more after 1.5s when the first error is retryable."""
    should_retry = retry_on or is_retryable_llm_error
    try:
        return func()
    except Exception as exc:
        if not should_retry(exc):
            raise
        logger.warning("LLM retry after retryable error: %s", exc)
        time.sleep(delay_seconds)
        return func()
