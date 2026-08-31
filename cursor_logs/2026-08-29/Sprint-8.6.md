# Sprint 8.6 — Warehouse Deployment Pack, .bat/.sh, pre-warmed FAISS, Telegram

**Date:** 2026-08-29  
**Status:** Completed

## Scope

- Background Telegram telemetry (startup, order processed, errors, operator diagnostics) that never crashes the app.
- Rotating UTF-8 file log `logs/warehouse_app.log` (5 MB × 3 backups).
- Windows `1_УСТАНОВКА.bat` / `2_ЗАПУСК.bat` and Unix `1_setup.sh` / `2_run.sh`.
- Isolated zip `dist/Warehouse_WMS_Pilot_v1.0.zip` with `.cache/` FAISS and `.env`.

## Files Created / Changed

| File | Action |
|------|--------|
| `src/utils/telemetry.py` | Created: async POST `sendMessage`, notify_* templates |
| `src/utils/logger.py` | `RotatingFileHandler` → `logs/warehouse_app.log` |
| `app_ui.py` | Startup/order/error alerts; footer button diagnostics |
| `scripts/run_order.py` | Same alerts; cache path from project root |
| `scripts/check_system_health.py` | `--warm` builds/loads FAISS |
| `scripts/build_warehouse_dist.py` | Created: zip packer + relative-path guard |
| `1_УСТАНОВКА.bat`, `2_ЗАПУСК.bat` | Created |
| `1_setup.sh`, `2_run.sh` | Created (LF) |
| `ИНСТРУКЦИЯ_ДЛЯ_ЕГОРА.txt` | Created |
| `.env.example` | `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` |
| `src/matcher/vector_store.py` | Relative `.cache` resolves via `Path(__file__)` |
| `tests/test_telemetry.py` | Created |

## Key Design Decisions

1. **Telegram is fire-and-forget.** `httpx` POST runs in a daemon thread with a 3 s timeout. Missing token, timeout, or HTTP errors are swallowed. CLI calls `flush_telemetry()` so the process can exit after the request.
2. **File logging is on import.** Any `from src.utils.logger import …` creates `logs/` and writes UTF-8 rotating logs without going through Rich.
3. **Install warms FAISS; daily start does not re-embed.** `1_УСТАНОВКА.bat` runs `check_system_health.py --warm`. The zip already contains `catalog_faiss.index` + `catalog_meta.pkl` (~23 MB uncompressed).
4. **Zip isolation.** Builder refuses hardcoded `C:\Users\…` paths, omits `.git`, `tests`, `venv`, `__pycache__`, `output/*.xlsx`, and prefixes entries with `Warehouse_WMS_Pilot_v1.0/`.

## Test Results

```
pytest -q --tb=line
130 passed, 1 warning in 30.31s
```

Acceptance: full suite green. Met (130, target 128+).

## Live CLI

```
LLM_PROVIDER=gemini python scripts/run_order.py "data/orders/order_transfering_01_09.xls"
python scripts/build_warehouse_dist.py
```

| Metric | Result | TZ target |
|--------|--------|-----------|
| Rows | 384 | 384 |
| Places | 871 | 871 |
| QUARANTINE | 1 | — |
| MATCHED_AUTO | 377 (98.2%) | — |
| MATCHED_LLM | 6 (1.6%) | — |
| `logs/warehouse_app.log` | created, UTF-8, order line present | rotating file |
| Archive | `dist/Warehouse_WMS_Pilot_v1.0.zip` (~19.6 MB, 40 files) | pre-warmed `.cache/` |
| Zip excludes | no `tests/`, no `venv/` | isolation |
| Zip includes | `.cache/`, `.env`, bat/sh, catalog, orders | TZ list |

WMS file: `output/WMS_Импорт_РС УрФО Империал_2026-08-29.xlsx`.

Log excerpt after CLI: `Order processed: order_transfering_01_09.xls rows=384 places=871 quarantine=1`.

## Challenges & Caveats

1. **HF model weights are not inside project `.cache/`.** FAISS vectors are packed; `sentence-transformers` still needs `multilingual-e5-small` from the Hugging Face cache on first load of the warehouse PC. After that, search is instant.
2. **Health `--warm` still probes LLM.** If Gemini/Ollama is unreachable during `1_УСТАНОВКА.bat`, the script exits with error even though FAISS may already be warm. Operator can still start Streamlit after fixing `.env`/network.
3. **`.env` is inside the zip** (Gemini + Telegram). Treat the archive as confidential; do not put it on a public share.
4. **On Windows, `chmod +x` for `.sh` is informational.** Warehouse PCs use the `.bat` files. Linux/macOS should `chmod +x 1_setup.sh 2_run.sh` after unzip if the bit is lost.

## Next sprint preview

- Optional sidecar of operator overrides for shift handover.
- Slimmer warehouse zip: drop diagnostic scripts (`diagnose_matcher.py`, `verify_ollama_integration.py`) from the pack.
