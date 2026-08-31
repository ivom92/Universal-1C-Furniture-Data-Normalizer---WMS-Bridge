# Sprint 8.15 — Polish: Single Browser Tab & Office COM Isolation

**Date:** 2026-08-30  
**Status:** Completed

## Scope

- Eliminate duplicate `http://localhost:8501` browser tabs on startup by making Streamlit headless and leaving browser open to launchers only.
- Audit parsers/adapters/utils for Microsoft Office COM automation; enforce guardrails in dist build.
- Rebuild warehouse zip with updated launcher/config bundle.

## Files Created / Changed

| File | Action |
|------|--------|
| `.streamlit/config.toml` | `server.headless = true` — suppresses Streamlit's internal `webbrowser.open` |
| `2_ЗАПУСК.bat` | Added `--server.headless true`; kept single delayed `start http://localhost:8501` (3 s) |
| `Запуск_WMS.vbs` | Added `--server.headless true`; kept single `WScript.Sleep 3000` + `sh.Run` URL |
| `scripts/build_warehouse_dist.py` | **New checks:** `assert_no_office_com()`, `assert_streamlit_headless()` before packaging |
| `dist/Warehouse_WMS_Pilot_v1.0.zip` | Rebuilt (53 files) |

## Key Design Decisions

1. **Single browser owner = launcher.** Streamlit `headless=true` in both `config.toml` and CLI flags (`--server.headless true`) so even a missing config in a partial deploy cannot re-enable auto-open. BAT/VBS each retain exactly one browser invocation after a 2–3 s warm-up delay.
2. **COM audit is build-time, not runtime.** Codebase already used only `openpyxl` / `xlrd` / `BeautifulSoup` / `lxml`. Added regex guards in `build_warehouse_dist.py` to block future regressions (`win32com`, `Excel.Application`, `os.startfile(`, `xlwings`, `comtypes`).
3. **No parser logic changes.** Office Click-to-Run modal (0x426-0x0) root cause was dual browser open + potential OS file association side effects; parsing/export paths were already COM-free.

## Office COM Audit (src/parsers, src/adapters, src/utils)

| Module | Excel read | Excel write | COM / shell hooks |
|--------|------------|-------------|-------------------|
| `v7_parser.py` | `openpyxl`, `xlrd`, `BeautifulSoup` (HTML-as-XLS) | — | None |
| `v8_loader.py` | `openpyxl` read-only | — | None |
| `soft_furniture_parser.py` | via `v7_parser.open_order_sheet` | — | None |
| `wms_excel_adapter.py` | — | `openpyxl` (file + `BytesIO`) | None |
| `history_manager.py` | raw bytes reread | sidecar JSON only | None |

`requirements.txt` contains no `pywin32`, `xlwings`, or `comtypes`.

## Test Results

```
.\venv\Scripts\python.exe -m pytest tests/ -v --tb=short
175 passed, 1 warning in 52.24s

.\venv\Scripts\python.exe scripts\run_order.py "data/orders/order_transfering_01_09.xls"
```

| Metric | Value |
|--------|--------|
| Rows | 384 |
| MATCHED_AUTO | 379 (98.7%) |
| MATCHED_LLM | 5 (1.3%) |
| QUARANTINE | 0 (0.0%) |

```
.\venv\Scripts\python.exe scripts\build_warehouse_dist.py
→ dist/Warehouse_WMS_Pilot_v1.0.zip — 19 659 730 bytes, 53 files
```

## Challenges & Caveats

- **`build_warehouse_dist.py` self-scan false positive:** Initial broad `\bwin32com\b` pattern matched the guard script's own error strings. Fixed by tightening import/call patterns and excluding `build_warehouse_dist.py` from the scan set.
- **Linux `2_run.sh` unchanged:** Headless Streamlit on macOS/Linux means no auto browser; operators open `:8501` manually (acceptable for warehouse Windows pilot focus).
- **Manual single-tab verification** requires launching `2_ЗАПУСК.bat` or `Запуск_WMS.vbs` on operator workstation — not automatable in CI.

## Next sprint preview

- Add pytest asserting `config.toml` headless + launcher single-URL contract (extend `test_silent_launcher_and_warehouse_readme_exist`).
- Optional: health-check banner in Streamlit UI reminding operators to use `Запуск_WMS.vbs` shortcut only once per session.
