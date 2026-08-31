"""Canonical token forms for vector search (packaging, abbreviations, collection prefixes)."""

from __future__ import annotations

import re

from src.preprocessor.normalizer import normalize_text

_PACKAGING_ALREADY_RE = re.compile(
    r"упаковка\s+(?:ун\s*)?\d+/\d+",
    re.IGNORECASE,
)
_BARE_RATIO_RE = re.compile(
    r"(?<![А-Яа-яA-Za-z0-9])((?:Ун\s*)?\d+/\d+)(?![А-Яа-яA-Za-z0-9/])",
    re.IGNORECASE,
)
_IMP_COLLECTION_RE = re.compile(
    r"\bIMP\s+(прих|сп|к|г)\b",
    re.IGNORECASE,
)
_IMP_COLLECTION_MAP = {
    "сп": "спальня",
    "к": "кухня",
    "прих": "прихожая",
    "г": "гостиная",
}

FURNITURE_ABBREVIATIONS: dict[str, str] = {
    r"\bд\.?\s*сон(ома)?\b": "дуб сонома",
    r"\bб\.?\s*дер(ево)?\b": "белое дерево",
    r"\bяс\.?\s*шим(о)?\b": "ясень шимо",
    r"\bателье\s*св(ет)?\b": "ателье светлый",
    r"\bкат(\d+)\b": r"категория \1",
    r"\(\s*FE\s*\)|(?<![A-Za-z(])FE(?![A-Za-z)])": "(FE) фасад эмаль",
    r"\bстарт\b": "",
    r"(\d+)\s*ящ\b": r"\1ящик",
    r"(?<![а-яёa-z0-9])ящ\b": "ящик",
    r"(\d+)\s*ств\b": r"\1створка",
    r"(?<![а-яёa-z0-9])ств\b": "створка",
}

_ABBREVIATION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), replacement)
    for pattern, replacement in FURNITURE_ABBREVIATIONS.items()
)


def canonicalize_search_text(text: str) -> str:
    """Expand abbreviations so embeddings see the same tokens as the v8 catalog."""
    if not text:
        return ""
    expanded = normalize_text(text)
    expanded = expand_furniture_abbreviations(expanded)
    expanded = _expand_imp_collection_prefixes(expanded)
    return _canonicalize_packaging_tokens(expanded)


def expand_furniture_abbreviations(text: str) -> str:
    """Normalize typical 1C furniture shorthand (colors, FE enamel, drawers)."""
    expanded = text
    for pattern, replacement in _ABBREVIATION_PATTERNS:
        expanded = pattern.sub(replacement, expanded)
    return re.sub(r"\s+", " ", expanded).strip()


def _expand_imp_collection_prefixes(text: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        key = match.group(1).lower()
        expanded = _IMP_COLLECTION_MAP.get(key)
        if not expanded:
            return match.group(0)
        return f"IMP {expanded}"

    return _IMP_COLLECTION_RE.sub(_replace, text)


def _canonicalize_packaging_tokens(text: str) -> str:
    protected: list[str] = []

    def _protect(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"\x00P{len(protected) - 1}\x00"

    masked = _PACKAGING_ALREADY_RE.sub(_protect, text)

    def _wrap_ratio(match: re.Match[str]) -> str:
        raw = match.group(1)
        compact = re.sub(r"\s+", "", raw)
        if cleaned := _compact_universal(compact):
            return f"упаковка {cleaned}"
        return f"упаковка {compact}"

    wrapped = _BARE_RATIO_RE.sub(_wrap_ratio, masked)
    for index, original in enumerate(protected):
        wrapped = wrapped.replace(f"\x00P{index}\x00", original)
    return re.sub(r"\s+", " ", wrapped).strip()


def _compact_universal(value: str) -> str:
    match = re.match(r"ун(\d+/\d+)$", value, re.IGNORECASE)
    if match:
        return f"Ун{match.group(1)}"
    return value
