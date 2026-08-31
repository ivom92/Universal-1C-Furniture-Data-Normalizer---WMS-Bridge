# Sprint 7.2 — Adaptive parser, hardware contour, canonical matcher

**Date:** 2026-08-29  
**Status:** Completed (with documented metric gaps on quarantine count and pytest wall time)

## Scope

- Make the 1C v7.7 parser layout-agnostic (fuzzy header map + integer-anchor block FSM, warehouse topology sanitization).
- Specialized matching for fittings / mouldings: no furniture-model requirement; `AUTO_NO_BARCODE` when the factory row has no EAN-13 or the SKU is absent from catalog v8.
- Canonical token forms for packaging ratios and `IMP` collection prefixes before FAISS.
- High-confidence auto-promotion when model + module + packaging + color agree (`≥ 0.88`).

## Files Created / Changed

| File | Action |
|------|--------|
| `src/parsers/v7_parser.py` | Header scan in first 15 rows; `Код`/`К-во`/`Адрес`; integer-anchor FSM; `sanitize_warehouse_topology` |
| `src/matcher/token_normalizer.py` | New: `упаковка 1/4`, `IMP сп/к/прих/г` expansions |
| `src/matcher/feature_extractor.py` | Topology sanitization on combined block text |
| `src/matcher/hybrid_matcher.py` | Hardware contour, passthrough, high-confidence boost, canonical search query |
| `src/parsers/v8_loader.py` | Broader hardware type tokens; `угол` only with цоколь / `\d+ гр` |
| `tests/excel_fixtures.py` | Mixed-height blocks + glued `Секция` tokens |
| `tests/test_parsers.py`, `tests/test_matcher.py`, `tests/test_features.py` | Layout, hardware, auto-promotion, token tests |

## Key Design Decisions

1. **Integer-anchor FSM.** A new item starts on a positive integer in the № column (or the first non-empty cell). Alias (yellow / `IMP`), service lines (`Продажи…`, `Перемещение на склад…`) and extra rows accumulate until the next integer. Quantity is read from the mapped qty column with fallbacks; fill color is required only before a table header is found.
2. **Hardware ≠ parenthetical handles.** `(ручка чёрная)` on furniture aliases is stripped before hardware detection so kitchen/Lazio packs are not forced through the fittings filter.
3. **Fittings not in this v8 dump.** Articles `1516`/`1517`, `Корнер`, `Плинтус RUS`, цоколь angles do not appear in `catalog_v8.xlsx`. After type/size/color rescue fails, they are exported as `MATCHED_AUTO` / `AUTO_NO_BARCODE` with the v7.7 name so the TSD file stays complete. Custom glass/mirrors still quarantine.
4. **High-confidence tuple** requires a non-empty catalog `Модуль` so Aurora feature-boost tests stay at `0.78 + 0.08` and are not forced to `0.88`.

## Test Results

```
pytest -q --tb=line
101 passed, 1 warning in 35.79s
```

A warmer cached subset earlier in the sprint was `25.72s` (101 tests). Acceptance asked for `< 25s`; wall time is still dominated by the first e5 encode in session-scoped FAISS tests, same as Sprint 7.1.

## Live CLI

```
LLM_PROVIDER=gemini python scripts/run_order.py "data/orders/order_transfering_01_09.xls"
```

| Metric | Result | TZ target |
|--------|--------|-----------|
| Rows | 384 | 384 |
| Places (qty sum) | 871 | 871 |
| MATCHED_AUTO | **370 (96.4%)** | ≥345–360 (~90–94%) |
| MATCHED_LLM | 8 (2.1%) | ~15–25 |
| QUARANTINE | **6 (1.6%)** | ≤4–5 |
| Factory barcodes recovered | 351 | — |

Fittings in the transfer list (planks 1516/1517/1519, corners, plugs, RUS plinths, цоколь angles, LED `04.002.20.312`) leave quarantine via catalog hit or `AUTO_NO_BARCODE`.

## Challenges & Caveats

1. **Quarantine is 6, not ≤5.** Remaining rows: three custom-size mirrors (`Зеркало 1754*368`, `1776*495`, `858х294`) — physically absent as those cuts in v8 — plus `Кухня Равенна Н60 2ящ`, `Кухня Равенна полка`, and `Ящик с доводчиком Н86 400` where Gemini returned `selected_nomenclature_code: null` after a non-empty FAISS pool (cascade invariant). Asserts were not loosened.
2. **MATCHED_LLM is 8, below ~15–25.** Auto-match and hardware passthrough absorbed most of the former LLM band; the model only sees residual collisions.
3. **This v8 catalog has no `1516` / `Корнер` / `Плинтус` nomenclature.** Live “matches” for those SKUs are empty-barcode TSD rows, not recovered EAN-13s.
4. **`.env` still has `LLM_PROVIDER=ollama`.** The acceptance CLI set `LLM_PROVIDER=gemini` in the process environment.
5. **Pytest `< 25s` was not met on the full suite** (`35.79s` this run). No tests were deleted to chase the clock.

## Next sprint preview

- Pull the three non-mirror quarantines into auto/LLM without relaxing package hard filters (Равенна Н60 2ящ, полка, ящик Н86).
- Persist a golden query-embedding cache so pytest stays near 25s cold.
- If the factory later ships fittings in v8, prefer catalog EAN over name-only passthrough.
