# Sprint 5.2 — Legacy .xls Compatibility & Watcher Fix

**Date:** 2026-08-29  
**Status:** Completed

## Scope

- Parse 1C 7.7 picking lists in both `.xlsx` (openpyxl) and Excel 97–2003 `.xls` (xlrd)
- Accept file paths, `bytes`, and `io.BytesIO` (Streamlit uploader)
- Disable Streamlit file watcher (`fileWatcherType = "none"`) to stop `torchvision` spam
- Keep `V7ParseResult` / `RawOrderBlock` contracts unchanged; xlsx/xls of the same document must be equivalent

## Files Created / Changed

| File | Action |
|------|--------|
| `requirements.txt` | Added `xlrd>=2.0.1` and `xlwt>=1.3.0` (fixture writer) |
| `.streamlit/config.toml` | `fileWatcherType = "none"`, port 8501, `[browser] gatherUsageStats = false` |
| `src/parsers/v7_parser.py` | Format detection, xlrd adapter, shared state machine |
| `app_ui.py` | Uploader `.xlsx`/`.xls`, parse from bytes, customer preview |
| `scripts/run_order.py` | CLI path for `.xlsx` and `.xls` |
| `scripts/diagnose_matcher.py` | Same order-path resolution |
| `tests/excel_fixtures.py` | **New** — synthetic 3-row v7 workbooks |
| `tests/test_parsers.py` | `.xls` parse, streams, equivalence, E2E WMS |

## Key Design Decisions

1. **Sheet adapters** (`_OpenpyxlSheetView` / `_XlrdSheetView`) — one state machine over 1-based cells; `excel_row_start` stays Excel-native.
2. **Magic bytes first** — OLE2 `D0 CF 11 E0` vs ZIP `PK 03 04`, then extension / filename from `st.file_uploader`.
3. **xlrd number normalization** — whole floats become `int` so line numbers stay `"1"` not `"1.0"`.
4. **Fill matching** — exact `FFE0FFE0` / `FFFFFFC0` plus RGB tolerance so xlwt palettes still classify as green/yellow.
5. **Customer labels** — `Покупатель:`, `Получатель:`, `Контрагент:` without changing `RawOrderBlock`.
6. **Watcher** — `none` instead of Sprint 5.1 `poll`, so Streamlit does not inspect `transformers`/`torch`.

## Test Results

```
pytest -v  →  59 passed
```

New coverage: header from `.xls`, 3-row blocks, `bytes`/`BytesIO`, xlsx↔xls `model_dump()` equality, Zero-Loss through `HybridMatcher` → `WMSExcelAdapter`.

## Next sprint preview

- Production hardening: real 1C 7.7 `.xls` print forms from the warehouse (merged cells, extra header rows)
- Optional: persist parsed preview cache keys by file hash instead of name+size
