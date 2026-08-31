# Sprint 8.18 — Health-Check Multi-Key Sync & Dist Rebuild

**Date:** 2026-08-31  
**Status:** Completed

## Scope

- Sync `scripts/check_system_health.py` with `KeyPool` / `parse_gemini_api_keys()` so `GEMINI_API_KEYS` pool is recognized.
- Add optional `--llm-deep` flag delegating to `test_gemini_connection.py`.
- Unit tests for Gemini key detection in health script.
- Rebuild warehouse dist.

## Files Created / Changed

| File | Action |
|------|--------|
| `scripts/check_system_health.py` | `_check_llm()` uses `parse_gemini_api_keys()`; new `_run_llm_deep_check()` + `--llm-deep` |
| `tests/test_system_health.py` | **New** — 7 tests (pool, single key, fail, deep check) |
| `dist/Warehouse_WMS_Pilot_v1.0.zip` | Rebuilt |

## Key Design Decisions

1. **Shallow check** probes `models.list` with the first pool key; detail reports total count: `Найдено ключей: N (модель: …)`.
2. **`--llm-deep`** appends a separate row invoking `scripts.test_gemini_connection.main()` (per-key ping + JSON contract).
3. **Fail message** unified: `Не задан GEMINI_API_KEYS или GEMINI_API_KEY в .env`.

## Test Results

```
check_system_health.py  → 0 FAIL, Gemini API OK (3 keys)
pytest tests/           → 197 passed
build_warehouse_dist.py → zip rebuilt
```

## Challenges & Caveats

- None; change is localized to health script env parsing.

## Next Sprint Preview

- Surface key-pool status in Streamlit UI header / telemetry.
