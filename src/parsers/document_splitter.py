"""Split a combined 1C v7.7 workbook into independent picking-list sections.

A single sheet may contain more than one "Отборочная ведомость" sub-document
back to back — e.g. a corpus-furniture table followed by a soft-furniture
table, each with its own header and its own "ИТОГО мест по отборочной
ведомости" footer. This module locates those section boundaries and exposes a
row-windowed view of the sheet for each section so the existing V7 / soft
furniture parsers can run against them unmodified, then merges the results
into a single ``V7ParseResult`` with continuous 1..N line numbering.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from src.models import RawOrderBlock, V7ParseResult
from src.parsers.soft_furniture_parser import parse_soft_furniture_blocks_from_sheet
from src.parsers.v7_parser import (
    V7Source,
    extract_customer_name_from_sheet,
    normalize_incoming_text,
    open_order_sheet,
    parse_v7_blocks_from_sheet,
)
from src.utils.logger import get_logger

logger = get_logger()

CORPUS_TITLE_RE = re.compile(
    r"отборочн\w*\s+ведомост\w*.{0,60}?корпусн\w*",
    re.IGNORECASE,
)
SOFT_TITLE_RE = re.compile(
    r"отборочн\w*\s+ведомост\w*.{0,60}?мягк\w*",
    re.IGNORECASE,
)
FOOTER_TOTAL_RE = re.compile(r"итого\s+мест[^0-9]{0,80}?(\d+)", re.IGNORECASE)
ORDER_REFERENCE_RE = re.compile(r"([A-ZА-ЯЁ]{2,6}-\d{3,})", re.IGNORECASE)
_SECTION_SCAN_COLS = 20


class SectionType(str, Enum):
    STANDARD = "STANDARD"
    SOFT = "SOFT"


@dataclass(frozen=True)
class DocumentSection:
    """One independent sub-document inside a combined (composite) workbook."""

    section_type: SectionType
    start_row: int
    end_row: int
    title: str
    declared_places: Optional[int] = None


class _WindowedSheetView:
    """Expose rows ``[start_row, end_row]`` of a source sheet as rows ``[1, N]``."""

    def __init__(self, source, start_row: int, end_row: int) -> None:
        self._source = source
        self._offset = start_row - 1
        self.max_row = max(end_row - start_row + 1, 0)
        self.max_column = source.max_column

    def cell_value(self, row: int, col: int) -> object:
        if row < 1 or row > self.max_row:
            return None
        return self._source.cell_value(row + self._offset, col)

    def cell_fill_rgb(self, row: int, col: int) -> str:
        if row < 1 or row > self.max_row:
            return "00000000"
        return self._source.cell_fill_rgb(row + self._offset, col)


def _join_row_text(sheet, row: int, *, max_col: int = _SECTION_SCAN_COLS) -> str:
    last_col = min(max(sheet.max_column, 1), max_col)
    parts: list[str] = []
    for col in range(1, last_col + 1):
        text = normalize_incoming_text(sheet.cell_value(row, col))
        if text:
            parts.append(text)
    return " ".join(parts)


def split_document_sections(sheet) -> list[DocumentSection]:
    """Locate STANDARD / SOFT sub-document boundaries by header/footer markers."""
    titles: list[tuple[int, SectionType, str]] = []
    footers: list[tuple[int, Optional[int]]] = []

    for row in range(1, sheet.max_row + 1):
        text = _join_row_text(sheet, row)
        if not text:
            continue
        if CORPUS_TITLE_RE.search(text):
            titles.append((row, SectionType.STANDARD, text))
            continue
        if SOFT_TITLE_RE.search(text):
            titles.append((row, SectionType.SOFT, text))
            continue
        footer_match = FOOTER_TOTAL_RE.search(text)
        if footer_match:
            footers.append((row, int(footer_match.group(1))))

    if not titles:
        return []

    sections: list[DocumentSection] = []
    for index, (title_row, section_type, title_text) in enumerate(titles):
        next_title_row = (
            titles[index + 1][0] if index + 1 < len(titles) else sheet.max_row + 1
        )
        section_footer = next(
            (footer for footer in footers if title_row < footer[0] < next_title_row),
            None,
        )
        end_row = section_footer[0] if section_footer is not None else next_title_row - 1
        declared_places = section_footer[1] if section_footer is not None else None
        sections.append(
            DocumentSection(
                section_type=section_type,
                start_row=title_row,
                end_row=end_row,
                title=title_text,
                declared_places=declared_places,
            )
        )
    return sections


def extract_order_reference(text: Optional[str]) -> Optional[str]:
    """Pull an order code like ``ЦНТ-001415`` out of a free-text service line."""
    if not text:
        return None
    match = ORDER_REFERENCE_RE.search(text)
    return match.group(1).upper() if match else None


def parse_composite_order(source: V7Source, filename: str | None = None) -> V7ParseResult:
    """Split a combined workbook into sections, parse each with its dedicated
    pipeline, and merge the results with continuous 1..N line numbering.

    Standard (corpus) sections use the V7 3-row block state machine;
    soft-furniture sections expand parent SKUs into physical packages. Each
    output block is tagged with ``is_soft_furniture`` (routing) and
    ``customer_override`` (per-row order id, e.g. ``ЦНТ-001415``), so the
    caller can dispatch matching and WMS export correctly per row.
    """
    resolved: V7Source = Path(source) if isinstance(source, str) else source
    source_name = filename or (resolved.name if isinstance(resolved, Path) else "unknown")

    with open_order_sheet(source, filename=filename) as sheet:
        sections = split_document_sections(sheet)
        if len(sections) < 2:
            logger.warning(
                "COMPOSITE_PICKING_LIST detected but only %s section(s) found in %s",
                len(sections),
                source_name,
            )

        all_blocks: list[RawOrderBlock] = []
        declared_total = 0
        has_declared = False
        primary_customer_name: str | None = None
        fallback_labels: list[str] = []

        for section in sections:
            window = _WindowedSheetView(sheet, section.start_row, section.end_row)
            if section.section_type is SectionType.SOFT:
                section_customer, blocks, declared = parse_soft_furniture_blocks_from_sheet(
                    window, source_name
                )
                for block in blocks:
                    block.is_soft_furniture = True
            else:
                section_customer = extract_customer_name_from_sheet(window, source_name)
                blocks = parse_v7_blocks_from_sheet(window)
                declared = section.declared_places
                if primary_customer_name is None:
                    primary_customer_name = section_customer

            running_reference = section_customer
            for block in blocks:
                block.excel_row_start = block.excel_row_start + section.start_row - 1
                reference = extract_order_reference(block.order_service_line)
                if reference:
                    running_reference = reference
                block.customer_override = running_reference

            all_blocks.extend(blocks)
            if section_customer not in fallback_labels:
                fallback_labels.append(section_customer)

            if declared is not None:
                declared_total += declared
                has_declared = True

        for index, block in enumerate(all_blocks, start=1):
            block.line_number = index

        actual_places = sum(block.quantity for block in all_blocks)
        mismatch = has_declared and actual_places != declared_total

        customer_name = primary_customer_name or (
            fallback_labels[0] if fallback_labels else source_name
        )

        logger.debug(
            "[CompositeSplit] File '%s': %s section(s), %s block(s), places=%s "
            "(declared=%s mismatch=%s)",
            source_name,
            len(sections),
            len(all_blocks),
            actual_places,
            declared_total if has_declared else None,
            mismatch,
        )

    return V7ParseResult(
        customer_name=customer_name,
        blocks=all_blocks,
        declared_places=declared_total if has_declared else None,
        checksum_mismatch=mismatch,
    )
