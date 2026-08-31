"""Batch scan-station helpers: quarantine / no-barcode isolation per order."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.adapters.wms_excel_adapter import WMSExcelAdapter
from src.models import MatchDecision

_NO_BARCODE_METHODS = frozenset(
    {
        "AUTO_NO_BARCODE",
        "MATCHED_AUTO_NO_BARCODE",
        "LLM_NO_BARCODE",
        "exact_article_no_barcode",
    }
)


def normalize_line_overrides(
    overrides: Mapping[int, str] | Mapping[str, str] | None,
) -> dict[int, str]:
    return WMSExcelAdapter.normalize_overrides(overrides)


def is_quarantine_open(decision: MatchDecision, overrides: Mapping[int, str]) -> bool:
    return decision.status == "QUARANTINE" and int(decision.order_line_number) not in overrides


def is_no_barcode_open(decision: MatchDecision, overrides: Mapping[int, str]) -> bool:
    if decision.status == "QUARANTINE":
        return False
    if int(decision.order_line_number) in overrides:
        return False
    barcode = ""
    if decision.matched_entity is not None:
        barcode = WMSExcelAdapter._clean_barcode(decision.matched_entity.barcode)
    method = (decision.match_method or "").strip()
    if method in _NO_BARCODE_METHODS or method.upper() in _NO_BARCODE_METHODS:
        return not barcode
    return not barcode


def partition_scan_attention(
    decisions: Sequence[MatchDecision],
    overrides: Mapping[int, str] | Mapping[str, str] | None = None,
) -> tuple[list[MatchDecision], list[MatchDecision]]:
    """Return (open QUARANTINE rows, open AUTO_NO_BARCODE / empty-EAN rows)."""
    resolved = normalize_line_overrides(overrides)
    quarantine = [item for item in decisions if is_quarantine_open(item, resolved)]
    no_barcode = [item for item in decisions if is_no_barcode_open(item, resolved)]
    return quarantine, no_barcode


def station_status_badge(decision: MatchDecision) -> str:
    if decision.status == "QUARANTINE":
        return "🟡 Карантин"
    return "⚪ Без ШК"


def format_station_order_label(
    filename: str,
    *,
    total_rows: int,
    total_places: int,
    quarantine_count: int,
) -> str:
    return (
        f"{filename} (Строк: {total_rows} | Мест: {total_places} | Карантин: {quarantine_count})"
    )


def attention_by_order(
    batch: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Isolate scan-attention rows per order_id (line numbers may overlap across files)."""
    isolated: dict[str, dict[str, Any]] = {}
    for item in batch:
        order_id = str(item.get("order_id") or "")
        decisions: Sequence[MatchDecision] = item.get("decisions") or []
        overrides = item.get("overrides") or {}
        quarantine, no_barcode = partition_scan_attention(decisions, overrides)
        isolated[order_id] = {
            "filename": item.get("filename") or "",
            "quarantine": quarantine,
            "no_barcode": no_barcode,
            "quarantine_lines": [int(row.order_line_number) for row in quarantine],
            "no_barcode_lines": [int(row.order_line_number) for row in no_barcode],
        }
    return isolated
