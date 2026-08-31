# Sprint 3 — Development Log (2026-08-28)

## Scope
Local FAISS vector search (`intfloat/multilingual-e5-small`), disk cache in `.cache/`, hybrid cascade matcher (hard constraints + vector similarity + confidence scoring), candidate pool for Sprint 4 LLM fallback.

## Files Created / Updated
```
src/models.py                    # MatchCandidate, MatchDecision
src/matcher/vector_store.py      # CatalogVectorStore (FAISS + e5 + cache)
src/matcher/hybrid_matcher.py    # HybridMatcher cascade
src/matcher/__init__.py          # exports
tests/test_matcher.py            # Sprint 3 test suite
```

## Key Design Decisions
- **e5 prefixes:** `passage:` for catalog rows, `query:` for order blocks (required by multilingual-e5-small).
- **Cosine via FAISS:** L2-normalized embeddings + `IndexFlatIP` → scores in `[-1, 1]`.
- **Disk cache:** `.cache/catalog_faiss.index` + `.cache/catalog_meta.pkl` keyed by SHA-256 fingerprint of sorted `nomenclature_code` values; reload < 1 s on repeat runs.
- **Hard filters:** exact packaging ratio (incl. `Ун1/1` normalization), dimension pair conflict detection (116×596 vs 140×596), glass/thickness gate for `Стекло` / `4мм`.
- **Decision thresholds:** `MATCHED_AUTO` when top-1 score ≥ 0.90 and gap to top-2 ≥ 0.03; else `NEEDS_LLM`; empty passed pool → `QUARANTINE`.
- **Zero-loss:** `match_order()` always returns one `MatchedOrderItem` per input block.

## Test Run
```bash
pytest tests/ -v
# 38 passed (17 Sprint 1 + 16 Sprint 2 + 5 Sprint 3)
```

Integration on `order_ruban.xlsx`: 55/55 blocks processed (zero-loss). Dimension-aware collision resolution disambiguates near-duplicate Plano facades at score ≥ 0.90.

## Next Sprint (Preview)
- `llm_resolver.py` — Gemini / Ollama structured JSON fallback for `NEEDS_LLM` rows.
- Wire candidate pool from `MatchDecision.candidates` into LLM prompt.
