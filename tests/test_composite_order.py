"""Sprint 8.24: Multi-Section Document Pipeline (COMPOSITE_PICKING_LIST).

Regression suite for combined 1C v7.7 picking lists that pack a corpus
("Корпусная мебель") table and a soft-furniture ("Мягкая мебель") table onto
one sheet — e.g. ``order_kildishov_no_barcodes.xls``: 6 corpus line items
(7 places, orders ЦНТ-001415 / ЦНТ-001292) + 1 soft-furniture parent expanded
into 3 packages (3 places, order ЦНТ-001393) = 9 WMS rows / 10 places total.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.parsers.document_detector import DocumentType, DocumentTypeDetector
from src.parsers.document_splitter import SectionType, split_document_sections
from src.parsers.v7_parser import open_order_sheet
from src.pipeline import parse_incoming_order, resolve_order_decisions
from tests.conftest import DATA_DIR
from tests.excel_fixtures import (
    write_sample_v7_xlsx,
    write_soft_furniture_multi_xlsx,
    write_soft_furniture_xlsx,
)

ORDER_KILDISHOV_PATH = DATA_DIR / "orders" / "order_kildishov_no_barcodes.xls"

EXPECTED_ORDER_BY_LINE = {
    1: "ЦНТ-001415",
    2: "ЦНТ-001415",
    3: "ЦНТ-001415",
    4: "ЦНТ-001415",
    5: "ЦНТ-001415",
    6: "ЦНТ-001292",
    7: "ЦНТ-001393",
    8: "ЦНТ-001393",
    9: "ЦНТ-001393",
}

pytestmark = pytest.mark.skipif(
    not ORDER_KILDISHOV_PATH.exists(),
    reason="Combined Kildishov order sample (order_kildishov_no_barcodes.xls) is missing",
)


def test_composite_document_detection() -> None:
    """The combined corpus+soft file must classify as COMPOSITE_PICKING_LIST,
    not STANDARD_PICKING_LIST and not SOFT_FURNITURE_TRANSFER."""
    doc_type = DocumentTypeDetector().detect(ORDER_KILDISHOV_PATH)
    assert doc_type == DocumentType.COMPOSITE_PICKING_LIST


class TestCompositeSplitAndZeroLoss:
    def test_two_sections_with_correct_declared_places(self) -> None:
        with open_order_sheet(ORDER_KILDISHOV_PATH) as sheet:
            sections = split_document_sections(sheet)
        assert len(sections) == 2
        assert sections[0].section_type is SectionType.STANDARD
        assert sections[0].declared_places == 7
        assert sections[1].section_type is SectionType.SOFT
        assert sections[1].declared_places == 3

    def test_nine_wms_rows_ten_places_zero_loss(self) -> None:
        _doc_type, parsed = parse_incoming_order(ORDER_KILDISHOV_PATH)

        corpus_blocks = [block for block in parsed.blocks if not block.is_soft_furniture]
        soft_blocks = [block for block in parsed.blocks if block.is_soft_furniture]
        assert len(parsed.blocks) == 9
        assert len(corpus_blocks) == 6
        assert len(soft_blocks) == 3

        total_places = sum(block.quantity for block in parsed.blocks)
        assert total_places == 10
        assert parsed.declared_places == 10
        assert parsed.checksum_mismatch is False

    def test_continuous_line_numbering_1_to_9(self) -> None:
        _doc_type, parsed = parse_incoming_order(ORDER_KILDISHOV_PATH)
        line_numbers = sorted(block.line_number for block in parsed.blocks)
        assert line_numbers == list(range(1, 10))


def test_order_ids_per_row() -> None:
    """Each row keeps the order id printed above/around it in the sheet, not a
    single document-wide value: rows 1-5 = ЦНТ-001415, row 6 = ЦНТ-001292,
    rows 7-9 (soft-furniture packages) = ЦНТ-001393."""
    _doc_type, parsed = parse_incoming_order(ORDER_KILDISHOV_PATH)
    blocks_by_line = {block.line_number: block for block in parsed.blocks}
    assert set(blocks_by_line) == set(EXPECTED_ORDER_BY_LINE)
    for line_number, expected_order_id in EXPECTED_ORDER_BY_LINE.items():
        assert blocks_by_line[line_number].customer_override == expected_order_id


class TestCompositeMatchingCascade:
    """Corpus rows must go through the full HybridMatcher cascade and recover
    real EAN-13 barcodes; soft-furniture rows must bypass it (AUTO_NO_BARCODE)."""

    def test_corpus_rows_matched_with_barcode(self, hybrid_matcher) -> None:
        doc_type, parsed = parse_incoming_order(ORDER_KILDISHOV_PATH)
        decisions = resolve_order_decisions(doc_type, parsed, hybrid_matcher)
        assert len(decisions) == 9
        by_line = {decision.order_line_number: decision for decision in decisions}

        # Rows 1-5: the Феникс-3 Вайт line — deterministic exact-article match.
        for line in range(1, 6):
            decision = by_line[line]
            assert decision.match_method == "exact_article"
            assert decision.matched_entity is not None
            assert decision.matched_entity.barcode
            assert decision.raw_block.customer_override == "ЦНТ-001415"

        # Row 6: different SKU, different order — still resolved with a barcode.
        row6 = by_line[6]
        assert row6.match_method != "AUTO_NO_BARCODE"
        assert row6.status in {"MATCHED_AUTO", "MATCHED_LLM"}
        assert row6.raw_block.customer_override == "ЦНТ-001292"

    def test_soft_furniture_rows_bypass_matcher(self, hybrid_matcher) -> None:
        doc_type, parsed = parse_incoming_order(ORDER_KILDISHOV_PATH)
        decisions = resolve_order_decisions(doc_type, parsed, hybrid_matcher)
        by_line = {decision.order_line_number: decision for decision in decisions}

        for line in range(7, 10):
            decision = by_line[line]
            assert decision.match_method == "AUTO_NO_BARCODE"
            assert decision.status == "MATCHED_AUTO"
            assert decision.raw_block.customer_override == "ЦНТ-001393"


def test_regression_all_previous_orders(tmp_path: Path) -> None:
    """Guard against detector regressions introduced by COMPOSITE_PICKING_LIST:
    single-type synthetic documents must keep their original classification.
    The remaining 225+ previous tests are the real regression gate, run via
    ``pytest tests/`` (see Sprint 8.24 log)."""
    standard_path = write_sample_v7_xlsx(tmp_path / "standard.xlsx")
    assert DocumentTypeDetector().detect(standard_path) == DocumentType.STANDARD_PICKING_LIST

    soft_path = write_soft_furniture_xlsx(tmp_path / "soft.xlsx")
    assert DocumentTypeDetector().detect(soft_path) == DocumentType.SOFT_FURNITURE_TRANSFER

    soft_multi_path = write_soft_furniture_multi_xlsx(tmp_path / "soft_multi.xlsx")
    assert DocumentTypeDetector().detect(soft_multi_path) == DocumentType.SOFT_FURNITURE_TRANSFER
