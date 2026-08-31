# Sprint 7.0 — Lexical article search & Universal Layout Engine

**Date:** 2026-08-29  
**Status:** Completed

## Scope

- Exact article / technical-number match (Step 0 before FAISS) for fittings, lamps, corners
- Multi-size width lists `160/140/120` → `[1600, 1400, 1200]` in hard filters
- Dynamic spatial column mapping for 1C v7.7 printed tables
- Streamlit: active LLM model name + per-row quarantine reason

## Files Created / Changed

| File | Action |
|------|--------|
| `src/parsers/v8_loader.py` | `extract_article_tokens`, `build_article_index`, hardware-type index from catalog text |
| `src/matcher/hybrid_matcher.py` | Lexical Step 0 (`exact_article`, score 1.0); alternative-width hard filter; quarantine reasons |
| `src/matcher/feature_extractor.py` | Triple-size pattern; do not treat `160/140` as package ratio |
| `src/models.py` | `ExtractedFeatures.alternative_widths` |
| `src/parsers/v7_parser.py` | `TableLayout` + sequential integer block assembly |
| `app_ui.py` | Model caption; quarantine «Причина» column |
| `tests/test_matcher.py` | Article 1516, lamp SKU, Chicago 160/140/120 |
| `tests/test_parsers.py` | Shifted-column layout; article token extraction |
| `tests/excel_fixtures.py` | `write_shifted_columns_v7_xlsx` |
| `tests/test_llm_resolver.py` | Count `exact_article*` as auto-match |

## Key Design Decisions

1. **Article index is catalog-driven.** Tokens (≥4 digits, dotted SKUs, `1754*368`) are harvested from v8 names at matcher init. Dimension runs (`116х596`) are masked so millimetre sizes are not treated as articles.
2. **Hardware type words** (планка, корнер, …) only disambiguate article hits or unique phrases already present in the loaded catalog — no furniture model lists.
3. **Alternative widths** scale cm-like values (`160` → `1600`) and pass the hard filter if the v8 candidate contains at least one listed width.
4. **Layout engine** binds `№`, rack, name, type, qty from the header row; continuation rows between integers `N` and the next number are accumulated (alias / service / extras).
5. **Quarantine copy** distinguishes missing article, custom glass/mirror size, and generic catalog miss.

## Test Results

```
pytest -q --tb=line
88 passed, 3 warnings in 325.59s
```

Acceptance: ≥ 88 tests, 0 failures. Met.

## Next sprint preview

- Measure live `Перемещение 01.09.xls`: quarantine 4–5 custom glass/mirrors, auto-match ~92–95%
