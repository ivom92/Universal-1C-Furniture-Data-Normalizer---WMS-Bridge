"""FSM parser for 1C soft-furniture transfer sheets (K parents × N packages)."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from src.models import RawOrderBlock, V7ParseResult
from src.parsers.v7_parser import (
    V7Source,
    normalize_incoming_text,
    open_order_sheet,
)
from src.utils.logger import get_logger

logger = get_logger()

_PACKAGES_MARKER_RE = re.compile(r"состоит\s+из\s+упаковок", re.IGNORECASE)
_FOOTER_RE = re.compile(r"итого\s+мест", re.IGNORECASE)
_SIGNATURE_RE = re.compile(r"кладовщик|экспедитор", re.IGNORECASE)
_ORDER_ID_RE = re.compile(r"заказ\s*:\s*([A-ZА-ЯЁ0-9\-]+)", re.IGNORECASE)
_ORDER_INFO_RE = re.compile(r"перемещение\s+на\s+склад", re.IGNORECASE)
_FABRIC_LABEL_RE = re.compile(
    r"^(?:ткань|цвет|обивка)\s*:?\s*(.+)$",
    re.IGNORECASE,
)
_LINE_NO_RE = re.compile(r"^\d+$")
_QTY_HEADER_RE = re.compile(r"кол-во|количество|к-во|^кол\.?$", re.IGNORECASE)
_NUM_HEADER_RE = re.compile(r"^№|^n\s*п", re.IGNORECASE)
_PRODUCT_HEADER_RE = re.compile(r"товар|наименование", re.IGNORECASE)
_FABRIC_HEADER_RE = re.compile(r"ткань|обивка", re.IGNORECASE)
_COLOR_HEADER_RE = re.compile(r"^цвет$", re.IGNORECASE)
_WEIGHT_HEADER_RE = re.compile(r"вес", re.IGNORECASE)
_MARK_HEADER_RE = re.compile(r"отметка", re.IGNORECASE)
_PACKAGE_RATIO_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
_TOTAL_PLACES_RE = re.compile(r"(\d+)")


class _State(str, Enum):
    SEEK_PARENT = "SEEK_PARENT"
    SEEK_ORDER_INFO = "SEEK_ORDER_INFO"
    READ_PACKAGES = "READ_PACKAGES"
    CHECK_TOTALS = "CHECK_TOTALS"


@dataclass
class _Columns:
    line_no: int | None = None
    product: int | None = None
    fabric: int | None = None
    color: int | None = None
    qty: int | None = None
    weight: int | None = None
    mark: int | None = None
    header_row: int | None = None


def parse_soft_furniture_order(
    source: Path | str | bytes | io.BytesIO,
    filename: str | None = None,
) -> V7ParseResult:
    """Expand each parent SKU into one WMS row per physical package."""
    resolved: V7Source
    if isinstance(source, str):
        resolved = Path(source)
    else:
        resolved = source
    source_name = filename or (
        resolved.name if isinstance(resolved, Path) else "unknown"
    )
    with open_order_sheet(source, filename=filename) as sheet:
        customer_name = _extract_customer_name(sheet, source_name)
        declared_places = _extract_declared_places(sheet)
        blocks = _parse_fsm(sheet, customer_name)

    actual_places = sum(block.quantity for block in blocks)
    mismatch = declared_places is not None and actual_places != declared_places
    if mismatch:
        logger.warning(
            "ИТОГО мест mismatch: declared=%s parsed=%s file=%s",
            declared_places,
            actual_places,
            source_name,
        )
    logger.debug(
        "[SoftFSM] File '%s' parents parsed. Checksum total: %s (declared=%s mismatch=%s)",
        source_name,
        actual_places,
        declared_places,
        mismatch,
    )
    return V7ParseResult(
        customer_name=customer_name,
        blocks=blocks,
        declared_places=declared_places,
        checksum_mismatch=mismatch,
    )


def parse_soft_furniture_blocks_from_sheet(
    sheet,
    source_name: str,
) -> tuple[str, list[RawOrderBlock], int | None]:
    """Parse packages directly from an already-open (possibly windowed) sheet.

    Used by the document splitter to run the soft-furniture cascade against a
    single section of a combined (``COMPOSITE_PICKING_LIST``) workbook.
    """
    customer_name = _extract_customer_name(sheet, source_name)
    declared_places = _extract_declared_places(sheet)
    blocks = _parse_fsm(sheet, customer_name)
    return customer_name, blocks, declared_places


def _extract_customer_name(sheet, source_name: str) -> str:
    last_row = min(sheet.max_row, 40)
    for row in range(1, last_row + 1):
        blob = _row_blob(sheet, row)
        match = _ORDER_ID_RE.search(blob)
        if match:
            return match.group(1).strip()
    return f"Перемещение ({Path(source_name).name})" if source_name else "Не указан"


def _extract_declared_places(sheet) -> int | None:
    last_row = sheet.max_row
    last_col = max(sheet.max_column, 1)
    for row in range(1, last_row + 1):
        blob = _row_blob(sheet, row)
        if not _FOOTER_RE.search(blob):
            continue
        match = _TOTAL_PLACES_RE.search(blob)
        if match:
            return int(match.group(1))
        for col in range(1, last_col + 1):
            text = normalize_incoming_text(sheet.cell_value(row, col))
            if text.isdigit():
                return int(text)
    return None


def _parse_fsm(sheet, customer_name: str) -> list[RawOrderBlock]:
    columns = _detect_columns(sheet)
    qty_col = columns.qty
    blocks: list[RawOrderBlock] = []
    state = _State.SEEK_PARENT
    parent_title = ""
    fabric_or_color = ""
    parent_qty = 1
    order_reference = customer_name
    saw_packages_marker = False
    parent_pkg_count = 0
    parent_pkg_places = 0
    line_number = 0
    last_row = sheet.max_row
    row = (columns.header_row or 0) + 1

    def finish_parent() -> None:
        nonlocal parent_pkg_count, parent_pkg_places
        if not parent_title or parent_pkg_count <= 0:
            parent_pkg_count = 0
            parent_pkg_places = 0
            return
        running = sum(block.quantity for block in blocks)
        logger.debug(
            "[SoftFSM] Parent '%s' -> extracted %s pkgs. Checksum total: %s",
            parent_title,
            parent_pkg_count,
            running,
        )
        parent_pkg_count = 0
        parent_pkg_places = 0

    def emit_package(package_name: str, quantity: int, excel_row: int) -> None:
        nonlocal line_number, parent_pkg_count, parent_pkg_places
        line_number += 1
        qty = max(quantity, 1) * max(parent_qty, 1)
        parent_pkg_count += 1
        parent_pkg_places += qty
        blocks.append(
            _package_block(
                line_number,
                parent_title,
                fabric_or_color,
                package_name,
                qty,
                excel_row,
                order_reference,
            )
        )

    def emit_monoblock(excel_row: int) -> None:
        emit_package("", 1, excel_row)

    while row <= last_row:
        cells = _row_cells(sheet, row)
        if not cells:
            row += 1
            continue
        blob = " ".join(cells)

        if _FOOTER_RE.search(blob) or _SIGNATURE_RE.search(blob):
            if parent_title and not saw_packages_marker:
                emit_monoblock(parent_excel_row)
            finish_parent()
            state = _State.CHECK_TOTALS
            break

        if state == _State.SEEK_PARENT:
            if _is_parent_row(sheet, row, columns, blob):
                parent_title, fabric_or_color, parent_qty = _read_parent(
                    sheet, row, columns, blob
                )
                order_reference = customer_name
                saw_packages_marker = False
                parent_excel_row = row
                state = _State.SEEK_ORDER_INFO
            row += 1
            continue

        if state == _State.SEEK_ORDER_INFO:
            if _is_parent_row(sheet, row, columns, blob):
                emit_monoblock(parent_excel_row)
                finish_parent()
                state = _State.SEEK_PARENT
                continue
            fabric = _extract_fabric(blob)
            if fabric:
                fabric_or_color = fabric
                row += 1
                continue
            if _is_order_info_row(blob):
                order_reference = _order_reference_from_blob(blob, customer_name)
                row += 1
                state = _State.READ_PACKAGES
                continue
            state = _State.READ_PACKAGES
            continue

        if state == _State.READ_PACKAGES:
            if _is_parent_row(sheet, row, columns, blob):
                if not saw_packages_marker:
                    emit_monoblock(parent_excel_row)
                finish_parent()
                state = _State.SEEK_PARENT
                continue
            if _PACKAGES_MARKER_RE.search(blob):
                saw_packages_marker = True
                package_name = _package_name_from_row(
                    sheet, row, qty_col, skip_marker=True
                )
                if not package_name:
                    package_name = _PACKAGES_MARKER_RE.sub("", blob).strip(" :")
                if package_name and _PACKAGE_RATIO_RE.search(package_name):
                    emit_package(
                        package_name,
                        _row_quantity(sheet, row, qty_col),
                        row,
                    )
                row += 1
                continue
            if saw_packages_marker:
                package_name = _package_name_from_row(sheet, row, qty_col)
                if package_name:
                    emit_package(
                        package_name,
                        _row_quantity(sheet, row, qty_col),
                        row,
                    )
                row += 1
                continue
            fabric = _extract_fabric(blob)
            if fabric:
                fabric_or_color = fabric
                row += 1
                continue
            if _is_order_info_row(blob):
                order_reference = _order_reference_from_blob(blob, customer_name)
                row += 1
                continue
            row += 1
            continue

        row += 1

    if parent_title and not saw_packages_marker and state != _State.CHECK_TOTALS:
        emit_monoblock(parent_excel_row)
    finish_parent()
    return blocks


def _read_parent(
    sheet,
    row: int,
    columns: _Columns,
    blob: str,
) -> tuple[str, str, int]:
    title = _parent_title(sheet, row, columns, blob)
    fabric = _fabric_from_columns(sheet, row, columns)
    qty = _row_quantity(sheet, row, columns.qty)
    return title, fabric, max(qty, 1)


def _parent_title(sheet, row: int, columns: _Columns, blob: str) -> str:
    if columns.product is not None:
        title = normalize_incoming_text(sheet.cell_value(row, columns.product))
        if title:
            return _strip_line_prefix(title)
    return _strip_line_prefix(blob)


def _fabric_from_columns(sheet, row: int, columns: _Columns) -> str:
    parts: list[str] = []
    for col in (columns.fabric, columns.color):
        if col is None:
            continue
        text = normalize_incoming_text(sheet.cell_value(row, col))
        if text and not _LINE_NO_RE.match(text):
            parts.append(text)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _package_block(
    line_number: int,
    parent_title: str,
    fabric_or_color: str,
    package_name: str,
    quantity: int,
    excel_row: int,
    customer_name: str,
) -> RawOrderBlock:
    nomenclature = f"{parent_title} {fabric_or_color} {package_name}".strip()
    nomenclature = re.sub(r"\s+", " ", nomenclature).strip()
    return RawOrderBlock(
        line_number=line_number,
        client_description=nomenclature,
        item_type="Упаковка",
        quantity=max(quantity, 1),
        factory_alias=None,
        order_service_line=customer_name,
        excel_row_start=excel_row,
    )


def _is_parent_row(sheet, row: int, columns: _Columns, blob: str) -> bool:
    if _PACKAGES_MARKER_RE.search(blob) or _FOOTER_RE.search(blob):
        return False
    if _SIGNATURE_RE.search(blob) or _is_order_info_row(blob):
        return False
    line_col = columns.line_no if columns.line_no is not None else 1
    first = normalize_incoming_text(sheet.cell_value(row, line_col))
    if not first or not _LINE_NO_RE.match(first):
        return False
    product = ""
    if columns.product is not None:
        product = normalize_incoming_text(sheet.cell_value(row, columns.product))
    if not product:
        product = _strip_line_prefix(blob)
    return bool(product) and not _PACKAGE_RATIO_RE.search(product)


def _is_order_info_row(blob: str) -> bool:
    lowered = blob.lower()
    return bool(_ORDER_INFO_RE.search(blob) or ("перемещение" in lowered and _ORDER_ID_RE.search(blob)))


def _order_reference_from_blob(blob: str, fallback: str) -> str:
    match = _ORDER_ID_RE.search(blob)
    if match:
        return match.group(1).strip()
    return fallback


def _extract_fabric(blob: str) -> str:
    match = _FABRIC_LABEL_RE.match(blob.strip())
    if match:
        return match.group(1).strip()
    return ""


def _package_name_from_row(
    sheet,
    row: int,
    qty_col: int | None,
    skip_marker: bool = False,
) -> str:
    last_col = max(sheet.max_column, 1)
    parts: list[str] = []
    for col in range(1, last_col + 1):
        if qty_col is not None and col == qty_col:
            continue
        text = normalize_incoming_text(sheet.cell_value(row, col))
        if not text or _LINE_NO_RE.match(text):
            continue
        if skip_marker:
            text = _PACKAGES_MARKER_RE.sub("", text).strip(" :")
            if not text:
                continue
        parts.append(text)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _strip_line_prefix(blob: str) -> str:
    return re.sub(r"^\d+\s+", "", blob).strip()


def _row_quantity(sheet, row: int, qty_col: int | None) -> int:
    if qty_col is not None:
        text = normalize_incoming_text(sheet.cell_value(row, qty_col))
        if text.isdigit():
            return int(text)
        try:
            return max(int(float(text.replace(",", "."))), 1)
        except (TypeError, ValueError):
            pass
    return 1


def _detect_columns(sheet) -> _Columns:
    last_row = min(sheet.max_row, 40)
    last_col = max(sheet.max_column, 1)
    best = _Columns()
    best_score = 0
    for row in range(1, last_row + 1):
        candidate = _Columns(header_row=row)
        score = 0
        for col in range(1, last_col + 1):
            text = normalize_incoming_text(sheet.cell_value(row, col))
            if not text:
                continue
            if candidate.line_no is None and _NUM_HEADER_RE.search(text):
                candidate.line_no = col
                score += 1
            elif candidate.product is None and _PRODUCT_HEADER_RE.search(text):
                candidate.product = col
                score += 1
            elif candidate.fabric is None and _FABRIC_HEADER_RE.search(text):
                candidate.fabric = col
                score += 1
            elif candidate.color is None and _COLOR_HEADER_RE.search(text):
                candidate.color = col
                score += 1
            elif candidate.qty is None and _QTY_HEADER_RE.search(text):
                candidate.qty = col
                score += 1
            elif candidate.weight is None and _WEIGHT_HEADER_RE.search(text):
                candidate.weight = col
                score += 1
            elif candidate.mark is None and _MARK_HEADER_RE.search(text):
                candidate.mark = col
                score += 1
        if score > best_score and candidate.line_no and (candidate.product or candidate.qty):
            best = candidate
            best_score = score
    if best.qty is None:
        best.qty = _detect_qty_column(sheet)
    if best.line_no is None:
        best.line_no = 1
    if best.product is None:
        best.product = 2
    return best


def _detect_qty_column(sheet) -> int | None:
    last_row = min(sheet.max_row, 30)
    last_col = max(sheet.max_column, 1)
    for row in range(1, last_row + 1):
        for col in range(1, last_col + 1):
            text = normalize_incoming_text(sheet.cell_value(row, col))
            if _QTY_HEADER_RE.search(text):
                return col
    return None


def _row_cells(sheet, row: int) -> list[str]:
    last_col = max(sheet.max_column, 1)
    values: list[str] = []
    for col in range(1, last_col + 1):
        text = normalize_incoming_text(sheet.cell_value(row, col))
        if text:
            values.append(text)
    return values


def _row_blob(sheet, row: int) -> str:
    return " ".join(_row_cells(sheet, row))
