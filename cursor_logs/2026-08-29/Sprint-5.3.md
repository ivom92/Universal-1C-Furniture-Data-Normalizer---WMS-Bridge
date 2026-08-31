# Sprint 5.3 — Production Hardening of the 1C 7.7 Parser

**Date:** 2026-08-29  
**Status:** Completed

## Scope

- Robust extraction of customer / warehouse recipient from 1C 7.7 print forms (отборочный, накладная, перемещение)
- Support optional colons, adjacent cells, merged cells, and the row under the label
- Zero-loss fallback: document title or filename instead of `Customer name not found`
- 1- and 2-row transfer item blocks without a yellow factory alias
- Diagnostic CLI `scripts/inspect_v7_file.py`
- Operator override of WMS `Заказчик` in Streamlit

## Files Created / Changed

| File | Action |
|------|--------|
| `scripts/inspect_v7_file.py` | **New** — first 35 rows A–H, types, fills, merged ranges |
| `src/parsers/v7_parser.py` | Header regex + 3 placement variants, document-title fallback, relaxed item FSM |
| `src/models.py` | `RawOrderBlock.factory_alias` is `Optional[str]` (None when alias row is absent) |
| `src/matcher/hybrid_matcher.py` | Safe concatenate when alias is None |
| `app_ui.py` | Editable «Заказчик / Получатель для WMS», parse-error expander |
| `tests/excel_fixtures.py` | `write_transfer_v7_xlsx` |
| `tests/test_parsers.py` | Five transfer / fallback tests |

## Key Design Decisions

1. **Never raise on missing header.** Labels are scanned with `HEADER_LABEL_PATTERN` (optional colon). If none match, use a document title in the first 10 rows (`Перемещение № …`, `Отборочный лист`, `Накладная`, `Расходная`). Last resort: `Перемещение ({filename})` plus `logger.warning`.
2. **Three cell layouts:** inline tail after the label; first non-empty cell to the right; cell directly below the label. Values shorter than 2 characters are skipped.
3. **Table header (`№` / `Код` / `Товар` / `Наименование`)** switches the FSM into transfer mode: numbered rows do not require green fill. After a main row, a yellow/IMP row is optional; a service line or the next numbered row closes the block. EOF flushes a pending 1- or 2-row block instead of raising `Incomplete 3-row block`.
4. **Contract:** `V7ParseResult` fields unchanged; `factory_alias=None` is the only widening, required for 2-row transfers. `order_service_line` stays `str` (empty string when missing).
5. **UI:** `st.text_input` is the WMS customer source; changing it after matching rebuilds the export bytes so every output row uses the operator value.

## Test Results

```
pytest -v  →  64 passed
```

New coverage: inline `Склад-получатель: Челябинск ТК` (merged A2:D2), adjacent-cell recipient, document-title fallback, 2-row items with `factory_alias is None`, filename fallback without any party label.

## How to verify

```bash
python scripts/inspect_v7_file.py "data/orders/Перемещ... 01.09.xls"
python scripts/run_order.py "data/orders/Перемещ... 01.09.xls"
streamlit run app_ui.py
```

`data/orders/` may be empty in this workspace; point the inspect/CLI paths at a real warehouse file when it is available.

## Next sprint preview

- Tune quantity/column mapping on live Челябинск transfer `.xls` after inspect dumps
- Persist operator-corrected customer name per file hash
