"""Tests for the sub-brand / color-palette disambiguation barrier (Sprint 8.23).

Covers:
- Sub-brand token extraction in `FeatureExtractor` (SUB_BRAND_MODIFIERS).
- Hard barrier (Rule 2): conflicting sub-brands disqualify a candidate.
- Soft barrier (Rule 1 / Rule 3): pool-aware boost/penalty ranks the
  sub-brand-aligned or pure base-series candidate above the rest.
- Color palette disambiguation: monochrome decor beats a partially
  overlapping composite decor when both are in the candidate pool.
- No regression for plain nomenclature without sub-brands/composite decor.
"""

from __future__ import annotations

from typing import Optional

from src.matcher.feature_extractor import FeatureExtractor
from src.matcher.hybrid_matcher import HybridMatcher
from src.models import CatalogEntity, RawOrderBlock


def _catalog_entity(
    *,
    nomenclature: str,
    nomenclature_code: str,
    packaging: Optional[str] = None,
    module: Optional[str] = None,
    label_model: Optional[str] = None,
    filling: Optional[str] = None,
    color: Optional[str] = None,
    barcode: Optional[str] = None,
) -> CatalogEntity:
    return CatalogEntity.model_validate(
        {
            "Номенклатура": nomenclature,
            "НоменклатураКод": nomenclature_code,
            "Упаковка": packaging,
            "Модуль": module,
            "ЭтикеткаМодель": label_model,
            "Начинка": filling,
            "Цвет": color,
            "Штрихкод": barcode,
        }
    )


def _raw_block(
    *,
    client_description: str,
    factory_alias: str = "",
    item_type: str = "Пачка",
    line_number: int = 1,
) -> RawOrderBlock:
    return RawOrderBlock(
        line_number=line_number,
        client_description=client_description,
        item_type=item_type,
        quantity=1,
        factory_alias=factory_alias or client_description,
        order_service_line="Продажи оптовые УРП_ test",
        excel_row_start=line_number,
    )


class FakeVectorStore:
    """Deterministic vector store stub for hard-filter/soft-barrier unit tests."""

    def __init__(
        self,
        hits: list[tuple[CatalogEntity, float]],
        catalog: list[CatalogEntity] | None = None,
    ) -> None:
        self._hits = hits
        self._catalog = catalog if catalog is not None else []

    @property
    def catalog(self) -> list[CatalogEntity]:
        return list(self._catalog)

    def search(self, query_text: str, top_k: int = 20) -> list[tuple[CatalogEntity, float]]:
        return self._hits[:top_k]


class TestFeatureExtractionSubBrand:
    def test_extracts_sub_brand_token(self, feature_extractor: FeatureExtractor) -> None:
        features = feature_extractor.extract_features(
            _raw_block(client_description="Система Чикаго Вайт Кровать 160 1/2 Белый")
        )
        assert "вайт" in features.sub_brands

    def test_no_sub_brand_for_plain_collection(self, feature_extractor: FeatureExtractor) -> None:
        features = feature_extractor.extract_features(
            _raw_block(client_description="Кровать 160 София (корпус) 1/1 Белый")
        )
        assert features.sub_brands == set()

    def test_composite_color_signal_detected(self, feature_extractor: FeatureExtractor) -> None:
        features = feature_extractor.extract_features(
            _raw_block(client_description="Фасад венге/лоредо 1/1")
        )
        assert features.is_composite_color is True

    def test_no_composite_color_signal_for_single_decor(self, feature_extractor: FeatureExtractor) -> None:
        features = feature_extractor.extract_features(
            _raw_block(client_description="Кровать 160 (корпус) 1/1 Белый")
        )
        assert features.is_composite_color is False


