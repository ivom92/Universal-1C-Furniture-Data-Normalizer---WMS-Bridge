# Sprint 6.0 — Cell topology cleanup, parallel LLM, pipeline speed

**Date:** 2026-08-29  
**Status:** Completed

## Scope

- Strip warehouse cell topology (`Р1.16.Я2`, `Р10.12.Я1`) from v7.7 item names so FAISS is not polluted
- Recognize `Рег.склад` / `Региональный склад` in transfer headers (`РС УрФО Империал`)
- Parallel `ThreadPoolExecutor` LLM fallback with request dedupe, 15s timeout, Gemini AFC disabled
- Streamlit: `width="stretch"`, match speed caption, quarantine grouped by reason

## Files Created / Changed

| File | Action |
|------|--------|
| `src/parsers/v7_parser.py` | Location column skip + prefix sanitize + `Рег.склад` header |
| `src/matcher/feature_extractor.py` | Defense-in-depth location prefix strip |
| `src/matcher/llm_resolver.py` | Thread-local Gemini client, JSON config without tools, LRU-style dict cache, 15s timeout |
| `src/matcher/hybrid_matcher.py` | Batched parallel LLM for `NEEDS_LLM`, duplicate-key reuse |
| `src/models.py` | `MatchDecision.status_detail` |
| `app_ui.py` | Stretch width, progress/speed, grouped quarantine |
| `tests/excel_fixtures.py` | `write_location_topology_v7_xlsx` |
| `tests/test_parsers.py` | Topology + `Рег.склад` tests |
| `tests/test_llm_resolver.py` | Parallel dedupe, timeout quarantine, AFC config, cache |

## Key Design Decisions

1. **Skip by header and by token.** Columns titled Линейка / Секция / Место are excluded from `client_description`. Cells matching `^Р\d+[\.\w\d]+$` are dropped even without that header. Prefix regex still strips `Р1.16.Я2 ` if it leaked into the name cell.
2. **Two-phase matching.** Vector/hard-filter runs first (`apply_llm=False`); unique `(description, alias, type, candidate codes)` keys are resolved in a pool of up to 8 workers; outcomes are copied onto duplicate rows.
3. **Timeout is non-fatal.** Gemini/Ollama HTTP timeout is 15s; timeout reasoning maps to quarantine `Таймаут LLM` without aborting the order.
4. **Gemini AFC off.** `GenerateContentConfig(tools=[], automatic_function_calling=disable=True)` avoids the unused-tools warning.

## Test Results

```
pytest -v  →  71 passed
```

Acceptance: ≥ 66 tests passing (71 collected).

## How to verify

```bash
pytest -v
python scripts/run_order.py "data/orders/Перемещение 01.09.xls"
streamlit run app_ui.py
```

Expected on «Перемещение 01.09»: item 1 auto-matches as `Аврора Зеркало 1/1 венге` (no `Р1.16.Я2`); WMS customer `РС УрФО Империал`; wall-clock well under a minute when LLM is used; Auto-Match ≥ 85–90%.

## Next sprint preview

- Calibrate Auto-Match on remaining transfer SKUs that still hit LLM/quarantine
- Persist operator-corrected customer name per file hash (carried from 5.3)
