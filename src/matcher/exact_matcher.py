"""Step-0 lexical catalog lookup by canonical name and millimetre dimensions."""

from __future__ import annotations

import re
from collections import defaultdict

from src.models import CatalogEntity, ExtractedFeatures
from src.preprocessor.normalizer import extract_dimension_tokens, normalize_text

_KIT_COUNT_RE = re.compile(r"(\d+)\s*шт", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


def _canonical_name_key(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", normalize_text(text or "").lower()).strip()


class ExactCatalogMatcher:
    """Deterministic lookup of v8 rows by canonical name and millimetre tokens."""

    def __init__(self, catalog: list[CatalogEntity]) -> None:
        self._by_dimension: dict[str, list[CatalogEntity]] = defaultdict(list)
        self._by_name: dict[str, list[CatalogEntity]] = defaultdict(list)
        for entity in catalog:
            name_key = _canonical_name_key(entity.nomenclature)
            if name_key:
                self._by_name[name_key].append(entity)
            blob = " ".join(
                part
                for part in (entity.nomenclature, entity.module, entity.characteristic)
                if part
            )
            for token in extract_dimension_tokens(blob):
                self._by_dimension[token].append(entity)

    def exact_name_candidates(self, *texts: str) -> list[CatalogEntity]:
        for text in texts:
            key = _canonical_name_key(text)
            if key and key in self._by_name:
                return list(self._by_name[key])
        return []

    def candidates_for(
        self,
        query: str,
        features: ExtractedFeatures | None = None,
    ) -> list[CatalogEntity]:
        named = self.exact_name_candidates(query)
        if named:
            return named

        tokens = extract_dimension_tokens(query)
        if features is not None:
            for dimension in features.dimensions:
                tokens.extend(extract_dimension_tokens(dimension))
        unique_tokens: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            if token not in seen:
                seen.add(token)
                unique_tokens.append(token)
        if not unique_tokens:
            return []

        ranked = sorted(
            unique_tokens,
            key=lambda token: (len(self._by_dimension.get(token, [])), -len(token)),
        )
        entities: list[CatalogEntity] = []
        for token in ranked:
            pool = self._by_dimension.get(token)
            if pool:
                entities = list(pool)
                break
        kit = _kit_count(query)
        if kit and entities:
            kit_hits = [
                entity
                for entity in entities
                if _kit_count(entity.nomenclature) == kit
            ]
            if kit_hits:
                return kit_hits
        return entities


def _kit_count(text: str) -> str | None:
    match = _KIT_COUNT_RE.search(text or "")
    if not match:
        return None
    return match.group(1)
