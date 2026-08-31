# Sprint 8.8 — Unicode FAISS IO и автозапуск UI на Windows

**Date:** 2026-08-30  
**Status:** Completed

## Scope

- Убрать вызовы `faiss.read_index` / `faiss.write_index` с путями ФС: C++ `fopen(char*)` на Windows не открывает каталоги с кириллицей (`C:\Users\Егор\...`).
- Вернуть автооткрытие браузера оператору склада (`headless = false` + fallback `start http://localhost:8501`).
- Стресс-тест кэша FAISS в каталоге `Тестовый_Каталог_Егор`.

## Files Created / Changed

| File | Action |
|------|--------|
| `src/matcher/vector_store.py` | `serialize_index` / `deserialize_index` + Python `open(..., "wb"/"rb")`; публичные `save()` / `load()` с логированием |
| `scripts/check_system_health.py` | Проверка кэша через тот же `read_faiss_index` (кириллические пути) |
| `.streamlit/config.toml` | `headless = false`; `fileWatcherType = "none"`, `port = 8501` без изменений |
| `2_ЗАПУСК.bat` | `chcp 65001`; `start ... timeout /t 3 ... start http://localhost:8501` перед Streamlit |
| `tests/test_matcher.py` | `test_faiss_cyrillic_path_io` |

## Key Design Decisions

1. **Байт-буфер, не путь.** `faiss.serialize_index` пишет в память; диск — только через Python, который на Windows использует Unicode API.
2. **Метаданные остаются pickle (`catalog_meta.pkl`).** В текущем контракте кэш — список `CatalogEntity`, а не DataFrame. Смена на parquet сломала бы уже собранные индексы на складе; формат FAISS на диске тот же, старый `.index` читается через `deserialize_index`.
3. **`save()` / `load()` возвращают `bool`.** Исключения логируются через `get_logger()`, пайплайн `build_or_load_index` по-прежнему пробрасывает ошибки сборки.
4. **Health-check** тоже не должен звать `read_index(str(path))` — иначе диагностика на машине Егора падала бы тем же `fopen`.

## Test Results

```
.\venv\Scripts\python.exe -m pytest tests/ -v --tb=short
134 passed, 1 warning in 81.29s
```

Новый тест: `TestCatalogVectorStore::test_faiss_cyrillic_path_io` PASSED.

## Live CLI

```
.\venv\Scripts\python.exe scripts/run_order.py "data/orders/order_transfering_01_09.xls"
.\venv\Scripts\python.exe scripts/build_warehouse_dist.py
```

| Metric | Result | TZ target |
|--------|--------|-----------|
| Rows | 384 | 384 |
| MATCHED_AUTO | 377 (98.2%) | — |
| MATCHED_LLM | 6 (1.6%) | — |
| QUARANTINE | 1 (0.3%) | — |
| Без ШК | 31 | — |
| Zip | 42 files, `vector_store.py` + `.streamlit/config.toml` + `2_ЗАПУСК.bat` | packed |

WMS file: `output/WMS_Импорт_РС УрФО Империал_2026-08-30.xlsx`.

## Challenges & Caveats

1. **ТЗ упоминало parquet/`catalog_df`.** Реальный `CatalogVectorStore` хранит Pydantic-каталог в pickle; переведен только FAISS IO, чтобы не ломать кэш и нулевую потерю строк.
2. **`frombuffer` копируется (`.copy()`).** Иначе буфер может оказаться read-only и сломать `deserialize_index`.
3. **Автооткрытие браузера** — гонка: `timeout /t 3` может открыть вкладку до готовности Streamlit; оператор обновит страницу. Это лучше, чем `headless = true` без URL.

## Next sprint preview

- При необходимости синхронизировать `2_run.sh` / `start_warehouse_app.bat` с тем же fallback открытия браузера.
- Опционально: предупреждение Hugging Face / `HF_HUB_OFFLINE` в дистрибутиве (хвост 8.7).
