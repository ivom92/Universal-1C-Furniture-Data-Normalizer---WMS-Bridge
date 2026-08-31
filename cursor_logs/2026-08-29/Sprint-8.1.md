# Sprint 8.1 — Package invariant, corpus/drawer isolation, barcode badges

**Date:** 2026-08-29  
**Status:** Completed (quarantine is 2, not 1 — documented below)

## Scope

- Hard package-ratio barrier: multi-place `X/Y` (`Y > 1`) only matches the same place token; `1/3` cannot match `Ун1/1`, `1/1`, or `2/3`.
- Isolate kitchen `(корпус)` rows from drawer/slide SKUs (`Ящик`, `Ящик с доводчиком`, `направляющие`, `комплект ящиков`).
- Streamlit / Rich badges: empty factory barcode → `🟢 Авто (без ШК)` (and LLM analogue), plus a `Без ШК` counter.

## Files Created / Changed

| File | Action |
|------|--------|
| `src/matcher/feature_extractor.py` | Shared `extract_package_ratio_from_text` for order and catalog names |
| `src/matcher/hybrid_matcher.py` | Package token from nomenclature when `Упаковка` is empty; `Package ratio mismatch`; corpus vs drawer hard filter; corpus rows ignore alias `доводчик` hardware typing |
| `src/matcher/llm_resolver.py` | Prompt: exact packaging; corpus ≠ drawer |
| `src/utils/reporter.py` | `get_status_badge`, `count_without_barcode`; Rich table uses badges |
| `app_ui.py` | Shared badges; metric **Без ШК** |
| `tests/test_matcher.py` | `test_package_ratio_hard_barrier`, `test_corpus_vs_drawer_isolation`, `test_no_barcode_status_badge` |

## Key Design Decisions

1. **Empty catalog `Упаковка` is not a free pass.** Place is taken from the field or from the nomenclature (`упаковка 1/3` / `Ун1/1`). Multi-place orders with no extractable candidate place fail the hard filter (`Package ratio mismatch`).
2. **`1/1` ↔ `Ун1/1` stays allowed** (single-place / universal). Any side with denominator `> 1` requires an exact token match.
3. **`(корпус)` is not hardware.** Yellow alias `карго с доводчиком` used to mark the row as hardware (`доводчик` type token), so the correct `Кухня Равенна Н20 карго (корпус) … 1/3` failed `hardware type/size/color mismatch` and the empty pool fell into fittings passthrough (`matched_entity=None`). Corpus queries that are not explicitly drawer orders skip hardware typing and passthrough.
4. **LLM picks are re-checked** with the same hard constraints (package + corpus isolation).

## Test Results

```
pytest -q --tb=line
113 passed, 1 warning in 29.69s
```

(Full suite after the corpus/hardware follow-up: **113 passed**, ~36s.)

Acceptance: all tests green, 110+. Met (113).

## Live CLI

```
LLM_PROVIDER=gemini python scripts/run_order.py "data/orders/order_transfering_01_09.xls"
```

| Metric | Result | TZ target |
|--------|--------|-----------|
| Rows | 384 | 384 |
| Places (qty sum) | 871 | 871 |
| MATCHED_AUTO | 376 (97.9%) | — |
| MATCHED_LLM | 6 (1.6%) | — |
| QUARANTINE | **2 (0.5%)** | keep zero-loss |
| Factory barcodes recovered | 352 | — |
| Без ШК (matched, empty EAN-13) | 30 | fittings/planks show без ШК |

- **№12** `Кухня Равенна Н20 карго (корпус) Белый упаковка 1/3` → `MATCHED_AUTO` / `exact_article` → `Кухня Равенна Н20 карго (корпус) Белый упаковка 1/3` (packaging `1/3`, **not** `Ящик с доводчиком … Ун1/1`).
- **№206, 215–223, 236–238** → `🟢 Авто (без ШК)` / `AUTO_NO_BARCODE`.
- Zero-loss: 384 rows, 871 places.

## Challenges & Caveats

1. **False-positive №12 was a cascade of two bugs.** Package filter was skipped when `Упаковка` was empty; even with a correct 1/3 corpus in FAISS, alias `с доводчиком` forced hardware type matching. Asserts were not loosened; hardware passthrough is blocked for `(корпус)` modules.
2. **Quarantine is 2, not 1.** №230 remains the Равенна glass shelf `565х255` (no v8 cut). **№340** is an additional quarantine row after the stricter package/corpus path. Asserts were not deleted to hide it.
3. **`.env` still defaults `LLM_PROVIDER=ollama`.** The acceptance run set `LLM_PROVIDER=gemini` in the process environment.
4. **Fittings without a v8 hit** still use `AUTO_NO_BARCODE` with `matched_entity=None` (passthrough) so the badge is `🟢 Авто (без ШК)` rather than a false “со штрихкодом”. Kitchen corpus rows no longer take that path.

## Next sprint preview

- Review №340: recover a valid 1/3 (or other) catalog row if it exists, without relaxing the package barrier.
- Optionally send empty hard-filter pools for non-hardware furniture to LLM instead of immediate quarantine, still keeping package/corpus invariants on the selected code.
