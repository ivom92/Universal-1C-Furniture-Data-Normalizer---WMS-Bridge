# Sprint 3.1 — Matcher Diagnostic & Calibration (2026-08-28)

## Scope
Calibrate hybrid matcher thresholds and collision logic; add diagnostic script for `order_ruban.xlsx`; eliminate false glass filter on 40mm countertops; achieve ≥75% auto-match rate without mis-sorting.

## Files Created / Updated
```
scripts/diagnose_matcher.py       # per-block matcher diagnostic CLI
src/matcher/hybrid_matcher.py     # calibrated cascade + diagnose_block()
tests/test_matcher.py             # assert MATCHED_AUTO >= 40 on Ruban order
```

## Key Design Decisions
- **Query cleaning:** strip `Совместимость:…`, `д/шкафа…`, `IMP ст` prefix from `factory_alias`; normalize `=` separators.
- **Thresholds:** base `MATCHED_AUTO` at **0.83**; feature-boost path at **0.80** when hard filters + dimensions/model/color/packaging + nomenclature align.
- **Collision gap:** computed only among candidates with identical characteristic key (model + color + dimension pairs + product slug) — color-variant or packaging-variant neighbors no longer block auto-match.
- **Distinctive alignment gate:** decor codes (e.g. `5270/FL`), identity tokens (≥75% overlap), specific part types, and dimension pairs must align before auto-match — prevents Полка→Пенал mis-sorts.
- **Glass filter fix:** only `4мм` thickness triggers glass mode (not `40мм`); countertops with missing v8 40mm rows correctly fall to QUARANTINE instead of wrong auto-match.
- **Thickness/linear hard filter:** countertop blocks with `40мм` + `2,00м` reject catalog rows with mismatched thickness or length.

## Results (`order_ruban.xlsx`)
```
MATCHED_AUTO=47 (85.5%), NEEDS_LLM=6, QUARANTINE=2
```
- **QUARANTINE (2):** KDR countertops — no matching 40mm/2.00m catalog rows (zero-loss preserved).
- **NEEDS_LLM (6):** ambiguous glass bundles, Полка vs Пенal collision, standalone glass rows for Sprint 4 LLM.

## Test Run
```bash
python scripts/diagnose_matcher.py
pytest tests/ -v
# 38 passed
```

## Next Sprint (Preview)
- Wire `NEEDS_LLM` rows (6 glass/edge cases) into `llm_resolver.py` Gemini/Ollama fallback.
