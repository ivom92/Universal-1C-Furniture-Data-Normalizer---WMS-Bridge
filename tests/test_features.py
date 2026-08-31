"""Tests for dynamic vocabulary and feature extraction."""

from __future__ import annotations

import pytest

from src.matcher.dynamic_vocab import DynamicVocabulary
from src.matcher.feature_extractor import FeatureExtractor
from src.matcher.token_normalizer import canonicalize_search_text
from src.models import RawOrderBlock
from tests.conftest import CATALOG_V8_PATH, ORDER_RUBAN_PATH


def _block(
    *,
    client_description: str,
    factory_alias: str = "",
    item_type: str = "",
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


class TestDynamicVocabulary:
    def test_builds_from_catalog_without_hardcoded_lists(self, vocabulary: DynamicVocabulary) -> None:
        assert len(vocabulary.known_models) >= 100
        assert len(vocabulary.known_colors) >= 100
        assert len(vocabulary.known_materials) >= 50
        assert "стекло" in vocabulary.known_part_types
        assert "столешница" in vocabulary.known_part_types
        assert "фасад" in vocabulary.known_part_types or "фасады" in vocabulary.known_part_types


class TestPackageRatioExtraction:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Йорк Комод 1/1 Белый", "1/1"),
            ("Нео (корпус и фасады) 2/2 белый", "2/2"),
            ("Система Йорк Стекло Ун1/1", "Ун1/1"),
            ("Йорк Тумба ТВ 1/3 белый", "1/3"),
        ],
    )
    def test_package_ratios(
        self,
        feature_extractor: FeatureExtractor,
        text: str,
        expected: str,
    ) -> None:
        features = feature_extractor.extract_features(_block(client_description=text))
        assert features.package_ratio == expected

    def test_universal_package_with_space(self, feature_extractor: FeatureExtractor) -> None:
        features = feature_extractor.extract_features(
            _block(client_description="Стекло 4мм Ун 1/1 Йорк")
        )
        assert features.package_ratio == "Ун1/1"


class TestDimensionExtraction:
    def test_latin_x_and_spaces(self, feature_extractor: FeatureExtractor) -> None:
        features = feature_extractor.extract_features(
            _block(client_description="Фасад 116 x 596 x 16 Плано 1/1")
        )
        assert "116x596x16" in features.dimensions

    def test_linear_meters(self, feature_extractor: FeatureExtractor) -> None:
        features = feature_extractor.extract_features(
            _block(client_description="КДР к Столешница 40мм 2,00м 3025/Q")
        )
        assert "2,00м" in features.dimensions

    def test_thickness(self, feature_extractor: FeatureExtractor) -> None:
        features = feature_extractor.extract_features(
            _block(client_description="КДР к Столешница 40мм 2,00м")
        )
        assert "40мм" in features.thicknesses

    def test_multi_size_widths_are_not_package_ratio(
        self,
        feature_extractor: FeatureExtractor,
    ) -> None:
        features = feature_extractor.extract_features(
            _block(client_description="Чикаго Нео 160/140/120 1/1")
        )
        assert features.package_ratio == "1/1"
        assert features.alternative_widths == [1600, 1400, 1200]
        assert "160/140/120" in features.dimensions

    def test_five_width_slash_list_is_not_package_ratio(
        self,
        feature_extractor: FeatureExtractor,
    ) -> None:
        features = feature_extractor.extract_features(
            _block(
                client_description=(
                    "Фурнитура Полка стеклянная 30/40/50/60/80 "
                    "(полкодержатели 8 шт) без цвета"
                ),
                item_type="Фурнитура",
            )
        )
        assert features.package_ratio == "1/1"
        assert features.alternative_widths == [300, 400, 500, 600, 800]


