# Sprint 8.28 — Hotfix: Gemini AQ. Keys Auth (`ACCESS_TOKEN_TYPE_UNSUPPORTED`)
**Date:** 2026-09-01  
**Status:** ⚠️ PARTIAL — 292 tests pass; live Gemini ping FAIL; CLI-прогон не выполнен (нет `data/orders/` в рабочей копии)

---

## Scope
Исправить аутентификацию Gemini API для новых ключей Google AI Studio с префиксом `AQ.`: убрать передачу ключа как `Authorization: Bearer`, использовать нативный Gemini auth через `x-goog-api-key` / `?key=`.

---

## Files Created / Changed

| File | Action | Description |
|------|--------|-------------|
| `src/llm/__init__.py` | **CREATED** | Пакет LLM-клиентов |
| `src/llm/gemini_client.py` | **CREATED** | `gemini_auth_headers`, `gemini_auth_query_params`, `build_gemini_client`, `probe_gemini_models_list`, `gemini_models_list_url` |
| `src/matcher/llm_resolver.py` | **MODIFIED** | Импорт `build_gemini_client` / `gemini_models_list_url` из `src.llm.gemini_client` |
| `src/matcher/key_rotator.py` | **MODIFIED** | `KeyPool.test_connection()` через `probe_gemini_models_list` |
| `scripts/test_gemini_connection.py` | **MODIFIED** | Пинг пула через централизованный probe |
| `scripts/check_system_health.py` | **MODIFIED** | Gemini health через `probe_gemini_models_list` (без прямого `httpx` + `params`) |
| `tests/test_gemini_client.py` | **CREATED** | Контракт: `x-goog-api-key` present, `Authorization: Bearer` absent |
| `tests/test_key_rotator.py` | **MODIFIED** | Мок `probe_gemini_models_list` вместо `httpx.Client` |
| `tests/test_system_health.py` | **MODIFIED** | Мок `probe_gemini_models_list` в `_check_llm` |
| `tests/test_llm_resolver.py` | **MODIFIED** | Assert `http_options.headers["x-goog-api-key"]` в `TestGeminiProxyBaseUrl` |

---

## Key Design Decisions

1. **Единая точка auth — `src/llm/gemini_client.py`.** Все httpx-пробы и `google.genai.Client` получают ключ через `gemini_auth_headers()`; Bearer нигде не формируется.
2. **`build_gemini_client` явно прокидывает `x-goog-api-key` в `HttpOptions.headers`.** Помимо `api_key=` в конструкторе SDK — двойная гарантия для AQ.-ключей и Cloudflare reverse-proxy.
3. **`probe_gemini_models_list` — только header auth.** Query `?key=` оставлен в helper `gemini_auth_query_params()` для REST/curl, но probe использует header, чтобы не дублировать credentials на прокси.
4. **Обратная совместимость импортов.** `build_gemini_client` и `gemini_models_list_url` по-прежнему доступны через `src.matcher.llm_resolver` (re-export через import).

---

## Test Results

```
pytest tests/ -q
292 passed, 1 warning in ~40s
```

### Новые тесты (`tests/test_gemini_client.py`)

| Test | Result |
|------|--------|
| `test_auth_headers_use_x_goog_api_key_not_bearer` | ✅ |
| `test_client_uses_x_goog_api_key_in_http_options` | ✅ |
| `test_probe_sends_native_auth_not_bearer` | ✅ |

---

## Live / Acceptance Checks

### Gemini deep check

```
python scripts/test_gemini_connection.py
→ FAIL — все 3 ключа: HTTP 401 ACCESS_TOKEN_TYPE_UNSUPPORTED
```

Дополнительная диагностика (curl/httpx, без SDK):
- `x-goog-api-key` → `ACCESS_TOKEN_TYPE_UNSUPPORTED`
- `?key=` → `ACCESS_TOKEN_TYPE_UNSUPPORTED`
- `Authorization: Bearer` → `API_KEY_SERVICE_BLOCKED` (другой reason — Google распознаёт Bearer как API key, но блокирует сервис)

Ошибка воспроизводится **напрямую** к `generativelanguage.googleapis.com`, не только через `GEMINI_BASE_URL` proxy.

### CLI-прогон (обязательный по `.cursorrules`)

```
python scripts/run_order.py "data/orders/order_transfering_01_09.xls"
→ НЕ ВЫПОЛНЕН: в рабочей копии отсутствуют файлы `data/orders/*.xls(x)` и `data/catalog_v8.xlsx`
```

---

## Challenges & Caveats

1. **Отчёт не был создан сразу после hotfix** — нарушение протокола `.cursorrules`: при падении acceptance-критериев отчёт всё равно обязан быть создан с секцией Challenges & Caveats. Исправлено этим файлом `cursor_logs/2026-09-01/Sprint-8.28.md`.
2. **Клиентский hotfix выполнен, но live Gemini ping не зелёный.** Код больше не отправляет Bearer; SDK capture показывает только `x-goog-api-key`. 401 сохраняется — вероятно комбинация: (a) backend-проблема Google с AQ.-ключами на аккаунте; (b) Cloudflare Worker (`gemini-proxy-warehouse.mokshin17.workers.dev`) может конвертировать header → Bearer при форвардинге (Worker не в репозитории).
3. **Критерий приёмки TZ п.1 не выполнен** (`🟢 Key 1/2/3: OK`). Требуется проверка/фикс Worker + эскалация в Google AI Support при сохранении 401 на direct API.
4. **CLI-прогон заблокирован отсутствием data-фикстур** в локальной копии; на прод-сервере с каталогом и заказом прогон нужно повторить отдельно.

---

## Next Sprint Preview
- Sprint 8.29: Cloudflare Worker — проброс `x-goog-api-key` as-is (без `Authorization: Bearer`); повторный live ping + CLI на сервере с data/.
- При сохранении 401 на direct Google API — curl-репрод в Google AI Developers Forum для AQ.-ключей.
