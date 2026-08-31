"""Canonical string normalization for catalog and order matching."""

from __future__ import annotations

import re
import unicodedata

from src.utils.logger import get_logger

logger = get_logger()

_DIM_SEP_CLASS = r"[xXхХ*×]"
_DIM_CHAIN_RE = re.compile(rf"(\d+(?:\s*{_DIM_SEP_CLASS}\s*\d+)+)")
_CANON_CHAIN_RE = re.compile(r"\d+(?:x\d+)+")
_SEP_SPLIT_RE = re.compile(_DIM_SEP_CLASS)
_TOKEN_RE = re.compile(r"\S+")
_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff]")
_MODULE_LOW_RE = re.compile(r"\b[HНhн]\s*[-_]?\s*(\d+)")
_MODULE_UP_RE = re.compile(r"\b[BВbв]\s*[-_]?\s*(\d+)")

# Visual Latin ↔ Cyrillic pairs (plus v/в required for «Раvенна»).
_LAT_TO_CYR = {
    "a": "а",
    "c": "с",
    "e": "е",
    "o": "о",
    "p": "р",
    "v": "в",
    "x": "х",
    "y": "у",
    "A": "А",
    "B": "В",
    "C": "С",
    "E": "Е",
    "H": "Н",
    "K": "К",
    "M": "М",
    "O": "О",
    "P": "Р",
    "T": "Т",
    "X": "Х",
}
_CYR_TO_LAT = {cyr: lat for lat, cyr in _LAT_TO_CYR.items()}
_MAJORITY_THRESHOLD = 0.6
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
_MOJIBAKE_LATIN_RE = re.compile(r"[\u00C0-\u00FF]")


def heal_mojibake(text: str) -> str:
    """Repair Windows-1251 bytes misread as Latin-1 (``Àâðîðà`` -> ``Аврора``)."""
    if not text:
        return ""
    if _CYRILLIC_RE.search(text):
        return text
    if not _MOJIBAKE_LATIN_RE.search(text):
        return text
    try:
        repaired = text.encode("latin-1").decode("cp1251", errors="ignore")
    except UnicodeEncodeError:
        return text
    if _CYRILLIC_RE.search(repaired):
        logger.debug("[Normalizer] Mojibake healed: %r -> %r", text[:60], repaired[:60])
        return repaired
    return text


def canonicalize_dimensions(text: str) -> str:
    """Rewrite WxH / WxHxD runs to compact latin ``x`` separators (``565x255``)."""
    if not text:
        return ""

    def _replace(match: re.Match[str]) -> str:
        raw_dim = match.group(0)
        parts = _SEP_SPLIT_RE.split(raw_dim)
        numbers = [part.strip() for part in parts if part.strip()]
        canon_dim = "x".join(numbers)
        if canon_dim != raw_dim:
            logger.debug("[Normalizer] Dim '%s' canonicalized -> '%s'", raw_dim, canon_dim)
        return canon_dim

    return _DIM_CHAIN_RE.sub(_replace, text)


def extract_dimension_tokens(text: str) -> list[str]:
    """Return unique canonical dimension tokens, including consecutive 2D windows."""
    if not text:
        return []
    canonical = canonicalize_dimensions(text)
    tokens: list[str] = []
    seen: set[str] = set()
    for match in _CANON_CHAIN_RE.finditer(canonical):
        parts = match.group(0).split("x")
        for window in range(2, len(parts) + 1):
            for start in range(0, len(parts) - window + 1):
                token = "x".join(parts[start : start + window])
                if token not in seen:
                    seen.add(token)
                    tokens.append(token)
    return tokens


def repair_mixed_script_token(token: str) -> str:
    """Repair Latin/Cyrillic homoglyphs using majority-script voting on one token."""
    if not token:
        return ""
    cyrillic = 0
    latin = 0
    for char in token:
        if _is_cyrillic_letter(char):
            cyrillic += 1
        elif _is_latin_letter(char):
            latin += 1
    total = cyrillic + latin
    if total == 0:
        return token
    if cyrillic > 0 and cyrillic / total >= _MAJORITY_THRESHOLD:
        repaired = "".join(_LAT_TO_CYR.get(char, char) for char in token)
        if repaired != token:
            logger.debug(
                "[Normalizer] Token '%s' -> '%s' (dominance=%.2f)",
                token,
                repaired,
                cyrillic / total,
            )
        return repaired
    if latin > 0 and latin / total >= _MAJORITY_THRESHOLD:
        repaired = "".join(_CYR_TO_LAT.get(char, char) for char in token)
        if repaired != token:
            logger.debug(
                "[Normalizer] Token '%s' -> '%s' (dominance=%.2f)",
                token,
                repaired,
                latin / total,
            )
        return repaired
    return token


def canonicalize_furniture_module_codes(text: str) -> str:
    """Canonical kitchen/module prefixes: ``H20`` → ``Н20``, ``B-60`` → ``В60``."""
    if not text:
        return ""
    rewritten = _MODULE_LOW_RE.sub(r"Н\1", text)
    return _MODULE_UP_RE.sub(r"В\1", rewritten)


def normalize_text(text: str) -> str:
    """Base matching chain: mojibake heal → dimensions → cleanup → mixed-script repair → module codes."""
    if not text:
        return ""
    cleaned = heal_mojibake(text)
    cleaned = canonicalize_dimensions(cleaned)
    cleaned = _strip_special_chars(cleaned)
    cleaned = _TOKEN_RE.sub(lambda match: repair_mixed_script_token(match.group(0)), cleaned)
    cleaned = canonicalize_furniture_module_codes(cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _strip_special_chars(text: str) -> str:
    cleaned = unicodedata.normalize("NFKC", text)
    cleaned = _ZERO_WIDTH_RE.sub("", cleaned)
    cleaned = cleaned.replace("\xa0", " ").replace("\u202f", " ")
    return re.sub(r"\s+", " ", cleaned).strip()


def _is_cyrillic_letter(char: str) -> bool:
    return "CYRILLIC" in unicodedata.name(char, "")


def _is_latin_letter(char: str) -> bool:
    return "LATIN" in unicodedata.name(char, "")
