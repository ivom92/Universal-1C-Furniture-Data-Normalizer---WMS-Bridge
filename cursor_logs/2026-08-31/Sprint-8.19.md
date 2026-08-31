# Sprint 8.19 — UI Key-Pool Status & Base URL Sanitization

## Scope
- Sync Streamlit sidebar LLM badge with `KeyPool.from_env()` (multi-key `GEMINI_API_KEYS` pool).
- Sanitize trailing slash on `GEMINI_BASE_URL` to prevent `//v1beta` double-slash network errors.
- Add sidebar button to ping all keys in the pool and show toast feedback.

## Files Changed
| File | Change |
|------|--------|
| `app_ui.py` | `_llm_status_info()`, KeyPool-based availability, `🔍 Проверить ключи` button |
| `src/matcher/key_rotator.py` | `KeyPoolPingResult`, `KeyPool.test_connection()` |
| `src/matcher/llm_resolver.py` | `resolve_gemini_base_url()` → `.rstrip("/")` |
| `tests/test_ui_contracts.py` | UI pool status + ping button contract tests |
| `tests/test_key_rotator.py` | `test_connection` unit tests |
| `tests/test_llm_resolver.py` | Trailing-slash sanitization assertions |

## Key Design Decisions
1. **`_llm_status_info()`** — single helper returns `(available, status_line, caption)` for Gemini/Ollama; Gemini green badge shows `🟢 LLM: доступен (Пул: N шт.)` plus caption `Пул ключей: N шт. | Модель: …`.
2. **`KeyPool.test_connection()`** — probes each key via `models.list` (same as health scripts); lazy-imports `gemini_models_list_url` to avoid circular imports at module load.
3. **URL sanitization** — applied in `resolve_gemini_base_url()` so resolver, health checks, and UI ping all share one canonical base URL.

## Test Results
```
pytest tests/ -v  →  204 passed
python scripts/build_warehouse_dist.py  →  dist/Warehouse_WMS_Pilot_v1.0.zip (19.7 MB, 56 files)
```

## Acceptance Criteria
- [x] Green LLM badge when `GEMINI_API_KEYS` is set (pool count shown)
- [x] Trailing slash on `GEMINI_BASE_URL` stripped before API calls
- [x] All tests green (204)
- [x] Warehouse dist rebuilt

## Next Sprint Preview
- Optional: surface per-key ping details in an expander (not just toast summary).
- Wire `check_system_health.py` deep check button into Streamlit support footer.
