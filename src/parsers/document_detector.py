"""Classify incoming 1C workbooks: standard picking list vs soft-furniture transfer."""

from __future__ import annotations

import io
from enum import Enum
from pathlib import Path

from src.parsers.document_splitter import CORPUS_TITLE_RE, SOFT_TITLE_RE
from src.parsers.v7_parser import V7Source, normalize_incoming_text, open_order_sheet

_HEADER_SCAN_ROWS = 20
_FULL_SCAN_ROWS = 250


class DocumentType(str, Enum):
    STANDARD_PICKING_LIST = "STANDARD_PICKING_LIST"
    SOFT_FURNITURE_TRANSFER = "SOFT_FURNITURE_TRANSFER"
    COMPOSITE_PICKING_LIST = "COMPOSITE_PICKING_LIST"


class DocumentTypeDetector:
    """Marker-based detector for 1C 7.7 warehouse documents."""

    def detect(
        self,
        file_path: Path | str | bytes | io.BytesIO,
        filename: str | None = None,
    ) -> DocumentType:
        source: V7Source
        if isinstance(file_path, str):
            source = Path(file_path)
        else:
            source = file_path
        with open_order_sheet(source, filename=filename) as sheet:
            header_blob = _join_cells(sheet, max_row=_HEADER_SCAN_ROWS).lower()
            full_blob = _join_cells(sheet, max_row=_FULL_SCAN_ROWS).lower()
        if CORPUS_TITLE_RE.search(full_blob) and SOFT_TITLE_RE.search(full_blob):
            return DocumentType.COMPOSITE_PICKING_LIST
        if "мягкая мебель" in header_blob or "состоит из упаковок" in full_blob:
            return DocumentType.SOFT_FURNITURE_TRANSFER
        return DocumentType.STANDARD_PICKING_LIST


def _join_cells(sheet, *, max_row: int) -> str:
    parts: list[str] = []
    last_row = min(sheet.max_row, max_row)
    last_col = max(sheet.max_column, 1)
    for row in range(1, last_row + 1):
        for col in range(1, last_col + 1):
            text = normalize_incoming_text(sheet.cell_value(row, col))
            if text:
                parts.append(text)
    return " ".join(parts)
