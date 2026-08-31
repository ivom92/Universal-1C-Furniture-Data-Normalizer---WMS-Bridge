"""RegEx-based feature extraction from v7.7 order blocks."""

from __future__ import annotations

import re

from src.matcher.dynamic_vocab import DynamicVocabulary
from src.matcher.token_normalizer import expand_furniture_abbreviations
from src.models import ExtractedFeatures, RawOrderBlock
from src.parsers.v7_parser import sanitize_warehouse_topology
from src.preprocessor.normalizer import normalize_text

_PACKAGE_RATIO_RE = re.compile(r"(\b\d+/\d+\b|Ун\s*\d+/\d+)", re.IGNORECASE)
_SLASH_WIDTH_LIST_RE = re.compile(r"(?<![\d/])(\d{2,4}(?:/\d{2,4}){2,})(?![\d/])")

# Sub-brand / sub-line modifier tokens: discriminating decor/collection suffixes
# (e.g. "Чикаго" vs "Чикаго Вайт", "Равенна Роял" vs "Равенна Тренд"). These are
# generic linguistic modifiers reused across many collections in the factory
# catalog, not model names themselves, so they are kept as a small fixed set
# rather than mined dynamically (Sprint 8.23).
SUB_BRAND_MODIFIERS = frozenset({
    "вайт", "white", "роял", "royal", "тренд", "trend",
    "классик", "classic", "люкс", "lux", "luxe", "модерн", "modern",
    "эко", "eco", "лайт", "light", "плюс", "plus", "престиж", "prestige",
    "комфорт", "comfort", "мини", "mini", "макси", "maxi", "элит", "elite",
    "софт", "soft", "гранд", "grand", "про", "pro", "нью", "new",
})

_SUB_BRAND_RE = re.compile(
    r"\b(" + "|".join(sorted((re.escape(term) for term in SUB_BRAND_MODIFIERS), reverse=True)) + r")\b",
    re.IGNORECASE,
)

# Composite/multi-part decor signal: "Ателье светлый/Белый", "венге/лоредо",
# "Белый/Графит" — two or more Cyrillic decor words joined by a slash or dash.
_COMPOSITE_COLOR_RE = re.compile(
    r"[а-яё]{3,}(?:\s+[а-яё]{3,}){0,2}\s*[/\-]\s*[а-яё]{3,}(?:\s+[а-яё]{3,}){0,2}",
    re.IGNORECASE,
)


def extract_sub_brands(text: str) -> set[str]:
    """Return normalized sub-brand modifier tokens found in *text* (case-insensitive)."""
    if not text:
        return set()
    normalized = normalize_text(text)
    return {match.group(1).lower() for match in _SUB_BRAND_RE.finditer(normalized)}


def has_composite_color_signal(text: str) -> bool:
    """True if *text* mentions a slash/dash-joined multi-part decor (composite color)."""
    if not text:
        return False
    return bool(_COMPOSITE_COLOR_RE.search(text))


def extract_package_ratio_from_text(text: str) -> str | None:
    """Return the first X/Y or УнX/Y token, ignoring triple width lists like 160/140/120."""
    if not text:
        return None
    text_for_ratio = _SLASH_WIDTH_LIST_RE.sub(" ", text)
    match = _PACKAGE_RATIO_RE.search(text_for_ratio)
    if not match:
        return None
    return FeatureExtractor._normalize_package_ratio(match.group(1))


_DIMENSION_RE = re.compile(
    r"\d+[,.]?\d*\s*м\b|\d+(?:x\d+)+",
    re.IGNORECASE,
)
_THICKNESS_RE = re.compile(r"\d+\s*мм\b", re.IGNORECASE)
_CM_WIDTH_THRESHOLD = 400


class FeatureExtractor:
    """Extracts structured matching features from arbitrary v7.7 block text."""

    def __init__(self, vocabulary: DynamicVocabulary) -> None:
        self._vocabulary = vocabulary

    def extract_features(self, raw_block: RawOrderBlock) -> ExtractedFeatures:
        combined_text = self._combine_block_text(raw_block)
        normalized_text = self._normalize_text(combined_text)

        alternative_widths = self._extract_alternative_widths(normalized_text)
        expanded_text = expand_furniture_abbreviations(combined_text)
        return ExtractedFeatures(
            package_ratio=self._extract_package_ratio(normalized_text, raw_block),
            dimensions=self._extract_dimensions(normalized_text),
            alternative_widths=alternative_widths,
            thicknesses=self._extract_thicknesses(normalized_text),
            matched_part_types=self._vocabulary.match_part_types(combined_text),
            matched_colors=self._vocabulary.match_colors(expanded_text),
            matched_models=self._vocabulary.match_models(combined_text),
            sub_brands=extract_sub_brands(combined_text),
            is_composite_color=has_composite_color_signal(expanded_text),
        )

    @staticmethod
    def _combine_block_text(raw_block: RawOrderBlock) -> str:
        combined = " ".join(
            part
            for part in (raw_block.client_description, raw_block.factory_alias)
            if part
        ).strip()
        return sanitize_warehouse_topology(combined)

    @staticmethod
    def _normalize_text(text: str) -> str:
        normalized = normalize_text(text)
        normalized = expand_furniture_abbreviations(normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.strip()

    def _extract_package_ratio(self, text: str, raw_block: RawOrderBlock) -> str | None:
        extracted = extract_package_ratio_from_text(text)
        if extracted:
            return extracted

        item_type = raw_block.item_type.strip().lower()
        if item_type == "стекло":
            return "Ун1/1"
        return "1/1"

    @staticmethod
    def _normalize_package_ratio(raw_ratio: str) -> str:
        cleaned = raw_ratio.strip()
        universal_match = re.match(r"Ун\s*(\d+/\d+)", cleaned, re.IGNORECASE)
        if universal_match:
            return f"Ун{universal_match.group(1)}"
        return cleaned

    @staticmethod
    def _extract_alternative_widths(text: str) -> list[int]:
        widths: list[int] = []
        seen: set[int] = set()
        for match in _SLASH_WIDTH_LIST_RE.finditer(text):
            for raw in match.group(1).split("/"):
                value = int(raw)
                if value < _CM_WIDTH_THRESHOLD:
                    value *= 10
                if value not in seen:
                    seen.add(value)
                    widths.append(value)
        return widths

    @staticmethod
    def _extract_dimensions(text: str) -> list[str]:
        seen: set[str] = set()
        dimensions: list[str] = []

        for match in _SLASH_WIDTH_LIST_RE.finditer(text):
            value = match.group(0)
            if value not in seen:
                seen.add(value)
                dimensions.append(value)

        for match in _DIMENSION_RE.finditer(text):
            value = re.sub(r"\s+", "", match.group(0))
            value = value.replace(",", ",")
            if value not in seen:
                seen.add(value)
                dimensions.append(value)

        return dimensions

    @staticmethod
    def _extract_thicknesses(text: str) -> list[str]:
        seen: set[str] = set()
        thicknesses: list[str] = []

        for match in _THICKNESS_RE.finditer(text):
            value = re.sub(r"\s+", "", match.group(0))
            if value not in seen:
                seen.add(value)
                thicknesses.append(value)

        return thicknesses
