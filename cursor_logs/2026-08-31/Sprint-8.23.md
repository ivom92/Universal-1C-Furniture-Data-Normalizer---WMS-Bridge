# Sprint 8.23 — Sub-Brand & Color Palette Barrier

## Scope
- Fix semantic re-sort risk: order line `Система Чикаго Вайт Кровать 160 ... 1/2` was being
  matched to the base collection `Система Чикаго` (composite decor `Ателье светлый/Белый`)
  instead of the sub-brand `Система Чикаго Вайт` (monochrome `Белый`) — both hit the FAISS
  `1.0` score cap and the tie was broken by hit order, not by the discriminating "Вайт" token.
- Add sub-brand/sub-line modifier extraction (`вайт`, `роял`, `тренд`, `лайт`, ...) and a
  hard/soft barrier in the ranking cascade.
- Add a monochrome-vs-composite decor tie-breaker for the same collision pattern.
- Verify order Васильева Т. line 25 resolves to the Chicago White SKU with its EAN-13 barcode.

## Files Changed
| File | Change |
|------|--------|
| `src/models.py` | `ExtractedFeatures.sub_brands: set[str]`, `ExtractedFeatures.is_composite_color: bool` |
| `src/matcher/feature_extractor.py` | `SUB_BRAND_MODIFIERS` fixed modifier set, `extract_sub_brands()`, `has_composite_color_signal()`; wired into `extract_features()` |
| `src/matcher/hybrid_matcher.py` | `_sub_brand_compatible()` hard filter (Rule 2), `_apply_sub_brand_pool_barrier()` (Rule 1/3 pool-aware boost/penalty), `_apply_color_palette_pool_barrier()` (monochrome vs composite decor), wired into `_score_candidates()` and `_apply_hard_constraints()` |
| `tests/test_subbrand_barrier.py` | New suite: feature extraction, sub-brand hard/soft barrier, color-palette disambiguation, no-regression corpus, real-catalog integration for order line 25 |

## Key Design Decisions
1. **Fixed modifier vocabulary, not catalog-mined.** `SUB_BRAND_MODIFIERS` is a small set of generic
   collection-line adjectives (`вайт/white`, `роял/royal`, `тренд/trend`, `лайт/light`, ...) reused
   across many unrelated collections in the v8 catalog (confirmed live: Чикаго, Равенна, Феникс,
   Оникс, Симпл, Ника, Рондо all use these same suffixes). It is a linguistic pattern, not a list of
   furniture model names, so it is kept as an explicit constant per the ticket rather than mined from
   `DynamicVocabulary` — mining it would require the exact bug (partial "чикаго"/"тренд" token overlap)
   we are fixing.
2. **Sub-brand source = `label_model`, not full nomenclature/decor text.** Extracting sub-brand tokens
   from the *whole* nomenclature blob picked up false positives from decor names that legitimately
   contain these words (e.g. `MODERN Ф-130 Лайт грей софт`, `Равенна Тренд Софт`). Restricting entity-side
   extraction to `ЭтикеткаМодель` (falling back to nomenclature only if that field is empty) keeps the
   signal to genuine collection/sub-line identity.
3. **Three-rule barrier, pool-aware:**
   - **Rule 2 (hard filter, `_apply_hard_constraints`):** if the query names a sub-brand and the
     candidate has a *different, non-overlapping* sub-brand, disqualify it outright
     (`Равенна Роял` query vs `Равенна Тренд` candidate → `hard_filter_passed=False`).
   - **Rule 1 (soft, pool-aware):** if the query names a sub-brand and at least one *passing* candidate
     in the pool carries it, boost aligned candidates (`+0.35`) and penalize pure base-series candidates
     with no sub-brand at all (`-0.50`). Pool-aware so a lone base-series hit (no sub-brand competitor
     present) is never penalized.
   - **Rule 3 (soft, pool-aware, symmetric):** if the query names *no* sub-brand and the pool contains a
     pure base-series candidate, sub-branded variants are penalized instead, so the plain collection wins.
4. **Color palette disambiguation mirrors the same pool-aware pattern.** `is_composite_color` flags when
   the *query itself* asks for a composite decor (`венге/лоредо`); the barrier only fires when the query
   wants a single decor (`Белый`) and the pool has both a monochrome exact match and a composite
   candidate that partially overlaps it (`Ателье светлый/Белый`, `Белый/Графит`) — the composite one is
   penalized (`-0.10`) so the monochrome exact match wins the tie.
