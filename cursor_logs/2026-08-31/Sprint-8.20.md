# Sprint 8.20 — Windows 8 / Legacy Console & Admin Launch Path Fix

## Scope
- Fix warehouse `.bat` launchers for Windows 8/8.1: cwd lock (`cd /d "%~dp0"` first line), safe `chcp`, Python UTF-8 env vars.
- Use `venv\Scripts\python.exe` directly (no `activate.bat`, no `py.exe` launcher).
- Harden `logger.py` / `app_ui.py` against broken legacy console (`lost sys.stderr`).
- Update VBS silent launcher and dist build validation.

## Files Changed
| File | Change |
|------|--------|
| `1_УСТАНОВКА.bat` | cwd first line; safe chcp; PYTHON env; `%PY%` for pip/health after venv |
| `2_ЗАПУСК.bat` | cwd first line; direct `venv\Scripts\python.exe -m streamlit` |
| `3_ОСТАНОВИТЬ.bat` | cwd first line; safe chcp |
| `Запуск_WMS.vbs` | `python.exe -m streamlit`; PYTHON env in cmd line |
| `app_ui.py` | `PYTHONIOENCODING` / `PYTHONLEGACYWINDOWSSTDIO` / `PYTHONUTF8` defaults |
| `src/utils/logger.py` | try/except around stdout/stderr `reconfigure` |
| `README_СКЛАД.txt` | Windows 8 / admin launch note |
| `scripts/build_warehouse_dist.py` | `assert_bat_launchers_windows8()` pre-build gate |
| `tests/test_ui_contracts.py` | launcher contract tests for cwd, chcp, venv python path |

## Key Design Decisions
1. **`cd /d "%~dp0"` as line 1** — before `@echo off`, so admin elevation to `System32` cannot break relative paths.
2. **`chcp 65001 >nul 2>&1`** — suppresses Windows 8 console device errors; UTF-8 handled via Python env + safe reconfigure.
3. **Direct venv python** — `2_ЗАПУСК.bat` and VBS skip `activate.bat` and call `venv\Scripts\python.exe -m streamlit`.
4. **Stop script** — no PYTHON env vars (no Python invocation); cwd + safe chcp only.

## Test Results
```
pytest tests/ -v  →  205 passed
python scripts/run_order.py "data/orders/order_transfering_01_09.xls"  →  OK
python scripts/build_warehouse_dist.py  →  dist/Warehouse_WMS_Pilot_v1.0.zip (19.7 MB, 56 files)
```

## Acceptance Criteria
- [x] `.bat` scripts work with normal and admin launch (cwd fixed)
- [x] Legacy console hardening (chcp redirect, PYTHON env, safe stderr)
- [x] All tests green (205)
- [x] Warehouse dist rebuilt

## Next Sprint Preview
- Optional: align `start_warehouse_app.bat` with same Windows 8 launcher pattern.
- Field validation on actual Windows 8.1 warehouse PC after zip deploy.
