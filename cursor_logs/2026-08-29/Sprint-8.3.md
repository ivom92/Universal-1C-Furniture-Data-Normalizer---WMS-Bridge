# Sprint 8.3 — Strict line numbers (№), source order, WMS 5-column export

**Date:** 2026-08-29  
**Status:** Completed

## Scope

- Add first WMS column `№` (`order_line_number` from the 1C 7.7 picking list).
- Sort export, Streamlit, CLI report, and matcher output by `order_line_number` so LLM thread-pool completion cannot shuffle rows.
- Extend audit and chaos tests for `[1..N]`, integer `№`, and zero-loss identity vs parsed blocks.

## Files Created / Changed

| File | Action |
|------|--------|
| `src/models.py` | `order_line_number` property on `RawOrderBlock`, `MatchDecision`, `MatchedOrderItem` |
| `src/adapters/wms_excel_adapter.py` | 5-column contract; sort before write; centered integer `№`; graphite header on all columns |
| `src/matcher/hybrid_matcher.py` | `_in_source_order()` after auto-match and LLM merge |
| `app_ui.py` | WMS caption, preview/results sorted by `№`, download uses sorted adapter |
| `src/utils/reporter.py` | CLI table sorted by `order_line_number` |
| `scripts/run_order.py` | Sort decisions before export |
| `scripts/audit_wms_export.py` | Header `№`, `[1..N]` ints, row identity vs parse |
| `scripts/generate_synthetic_orders.py` | `write_html_xls_with_skipped_cells`; 4th named fixture |
| `tests/test_wms_adapter.py` | 5 columns; `test_wms_export_strict_line_number_order` |
| `tests/test_chaos.py` | Monotonic `1..N` on HTML-XLS with skipped cells |
| `tests/test_end_to_end.py`, `tests/test_parsers.py`, `tests/test_matcher.py` | Column index shift |
| `.cursorrules` | Target contract updated to 5 columns |

## Key Design Decisions

1. **`order_line_number` is an alias, not a second stored field.** Parser still fills `line_number`; the property is the export/sort key required by the TZ (`sorted(..., key=lambda x: x.order_line_number)`).
2. **Sort in three places.** Matcher restores order after parallel LLM; adapter sorts again before Excel; UI/CLI sort for display. Shuffled decision lists still export as `1, 2, 3, … N`.
3. **Identity audit.** The *i*-th WMS data row must have `№ == i` and quantity equal to the *i*-th source block after sorting parsed blocks by `order_line_number`.
4. **Chaos skipped cells.** HTML-as-XLS fixture inserts blank rows and ragged empty `<td>`s; parser still yields consecutive `1..N`, and WMS column `№` matches.

## Test Results

```
python scripts/generate_synthetic_orders.py
pytest tests/test_chaos.py -v
pytest -q --tb=line
```

```
3 passed in 21.82s
121 passed, 1 warning in 27.67s
```

Acceptance: full suite green. Met (121).

## Live CLI

```
LLM_PROVIDER=gemini python scripts/run_order.py "data/orders/order_transfering_01_09.xls"
python scripts/audit_wms_export.py
```

| Metric | Result | TZ target |
|--------|--------|-----------|
| Rows | 384 | 384 |
| Places (qty sum) | 871 | 871 |
| Column `№` | `[1..384]` ints | first column `№` |
| MATCHED_AUTO | 377 (98.2%) | — |
| MATCHED_LLM | 6 (1.6%) | — |
| QUARANTINE | 1 (0.3%) | — |
| Factory barcodes recovered | 352 | — |
| Без ШК | 31 | — |
| Audit | PASS | 384==384, 871==871, №=[1..384], first 20 match mapping |

WMS file: `output/WMS_Импорт_РС УрФО Империал_2026-08-29.xlsx`.

## Challenges & Caveats

1. **Openpyxl column A width.** Autofit is capped to 8–10 characters for `№` so the graphite header does not stretch the index column.
2. **Filtered Streamlit tabs.** The full table is `1..N`. Status tabs keep original `№` values (gaps are expected when a subset is shown).
3. **`.env` still defaults `LLM_PROVIDER=ollama`.** Acceptance used `LLM_PROVIDER=gemini` in the process environment.

## Next sprint preview

- Operator overrides from Streamlit written back into the same 5-column WMS workbook.
- Optional audit of Excel `№` vs printed column A when a source file has non-contiguous printed numbers (current contract assumes sequential 1..N from the parser).
