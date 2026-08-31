# Sprint 4 — Dual-Engine LLM Fallback Resolver & Rich Terminal Reporter

**Date:** 2026-08-28  
**Status:** Completed

## Scope

- `LLMResolver` with dual backends: Google Gemini (`google-genai`) and local Ollama (`httpx`)
- `HybridMatcher` integration for `NEEDS_LLM` → `MATCHED_LLM` / `QUARANTINE`
- Rich terminal reporter (`print_match_summary`)
- Full-cycle CLI script `scripts/run_order.py`
- Unit/integration tests in `tests/test_llm_resolver.py`

## Files Created / Changed

| File | Action |
|------|--------|
| `src/models.py` | Added `LLMResolutionResponse`, `MatchDecision.match_method` |
| `src/matcher/llm_resolver.py` | **New** — Dual-engine LLM client |
| `src/matcher/hybrid_matcher.py` | LLM resolver hook, `match_order_decisions`, `MATCHED_LLM` path |
| `src/utils/reporter.py` | **New** — Rich table + summary panel |
| `scripts/run_order.py` | **New** — Full pipeline CLI |
| `tests/test_llm_resolver.py` | **New** — 8 tests (parsing, mock, graceful degradation) |
| `.env.example` | **New** — LLM env template |
| `.env` | Added Ollama vars |
| `requirements.txt` | Added `httpx`, `python-dotenv` |

## Key Design Decisions

1. **Graceful degradation:** `LLMResolver.resolve()` catches all exceptions and returns `selected_nomenclature_code=None` with reasoning `"LLM Fallback unavaliable"` — pipeline never crashes.
2. **Zero-Loss preserved:** `match_order_decisions` returns exactly one `MatchDecision` per input block; failed LLM → `QUARANTINE`, not dropped rows.
3. **Structured output:** Gemini uses Pydantic `response_schema`; Ollama uses `format: json` + `LLMResolutionResponse.model_validate`.
4. **No OpenAI:** Only `google-genai` and `httpx` for Ollama HTTP API.
5. **String codes:** `selected_nomenclature_code` validated as `str`, never cast to int.

## Live Run (`run_order.py` + `gemini-2.5-flash`)

| Status | Count | % |
|--------|-------|---|
| MATCHED_AUTO | 47 | 85.5% |
| MATCHED_LLM | 6 | 10.9% |
| QUARANTINE | 2 | 3.6% |

Note: `gemini-2.0-flash` deprecated by Google API (404); default updated to `gemini-2.5-flash`.

## Test Results

```
pytest tests/ -v  → 46 passed (Sprints 1–4)
```

## Next Sprint Preview

- Sprint 5: WMS Excel export (`[Наименование, Штрихкод, Количество, Заказчик]`) with openpyxl string formatting
- Optional Streamlit UI for warehouse operators
