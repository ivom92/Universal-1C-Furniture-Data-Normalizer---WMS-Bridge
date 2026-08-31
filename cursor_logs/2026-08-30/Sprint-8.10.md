# Sprint 8.10 — Де-омоглифизация токенов и FSM-парсер мягкой мебели

**Date:** 2026-08-30  
**Status:** Completed

## Scope

- Универсальный ремонт смешанной раскладки на уровне токена (мажоритарный алфавит), без поломки английских декоров (`shadow`, `palermo`, `loft`).
- Канонизация модульных префиксов `Н/H` и `В/B` перед цифрами.
- Production FSM-парсер мягкой мебели: 1..K товаров, 1..N упаковок, моноблоки, динамические колонки, сверка `ИТОГО мест`.

## Files Created / Changed

| File | Action |
|------|--------|
| `src/preprocessor/normalizer.py` | `repair_mixed_script_token`, `canonicalize_furniture_module_codes`, `normalize_text` |
| `src/preprocessor/__init__.py` | Экспорт новых функций |
| `src/matcher/token_normalizer.py` | Поисковая нормализация через `normalize_text` |
| `src/matcher/exact_matcher.py` | Step 0 ключ имени через `normalize_text` |
| `src/matcher/feature_extractor.py` | Цепочка признаков через `normalize_text` |
| `src/parsers/soft_furniture_parser.py` | FSM: SEEK_PARENT → SEEK_ORDER_INFO → READ_PACKAGES → CHECK_TOTALS |
| `tests/test_normalizer.py` | Омоглифы, латиница, модули |
| `tests/test_soft_furniture_parser.py` | Мульти-товар + живой bed-файл |
| `tests/excel_fixtures.py` | `write_soft_furniture_multi_xlsx` |
| `dist/Warehouse_WMS_Pilot_v1.0.zip` | Пересобран |

## Key Design Decisions

1. **Мажоритарное голосование ≥ 0.6.** Считаются только буквы; при равенстве долей токен не трогается. Английские декоры остаются латиницей; кириллические имена чинятся (`Крoвать`, `Раvенна`). Пара `v/в` добавлена к списку омоглифов из ТЗ, иначе пример `Раvенна` не закрывался.
2. **Модули после ремонта скрипта.** `H20` / `B-60` приводятся к `Н20` / `В60` на всём тексте, не ломая `shadow`.
3. **FSM + динамические колонки.** Заголовок ищется по `№`, `Товар`/`Наименование`, `Ткань`, `Цвет`, `Кол-во`, `Вес`, `Отметка`. Нет жёсткой привязки к A/B/C. Моноблок без маркера `Состоит из упаковок:` выгружается одной строкой `"{title} {fabric}"`.
4. **Каскад матчинга не менялся.** Мягкая мебель по-прежнему `AUTO_NO_BARCODE`. Стандартные бланки: Lexical → FAISS → Feature Boost → LLM → Quarantine.

## Test Results

```
.\venv\Scripts\python.exe -m pytest tests/ -q --tb=short
154 passed, 1 warning in 50.34s
```

## Live CLI

```
.\venv\Scripts\python.exe scripts/run_order.py "data/orders/order_transfering_01_09.xls"
.\venv\Scripts\python.exe scripts/run_order.py "data/orders/order_transfering_01_09_bed.xls"
.\venv\Scripts\python.exe scripts/build_warehouse_dist.py
```

| Metric | Result | TZ target |
|--------|--------|-----------|
| Rows (01_09.xls) | 384 | 384 |
| MATCHED_AUTO | 379 (98.7%) | — |
| MATCHED_LLM | 5 (1.3%) | — |
| QUARANTINE | **0 (0.0%)** | 0 |
| №230 | `MATCHED_AUTO` / `exact_article` / ШК `4603734801972` | строго MATCHED_AUTO |
| Bed file | 3 строки, сумма мест 3, `AUTO_NO_BARCODE` | 3 |
| Zip | 48 files, `dist/Warehouse_WMS_Pilot_v1.0.zip` | packed |

## Challenges & Caveats

1. **В ТЗ нет пары `v/в`, но есть приёмочный пример `Раvенна`.** Латинская `v` добавлена в словарь омоглифов; заглавная `В` по-прежнему мапится на латинскую `B` (как `B/В` в спецификации).
2. **Ткань в живом bed-файле сидит в колонке «Цвет», а `palermo` — в наименовании.** Парсер берёт ткань/цвет из найденных колонок и не склеивает вес/кол-во в имя.
3. **FAISS cache не бампился.** Homoglyph/module repair идёт на query/exact-name стороне; passage каталога не менялся.

## Next sprint preview

- Если появятся бланки с `parent_qty > 1` (несколько комплектов упаковок) — отдельно прогнать живые файлы на умножение мест.
- Опционально: индексировать каталог уже после `normalize_text` (тогда нужен cache v3).