class TestSubBrandBarrier:
    def test_subbrand_chicago_white_matching(self, feature_extractor: FeatureExtractor) -> None:
        base = _catalog_entity(
            nomenclature="Система Чикаго Кровать 160 с ламелями (корпус и фурнитура) Ателье светлый/Белый упаковка 1/2",
            nomenclature_code="00000075429",
            packaging="1/2",
            module="Кровать 160 с ламелями",
            label_model="Система Чикаго",
            color="Ателье светлый/Белый",
            barcode="4673735527058",
        )
        white = _catalog_entity(
            nomenclature="Система Чикаго Вайт Кровать 160 с ламелями (корпус и фурнитура) Белый упаковка 1/2",
            nomenclature_code="00000075426",
            packaging="1/2",
            module="Кровать 160 с ламелями",
            label_model="Система Чикаго Вайт",
            color="Белый",
            barcode="4673735527409",
        )

        matcher = HybridMatcher(
            FakeVectorStore([(base, 0.99), (white, 0.98)], catalog=[base, white]),
            feature_extractor,
        )
        block = _raw_block(
            client_description="Система Чикаго Вайт Кровать 160 с ламелями (корпус и фурнитура) 1/2 Белый",
            factory_alias="IMP сп Чикаго вайт кровать 160 б/м с ламелью = белый",
        )

        decision = matcher.match_block(block)

        assert decision.matched_entity is not None
        assert decision.matched_entity.nomenclature_code == "00000075426"
        assert decision.matched_entity.label_model == "Система Чикаго Вайт"
        assert decision.matched_entity.barcode == "4673735527409"

        # Also verify the FAISS-stage scoring barrier directly: even when the vector
        # search itself ranks the base series higher, the sub-brand barrier must
        # invert that ordering before the final candidate is chosen.
        features = feature_extractor.extract_features(block)
        scored = matcher._score_candidates(block, features, [(base, 0.99), (white, 0.98)])
        base_candidate = next(c for c in scored if c.catalog_entity.nomenclature_code == "00000075429")
        white_candidate = next(c for c in scored if c.catalog_entity.nomenclature_code == "00000075426")
        assert white_candidate.similarity_score > base_candidate.similarity_score

    def test_subbrand_conflict_rejection(self, feature_extractor: FeatureExtractor) -> None:
        trend = _catalog_entity(
            nomenclature="Кухня Равенна Тренд Н20 карго (корпус) Белый упаковка 1/2",
            nomenclature_code="00000091001",
            packaging="1/2",
            label_model="Кухня Равенна Тренд",
            color="Белый",
            barcode="4600000091001",
        )

        matcher = HybridMatcher(
            FakeVectorStore([(trend, 0.95)], catalog=[trend]),
            feature_extractor,
        )
        block = _raw_block(
            client_description="Кухня Равенна Роял Н20 карго (корпус) Белый упаковка 1/2",
        )

        decision = matcher.match_block(block)

        for candidate in decision.candidates:
            if candidate.catalog_entity.nomenclature_code == "00000091001":
                assert candidate.hard_filter_passed is False
                assert candidate.penalty_reason == "Sub-brand conflict"
        assert decision.status != "MATCHED_AUTO"
        if decision.matched_entity is not None:
            assert decision.matched_entity.nomenclature_code != "00000091001"

    def test_subbrand_royal_pool_pick(self, feature_extractor: FeatureExtractor) -> None:
        """When both the base series and the matching sub-brand are candidates, the
        sub-brand-aligned one must win even if the base series scored slightly higher."""
        base = _catalog_entity(
            nomenclature="Кухня Равенна Н20 карго (корпус) Белый упаковка 1/2",
            nomenclature_code="00000091002",
            packaging="1/2",
            label_model="Кухня Равенна",
            color="Белый",
            barcode="4600000091002",
        )
        royal = _catalog_entity(
            nomenclature="Кухня Равенна Роял Н20 карго (корпус) Белый упаковка 1/2",
            nomenclature_code="00000091003",
            packaging="1/2",
            label_model="Кухня Равенна Роял",
            color="Белый",
            barcode="4600000091003",
        )

        matcher = HybridMatcher(
            FakeVectorStore([(base, 0.97), (royal, 0.94)], catalog=[base, royal]),
            feature_extractor,
        )
        block = _raw_block(
            client_description="Кухня Равенна Роял Н20 карго (корпус) Белый упаковка 1/2",
        )

        decision = matcher.match_block(block)

        assert decision.matched_entity is not None
        assert decision.matched_entity.nomenclature_code == "00000091003"


class TestColorPaletteDisambiguation:
    def test_color_palette_monochrome_vs_composite(self, feature_extractor: FeatureExtractor) -> None:
        monochrome = _catalog_entity(
            nomenclature="Стеллаж Йорк Н86 (корпус) Белый упаковка 1/1",
            nomenclature_code="00000092001",
            packaging="1/1",
            label_model="Йорк",
            color="Белый",
            barcode="4600000092001",
        )
        composite = _catalog_entity(
            nomenclature="Стеллаж Йорк Н86 (корпус) Белый/Графит упаковка 1/1",
            nomenclature_code="00000092002",
            packaging="1/1",
            label_model="Йорк",
            color="Белый/Графит",
            barcode="4600000092002",
        )

        matcher = HybridMatcher(
            FakeVectorStore([(composite, 0.96), (monochrome, 0.95)], catalog=[monochrome, composite]),
            feature_extractor,
        )
        block = _raw_block(client_description="Стеллаж Йорк Н86 (корпус) Белый упаковка 1/1")

        decision = matcher.match_block(block)

        assert decision.matched_entity is not None
        assert decision.matched_entity.nomenclature_code == "00000092001"

    def test_requested_composite_color_is_not_penalized(self, feature_extractor: FeatureExtractor) -> None:
        monochrome = _catalog_entity(
            nomenclature="Стеллаж Йорк Н86 (корпус) Белый упаковка 1/1",
            nomenclature_code="00000092003",
            packaging="1/1",
            label_model="Йорк",
            color="Белый",
            barcode="4600000092003",
        )
        composite = _catalog_entity(
            nomenclature="Стеллаж Йорк Н86 (корпус) Белый/Графит упаковка 1/1",
            nomenclature_code="00000092004",
            packaging="1/1",
            label_model="Йорк",
            color="Белый/Графит",
            barcode="4600000092004",
        )

        matcher = HybridMatcher(
            FakeVectorStore([(composite, 0.96), (monochrome, 0.90)], catalog=[monochrome, composite]),
            feature_extractor,
        )
        block = _raw_block(client_description="Стеллаж Йорк Н86 (корпус) Белый/Графит упаковка 1/1")

        decision = matcher.match_block(block)

        assert decision.matched_entity is not None
        assert decision.matched_entity.nomenclature_code == "00000092004"


