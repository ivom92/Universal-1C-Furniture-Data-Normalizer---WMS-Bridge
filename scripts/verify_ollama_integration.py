"""Verify local Ollama (qwen2.5:7b) integration and run full order_ruban pipeline."""

from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

from rich.panel import Panel
from rich.table import Table

from src.matcher.dynamic_vocab import DynamicVocabulary
from src.matcher.feature_extractor import FeatureExtractor
from src.matcher.hybrid_matcher import HybridMatcher
from src.matcher.llm_resolver import LLMResolver
from src.matcher.vector_store import CatalogVectorStore
from src.models import MatchDecision
from src.parsers.v7_parser import parse_v7_order
from src.parsers.v8_loader import load_catalog_v8
from src.utils.logger import console
from src.utils.reporter import print_match_summary

CATALOG_PATH = PROJECT_ROOT / "data" / "catalog_v8.xlsx"
ORDER_PATH = PROJECT_ROOT / "data" / "orders" / "order_ruban.xlsx"
EXPECTED_TOTAL = 55
EXPECTED_AUTO = 47
EXPECTED_LLM_RANGE = (5, 6)
EXPECTED_QUARANTINE_RANGE = (2, 3)


def _catalog_lookup(decisions: list[MatchDecision]) -> dict[str, str]:
    names: dict[str, str] = {}
    for decision in decisions:
        for candidate in decision.candidates:
            entity = candidate.catalog_entity
            names[entity.nomenclature_code] = entity.nomenclature
        if decision.matched_entity is not None:
            entity = decision.matched_entity
            names[entity.nomenclature_code] = entity.nomenclature
    return names


def _check_ollama(resolver: LLMResolver) -> None:
    console.print("[bold]Шаг 1. Проверка Ollama[/bold]")
    if not resolver.is_available():
        console.print(
            f"[red]Ollama недоступен[/red] по адресу {resolver.ollama_base_url}. "
            "Запустите `ollama serve` и убедитесь, что модель загружена."
        )
        raise SystemExit(1)

    console.print(f"[green]✓[/green] Ollama отвечает: {resolver.ollama_base_url}")

    if not resolver.has_ollama_model():
        console.print(
            f"[red]Модель не найдена:[/red] {resolver.ollama_model}. "
            f"Выполните: ollama pull {resolver.ollama_model}"
        )
        raise SystemExit(1)

    console.print(f"[green]✓[/green] Модель доступна: {resolver.ollama_model}")
    console.print()


def _find_needs_llm_blocks(
    blocks,
    vector_store: CatalogVectorStore,
    feature_extractor: FeatureExtractor,
) -> list[MatchDecision]:
    pre_matcher = HybridMatcher(vector_store, feature_extractor, llm_resolver=None)
    needs_llm: list[MatchDecision] = []
    for block in blocks:
        decision = pre_matcher.match_block(block)
        if decision.status == "NEEDS_LLM":
            needs_llm.append(decision)
    return needs_llm


def _verify_borderline_positions(
    needs_llm: list[MatchDecision],
    resolver: LLMResolver,
    catalog_names: dict[str, str],
) -> None:
    console.print(
        f"[bold]Шаг 3. Верификация {len(needs_llm)} пограничных позиций (NEEDS_LLM)[/bold]"
    )
    if not needs_llm:
        console.print("[yellow]Пограничные позиции не найдены — пропуск LLM-тестов.[/yellow]")
        console.print()
        return

    table = Table(show_header=True, header_style="bold cyan", expand=True)
    table.add_column("№", width=4, justify="right")
    table.add_column("1С 7.7 (клиент)", min_width=24, overflow="fold")
    table.add_column("Код v8", width=13)
    table.add_column("Номенклатура v8", min_width=24, overflow="fold")
    table.add_column("Latency", width=8, justify="right")
    table.add_column("Confidence", width=10, justify="right")
    table.add_column("Статус", width=14)

    for decision in needs_llm:
        block = decision.raw_block
        started = time.perf_counter()
        resolution = resolver.resolve(
            block,
            decision.extracted_features,
            decision.candidates,
        )
        latency = time.perf_counter() - started

        selected_code = resolution.selected_nomenclature_code
        if selected_code and selected_code in catalog_names:
            nomenclature = catalog_names[selected_code]
            status = "MATCHED_LLM"
            status_style = "bold blue"
        elif selected_code:
            nomenclature = "(код не в пуле кандидатов)"
            status = "QUARANTINE"
            status_style = "bold yellow"
        else:
            nomenclature = "—"
            status = "QUARANTINE"
            status_style = "bold yellow"

        console.print(
            f"[dim]#{block.line_number}[/dim] {block.client_description[:70]}… "
            f"[dim]({latency:.2f}s, conf={resolution.confidence:.2f})[/dim]"
        )
        if resolution.reasoning:
            console.print(f"  [dim]Reasoning:[/dim] {resolution.reasoning}")

        table.add_row(
            str(block.line_number),
            block.client_description,
            selected_code or "—",
            nomenclature,
            f"{latency:.2f}s",
            f"{resolution.confidence:.2f}",
            f"[{status_style}]{status}[/{status_style}]",
        )

    console.print()
    console.print(table)
    console.print()


