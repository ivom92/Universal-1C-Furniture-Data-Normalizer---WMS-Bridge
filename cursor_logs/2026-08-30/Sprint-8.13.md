# Sprint 8.13 — Batch scan station + silent Windows launcher

**Date:** 2026-08-30  
**Status:** Completed

## Scope

- Tab 2 («Станция ШК / Карантин»): dropdown over all orders in the current batch; scan table shows `QUARANTINE` and `AUTO_NO_BARCODE` for the selected file only.
- Silent warehouse launcher `Запуск_WMS.vbs` (hidden `cmd.exe`, browser still opens).
- Dist zip includes the VBS launcher and `README_СКЛАД.txt`.

## Files Created / Changed

| File | Action |
|------|--------|
| `app_ui.py` | `st.selectbox` on Tab 2; per-`order_id` operator overrides; station table `№ / Статус / Наименование / Кол-во / Штрихкод` |
| `src/utils/scan_station.py` | Isolation helpers: `partition_scan_attention`, `attention_by_order` |
| `Запуск_WMS.vbs` | Hidden Streamlit launch + `http://localhost:8501` |
| `README_СКЛАД.txt` | Warehouse short instruction (VBS as preferred start) |
| `ИНСТРУКЦИЯ_ДЛЯ_ЕГОРА.txt` | Same VBS recommendation |
| `scripts/build_warehouse_dist.py` | Pack `Запуск_WMS.vbs` and `README_СКЛАД.txt` |
| `tests/test_ui_contracts.py` | 3-order batch isolation (31 hardware `AUTO_NO_BARCODE`) |
| `tests/test_history_manager.py` | Scan override updates only the selected Excel |
| `dist/Warehouse_WMS_Pilot_v1.0.zip` | Rebuilt (52 files, includes VBS) |

## Key Design Decisions

1. **Batch selector uses `order_id`, not filename.** Duplicate stems and overlapping line numbers (`№1` in two files) must not share widget state or overrides.
2. **Overrides live in `operator_overrides_by_order`.** Switching the dropdown activates that order’s map, then `update_excel` / `update_session` write only that run’s files.
3. **Station badges:** 🟡 карантин, ⚪ без заводского ШК. Green banner names the selected file when nothing is left to scan.
4. **VBS `sh.Run cmd, 0, False`:** window style 0 hides `cmd.exe`; Streamlit still binds `:8501`. Console launcher `2_ЗАПУСК.bat` remains for logs.

## Test Results

```
.\venv\Scripts\python.exe -m pytest tests/ -q --tb=short
175 passed, 1 warning in 40.52s
```

CLI:

```
.\venv\Scripts\python.exe scripts\run_order.py "data/orders/order_transfering_01_09.xls"
```

| Metric | Value |
|--------|--------|
| Rows | 384 |
| MATCHED_AUTO | 379 (98.7%) |
| MATCHED_LLM | 0 |
| QUARANTINE | 5 (1.3%) |
| Factory barcodes | 348 |
| Без ШК | 31 (8.1%) |
| Profiler | 18.40s (Parse=0.21, Exact=0.95, FAISS=14.20, LLM=1.86, Excel=0.12) |

Dist: `dist/Warehouse_WMS_Pilot_v1.0.zip` — 19 654 772 bytes, 52 files; members include `Запуск_WMS.vbs` and `README_СКЛАД.txt`.

## Challenges & Caveats

- Streamlit `data_editor` / form keys are suffixed with `order_id`. A shared key would leak typed EAN-13 between files with the same line numbers.
- VBS MsgBox is ASCII-only so encoding does not depend on the system ANSI code page. Russian copy lives in `README_СКЛАД.txt`.
- Tab 2 UI was not driven in a real browser in this sprint; contracts are unit-tested. Warehouse check: load three files, switch the dropdown, confirm 31 no-barcode rows on «Перемещение 01.09.xls».

## Next sprint preview

- Optional: restore the full batch (not only last run) after F5 from shift manifest.
- Kill/restart helper if `:8501` is already taken when double-clicking `Запуск_WMS.vbs`.
