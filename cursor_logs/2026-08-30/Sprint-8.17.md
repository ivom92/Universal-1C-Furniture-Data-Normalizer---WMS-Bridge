# Sprint 8.17 — Gemini Multi-Key Pooling & Auto-Failover

**Date:** 2026-08-30  
**Status:** Completed

## Scope

- Support comma-separated `GEMINI_API_KEYS` pool with backward-compatible fallback to single `GEMINI_API_KEY`.
- Thread-safe round-robin key rotation with 60s cooldown after quota/auth errors.
- Automatic failover in `LLMResolver` on 429/403/401/timeout without quarantining the row.
- Per-key diagnostic ping in `scripts/test_gemini_connection.py`.

## Files Created / Changed

| File | Action |
|------|--------|
| `src/matcher/key_rotator.py` | **New** — `parse_gemini_api_keys()`, `KeyPool` with round-robin + cooldown |
| `src/matcher/llm_resolver.py` | Integrated `KeyPool`, per-key clients, `_resolve_gemini_with_key()`, failover loop |
| `scripts/test_gemini_connection.py` | Per-key pool ping + contract test via full resolver |
| `.env.example` | Documented `GEMINI_API_KEYS` |
| `tests/test_key_rotator.py` | **New** — parsing, round-robin, cooldown tests |
| `tests/test_llm_resolver.py` | Failover-on-429 test, updated AFC/config tests |
| `dist/Warehouse_WMS_Pilot_v1.0.zip` | Rebuilt (56 files, includes `key_rotator.py`) |

## Key Design Decisions

1. **`KeyPool.from_env(explicit_key=...)`** — constructor `gemini_api_key` overrides env; comma-separated values in either source expand to a multi-key pool.
2. **Failover vs retry** — 429/403/401/timeout rotate keys immediately; 504 still retries once on the same key via `is_retryable_llm_error_without_failover`.
3. **Per-thread client cache** — `_thread_local.gemini_clients` dict keyed by API key for safe parallel batch resolution.
4. **Quarantine invariant preserved** — all keys exhausted → `logger.error` + exception → `resolve()` returns `LLM Fallback unavaliable` (QUARANTINE only when LLM explicitly returns null).

## Test Results

```
pytest tests/ -v          → 190 passed
test_gemini_connection.py → 3/3 keys 🟢 OK, JSON-контракт Аврора 1/2 OK
run_order (transfering)   → MATCHED_AUTO: 379, MATCHED_LLM: 5, QUARANTINE: 0
build_warehouse_dist.py   → dist/Warehouse_WMS_Pilot_v1.0.zip rebuilt
```

## Challenges & Caveats

- Contract test in diagnostic script hit transient Cloudflare 504; existing 1.5s retry on same key recovered successfully (not a key-pool event).
- Ping uses `gemini-3.5-flash-lite` hardcoded per spec; resolver still respects `GEMINI_MODEL` env with model-id fallback chain.

## Next Sprint Preview

- Optional: expose key-pool health metrics in Streamlit UI / telemetry.
- Consider configurable `GEMINI_KEY_COOLDOWN_SECONDS` env for longer free-tier reset windows.