def _validate_summary(decisions: list[MatchDecision]) -> dict[str, int]:
    status_counts = {
        "MATCHED_AUTO": 0,
        "MATCHED_LLM": 0,
        "QUARANTINE": 0,
        "NEEDS_LLM": 0,
    }
    for decision in decisions:
        status_counts[decision.status] = status_counts.get(decision.status, 0) + 1
    return status_counts


def main() -> None:
    if not CATALOG_PATH.exists():
        console.print(f"[red]Каталог не найден:[/red] {CATALOG_PATH}")
        raise SystemExit(1)
    if not ORDER_PATH.exists():
        console.print(f"[red]Заказ не найден:[/red] {ORDER_PATH}")
        raise SystemExit(1)

    console.print(
        "[bold]Sprint 4.1 — Верификация Ollama (qwen2.5:7b)[/bold]\n"
        f"Каталог: {CATALOG_PATH.name} | Заказ: {ORDER_PATH.name}"
    )
    console.print()

    resolver = LLMResolver(provider="ollama")
    _check_ollama(resolver)

    console.print("[bold]Шаг 2. Загрузка данных[/bold]")
    catalog = load_catalog_v8(CATALOG_PATH)
    vocabulary = DynamicVocabulary(catalog)
    feature_extractor = FeatureExtractor(vocabulary)
    vector_store = CatalogVectorStore()
    vector_store.build_or_load_index(catalog)
    parsed = parse_v7_order(ORDER_PATH)
    blocks = parsed.blocks
    console.print(
        f"[green]✓[/green] Каталог: {len(catalog)} поз. | "
        f"Заказ: {len(blocks)} строк | Заказчик: {parsed.customer_name}"
    )
    console.print()

    needs_llm = _find_needs_llm_blocks(blocks, vector_store, feature_extractor)
    console.print(f"[dim]NEEDS_LLM (до LLM):[/dim] {len(needs_llm)} позиций")
    catalog_names = {entity.nomenclature_code: entity.nomenclature for entity in catalog}
    _verify_borderline_positions(needs_llm, resolver, catalog_names)

    console.print("[bold]Шаг 4. Полный интеграционный прогон (LLM_PROVIDER=ollama)[/bold]")
    matcher = HybridMatcher(vector_store, feature_extractor, llm_resolver=resolver)
    decisions = matcher.match_order_decisions(blocks)

    if len(decisions) != len(blocks):
        console.print(
            f"[red]Zero-Loss нарушен:[/red] {len(blocks)} входных → {len(decisions)} решений"
        )
        raise SystemExit(1)

    print_match_summary(decisions, parsed.customer_name)

    counts = _validate_summary(decisions)
    total = len(decisions)
    llm = counts["MATCHED_LLM"]
    quarantine = counts["QUARANTINE"]
    auto = counts["MATCHED_AUTO"]
    needs_llm_left = counts["NEEDS_LLM"]

    acceptance_ok = (
        total == EXPECTED_TOTAL
        and auto == EXPECTED_AUTO
        and EXPECTED_LLM_RANGE[0] <= llm <= EXPECTED_LLM_RANGE[1]
        and EXPECTED_QUARANTINE_RANGE[0] <= quarantine <= EXPECTED_QUARANTINE_RANGE[1]
        and needs_llm_left == 0
    )

    lines = [
        f"Всего: {total} (ожидание: {EXPECTED_TOTAL})",
        f"MATCHED_AUTO: {auto} (ожидание: {EXPECTED_AUTO})",
        f"MATCHED_LLM: {llm} (ожидание: {EXPECTED_LLM_RANGE[0]}–{EXPECTED_LLM_RANGE[1]})",
        f"QUARANTINE: {quarantine} (ожидание: {EXPECTED_QUARANTINE_RANGE[0]}–{EXPECTED_QUARANTINE_RANGE[1]})",
        f"NEEDS_LLM (остаток): {needs_llm_left} (ожидание: 0)",
    ]
    border_style = "green" if acceptance_ok else "yellow"
    title = "Критерии приёмки" + (" — OK" if acceptance_ok else " — проверьте вручную")
    console.print(Panel("\n".join(lines), title=f"[bold]{title}[/bold]", border_style=border_style))

    if needs_llm_left > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
