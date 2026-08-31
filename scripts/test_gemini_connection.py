"""Direct Gemini API connectivity diagnostic (key pool, proxy, JSON matching contract)."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=True)
except ImportError:
    pass

from src.config import get_config
from src.llm.gemini_client import build_gemini_client
from src.matcher.llm_resolver import LLMResolver
from src.models import CatalogEntity, ExtractedFeatures, LLMResolutionResponse, MatchCandidate, RawOrderBlock
from src.utils.logger import console

_PING_PROMPT = "Ping: ответь одним словом OK"
_EMPTY_KEYS_HINT = (
    "⚠️ Переменная GEMINI_API_KEYS пуста. Добавьте ключи в .env или Coolify."
)


@dataclass(frozen=True)
class KeyPingResult:
    index: int
    ok: bool
    latency_ms: float
    error_code: str | None = None


def _mask_key_suffix(api_key: str) -> str:
    if len(api_key) <= 4:
        return "***"
    return f"...{api_key[-4:]}"


def _interpret_gemini_error(exc: BaseException) -> tuple[str, str]:
    """Return (error_code, error_msg) for display."""
    text = f"{type(exc).__name__} {exc}"
    upper = text.upper()

    if "429" in upper or "RESOURCE_EXHAUSTED" in upper or "QUOTA" in upper:
        return "429", "RESOURCE_EXHAUSTED / Quota exceeded"
    if "401" in upper or "UNAUTHENTICATED" in upper:
        return "401", "UNAUTHENTICATED / invalid API key"
    if "403" in upper or "PERMISSION_DENIED" in upper or "FORBIDDEN" in upper:
        return "403", "PERMISSION_DENIED / access forbidden"
    if "404" in upper or "NOT_FOUND" in upper:
        return "404", "NOT_FOUND — model unavailable"
    if "504" in upper or "DEADLINE_EXCEEDED" in upper or "TIMEOUT" in upper:
        return "504", "DEADLINE_EXCEEDED / timeout"
    return type(exc).__name__, str(exc)


def _format_ok_status(index: int, latency_ms: float) -> str:
    return f"🟢 Ключ #{index}: OK (Задержка {latency_ms:.0f}мс)"


def _format_error_status(index: int, error_code: str) -> str:
    return f"🔴 Ключ #{index}: Ошибка ({error_code})"


def _print_config(keys: list[str], base_url: str | None, model: str, provider: str) -> None:
    console.print("[bold]Диагностика Gemini API[/bold]")
    console.print(f"  LLM_PROVIDER:    {provider}")
    console.print(f"  GEMINI_API_KEYS: {len(keys)} ключ(ей) в пуле")
    console.print(f"  GEMINI_BASE_URL: {base_url or '(прямой доступ к Google)'}")
    console.print(f"  GEMINI_MODEL:    {model}")
    console.print()


def _ping_generate_content(
    index: int,
    api_key: str,
    *,
    base_url: str | None,
    model: str,
) -> KeyPingResult:
    """Send a micro ``generateContent`` request through the configured Cloudflare proxy."""
    started = time.perf_counter()
    try:
        client = build_gemini_client(api_key, timeout=25.0, base_url=base_url)
        response = client.models.generate_content(
            model=model,
            contents=_PING_PROMPT,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        text = (response.text or "").strip()
        if not text:
            return KeyPingResult(index=index, ok=False, latency_ms=elapsed_ms, error_code="EMPTY")
        return KeyPingResult(index=index, ok=True, latency_ms=elapsed_ms)
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        code, _msg = _interpret_gemini_error(exc)
        return KeyPingResult(index=index, ok=False, latency_ms=elapsed_ms, error_code=code)


def _print_key_result(result: KeyPingResult, api_key: str) -> None:
    suffix = _mask_key_suffix(api_key)
    if result.ok:
        console.print(f"{_format_ok_status(result.index, result.latency_ms)} [dim]{suffix}[/dim]")
        return
    console.print(
        f"{_format_error_status(result.index, result.error_code or 'UNKNOWN')} "
        f"[dim]{suffix}[/dim]"
    )


def _test_matching_contract(base_url: str | None, model: str) -> bool:
    console.print()
    console.print("[bold]Тест сопоставления:[/bold] мебельная позиция Аврора 1/2 (JSON-контракт LLMResolver)")

    entity_a = CatalogEntity.model_validate(
        {
            "Номенклатура": "Аврора Кровать 140 с основанием 1/2 дуб сонома/белый",
            "НоменклатураКод": "00000012345",
            "Упаковка": "1/2",
            "Модуль": "140",
            "ЭтикеткаМодель": "Аврора",
        }
    )
    entity_b = CatalogEntity.model_validate(
        {
            "Номенклатура": "Аврора Кровать 140 с основанием 2/2 дуб сонома/белый",
            "НоменклатураКод": "00000012346",
            "Упаковка": "2/2",
            "Модуль": "140",
            "ЭтикеткаМодель": "Аврора",
        }
    )
    block = RawOrderBlock(
        line_number=1,
        client_description="Аврора Кровать 140 с основанием 1/2 дуб сонома/белый",
        item_type="Пачка",
        quantity=1,
        factory_alias="Аврора Кровать 140 1/2",
        order_service_line="Продажи оптовые УРП_ test",
        excel_row_start=1,
    )
    features = ExtractedFeatures(package_ratio="1/2", matched_models=["Аврора"])
    candidates = [
        MatchCandidate(catalog_entity=entity_a, similarity_score=0.91),
        MatchCandidate(catalog_entity=entity_b, similarity_score=0.89),
    ]

    resolver = LLMResolver(
        provider="gemini",
        gemini_model=model,
        timeout=25.0,
    )
    if base_url:
        resolver._gemini_base_url = base_url  # noqa: SLF001 — diagnostic override

    started = time.perf_counter()
    try:
        result = resolver.resolve(block, features, candidates)
        elapsed = time.perf_counter() - started
    except Exception as exc:
        code, msg = _interpret_gemini_error(exc)
        console.print(f"[red][FAIL][/red] resolve() → {code} - {msg}")
        return False

    if not isinstance(result, LLMResolutionResponse):
        console.print("[red][FAIL][/red] Ответ не является LLMResolutionResponse")
        return False

    console.print(
        f"  SKU={result.selected_nomenclature_code!r} | "
        f"conf={result.confidence:.2f} | reasoning={result.reasoning!r}"
    )
    if result.selected_nomenclature_code == "00000012345":
        console.print(
            f"[green][SUCCESS][/green] JSON-контракт OK, выбран 1/2 "
            f"(время {elapsed:.2f}s)"
        )
        return True

    console.print(
        "[yellow][WARN][/yellow] Ответ валиден, но выбран другой код "
        f"({result.selected_nomenclature_code!r}). Проверьте модель вручную."
    )
    return result.selected_nomenclature_code is not None


def main() -> None:
    config = get_config()
    keys = config.gemini_api_keys
    base_url = config.gemini_base_url
    model = config.gemini_model
    provider = config.llm_provider.strip().lower()

    _print_config(keys, base_url, model, provider)

    if not keys:
        console.print(f"[yellow]{_EMPTY_KEYS_HINT}[/yellow]")
        raise SystemExit(1)

    console.print(f"[bold]Проверка пула из {len(keys)} ключей (generateContent через прокси)[/bold]")
    console.print()

    if provider != "gemini":
        console.print(f"[yellow]LLM_PROVIDER={provider!r} — диагностика всё равно проверит Gemini.[/yellow]")
        console.print()

    ping_results: list[KeyPingResult] = []
    for index, api_key in enumerate(keys, start=1):
        result = _ping_generate_content(index, api_key, base_url=base_url, model=model)
        _print_key_result(result, api_key)
        ping_results.append(result)

    ok_ping = any(result.ok for result in ping_results)
    ok_match = _test_matching_contract(base_url, model) if ok_ping else False

    console.print()
    active = sum(1 for result in ping_results if result.ok)
    if ok_ping and ok_match:
        console.print(
            f"[bold green]Итог: SUCCESS — {active}/{len(keys)} ключей активны, "
            "JSON-контракт соблюдён.[/bold green]"
        )
        raise SystemExit(0)
    if ok_ping:
        console.print(
            f"[bold yellow]Итог: PARTIAL — {active}/{len(keys)} ключей активны, "
            "но тест сопоставления не прошёл.[/bold yellow]"
        )
        raise SystemExit(1)
    console.print("[bold red]Итог: FAIL — все ключи недоступны. Проверьте ключи, прокси и квоту.[/bold red]")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
