"""Rich terminal reporter for catalog matching results."""

from __future__ import annotations

from collections.abc import Mapping

from rich.panel import Panel
from rich.table import Table

from src.models import MatchDecision, MatchedOrderItem
from src.utils.logger import console

_STATUS_STYLES = {
    "MATCHED_AUTO": "green",
    "MATCHED_LLM": "bold blue",
    "QUARANTINE": "bold yellow",
    "NEEDS_LLM": "bold magenta",
}
_LLM_METHODS = frozenset({"MATCHED_LLM", "LLM", "LLM_NO_BARCODE"})


def _status_style(status: str) -> str:
    return _STATUS_STYLES.get(status, "white")


def _barcode_present(value: object | None) -> bool:
    return bool(value is not None and str(value).strip())


def get_status_badge(item: MatchDecision | MatchedOrderItem) -> str:
    """Operator badge for Streamlit and Rich CLI (barcode-aware)."""
    if isinstance(item, MatchedOrderItem):
        reason = (item.match_reason or "").strip()
        if reason == "QUARANTINE":
            return "🟡 Карантин"
        method = reason
        barcode = item.barcode
    else:
        if item.status == "QUARANTINE":
            return "🟡 Карантин"
        if item.status == "NEEDS_LLM":
            return "🟠 Ожидает LLM"
        method = (item.match_method or item.status or "").strip()
        barcode = item.matched_entity.barcode if item.matched_entity is not None else None

    method_upper = method.upper()
    is_llm = (
        item.status == "MATCHED_LLM"
        if isinstance(item, MatchDecision)
        else False
    ) or method in _LLM_METHODS or method_upper.startswith("LLM")
    if is_llm:
        return "🔵 LLM" if _barcode_present(barcode) else "🔵 LLM (без ШК)"

    if _barcode_present(barcode):
        return "🟢 Авто (со штрихкодом)"
    return "🟢 Авто (без ШК)"


def count_without_barcode(
    decisions: list[MatchDecision],
    overrides: Mapping[int, str] | Mapping[str, str] | None = None,
) -> int:
    """Rows matched without a factory EAN-13 (empty Штрихкод), minus operator scans."""
    overridden: set[int] = set()
    if overrides:
        for key, value in overrides.items():
            if value is not None and str(value).strip():
                overridden.add(int(key))
    total = 0
    for decision in decisions:
        if decision.status not in {"MATCHED_AUTO", "MATCHED_LLM"}:
            continue
        if int(decision.order_line_number) in overridden:
            continue
        barcode = decision.matched_entity.barcode if decision.matched_entity is not None else None
        if not _barcode_present(barcode):
            total += 1
    return total


def _factory_name(decision: MatchDecision) -> str:
    if decision.matched_entity is not None:
        return decision.matched_entity.nomenclature
    if decision.status == "QUARANTINE":
        return "— (Отсутствует в 1С 8)"
    if decision.candidates:
        return decision.candidates[0].catalog_entity.nomenclature
    return "—"


def _barcode(decision: MatchDecision) -> str:
    if decision.matched_entity is not None and decision.matched_entity.barcode:
        return decision.matched_entity.barcode
    return "—"


def _match_method(decision: MatchDecision) -> str:
    if decision.match_method:
        return decision.match_method
    if decision.status == "MATCHED_AUTO":
        return "vector_auto"
    return decision.status


def print_match_summary(decisions: list[MatchDecision], customer_name: str) -> None:
    """Print a colorized Rich table and summary panel for matching decisions."""
    table = Table(
        title=f"Отчёт сопоставления: {customer_name}",
        show_header=True,
        header_style="bold cyan",
        show_lines=False,
        expand=True,
    )
    table.add_column("№", width=4, justify="right")
    table.add_column("Статус", width=24)
    table.add_column("Клиентское наименование (1С 7.7)", min_width=28, overflow="fold")
    table.add_column("Фабричный эталон (1С v8)", min_width=28, overflow="fold")
    table.add_column("Штрихкод (EAN-13)", width=16)
    table.add_column("Скор", width=7, justify="right")
    table.add_column("Метод", width=14)

    status_counts = {
        "MATCHED_AUTO": 0,
        "MATCHED_LLM": 0,
        "QUARANTINE": 0,
        "NEEDS_LLM": 0,
    }
    barcodes_recovered = 0

    for decision in sorted(decisions, key=lambda item: item.order_line_number):
        block = decision.raw_block
        status = decision.status
        status_counts[status] = status_counts.get(status, 0) + 1

        if decision.matched_entity is not None and decision.matched_entity.barcode:
            barcodes_recovered += 1

        score_text = f"{decision.confidence_score:.3f}" if decision.confidence_score else "—"
        style = _status_style(status)

        badge = get_status_badge(decision)
        table.add_row(
            str(block.order_line_number),
            f"[{style}]{badge}[/{style}]",
            block.client_description,
            _factory_name(decision),
            _barcode(decision),
            score_text,
            _match_method(decision),
        )

    console.print(table)
    console.print()

    total = len(decisions)
    auto = status_counts["MATCHED_AUTO"]
    llm = status_counts["MATCHED_LLM"]
    quarantine = status_counts["QUARANTINE"]
    needs_llm = status_counts["NEEDS_LLM"]
    without_barcode = count_without_barcode(decisions)

    def _pct(count: int) -> str:
        return f"{count / total * 100:.1f}%" if total else "0.0%"

    summary_lines = [
        f"Всего строк: {total}",
        f"Авто-сопоставлено (MATCHED_AUTO): {auto} ({_pct(auto)})",
        f"Разрешено через LLM (MATCHED_LLM): {llm} ({_pct(llm)})",
        f"В карантине (QUARANTINE): {quarantine} ({_pct(quarantine)})",
    ]
    if needs_llm:
        summary_lines.append(f"Ожидают LLM (NEEDS_LLM): {needs_llm} ({_pct(needs_llm)})")
    summary_lines.append(f"Восстановлено заводских штрихкодов: {barcodes_recovered} шт.")
    summary_lines.append(f"Без ШК: {without_barcode} ({_pct(without_barcode)})")

    console.print(
        Panel(
            "\n".join(summary_lines),
            title="[bold]Сводка[/bold]",
            border_style="green",
        )
    )
