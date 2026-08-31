# Sprint 6.1 — Matcher feature boost, no-barcode vs quarantine, Flash-Lite

**Date:** 2026-08-29  
**Status:** Completed

## Scope

- Treat factory SKUs without EAN-13 as successful matches (`MATCHED_AUTO` / `MATCHED_LLM`), not quarantine
- Add packaging/model/color scoring boosts so Aurora kits auto-match at ≥ 0.83
- Point Gemini at `gemini-2.0-flash-lite`, 25s timeout, one retry on 504/429
- Show total package places in Streamlit next to row count

## Files Created / Changed

| File | Action |
|------|--------|
| `src/matcher/hybrid_matcher.py` | Feature boosts on passed candidates; `AUTO_NO_BARCODE` / `LLM_NO_BARCODE`; quarantine only when `matched_entity is None` |
| `src/matcher/llm_resolver.py` | Default `gemini-2.0-flash-lite`, timeout 25s, `temperature=0.0`, retry with 1.5s backoff |
| `src/models.py` | Match-method contract includes no-barcode methods |
| `app_ui.py` | Status badges; metrics for positions + package sum; quarantine labeled as missing in v8 |
| `.env.example` | Default `GEMINI_MODEL=gemini-2.0-flash-lite` |
| `tests/test_matcher.py` | No-barcode WMS export; Aurora boost parametrized cases |
| `tests/test_llm_resolver.py` | Flash-Lite defaults, retry, temperature 0.0 |
| `tests/test_end_to_end.py` | Matched rows without EAN keep catalog name and empty barcode |

## Key Design Decisions

1. **Quarantine = no catalog entity.** Missing factory barcode is a WMS empty cell, not an error. Methods: `AUTO_NO_BARCODE` / `LLM_NO_BARCODE` while status stays `MATCHED_*`.
2. **Boost after hard filters.** `+0.03` packaging, `+0.03` model, `+0.02` color, capped at 1.0. Auto-match if boosted score ≥ 0.83 and there is no same-characteristic collision.
3. **Retry is narrow.** One extra call after 1.5s only for 504 / `DEADLINE_EXCEEDED` / 429 (and HTTP timeouts). Other errors still degrade without a second hop.
4. **Package metric** is `sum(block.quantity)` — operator can reconcile places with the invoice, not just line count.

## UI

| Element | Behavior |
|---------|----------|
| Preview header | Позиций в заказе + Всего упаковок (мест), шт. |
| Results metrics | Positions, packages, auto-matched, quarantine (нет в v8) |
| Table badges | 🟢 Авто (со штрихкодом) / 🟢 Авто (без ШК) / 🔵 LLM (разрешено через ИИ) / 🟡 Карантин |

## Test Results

```
pytest tests -v  →  80 passed
```

Acceptance: ≥ 75 tests, 0 failures (80 collected).

## Next sprint preview

- Measure live transfer order: true quarantine ≈ 4–5, auto-match ≥ 90%, LLM calls ~10–15
- Optional: persist operator customer name per file hash (from 5.3 / 6.0)