class TestNoRegressionOnStandardCorpus:
    def test_plain_bed_without_subbrand(self, feature_extractor: FeatureExtractor) -> None:
        entity = _catalog_entity(
            nomenclature="Кровать 160 София (корпус) Белый упаковка 1/1",
            nomenclature_code="00000093001",
            packaging="1/1",
            label_model="София",
            color="Белый",
            barcode="4600000093001",
        )
        matcher = HybridMatcher(
            FakeVectorStore([(entity, 0.95)], catalog=[entity]),
            feature_extractor,
        )
        decision = matcher.match_block(
            _raw_block(client_description="Кровать 160 София (корпус) Белый упаковка 1/1")
        )
        assert decision.status == "MATCHED_AUTO"
        assert decision.matched_entity is not None
        assert decision.matched_entity.nomenclature_code == "00000093001"

    def test_plain_drawer_unit_without_subbrand(self, feature_extractor: FeatureExtractor) -> None:
        entity = _catalog_entity(
            nomenclature="Тумба Т-1 (корпус) Белый упаковка 1/1",
            nomenclature_code="00000093002",
            packaging="1/1",
            label_model="Т-1",
            color="Белый",
            barcode="4600000093002",
        )
        matcher = HybridMatcher(
            FakeVectorStore([(entity, 0.95)], catalog=[entity]),
            feature_extractor,
        )
        decision = matcher.match_block(
            _raw_block(client_description="Тумба Т-1 (корпус) Белый упаковка 1/1")
        )
        assert decision.status == "MATCHED_AUTO"
        assert decision.matched_entity is not None
        assert decision.matched_entity.nomenclature_code == "00000093002"

    def test_no_sub_brand_score_untouched_when_pool_has_no_base(
        self,
        feature_extractor: FeatureExtractor,
    ) -> None:
        """If every passing candidate already carries the same sub-brand, no barrier
        adjustment should fire (nothing to disambiguate)."""
        royal_a = _catalog_entity(
            nomenclature="Кухня Равенна Роял Н20 карго (корпус) Белый упаковка 1/2",
            nomenclature_code="00000094001",
            packaging="1/2",
            module="Н20 карго",
            label_model="Кухня Равенна Роял",
            color="Белый",
            barcode="4600000094001",
        )
        royal_b = _catalog_entity(
            nomenclature="Кухня Равенна Роял Н40 карго (корпус) Белый упаковка 1/2",
            nomenclature_code="00000094002",
            packaging="1/2",
            module="Н40 карго",
            label_model="Кухня Равенна Роял",
            color="Белый",
            barcode="4600000094002",
        )
        matcher = HybridMatcher(
            FakeVectorStore([(royal_a, 0.93), (royal_b, 0.80)], catalog=[royal_a, royal_b]),
            feature_extractor,
        )
        decision = matcher.match_block(
            _raw_block(client_description="Кухня Равенна Роял Н20 карго (корпус) Белый упаковка 1/2")
        )
        candidate_a = next(
            c for c in decision.candidates if c.catalog_entity.nomenclature_code == "00000094001"
        )
        assert candidate_a.similarity_score >= 0.93


class TestVasilyevaRow25Integration:
    """Reproduces the exact Sprint 8.23 regression: order line 25 of
    `order_vasilyeva_t_no_barcodes.xls` must resolve to the Chicago White line,
    not the base Chicago collection."""

    def test_chicago_white_bed_resolves_over_base_series(
        self,
        hybrid_matcher: HybridMatcher,
    ) -> None:
        block = _raw_block(
            client_description=(
                "Система Чикаго Вайт Кровать 160 с ламелями (корпус и фурнитура) 1/2 Белый"
            ),
            factory_alias="IMP сп Чикаго вайт кровать 160 б/м с ламелью = белый",
            line_number=25,
        )

        decision = hybrid_matcher.match_block(block)

        assert decision.matched_entity is not None
        assert decision.matched_entity.label_model == "Система Чикаго Вайт"
        assert decision.matched_entity.barcode
        assert len(decision.matched_entity.barcode) == 13
