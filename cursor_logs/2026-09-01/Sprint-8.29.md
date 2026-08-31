# Sprint 8.29 — Unified AppConfig & Cloudflare Gemini Proxy
**Date:** 2026-09-01  
**Status:** ⚠️ PARTIAL — 300 tests pass; acceptance one-liner OK; live Gemini ping FAIL (401 AQ. keys / proxy)

---

## Scope
Восстановить полный `AppConfig` в `src/config.py` (после неполной реализации Sprint 8.26), централизовать чтение `GEMINI_API_KEYS`, `GEMINI_BASE_URL` и связанных переменных, обеспечить маршрутизацию всех Gemini HTTP-вызовов через Cloudflare Worker proxy.

---

## Files Created / Changed

| File | Action | Description |
|------|--------|-------------|
| `src/config.py` | **MODIFIED** | Полный `AppConfig`: PIN, LLM, Gemini keys/model/proxy, Telegram; property `gemini_api_keys`; `load_dotenv()` при import |
| `src/llm/gemini_client.py` | **MODIFIED** | `resolve_gemini_base_url()` через `get_config()`; probe/build default to configured proxy |
| `src/matcher/llm_resolver.py` | **MODIFIED** | Defaults из `get_config()`; proxy URL из `resolve_gemini_base_url` |
| `src/matcher/key_rotator.py` | **MODIFIED** | `parse_gemini_api_keys()` делегирует в `get_config().gemini_api_keys` |
| `scripts/test_gemini_connection.py` | **MODIFIED** | Диагностика через `get_config()` |
| `scripts/check_system_health.py` | **MODIFIED** | `_check_llm()` через `get_config().gemini_api_keys` |
| `tests/test_config.py` | **CREATED** | 6 тестов AppConfig / get_config |
| `tests/test_gemini_client.py` | **MODIFIED** | Тест default proxy из config |
| `tests/test_llm_resolver.py` | **MODIFIED** | Default model `gemini-3.5-flash-lite` |
| `tests/test_auth.py` | **MODIFIED** | Assert `gemini_api_keys` / `gemini_base_url` на AppConfig |

---

## Key Design Decisions

1. **`get_config()` читает только `os.environ`.** `load_dotenv()` вызывается один раз при import `src.config`, чтобы monkeypatch в pytest не перезаписывался повторным `load_dotenv()` внутри `get_config()`.
2. **`resolve_gemini_base_url()` — единая точка proxy URL** в `gemini_client.py`; re-export через `llm_resolver` для обратной совместимости.
3. **`build_gemini_client` без явного `base_url`** автоматически подставляет `get_config().gemini_base_url` в `HttpOptions`.
4. **`gemini_api_keys` property** парсит `GEMINI_API_KEYS` (comma-separated) с fallback на `GEMINI_API_KEY`.

---

## Test Results

```
pytest tests/ -q
300 passed, 1 warning in ~42s
```

### Acceptance one-liner

```
python -c "from src.config import get_config; print('KEYS:', len(get_config().gemini_api_keys)); print('PROXY:', get_config().gemini_base_url)"
→ KEYS: 3
→ PROXY: https://gemini-proxy-warehouse.mokshin17.workers.dev
```

### Live Gemini check

```
python scripts/test_gemini_connection.py
→ FAIL — 3/3 keys HTTP 401 через proxy (ACCESS_TOKEN_TYPE_UNSUPPORTED)
```

Proxy URL корректно отображается и используется в запросах; ошибка auth сохраняется (AQ.-ключи / Worker forwarding — см. Sprint 8.28 caveats).

---

## Challenges & Caveats

1. **Live ping не зелёный** — клиент и config исправлены, но Google API / Cloudflare Worker по-прежнему возвращают 401 для AQ.-ключей. Требуется фикс Worker (проброс `x-goog-api-key` без Bearer) и/или эскалация в Google.
2. **CLI-прогон `run_order.py`** не выполнен — в локальной копии отсутствуют `data/orders/` и `data/catalog_v8.xlsx`.
3. **Default Gemini model** в тестах обновлён на `gemini-3.5-flash-lite` (соответствует `.env` / AppConfig default).

---

## Next Sprint Preview
- Cloudflare Worker: forward `x-goog-api-key` as-is; повторный live ping.
- CLI-прогон на сервере с data-фикстурами после восстановления Gemini auth.
