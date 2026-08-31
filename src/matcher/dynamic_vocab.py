"""Dynamic vocabulary built from the 1C v8 catalog at runtime."""

from __future__ import annotations

import re
from collections import Counter

from src.models import CatalogEntity

_FIRST_WORD_RE = re.compile(r"^([А-Яа-яA-Za-z]+)")
_MODEL_TOKEN_RE = re.compile(r"[А-Яа-яA-Za-z]+")


class DynamicVocabulary:
    """Catalog-derived knowledge sets — no hardcoded furniture lists."""

    MIN_PART_TYPE_FREQUENCY = 15
    MIN_PART_TYPE_WORD_LENGTH = 4
    MIN_MODEL_TOKEN_FREQUENCY = 2
    MAX_MODEL_TOKEN_FREQUENCY = 50
    MIN_MODEL_TOKEN_LENGTH = 3

    def __init__(self, catalog: list[CatalogEntity]) -> None:
        self.known_models = self._collect_unique(catalog, lambda entity: entity.label_model)
        self.known_colors = self._collect_unique(catalog, lambda entity: entity.color)
        self.known_materials = self._collect_unique(catalog, lambda entity: entity.filling)
        self.known_modules = self._collect_unique(catalog, lambda entity: entity.module)
        self.known_part_types = self._build_part_types(catalog)

        self._model_match_terms = self._build_model_match_terms(self.known_models)
        self._models_sorted = self._sorted_by_length(self._model_match_terms)
        self._colors_sorted = self._sorted_by_length(self.known_colors)
        self._materials_sorted = self._sorted_by_length(self.known_materials)
        self._part_types_sorted = self._sorted_by_length(self.known_part_types)

    @staticmethod
    def _collect_unique(
        catalog: list[CatalogEntity],
        getter,
    ) -> frozenset[str]:
        values: set[str] = set()
        for entity in catalog:
            raw = getter(entity)
            if raw:
                cleaned = str(raw).strip()
                if cleaned:
                    values.add(cleaned)
        return frozenset(values)

    def _build_part_types(self, catalog: list[CatalogEntity]) -> frozenset[str]:
        part_types: set[str] = set()
        first_words: Counter[str] = Counter()

        for entity in catalog:
            if entity.label_type:
                part_types.add(entity.label_type.strip().lower())

            match = _FIRST_WORD_RE.match(entity.nomenclature or "")
            if match:
                first_words[match.group(1).lower()] += 1

        for word, count in first_words.items():
            if (
                count >= self.MIN_PART_TYPE_FREQUENCY
                and len(word) >= self.MIN_PART_TYPE_WORD_LENGTH
            ):
                part_types.add(word)

        return frozenset(part_types)

    def _build_model_match_terms(self, known_models: frozenset[str]) -> frozenset[str]:
        terms: set[str] = set(known_models)
        token_counts: Counter[str] = Counter()

        for model in known_models:
            for token in _MODEL_TOKEN_RE.findall(model):
                if len(token) >= self.MIN_MODEL_TOKEN_LENGTH:
                    token_counts[token] += 1

        for token, count in token_counts.items():
            if self.MIN_MODEL_TOKEN_FREQUENCY <= count <= self.MAX_MODEL_TOKEN_FREQUENCY:
                terms.add(token)

        return frozenset(terms)

    @staticmethod
    def _sorted_by_length(terms: frozenset[str]) -> tuple[str, ...]:
        return tuple(sorted(terms, key=lambda term: (-len(term), term.lower())))

    @staticmethod
    def _has_word_boundary(text: str, start: int, end: int) -> bool:
        def is_word_char(character: str) -> bool:
            return character.isalpha() or character.isdigit()

        left_ok = start == 0 or not is_word_char(text[start - 1])
        right_ok = end == len(text) or not is_word_char(text[end])
        return left_ok and right_ok

    @classmethod
    def find_matches(
        cls,
        text: str,
        terms: tuple[str, ...],
        *,
        require_word_boundary: bool = False,
    ) -> list[str]:
        """Return catalog terms found in *text*, longest match first, no overlaps."""
        if not text or not terms:
            return []

        lowered = text.lower()
        occupied: list[tuple[int, int]] = []
        found: list[tuple[int, str]] = []

        for term in terms:
            term_lower = term.lower()
            start = 0
            while True:
                index = lowered.find(term_lower, start)
                if index == -1:
                    break
                end = index + len(term)
                if require_word_boundary and not cls._has_word_boundary(text, index, end):
                    start = index + 1
                    continue
                if not any(not (end <= left or index >= right) for left, right in occupied):
                    occupied.append((index, end))
                    found.append((index, term))
                start = index + 1

        found.sort(key=lambda item: item[0])
        return [term for _, term in found]

    def match_models(self, text: str) -> list[str]:
        return self.find_matches(text, self._models_sorted, require_word_boundary=True)

    def match_colors(self, text: str) -> list[str]:
        return self.find_matches(text, self._colors_sorted)

    def match_materials(self, text: str) -> list[str]:
        return self.find_matches(text, self._materials_sorted)

    def match_part_types(self, text: str) -> list[str]:
        return self.find_matches(text, self._part_types_sorted, require_word_boundary=True)
