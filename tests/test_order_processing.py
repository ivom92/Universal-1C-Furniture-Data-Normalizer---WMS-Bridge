"""Real-order acceptance checks for Sprint 8.9."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.pipeline import parse_incoming_order
from tests.conftest import CATALOG_V8_PATH, DATA_DIR

ORDER_TRANSFER_PATH = DATA_DIR / "orders" / "order_transfering_01_09.xls"
ORDER_BED_CANDIDATES = (
    DATA_DIR / "orders" / "order_transfering_01_09_bed.xls",
    DATA_DIR / "orders" / "order_transfering_01_09_bed.xlsx",
    DATA_DIR / "orders" / "order_transfering_01_09_bed",
)


@pytest.mark.skipif(
    not ORDER_TRANSFER_PATH.exists() or not CATALOG_V8_PATH.exists(),
    reason="Reference transfer order or catalog is missing",
)
class TestPosition230CanonicalMatch:
    def test_line_230_matched_auto_barcode(
        self,
        hybrid_matcher,
    ) -> None:
        _doc_type, parsed = parse_incoming_order(ORDER_TRANSFER_PATH)
        block = next(item for item in parsed.blocks if item.line_number == 230)
        decision = hybrid_matcher.match_block(block)
        assert decision.status == "MATCHED_AUTO"
        assert decision.matched_entity is not None
        assert decision.matched_entity.nomenclature_code == "00000025467"
        assert decision.matched_entity.barcode == "4603734801972"


@pytest.mark.skipif(
    not any(path.exists() for path in ORDER_BED_CANDIDATES),
    reason="Soft-furniture transfer sample is missing",
)
class TestSoftFurnitureRealFile:
    def test_three_packages(self) -> None:
        path = next(path for path in ORDER_BED_CANDIDATES if path.exists())
        from src.parsers.soft_furniture_parser import parse_soft_furniture_order

        parsed = parse_soft_furniture_order(path)
        assert len(parsed.blocks) == 3
        assert sum(block.quantity for block in parsed.blocks) == 3
        assert all(
            " " in block.client_description and block.client_description.strip()
            for block in parsed.blocks
        )