class TestProductTypeMatching:
    def test_countertop_part_type(self, feature_extractor: FeatureExtractor) -> None:
        features = feature_extractor.extract_features(
            _block(client_description="КДР к Столешница 40мм 1U (кат1) 2,00м 3025/Q")
        )
        assert any("столешниц" in part for part in features.matched_part_types)

    def test_commode_model(self, feature_extractor: FeatureExtractor) -> None:
        features = feature_extractor.extract_features(
            _block(client_description="Йорк Комод 1д1в (корпус, фасад и фурнитура) 1/1 Белый")
        )
        assert any("Йорк" in model for model in features.matched_models)
        assert any("комод" in part for part in features.matched_part_types)

    def test_glass_part_type_and_thickness(self, feature_extractor: FeatureExtractor) -> None:
        features = feature_extractor.extract_features(
            _block(
                client_description="Стекло 4мм 839х372 с кантом",
                factory_alias="IMP ст Йорк комод 1в1д = белый/белый глянец",
                item_type="Стекло",
            )
        )
        assert "4мм" in features.thicknesses
        assert "839x372" in features.dimensions
        assert any("стекл" in part for part in features.matched_part_types)

    def test_arbitrary_alias_prefix_without_imp_hardcode(
        self,
        feature_extractor: FeatureExtractor,
    ) -> None:
        features = feature_extractor.extract_features(
            _block(
                client_description="Пакет фасады 1/2 Плано",
                factory_alias="Пакет фасады Плано 116х596 1/2",
            )
        )
        assert features.package_ratio == "1/2"
        assert any("Плано" in model for model in features.matched_models)


class TestCanonicalTokenNormalizer:
    def test_packaging_ratio_becomes_упаковка(self) -> None:
        text = canonicalize_search_text("Лацио Сканди Витрина 1д 1/4 Вотан")
        assert "упаковка 1/4" in text.lower()

    def test_imp_collection_prefixes(self) -> None:
        expanded = canonicalize_search_text("IMP сп Лацио 1/2")
        assert "спальня" in expanded.lower()
        assert "упаковка 1/2" in expanded.lower()
        assert "кухня" in canonicalize_search_text("IMP к Нео").lower()
        assert "прихожая" in canonicalize_search_text("IMP прих Йорк").lower()
        assert "гостиная" in canonicalize_search_text("IMP г Нео").lower()

    def test_furniture_abbreviations(self) -> None:
        from src.matcher.token_normalizer import expand_furniture_abbreviations

        assert "дуб сонома" in expand_furniture_abbreviations("Фасад д.сон. 1/1").lower()
        assert "белое дерево" in expand_furniture_abbreviations("б.дер. корпус").lower()
        assert "ясень шимо" in expand_furniture_abbreviations("яс.шимо").lower()
        assert "ящик" in expand_furniture_abbreviations("Н60 2ящ").lower()
        assert "створка" in expand_furniture_abbreviations("2ств шкаф").lower()
        assert "(FE)" in expand_furniture_abbreviations("Равенна Н60 (FE)")
        assert "фасад эмаль" in expand_furniture_abbreviations("Равенна FE корпус").lower()
        assert "старт" not in expand_furniture_abbreviations("ящик СТАРТ Н86").lower()

    def test_canonical_packaging_and_dimensions(self) -> None:
        text = canonicalize_search_text("Полка 565 х 255 1/1")
        assert "565x255" in text
        assert "упаковка 1/1" in text.lower()


@pytest.mark.skipif(
    not ORDER_RUBAN_PATH.exists() or not CATALOG_V8_PATH.exists(),
    reason="Real data files are missing",
)
class TestOrderRubanIntegration:
    def test_all_blocks_have_package_ratio(
        self,
        order_ruban_parsed,
        feature_extractor: FeatureExtractor,
    ) -> None:
        missing: list[int] = []
        for block in order_ruban_parsed.blocks:
            features = feature_extractor.extract_features(block)
            if features.package_ratio is None:
                missing.append(block.line_number)

        assert missing == [], f"Blocks without package_ratio: {missing}"

    def test_countertop_blocks_default_to_single_package(
        self,
        order_ruban_parsed,
        feature_extractor: FeatureExtractor,
    ) -> None:
        for line_number in (1, 2):
            block = order_ruban_parsed.blocks[line_number - 1]
            features = feature_extractor.extract_features(block)
            assert features.package_ratio == "1/1"
            assert "2,00м" in features.dimensions
            assert "40мм" in features.thicknesses

    def test_glass_blocks_without_explicit_ratio_use_universal(
        self,
        order_ruban_parsed,
        feature_extractor: FeatureExtractor,
    ) -> None:
        for line_number in (52, 53, 54, 55):
            block = order_ruban_parsed.blocks[line_number - 1]
            features = feature_extractor.extract_features(block)
            assert features.package_ratio == "Ун1/1"
