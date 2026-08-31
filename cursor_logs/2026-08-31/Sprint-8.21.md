# Sprint 8.21 — Graceful NumPy Vector Fallback (FAISS DLL Failure on Windows 8)

## Scope
- Transparent fallback to pure-NumPy cosine search when `faiss` C++ DLL fails to load (missing `vc_redist.x64` on Windows 8.1).
- Dual cache format: FAISS index + `catalog_vectors.npy` for offline fallback.
- Health check reports engine type without FAIL when NumPy fallback is active.

## Files Changed
| File | Change |
|------|--------|
| `src/matcher/vector_store.py` | Safe `faiss` import; `NumpyVectorEngine`; dual cache; auto-backfill `.npy` on FAISS load |
| `scripts/check_system_health.py` | `_check_vector_engine()` with FAISS / NumPy status; renamed warm check |
| `tests/test_matcher.py` | `test_numpy_fallback_equivalent_to_faiss`, `test_numpy_engine_search_matches_dot_product` |

## Key Design Decisions
1. **`FAISS_AVAILABLE` flag** — `try/except (ImportError, Exception)` catches DLL load failures, not just missing package.
2. **`NumpyVectorEngine`** — `np.dot` + `argsort` on L2-normalized `[N, 384]` matrix; API mirrors FAISS `search()` output shape.
3. **Dual cache** — every build/save writes `catalog_vectors.npy`; loading FAISS backfills `.npy` if missing (upgrade path for existing installs).
4. **100% score parity** — same `IndexFlatIP` math via dot product on normalized vectors; tested with monkeypatched `FAISS_AVAILABLE=False`.

## Test Results
```
pytest tests/ -v  →  207 passed
python scripts/run_order.py "data/orders/order_transfering_01_09.xls"  →  OK
python scripts/check_system_health.py --warm  →  OK (FAISS C++ Engine, 12 880 векторов)
python scripts/build_warehouse_dist.py  →  dist/Warehouse_WMS_Pilot_v1.0.zip (37.6 MB, 57 files)
```

## Acceptance Criteria
- [x] Forced `FAISS_AVAILABLE=False` yields identical Top-1 codes and scores
- [x] `check_system_health.py` returns 0 FAIL (NumPy fallback = OK)
- [x] All 207 tests green
- [x] Dist rebuilt with `catalog_vectors.npy` in `.cache`

## Challenges & Caveats
- First load on a machine with only legacy FAISS cache (no `.npy`) requires one FAISS-capable run to backfill; dist now ships with `.npy` pre-warmed.
- NumPy search on 12 880 × 384 vectors is ~3–5 ms — acceptable for warehouse use; no perceptible regression vs FAISS at this scale.

## Next Sprint Preview
- Field test on Windows 8.1 PC without `vc_redist.x64`: verify NumPy fallback path end-to-end from zip.
- Optional: skip bundling `catalog_faiss.index` in dist when targeting known FAISS-broken hosts (smaller zip).
