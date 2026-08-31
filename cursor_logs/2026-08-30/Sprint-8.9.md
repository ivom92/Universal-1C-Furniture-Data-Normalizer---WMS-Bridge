# Sprint 8.9 — Канонические габариты (фикс №230) и парсер мягкой мебели

**Date:** 2026-08-30  
**Status:** Completed

## Scope

- Убрать ложный карантин позиции №230 (`Кухня Равенна полка стеклянная 60 (2 шт 5мм) 565х255`) из-за русской `х` vs латинской `x`.
- Добавить детектор типа документа и иерархический парсер отборочных ведомостей мягкой мебели (1..N грузомест).
- Маршрутизировать обе ветки в один контракт WMS (`WMSExcelAdapter`).

## Files Created / Changed

| File | Action |
|------|--------|
| `src/preprocessor/normalizer.py` | `canonicalize_dimensions`, `extract_dimension_tokens` |
| `src/matcher/exact_matcher.py` | Step 0: каноническое имя + индекс габаритов + разведение `N шт` |
| `src/matcher/token_normalizer.py` | Габариты канонизируются до аббревиатур и упаковки |
| `src/matcher/feature_extractor.py` | Извлечённые размеры в форме `565x255` / `116x596x16` |
| `src/matcher/hybrid_matcher.py` | Exact name/dim lookup; `_glass_compatible` по `стекл\|зеркал`; пары из канонического `x` |
| `src/matcher/vector_store.py` | Канонические габариты в passage; `_CACHE_VERSION = 2` |
| `src/parsers/v8_loader.py` | Маскирование артикулов после канонизации `*`/`х` |
| `src/parsers/v7_parser.py` | `open_order_sheet` context manager |
| `src/parsers/document_detector.py` | `DocumentTypeDetector` |
| `src/parsers/soft_furniture_parser.py` | Разворот родителя в 1..N пакетов |
| `src/pipeline.py` | Изоляция STANDARD vs SOFT_FURNITURE |
| `scripts/run_order.py`, `app_ui.py` | Маршрутизация через `pipeline` |
| `tests/test_normalizer.py` | Новые |
| `tests/test_soft_furniture_parser.py` | Новые |
| `tests/test_order_processing.py` | Приёмка №230 + живой bed-файл |
| `tests/test_features.py`, `tests/test_matcher.py`, `tests/excel_fixtures.py` | Регрессия габаритов / fixture bed |

## Key Design Decisions

1. **Единый разделитель `x`.** Все `х/Х/x/X/*/×` в цифровых прогонах сжимаются в `565x255` / `AxBxC` до FAISS, slug и hard-filter пар.
2. **Step 0 — каноническое имя.** У №230 клиентская строка совпадает с номенклатурой v8 после канонизации; индекс имени бьёт однозначно, не смешивая комплект `2 шт` с соседним `1 шт` (`00000041929`).
3. **Стекло в типе «Стекло».** Хард-фильтр требовал подстроку `стекло`, которой нет в `стеклянная` — кандидат из каталога отбрасывался. Проверка заменена на уже существующий `_GLASS_OR_MIRROR_RE`.
4. **Мягкая мебель не идёт в гибридный матчер.** `AUTO_NO_BARCODE`, пустой ШК, склейка `[Товар] [Ткань] [Пакет]`; сверка суммы мест с `ИТОГО мест`.
5. **Стандартный бланк не трогался.** `parse_v7_order` и каскад Lexical → FAISS → Feature Boost → LLM → Quarantine сохранены.

## Test Results

```
.\venv\Scripts\python.exe -m pytest tests/ -q --tb=short
146 passed, 1 warning in 32.85s
```

## Live CLI

```
.\venv\Scripts\python.exe scripts/run_order.py "data/orders/order_transfering_01_09.xls"
.\venv\Scripts\python.exe scripts/build_warehouse_dist.py
```

| Metric | Result | TZ target |
|--------|--------|-----------|
| Rows | 384 | 384 |
| MATCHED_AUTO | 379 (98.7%) | — |
| MATCHED_LLM | 5 (1.3%) | — |
| QUARANTINE | **0 (0.0%)** | 0 или только реально отсутствующие |
| №230 | `MATCHED_AUTO` / `exact_article` / ШК `4603734801972` | код `00000025467` |
| Zip | 48 files, `dist/Warehouse_WMS_Pilot_v1.0.zip` | packed |

WMS file: `output/WMS_Импорт_РС УрФО Империал_2026-08-30.xlsx`.

## Challenges & Caveats

1. **Канонизация габаритов сама по себе не вытаскивала №230 из карантина.** После нормализации `х`→`x` exact-index находил *два* SKU `565x255` (1 шт и 2 шт), а `_glass_compatible` резал оба, потому что в названии `стеклянная`, а тип блока `Стекло`. Ассерты не ослаблялись: добавлены точное имя + kit-count и исправлен glass-check.
2. **Живой `order_transfering_01_09_bed.xls` есть в `data/orders/`.** Парсер на нём даёт 3 места; синтетический xlsx в тестах покрывает те же маркеры (`Мягкая мебель`, `Состоит из упаковок:`).
3. **FAISS cache v2.** Старый `.cache` с версией 1 пересобирается (passage с латинским `x`).

## Next sprint preview

- Если появятся бланки мягкой мебели с несколькими родителями на одном листе — прогнать живые файлы и при необходимости уточнить смену parent-блока.
- Опционально: не пересобирать FAISS, если меняется только нормализация query (сейчас passage тоже канонизирован — v2 оправдан).
