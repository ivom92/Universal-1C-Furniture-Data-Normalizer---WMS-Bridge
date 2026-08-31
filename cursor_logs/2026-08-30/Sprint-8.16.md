# Sprint 8.16 — Process Lifecycle & Clean Shutdown

**Date:** 2026-08-30  
**Status:** Completed

## Scope

- Fix Windows file locking when browser tab is closed but Streamlit `python.exe` keeps running.
- Add in-UI shutdown button and external stop scripts (`3_ОСТАНОВИТЬ.bat`, `Остановить_WMS.vbs`).
- Make launchers idempotent: if port 8501 is already listening, open browser only (no duplicate process).
- Package stop scripts in warehouse zip; update operator instructions.

## Files Created / Changed

| File | Action |
|------|--------|
| `app_ui.py` | `_shutdown_server()` + sidebar button «🛑 Завершить работу сервера» (`SIGTERM` / `os._exit` fallback) |
| `3_ОСТАНОВИТЬ.bat` | **New:** `netstat` + `taskkill` on port 8501, Russian success message, 3 s pause |
| `Остановить_WMS.vbs` | **New:** silent stop via hidden `cmd.exe` (no console window) |
| `2_ЗАПУСК.bat` | Port 8501 check before `streamlit run`; opens browser only if already running |
| `Запуск_WMS.vbs` | Same idempotent port check; `WScript.Quit 0` when server already up |
| `scripts/build_warehouse_dist.py` | Added `3_ОСТАНОВИТЬ.bat`, `Остановить_WMS.vbs` to `INCLUDE_FILES` |
| `README_СКЛАД.txt` | Section «Остановка сервера» + idempotent launch note |
| `ИНСТРУКЦИЯ_ДЛЯ_ЕГОРА.txt` | Stop instructions; updated «Не удаляйте» file list |
| `tests/test_ui_contracts.py` | 3 new contract tests for stop scripts, shutdown button, dist inclusion |
| `dist/Warehouse_WMS_Pilot_v1.0.zip` | Rebuilt (55 files) |

## Key Design Decisions

1. **Shutdown path:** UI shows `st.warning`, sleeps 0.5 s, then `os.kill(pid, SIGTERM)` with `os._exit(0)` fallback on Windows edge cases.
2. **Port-based lifecycle:** Both start and stop scripts use `netstat -aon | findstr :8501 | findstr LISTENING` — reliable for Streamlit default port without PID files.
3. **Idempotent launch:** BAT/VBS exit early with browser open when port is occupied — prevents «Address already in use» on double-click.
4. **Silent stop:** VBS runs `taskkill` synchronously (`sh.Run ..., True`) so folder unlock is immediate before script exits.

## Test Results

```
.\venv\Scripts\python.exe -m pytest tests/ -v --tb=short
178 passed, 1 warning in 59.95s

.\venv\Scripts\python.exe scripts\run_order.py "data/orders/order_transfering_01_09.xls"
→ exit 0

.\venv\Scripts\python.exe scripts\build_warehouse_dist.py
→ dist/Warehouse_WMS_Pilot_v1.0.zip — 19 661 831 bytes, 55 files
```

New contract tests: `test_stop_scripts_and_idempotent_launch_bat`, `test_app_ui_has_shutdown_button`, `test_build_dist_includes_lifecycle_scripts`.

## Challenges & Caveats

- **Closing browser tab ≠ stopping server:** Operators must use shutdown button or stop scripts; documented prominently in both README files.
- **`SIGTERM` on Windows:** May not always propagate cleanly through Streamlit's process tree; `os._exit(0)` fallback ensures termination.
- **Multiple PIDs on 8501:** `for /f` loop kills all LISTENING PIDs — handles rare orphan listener edge cases.
- **Manual verification** of file-unlock after stop requires operator workstation — not automatable in CI.

## Next sprint preview

- Optional: Streamlit `on_shutdown` hook or `atexit` for graceful cache flush before kill.
- Optional: tray icon / Windows service wrapper for non-technical operators who forget to stop.
