# Sprint 8.0 — Production Hardening, Synthetic Chaos Engine & UI polish

**Date:** 2026-08-29  
**Status:** Completed (quarantine count is 1, not 4 — documented below)

## Scope

- Parse HTML tables saved as `.xls`; NFKC incoming cells without destroying `№`.
- Furniture abbreviation dictionary (`д.сон.`, `ящ`, `(FE)`, `СТАРТ`, …).
- Synthetic chaos generator + zero-loss pytest path.
- Quarantine UI copy for operators.
- Pull Равенна `(FE)` / line 51 and СТАРТ drawer (line 384) into auto-match.

## Files Created / Changed

| File | Action |
|------|--------|
| `src/parsers/v7_parser.py` | HTML-as-XLS detector + `_MatrixSheetView`; NFKC with `№` guard; footer `ИТОГО`/`Экспедитор` cutoff |
| `src/matcher/token_normalizer.py` | `FURNITURE_ABBREVIATIONS` + FE / ящ / ств / СТАРТ |
| `src/matcher/feature_extractor.py` | Expand abbreviations before color/dimension parse |
| `src/matcher/hybrid_matcher.py` | Finish-suffix hard filter; 1/1↔Ун1/1; nomenclature slug lexical; alias isolation; cut-to-size glass |
| `src/parsers/v8_loader.py` | Hardware types `доводчик` / `направляющ` |
| `app_ui.py`, `src/utils/reporter.py` | Quarantine factory name `— (Отсутствует в 1С 8)` + operator note |
| `scripts/generate_synthetic_orders.py` | Chaos generator (HTML-XLS, messy `*`, no-alias `.xls`) |
| `tests/test_chaos.py`, `tests/test_parsers.py`, `tests/test_features.py`, `tests/test_matcher.py` | Coverage |
| `requirements.txt` | `lxml`, `beautifulsoup4` |

## Key Design Decisions

1. **NFKC keeps `№`.** Compatibility decomposition turns `№` into `No` and broke header detection. The parser guards U+2116 around NFKC, then still collapses `\xa0`.
2. **`(FE)` / `(SB)` / `(Д)` is a hard finish filter.** Line 48/49 keep enamel SKUs; line 51 (no suffix) maps to the non-FE корпус row via slug equality after stripping `без цвета` and packaging.
3. **Incompatible yellow aliases are dropped** when distinctive-token overlap with the client name is `≤ 0.5`. Line 384 no longer inherits `IMP к Равенна … (SB)` from the sheet footer, so СТАРТ does not fail a phantom SB finish check.
4. **Glass/mirrors with extracted WxH require an exact pair in v8.** Candidates without those dimensions no longer pass “missing pairs = compatible”.

## Test Results

```
pytest -q --tb=line
110 passed, 1 warning in 29.45s
```

Acceptance: ≥110 tests, 100% passed. Met.

## Live CLI

```
LLM_PROVIDER=gemini python scripts/run_order.py "data/orders/order_transfering_01_09.xls"
```

| Metric | Result | TZ target |
|--------|--------|-----------|
| Rows | 384 | 384 |
| Places (qty sum) | 871 | 871 |
| MATCHED_AUTO | **377 (98.2%)** | ≥372–376 (>97%) |
| MATCHED_LLM | **6 (1.6%)** | ≈4–8 |
| QUARANTINE | **1 (0.3%)** | строго 4 |
| Factory barcodes recovered | 351 | — |

- **№51** `Кухня Равенна Н60 2ящ` → `MATCHED_AUTO` / `exact_article` (non-FE корпус).
- **№48/49** `(FE)` → `MATCHED_AUTO` enamel SKUs.
- **№384** `Ящик с доводчиком Н86 400 белый СТАРТ` → catalog `Ящик с доводчиком Н86 400 Белый упаковка Ун1/1` (`AUTO_NO_BARCODE` if that v8 row has no EAN-13).

## Challenges & Caveats

1. **Quarantine is 1, not 4.** The three mirrors (`1754*368`, `1776*495`, `858х294`) **are in `catalog_v8.xlsx`** (Эшли / Аврора / Интер with those millimetre cuts). Sprint 7.2 treated them as physically absent; they now auto/LLM-match. The remaining quarantine row is **№230** `Кухня Равенна полка стеклянная 60 … 565х255` — no v8 row with that cut. Asserts were not loosened to force four quarantine slots.
2. **`.env` still defaults `LLM_PROVIDER=ollama`.** The acceptance run set `LLM_PROVIDER=gemini` in the process environment.
3. **NFKC is not applied blindly to `№`.** Full unguarded NFKC would rename every `№` column to `No` and collapse the layout engine.

## Next sprint preview

- If the factory later adds Равенна glass shelf 565×255, №230 should leave quarantine without changing cut-to-size rules.
- Persist synthetic fixtures under `data/synthetic/` in CI if chaos generation from the live catalog becomes too slow.
