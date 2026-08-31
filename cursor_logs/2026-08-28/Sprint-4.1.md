# Sprint 4.1 — Локальная верификация Ollama (qwen2.5:7b)

**Date:** 2026-08-28  
**Status:** Completed

## Scope

- Робастный парсинг JSON-ответов Ollama (Markdown-обёртки, prose до/после JSON)
- Healthcheck Ollama: `is_available()` + `has_ollama_model()`
- Температура `0.1` в `/api/generate` для детерминированного выбора
- Скрипт `scripts/verify_ollama_integration.py` — 6 пограничных позиций + полный прогон
- Unit-тесты sanitization и healthcheck
- `.env`: `LLM_PROVIDER=ollama`

## Files Created / Changed

| File | Action |
|------|--------|
| `src/matcher/llm_resolver.py` | `sanitize_json_text`, `parse_llm_json_response`, `is_available()`, `has_ollama_model()`, temperature 0.1 |
| `scripts/verify_ollama_integration.py` | **New** — Ollama verification + full pipeline |
| `tests/test_llm_resolver.py` | +7 tests (JSON sanitization, healthcheck, Ollama resolve) |
| `.env` | `LLM_PROVIDER=ollama` |

## Key Design Decisions

1. **JSON extraction:** `re.search(r'\{.*\}', text, re.DOTALL)` после удаления `` ```json `` fences — устойчиво к «болтливым» локальным моделям.
2. **Healthcheck timeout:** 2.0 s на `GET /api/tags` — быстрый fail при недоступном Ollama.
3. **Verify script flow:** pre-scan без LLM → 6× прямой вызов `LLMResolver` с latency → полный `match_order_decisions` + `print_match_summary`.
4. **Zero-Loss:** скрипт завершается с exit code 1, если `NEEDS_LLM > 0` после полного прогона.

## Ollama Verification (6 NEEDS_LLM positions)

| № | 1С 7.7 | Код v8 | Latency | Confidence | Result |
|---|--------|--------|---------|------------|--------|
| 14 | Йорк Полка навесная 1/1 Кашемир | 00000108182 | 38.95s | 0.93 | MATCHED_LLM |
| 15 | Йорк Стекло 4мм 839х372 +8 отв. | 00000092203 | 31.52s | 0.94 | MATCHED_LLM |
| 17 | Йорк Стекло 5мм 350х305 полировка | 00000078249 | 26.60s | 0.95 | MATCHED_LLM |
| 52 | Стекло 4мм 579х372 +6 отв. | 00000092205 | 31.67s | 0.92 | MATCHED_LLM |
| 53 | Стекло 4мм 839х372 +8 отв. | 00000092203 | 34.53s | 0.92 | MATCHED_LLM |
| 55 | Стекло 5мм 350х305 | 00000078249 | 27.41s | 0.90 | MATCHED_LLM |

Средняя latency: ~31.8 s/запрос (CPU inference, qwen2.5:7b).

## Full Pipeline (`order_ruban.xlsx`, LLM_PROVIDER=ollama)

| Status | Count | % |
|--------|-------|---|
| MATCHED_AUTO | 47 | 85.5% |
| MATCHED_LLM | 6 | 10.9% |
| QUARANTINE | 2 | 3.6% |
| **Total** | **55** | 100% |

QUARANTINE: строки #1–2 (столешницы КДР 40мм — нет точного совпадения в v8).

**Критерии приёмки: OK** — все целевые диапазоны совпали с Gemini baseline (Sprint 4).

## Test Results

```
pytest tests/ -v  → 53 passed (Sprints 1–4.1)
python scripts/verify_ollama_integration.py  → exit 0 (~293 s)
```

## Next Sprint Preview

- Sprint 5: WMS Excel export с openpyxl string formatting для штрихкодов
