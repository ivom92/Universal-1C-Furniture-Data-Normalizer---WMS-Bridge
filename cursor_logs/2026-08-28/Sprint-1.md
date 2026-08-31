# Sprint 1 — Development Log (2026-08-28)

## Scope
ETL Core: data architecture, Pydantic contracts, v8 catalog loader, v7 order parser, integration tests on real files.

## Project Structure Created
```
requirements.txt
src/
  __init__.py
  models.py
  parsers/
    __init__.py
    v7_parser.py
    v8_loader.py
tests/
  __init__.py
  conftest.py
  test_parsers.py
output/
  .gitkeep
cursor_logs/
  2026-08-28/
    Sprint-1.md
```

## Pydantic v2 Models (`src/models.py`)
| Model | Purpose |
|---|---|
| `RawOrderBlock` | One 3-row v7.7 item block: line_number, client_description (B-F), item_type (G), quantity (H), factory_alias (yellow row), order_service_line, excel_row_start |
| `V7ParseResult` | Header customer_name + list of blocks |
| `CatalogEntity` | All 17 v8 catalog columns with Russian field aliases; strict str validation for `nomenclature_code` / `barcode` |
| `MatchedOrderItem` | WMS 4-column contract + optional match metadata for Sprint 2+ |

**Guardrails enforced in models:**
- `НоменклатураКод` and `Штрихкод` never accepted as `int`/`float` at model boundary (must be normalized upstream).
- `barcode=None` for missing EAN-13 (zero-loss invariant preparation).

## v8 Loader (`src/parsers/v8_loader.py`)
- Reads `catalog_v8.xlsx` via **openpyxl** with `data_only=False` to access cell `number_format`.
- Validates header row against 17 canonical column names.
- **`format_nomenclature_code`**: applies Excel zero-pad format `00000000000` → e.g. `64794` → `'00000064794'`.
- **`restore_barcode`**: int → str; spaced strings → None; scientific strings (`2,006E+12`) → integer string via `Decimal`.
- Real catalog stats: **12 880 rows**, **10 200** with barcode, **2 680** without.

## v7 Parser (`src/parsers/v7_parser.py`)
- **Customer extraction**: scans header for `Покупатель:` label; reads adjacent cell → `'Рубан Кристина Олеговна ИП'`.
- **State machine** (`SCAN → MAIN → ALIAS → SERVICE → SCAN`):
  - MAIN row: col A = integer № п/п, fill `FFE0FFE0` on col B.
  - ALIAS row: fill `FFFFFFC0` or text starting with `IMP`.
  - SERVICE row: text in cols B-E containing `Продажи` / `Заказ:` / `УРЛ_`.
- Real order `order_ruban.xlsx`: **55 blocks**, line numbers 1..55 sequential.

## Tests (`tests/test_parsers.py`)
- Unit tests for code/barcode coercion helpers.
- Integration tests on real `data/catalog_v8.xlsx` and `data/orders/order_ruban.xlsx`.
- Verified known barcode `2006000045445` → nomenclature_code `00000097658` (Плано facade).

## Test Run
```bash
pytest tests/test_parsers.py -v
# 17 passed
```

## Next Sprint (Preview)
- `feature_extractor.py` — RegEx for packaging ratio, dimensions, glass type.
- `dynamic_vocab.py` — KNOWN_MODELS / COLORS / MATERIALS from catalog DataFrame.
- `vector_store.py` — multilingual-e5-small + FAISS indexing.

## Доп информация
 Ранний фоновый запуск на инспекцию Excel завершился с ошибкой: при выводе кириллицы в консоль Windows (cp1252) возник UnicodeEncodeError. На результат Спринта 1 это не повлияло — инспекция и тесты были перезапущены с PYTHONIOENCODING=utf-8, все 17 тестов прошли, парсеры работают на реальных файлах из data/.
