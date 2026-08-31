"""Direct Gemini API connectivity diagnostic (key pool, proxy, JSON matching contract)."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=True)
except ImportError:
    pass

from src.matcher.key_rotator import parse_gemini_api_keys
from src.matcher.llm_resolver import (
    LLMResolver,
    build_gemini_client,
    gemini_models_list_url,
    resolve_gemini_base_url,
)
from src.models import CatalogEntity, ExtractedFeatures, LLMResolutionResponse, MatchCandidate, RawOrderBlock
from src.utils.logger import console

_PING_MODEL = "gemini-3.5-flash-lite"
_PING_PROMPT = "Ping: ответь одним словом OK"


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
        return "404", f"NOT_FOUND — model {_PING_MODEL} unavailable"
    if "504" in upper or "DEADLINE_EXCEEDED" in upper or "TIMEOUT" in upper:
        return "504", "DEADLINE_EXCEEDED / timeout"
    return type(exc).__name__, str(exc)


def _print_config(keys: list[str], base_url: str | None, model: str) -> None:
    provider = os.environ.get("LLM_PROVIDER", "gemini").strip().lower()

    console.print("[bold]Диагностика Gemini API[/bold]")
    console.print(f"  LLM_PROVIDER:    {provider}")
    console.print(f"  GEMINI_API_KEYS: {len(keys)} ключ(ей) в пуле")
    console.print(f"  GEMINI_BASE_URL: {base_url or '(прямой доступ к Google)'}")
    console.print(f"  GEMINI_MODEL:    {model}")
    console.print()


def _probe_models_list(api_key: str, base_url: str | None) -> bool:
    import httpx

    url = gemini_models_list_url(base_url)
    console.print(f"[dim]Проверка списка моделей ({_mask_key_suffix(api_key)}): {url}[/dim]")
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url, params={"key": api_key})
        if response.status_code == 200:
            console.print(f"[green]  models.list → HTTP {response.status_code}[/green]")
            return True
        code, msg = _interpret_gemini_error(Exception(f"HTTP {response.status_code} {response.text[:120]}"))
        console.print(f"[red]  models.list → HTTP {response.status_code}[/red]")
        console.print(f"[dim]  {response.text[:300]}[/dim]")
        console.print(f"  → {code} - {msg}")
        return False
    except Exception as exc:
        code, msg = _interpret_gemini_error(exc)
        console.print(f"[red]  models.list → {type(exc).__name__}: {exc}[/red]")
        console.print(f"  → {code} - {msg}")
        return False


def _ping_single_key(
    index: int,
    total: int,
    api_key: str,
    base_url: str | None,
    model: str,
) -> bool:
    label = f"[Ключ {index}/{total}] {_mask_key_suffix(api_key)}"
    started = time.perf_counter()
    try:
        client = build_gemini_client(api_key, timeout=25.0, base_url=base_url)
        response = client.models.generate_content(
            model=model,
            contents=_PING_PROMPT,
        )
        elapsed = time.perf_counter() - started
        text = (response.text or "").strip()
        if not text:
            console.print(f"{label} ──► [red]🔴 ОШИБКА: EMPTY - Пустой ответ от модели[/red]")
            return False
        console.print(f"{label} ──► [green]🟢 OK (Время ответа: {elapsed:.2f}s)[/green]")
        return True
    except Exception as exc:
        elapsed = time.perf_counter() - started
        code, msg = _interpret_gemini_error(exc)
        console.print(
            f"{label} ──► [red]🔴 ОШИБКА: {code} - {msg}[/red] "
            f"[dim](после {elapsed:.2f}s)[/dim]"
        )
        return False


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
    keys = parse_gemini_api_keys()
    base_url = resolve_gemini_base_url()
    model = os.environ.get("GEMINI_MODEL", _PING_MODEL).strip()

    _print_config(keys, base_url, model)

    if not keys:
        console.print("[red]GEMINI_API_KEYS / GEMINI_API_KEY не заданы. Заполните .env и повторите.[/red]")
        raise SystemExit(1)

    console.print(f"[bold]Найден пул из {len(keys)} ключей Gemini API[/bold]")
    console.print()

    provider = os.environ.get("LLM_PROVIDER", "gemini").strip().lower()
    if provider != "gemini":
        console.print(f"[yellow]LLM_PROVIDER={provider!r} — диагностика всё равно проверит Gemini.[/yellow]")
        console.print()

    ping_results: list[bool] = []
    for index, api_key in enumerate(keys, start=1):
        _probe_models_list(api_key, base_url)
        ping_results.append(_ping_single_key(index, len(keys), api_key, base_url, _PING_MODEL))
        console.print()

    ok_ping = any(ping_results)
    ok_match = _test_matching_contract(base_url, model) if ok_ping else False

    console.print()
    if ok_ping and ok_match:
        active = sum(ping_results)
        console.print(
            f"[bold green]Итог: SUCCESS — {active}/{len(keys)} ключей активны, "
            "JSON-контракт соблюдён.[/bold green]"
        )
        raise SystemExit(0)
    if ok_ping:
        active = sum(ping_results)
        console.print(
            f"[bold yellow]Итог: PARTIAL — {active}/{len(keys)} ключей активны, "
            "но тест сопоставления не прошёл.[/bold yellow]"
        )
        raise SystemExit(1)
    console.print("[bold red]Итог: FAIL — все ключи недоступны. Проверьте ключи, прокси и квоту.[/bold red]")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
