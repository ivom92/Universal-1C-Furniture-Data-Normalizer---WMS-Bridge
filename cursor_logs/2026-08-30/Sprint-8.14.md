# Sprint 8.14 — Gemini Diagnostic, LLM Logging & Windows MAX_PATH Fix

**Date:** 2026-08-30  
**Status:** Completed

## Scope

- Direct Gemini API diagnostic script (`scripts/test_gemini_connection.py`) with ping + JSON matching contract test.
- Detailed error logging in `llm_resolver.py` (401/403/404/429/timeout, missing API key).
- Windows installer hardening: `LongPathsEnabled` registry key, path-length / OneDrive warnings, `pip --no-cache-dir`.
- Model fallback chain extended for new free API keys (`gemini-3.5-flash-lite`).
- Rebuilt warehouse dist zip.

## Files Created / Changed

| File | Action |
|------|--------|
| `scripts/test_gemini_connection.py` | **New** — ping, models.list probe, furniture JSON contract test |
| `src/matcher/llm_resolver.py` | `logger.error` on Gemini failures; missing-key warning; 404 fallback chain → `3.5-flash-lite` → `2.5-flash` |
| `1_УСТАНОВКА.bat` | `LongPathsEnabled=1`, path length / OneDrive checks, `--no-cache-dir` |
| `.env` | `GEMINI_MODEL=gemini-3.5-flash-lite` (new free key requirement) |
| `.env.example` | Updated default model comment |
| `tests/test_llm_resolver.py` | Fallback chain test updated for 3-model retry |
| `dist/Warehouse_WMS_Pilot_v1.0.zip` | Rebuilt (53 files) |

## Key Design Decisions

1. **New free Gemini keys reject `gemini-2.5-flash-lite`.** Google API returns `404 NOT_FOUND` with message to use `gemini-3.5-flash-lite`. Fallback chain tries configured model → `3.5-flash-lite` → `2.5-flash` so legacy and new keys both work.
2. **Diagnostic script is standalone.** Loads `.env` with `override=True`, probes `models.list` via Cloudflare proxy, then runs a real `LLMResolver.resolve()` on an Аврора 1/2 vs 2/2 disambiguation case.
3. **Installer path warnings are non-blocking.** `LongPathsEnabled` reg key is best-effort (may need admin); warnings guide operator to `C:\WMS\` if path > 100 chars or OneDrive detected.

## Test Results

```
.\venv\Scripts\python.exe scripts\test_gemini_connection.py
→ SUCCESS (ping 1.75s, JSON contract OK)

.\venv\Scripts\python.exe -m pytest tests/ -v --tb=short
175 passed, 1 warning in 39.62s

.\venv\Scripts\python.exe scripts\run_order.py "data/orders/order_transfering_01_09.xls"
```

| Metric | Value |
|--------|--------|
| Rows | 384 |
| MATCHED_AUTO | 379 (98.7%) |
| MATCHED_LLM | 5 (1.3%) |
| QUARANTINE | 0 (0.0%) |

Dist: `dist/Warehouse_WMS_Pilot_v1.0.zip` — 19 659 252 bytes, 53 files (includes `test_gemini_connection.py`).

## Challenges & Caveats

- **Model deprecation:** Sprint spec referenced `gemini-2.5-flash-lite`, but Google's free tier for new keys only serves `gemini-3.5-flash-lite`. Documented in `.env` and fallback chain; no matcher logic changed.
- **AFC deprecation warning** from `google-genai` SDK on `generate_content` is cosmetic; AFC is already disabled in `_gemini_generate_config`.
- **`LongPathsEnabled` reg add** may silently fail without admin rights; pip `--no-cache-dir` is the primary WinError 206 mitigation alongside path warnings.

## Next sprint preview

- Add `test_gemini_connection.py` to `check_system_health.py` optional `--llm-deep` flag.
- Suppress or migrate away from deprecated AFC `generate_content` path when SDK provides stable alternative.
