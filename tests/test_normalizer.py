"""Canonical millimetre-run normalization (Fix #230)."""

from __future__ import annotations

from src.preprocessor.normalizer import (
    canonicalize_dimensions,
    canonicalize_furniture_module_codes,
    extract_dimension_tokens,
    heal_mojibake,
    normalize_text,
    repair_mixed_script_token,
)


class TestHealMojibake:
    def test_avrora_bed_line(self) -> None:
        raw = "Àâðîðà Êðîâàòü 90 ñî âñòðîåííûì îñíîâàíèåì"
        expected = "Аврора Кровать 90 со встроенным основанием"
        assert heal_mojibake(raw) == expected
        assert normalize_text(raw) == expected

    def test_alena_wardrobe_line(self) -> None:
        raw = "Àë¸íà Øêàô 3-õ äâåðíûé (êîðïóñ)"
        expected = "Алёна Шкаф 3-х дверный (корпус)"
        assert heal_mojibake(raw) == expected
        assert normalize_text(raw) == expected

    def test_preserves_valid_cyrillic(self) -> None:
        text = "Аврора Кровать 90 белый"
        assert heal_mojibake(text) == text
        assert normalize_text(text) == text


class TestCanonicalizeDimensions:
    def test_cyrillic_and_latin_and_spaces(self) -> None:
        assert canonicalize_dimensions("565х255") == "565x255"
        assert canonicalize_dimensions("565 x 255") == "565x255"
        assert canonicalize_dimensions("565X255") == "565x255"
        assert canonicalize_dimensions("565×255") == "565x255"
        assert canonicalize_dimensions("565*255") == "565x255"

    def test_3d_chain(self) -> None:
        assert canonicalize_dimensions("116 х 596 х 16") == "116x596x16"

    def test_preserves_surrounding_text(self) -> None:
        text = canonicalize_dimensions(
            "Кухня Равенна полка стеклянная 60 (2 шт 5мм) 565х255 упаковка 1/1"
        )
        assert "565x255" in text
        assert "полка стеклянная" in text

    def test_extract_tokens_include_pair_windows(self) -> None:
        tokens = extract_dimension_tokens("116х596х16")
        assert "116x596x16" in tokens
        assert "116x596" in tokens
        assert "596x16" in tokens


class TestMixedScriptRepair:
    def test_cyrillic_majority_latin_homoglyphs(self) -> None:
        assert repair_mixed_script_token("Кр\u006fвать") == "Кровать"
        assert repair_mixed_script_token("Ра\u0076енна") == "Равенна"

    def test_preserves_english_decor_tokens(self) -> None:
        for token in ("shadow", "palermo", "velvet", "loft", "SF"):
            assert repair_mixed_script_token(token) == token

    def test_latin_majority_cyrillic_homoglyphs(self) -> None:
        assert repair_mixed_script_token("sh\u0430dow") == "shadow"
        assert repair_mixed_script_token("p\u0430lermo") == "palermo"

    def test_equal_ratio_left_unchanged(self) -> None:
        token = "\u0430a"
        assert repair_mixed_script_token(token) == token


class TestFurnitureModuleCodes:
    def test_low_and_high_prefixes(self) -> None:
        assert canonicalize_furniture_module_codes("H20") == "Н20"
        assert canonicalize_furniture_module_codes("h-60") == "Н60"
        assert canonicalize_furniture_module_codes("B-60") == "В60"
        assert canonicalize_furniture_module_codes("в-30") == "В30"

    def test_normalize_text_chain(self) -> None:
        text = normalize_text("Кухня Ра\u0076енна H20 shadow 565х255")
        assert "Равенна" in text
        assert "Н20" in text
        assert "shadow" in text
        assert "565x255" in text
        assert canonicalize_dimensions("565х255") == "565x255"
