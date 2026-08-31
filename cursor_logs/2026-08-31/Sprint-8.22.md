# Sprint 8.22 — Mojibake / Windows-1251 Encoding Auto-Detection (1C v7.7)

## Scope
- Auto-detect `Windows-1251` vs `UTF-8` when parsing 1C v7.7 HTML tables saved as `.xls` without charset header.
- Add `heal_mojibake()` to recover Latin-1 misread Cyrillic (`Àâðîðà` → `Аврора`).
- Prevent false Quarantine on orders like «Отборочная Васильева Т. без ШК.xls».

## Files Changed
| File | Change |
|------|--------|
| `src/parsers/v7_parser.py` | `_decode_html_bytes()`, `_looks_like_cp1251_mojibake()`; `normalize_incoming_text` calls `heal_mojibake` |
| `src/preprocessor/normalizer.py` | `heal_mojibake()`; first step in `normalize_text()` |
| `src/preprocessor/__init__.py` | Export `heal_mojibake` |
| `tests/test_normalizer.py` | `TestHealMojibake` (2 acceptance strings + preserve Cyrillic) |
| `tests/test_parsers.py` | CP1251 HTML-XLS decode + mojibake safety-net tests |

## Key Design Decisions
1. **Decode cascade** — `utf-8` (strict) → mojibake heuristic → `cp1251` → `cp866`; replaces blind `utf-8-sig` with `errors=replace`.
2. **Mojibake heuristic** — Latin extended chars `[\u00C0-\u00FF]` present, standard Cyrillic `[А-Яа-яЁё]` absent → re-decode as `cp1251`.
3. **Dual-layer repair** — encoding fix at parse time (primary) + `heal_mojibake` in `normalize_incoming_text` and `normalize_text` (matcher safety net).
4. **Repair algorithm** — `text.encode("latin-1").decode("cp1251", errors="ignore")`; accept only if result contains valid Cyrillic.

## Test Results
```
pytest tests/ -v  →  212 passed (+5 new)
python scripts/build_warehouse_dist.py  →  dist/Warehouse_WMS_Pilot_v1.0.zip (37.6 MB, 57 files)
```

## Acceptance Criteria
- [x] `"Àâðîðà Êðîâàòь 90 …"` → `"Аврора Кровать 90 со встроенным основанием"`
- [x] `"Àë¸íà Øêàô 3-õ …"` → `"Алёна Шкаф 3-х дверный (корпус)"`
- [x] CP1251 HTML-XLS synthetic fixture parses clean Cyrillic (Vasilyeva pattern)
- [x] All 212 tests green (207+ regression stable)
- [x] Dist rebuilt

## Challenges & Caveats
- Real file `Отборочная Васильева Т. без ШК.xls` is not in repo; validated via CP1251-encoded synthetic HTML-XLS mirroring session JSON mojibake symptoms.
- UTF-8 files with only ASCII/Latin product names remain unaffected (no false Cyrillic trigger).

## Next Sprint Preview
- Re-run Vasilyeva order in UI after deploying updated dist; confirm 0 false Quarantine on 12 previously broken lines.
- Optional: add `data/orders/` fixture from production file for permanent regression coverage.
