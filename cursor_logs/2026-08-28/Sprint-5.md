# Sprint 5 — WMS Excel Export Adapter, Streamlit Web UI & End-to-End Integration

**Date:** 2026-08-28  
**Status:** Completed (Final Sprint)

## Scope

- `WMSExcelAdapter` — 4-column Excel export for warehouse WMS import
- Streamlit web UI (`app_ui.py`) with drag-and-drop upload, metrics, colored results table, download button
- End-to-end integration test (`tests/test_end_to_end.py`) — full pipeline v7.7 → WMS Excel validation via openpyxl

## Files Created / Changed

| File | Action |
|------|--------|
| `src/adapters/__init__.py` | **New** — adapters package |
| `src/adapters/wms_excel_adapter.py` | **New** — WMS Excel export (`export`, `export_to_bytes`) |
| `app_ui.py` | **New** — Streamlit warehouse operator UI |
| `tests/test_end_to_end.py` | **New** — E2E pipeline + openpyxl contract validation |

## Key Design Decisions

1. **WMS row mapping from `MatchDecision`:** `MATCHED_AUTO` / `MATCHED_LLM` → v8 `nomenclature` + `barcode`; all other statuses (including `QUARANTINE`, `NEEDS_LLM`) → original v7.7 `client_description` + empty barcode. Preserves Zero-Loss: `len(output_rows) == len(decisions)`.
2. **Barcode string safety:** openpyxl cells for `Штрихкод` use `number_format='@'` and `data_type='s'` to prevent scientific notation corruption of EAN-13 codes.
3. **Streamlit caching:** `@st.cache_resource` on catalog + FAISS index load; provider switch invalidates cache via function parameter.
4. **LLM availability indicator:** Gemini checks `GEMINI_API_KEY`; Ollama uses existing `LLMResolver.is_available()` healthcheck.
5. **E2E test without network:** Uses `HybridMatcher` without LLM resolver for deterministic CI; validates rows 1–2 (custom countertops) have empty barcodes and client names preserved.

## WMS Export Contract

| Column | Source |
|--------|--------|
| Наименование | v8 nomenclature (matched) or v7.7 client description (unmatched) |
| Штрихкод | v8 EAN-13 as string, or empty |
| Количество | v7.7 block quantity |
| Заказчик | Header `Покупатель:` propagated to all rows |

## Test Results

```
pytest tests/ -v  →  54 passed (Sprints 1–5)
```

E2E assertions on `order_ruban.xlsx`:
- 55 data rows + 1 header
- Headers: `["Наименование", "Штрихкод", "Количество", "Заказчик"]`
- All rows: `Заказчик = "Рубан Кристина Олеговна ИП"`
- Matched barcodes: 13-digit strings, `data_type='s'`
- Rows 1–2: empty barcode, quantity preserved, client nomenclature

## Running the UI

```powershell
cd "c:\Cursor\Universal 1C Furniture Data Normalizer & WMS Bridge"
.\venv\Scripts\streamlit run app_ui.py
```

## Project Completion

Sprints 1–5 deliver the full pipeline:

1. **Sprint 1** — Pydantic models, v7/v8 parsers, barcode string safety
2. **Sprint 2** — Dynamic vocabulary, feature extraction
3. **Sprint 3** — FAISS vector store, hybrid matcher with hard filters
4. **Sprint 4** — Dual-engine LLM resolver (Gemini/Ollama), Rich CLI reporter
5. **Sprint 5** — WMS Excel adapter, Streamlit UI, E2E integration test

**Acceptance criteria met:** all tests green, WMS Excel valid for import, Streamlit app launchable, sprint log complete.