5. **Scores stay simple floats, not a fifth cascade stage.** Both barriers run as a post-processing pass
   over the FAISS + feature-boost candidate list inside `_score_candidates`, preserving the existing
   4-stage cascade (Lexical Exact → FAISS → Feature Boost → LLM Fallback → Quarantine) — no new stage was
   introduced, and Quarantine still only fires when `hard_filter_passed` is false for every candidate or
   the LLM returns `null`.

## Test Results
```
pytest tests/test_subbrand_barrier.py -v   → 13 passed (new suite)
pytest tests/ -q                            → 225 passed (212 baseline + 13 new), 0 failed

python scripts/run_order.py "data/orders/order_transfering_01_09.xls"
  → 384 rows | MATCHED_AUTO 377 (98.2%) | MATCHED_LLM 5 (1.3%) | QUARANTINE 2 (0.5%) | barcodes restored 351

python scripts/run_order.py "data/orders/order_vasilyeva_t_no_barcodes.xls"
  → 32 rows | MATCHED_AUTO 31 (96.9%) | MATCHED_LLM 1 (3.1%) | QUARANTINE 0 (0.0%)
  → Row #25: SKU barcode 4673735527409 → "Система Чикаго Вайт Кровать 160 с ламелями (корпус и
    фурнитура) Белый упаковка 1/2" (label_model="Система Чикаго Вайт", score 1.000, vector_auto)
```

## Acceptance Criteria
- [x] All pre-existing 212 tests pass (no regressions), verified with `pytest tests/ -q`.
- [x] New `tests/test_subbrand_barrier.py` (13 tests) green: Chicago White vs base, sub-brand conflict
      rejection (Равенна Роял != Равенна Тренд), pool-based sub-brand ranking, monochrome-vs-composite
      decor, no-regression on plain corpus, real-catalog integration for order line 25.
- [x] Order Васильева Т. line 25 (`Чикаго Вайт 1/2`) resolves to the Chicago White SKU with its
      EAN-13 barcode (`4673735527409`), confirmed via a real `HybridMatcher` + real catalog CLI run.
- [x] Zero-Loss Invariant held: 32/32 rows accounted for (31 auto + 1 LLM), 0 quarantined.
- [x] Package Barrier untouched: `_packaging_compatible` check still runs before the new sub-brand
      barrier in `_apply_hard_constraints`; `1/2 != 1/1` isolation covered by existing
      `test_package_ratio_hard_barrier` / `test_hard_filter_packaging_isolation` (still green).
- [x] Speed: FAISS-stage matching (the stage the new barriers run in) averaged ~0.04s/row across the
      384-row order (`FAISS=15.87s` / 384 rows); LLM network latency is unrelated to this change.

## Challenges & Caveats
- The synthetic unit test for the Chicago White scenario (`test_subbrand_chicago_white_matching`)
  initially resolved via the **Lexical Exact** stage (nomenclature-slug match), not FAISS, because the
  hand-crafted client text happened to align closely enough with one catalog nomenclature string. This
  is not a bug — it is an even earlier, stronger disambiguation than the ticket's FAISS-stage barrier —
  but it meant the test needed a direct `HybridMatcher._score_candidates` call to also assert the
  FAISS-stage score ordering explicitly, in addition to the final `matched_entity` assertion.
- `SUB_BRAND_MODIFIERS` includes short generic-looking tokens (`про`, `нью`, `софт`) that overlap with
  ordinary Russian words in isolation. Restricting extraction to `\b...\b` word-boundary matches and, on
  the catalog side, to the `ЭтикеткаМодель` field (not full nomenclature/decor text) kept false positives
  out of the 225-test regression run and the two real CLI orders, but a wider real-world order corpus
  should be watched for edge cases (e.g. a genuine product named literally "Про" or "Нью").
- The composite-color detector (`has_composite_color_signal`) is a generic slash/dash Cyrillic-word
  regex, not a catalog-derived decor list (per the anti-hardcoding rule, decor names themselves are never
  hardcoded) — it only flags *structure* (two decor-like words joined by `/` or `-`), not specific colors.

## Next Sprint Preview
- Monitor the sub-brand barrier against a larger sample of real orders (beyond Ruban/Vasilyeva/Transfering)
  to catch any collection whose `ЭтикеткаМодель` legitimately contains a modifier word as part of its
  base name rather than a true sub-line distinction.
- Consider surfacing `sub_brands` / composite-decor penalties in the Rich CLI diagnostic table
  (`diagnose_block`) so operators can see *why* a near-tie was broken, not just the final score.
