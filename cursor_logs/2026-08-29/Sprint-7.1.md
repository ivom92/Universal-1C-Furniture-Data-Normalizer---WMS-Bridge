# Sprint 7.1 — Gemini 2.5-flash-lite, cascade restore, test speed

**Date:** 2026-08-29  
**Status:** Completed (with documented metric gaps on the live warehouse file)

## Scope

- Replace deprecated `gemini-2.0-flash-lite` with `gemini-2.5-flash-lite` and 404 fallback to `gemini-2.5-flash`
- Restore FAISS → LLM candidate handoff (`resolve_candidates_batch`) and v8 code lookup
- Session-scope catalog/FAISS in pytest; mock live Gemini in the suite
- Encode honest reporting rules in `.cursorrules`
- Live CLI on the 01.09 transfer workbook

## Files Created / Changed

| File | Action |
|------|--------|
| `.cursorrules` | v4.2; Gemini 2.5 stack; STRICT DEVELOPMENT & REPORTING PROTOCOL |
| `.env.example` | Default `GEMINI_MODEL=gemini-2.5-flash-lite` |
| `src/matcher/llm_resolver.py` | New default model, 404 fallback, `resolve_candidates_batch`, warning-only network failures |
| `src/matcher/hybrid_matcher.py` | Batch LLM jobs, catalog lookup by `НоменклатураКод` |
| `src/matcher/vector_store.py` | Shared `multilingual-e5-small` process cache; query search cache |
| `app_ui.py` | Default Gemini model caption |
| `tests/conftest.py` | Session catalog / vocabulary / FAISS; block live Gemini client |
| `tests/test_llm_resolver.py` | Fallback + cascade tests; no extra FAISS rebuild |
| `tests/test_matcher.py`, `tests/test_features.py`, `tests/test_end_to_end.py` | Shared session fixtures |

## Key Design Decisions

1. **404 fallback is in-process.** A `404 NOT_FOUND` on `gemini-2.5-flash-lite` retries once with `gemini-2.5-flash` and pins that id for the rest of the process. Other errors stay `logger.warning` and return a null code (no crash).
2. **Batch API is the production path.** `match_order_decisions` calls `resolve_candidates_batch`; MagicMock tests that do not return a real list still fall back to `resolve()`.
3. **LLM codes are resolved against the full v8 catalog**, not only the top-5 FAISS pool, so a valid factory code is not lost if it was ranked just outside the prompt list.
4. **Pytest does not call Google.** Session autouse patches `LLMResolver._gemini_client`. Catalog + FAISS index live in `.cache_pytest` for the session.

## Test Results

```
pytest -q --tb=line --durations=15
92 passed, 1 warning in 24.37s
```

Acceptance: pytest `-q` under 25s. Met (24.37s). First encode of the Ruban order in `test_end_to_end` remains the slowest call (~10s); later Ruban FAISS searches hit the in-memory query cache.

## Live CLI

Command (Gemini override; repo file name differs from the TZ path):

```
LLM_PROVIDER=gemini python scripts/run_order.py "data/orders/order_transfering_01_09.xls"
```

There is no `data/orders/Перемещение 01.09.xls` in the workspace; `order_transfering_01_09.xls` is the 01.09 transfer list.

| Metric | Result | TZ target |
|--------|--------|-----------|
| Rows | 384 | — |
| MATCHED_AUTO | 303 (78.9%) | ≥330–350 |
| MATCHED_LLM | **57 (14.8%)** | >0, ~20–40 |
| QUARANTINE | 24 (6.2%) | ≤5–10 |
| Factory barcodes recovered | 353 | — |

LLM cascade is restored: `MATCHED_LLM` is 57, not 0.

## Challenges & Caveats

1. **Auto-match and quarantine miss the numeric TZ band.** Asserts were not loosened. 303 auto + 57 LLM = 360 resolved rows; 24 remain in quarantine. Typical quarantine lines are fittings/corners (`Корнер заглушка`, цоколь), `Планка 1516/1517`, LED article `04.002.20.312`, custom glass/mirrors, and one `Система Лацио Сканди Шкаф` row where Gemini returned `selected_nomenclature_code: null` after seeing FAISS candidates.
2. **`.env` still has `LLM_PROVIDER=ollama`.** The acceptance run set `LLM_PROVIDER=gemini` in the process environment. A warehouse CLI without that override will not use Gemini.
3. **Task 4 asked for &lt;20s pytest.** Cached session run is **24.37s**, under the 25s acceptance line but above 20s, dominated by the first full-catalog query-encode pass and `sentence-transformers` load. Further cuts would require skipping real e5 encoding in integration tests.
4. **Hard-filter empty pools still skip HTTP LLM** (resolver would only return “No candidates available”). Custom sizes with zero FAISS survivors stay quarantine without a model round-trip.

## Next sprint preview

- Recover hardware/corner/plinth rows (article + type disambiguation) to pull quarantine toward ≤10 without relaxing package/dimension hard filters
- Raise auto-match on high-gap kitchen furniture so MATCHED_AUTO approaches 330 while keeping LLM for true collisions
- Persist FAISS query embeddings or a golden query cache so pytest stays near 20s cold
