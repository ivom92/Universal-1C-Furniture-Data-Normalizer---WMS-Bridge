# Sprint 8.12 — Telemetry & Deep Observability

**Date:** 2026-08-30  
**Status:** Completed

## Scope

- Telegram Smart Cards: SUCCESS / WARNING / ERROR HTML-карточки, пакетная сводка, latch против спама `notify_startup` при rerender Streamlit.
- «Чёрный ящик»: DEBUG в `logs/warehouse_app.log` (ротация 10 MB × 5), INFO в консоль.
- Пошаговая трассировка Normalizer / SoftFSM / Matcher / LLM и профайлер этапов ETL.

## Files Created / Changed

| File | Action |
|------|--------|
| `src/utils/telemetry.py` | Latch `_STARTUP_NOTIFIED`, `CardType`, HTML-карточки, `notify_batch_completion`, ERROR + `sendDocument` лога |
| `src/utils/logger.py` | File=DEBUG 10MB×5, Console=INFO |
| `src/pipeline.py` | `StageTimings`, `[Profiler]` |
| `src/matcher/hybrid_matcher.py` | DEBUG Exact/FAISS/Barrier, INFO LLM fallback, WARNING карантин, тайминги |
| `src/matcher/llm_resolver.py` | DEBUG промпт/ответ через warehouse logger |
| `src/preprocessor/normalizer.py` | DEBUG омоглифы и канон габаритов |
| `src/parsers/soft_furniture_parser.py` | DEBUG FSM родителей, `checksum_mismatch` в `V7ParseResult` |
| `src/models.py` | `declared_places`, `checksum_mismatch` |
| `app_ui.py` / `scripts/run_order.py` | Пакетная карточка, checksum stats, Excel в профайлере |
| `tests/test_telemetry.py` | Карточки SUCCESS/WARNING/ERROR/batch + latch (HTTP × 1) |
| `dist/Warehouse_WMS_Pilot_v1.0.zip` | Пересобран |

## Key Design Decisions

1. **Latch на уровне процесса, не session_state.** Streamlit session_state сбрасывается; модульный `_STARTUP_NOTIFIED` переживает rerun в том же Python-процессе. Тесты сбрасывают через `reset_startup_latch()`.
2. **WARNING = карантин > 0 или checksum мест.** Мягкая мебель прокидывает `V7ParseResult.checksum_mismatch`; в карточке до 5 строк карантина с причиной.
3. **Пакет M>1 — одна сводная карточка.** Одиночный файл по-прежнему шлёт SUCCESS/WARNING. ERROR всегда с аттачем `warehouse_app.log`.
4. **Два потока логов.** Консоль не получает DEBUG (тысячи Package Barrier). Файл пишет полный каскад решений и `[Profiler]` с Parse/Exact/FAISS/LLM/Excel.

## Test Results

```
.\venv\Scripts\python.exe -m pytest tests/ -q --tb=short
170 passed, 1 warning in 53.66s
```

```
.\venv\Scripts\python.exe scripts/run_order.py "data/orders/order_transfering_01_09.xls"
Заказчик: РС УрФО Империал | Позиций: 384
Авто-сопоставлено (MATCHED_AUTO): 379 (98.7%)
Разрешено через LLM (MATCHED_LLM): 5 (1.3%)
В карантине (QUARANTINE): 0 (0.0%)
Восстановлено заводских штрихкодов: 353 шт.
Без ШК: 31 (8.1%)
[Profiler] ... (Parse=0.29s, Exact=1.17s, FAISS=14.97s, LLM=2.94s, Excel=0.24s)
card=SUCCESS
```

В `logs/warehouse_app.log` зафиксированы `[Normalizer]`, `[Matcher:Row …] Rejected … Package ratio mismatch`, `[Profiler]`.

```
.\venv\Scripts\python.exe scripts/build_warehouse_dist.py
Архив: dist/Warehouse_WMS_Pilot_v1.0.zip (19651403 байт, 49 файлов)
```

## Challenges & Caveats

1. **Объём DEBUG при канонизации габаритов.** Первая версия писала всю номенклатуру в сообщение. Сужено до фрагмента `59,6х225,8` → `59,6x225,8`, иначе лог раздувается на загрузке каталога.
2. **MagicMock в тестах пайплайна.** `hasattr(matcher, "reset_stage_timings")` на моке всегда True. Сброс таймингов идёт через `type(matcher)`, числа профайлера парсятся через `_safe_timing`.
3. **INFO LLM fallback в консоли.** Спека требует `logger.info` на вызов LLM — 5 строк на 384 позиции, это не DEBUG-спам.

## Next sprint preview

- Живой прогон UI: пакет из двух файлов → одна Telegram-карточка; F5 не должен слать повторный startup в том же процессе.
- При необходимости — сэмплирование DEBUG канонизации на каталоге, если ротация 10 MB начнёт часто срабатывать на складе.
