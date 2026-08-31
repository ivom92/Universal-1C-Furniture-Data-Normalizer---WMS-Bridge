# Sprint 8.27 — Warehouse Operator Sidebar Redesign & Server Lockdown
**Date:** 2026-08-31  
**Status:** ✅ DONE — 284 tests pass (278 baseline + 6 new)

---

## Scope
Make the Streamlit sidebar operator-first after the Coolify move: remove the dangerous process-kill control, hide engineering diagnostics behind a collapsed expander, and leave the warehouse operator with compact readiness status plus two working actions.

---

## Files Created / Changed

| File | Action | Description |
|------|--------|-------------|
| `app_ui.py` | **MODIFIED** | Extracted `_render_sidebar()`, removed `_shutdown_server` / `os._exit` / `SIGTERM`, operator-first layout |
| `tests/test_ui_contracts.py` | **MODIFIED** | Inverted shutdown contract; updated session-reset label; added `TestOperatorSidebar` (6 tests) |

---

## Key Design Decisions

### 1. Kill-server control removed (Coolify lockdown)
- Deleted `_shutdown_server()`, the `import signal` dependency, `os.kill(..., SIGTERM)`, and the `os._exit(0)` fallback.
- The Windows-8.1 file-lock caption is gone. Stopping the hosted process is an ops action (Coolify / Traefik), not an operator button.

### 2. `_render_sidebar()` — three-block operator layout
1. **Warehouse status** — `### 📦 Мебельный Склад` plus compact `st.caption` badges (`Каталог 1С`, `ИИ-Ассистент: Активен (FAISS + E5)`). No full-width `st.success` banner.
2. **Operator actions** — single primary `➕ Начать новый заказ` (`_clear_active_view()` + `st.rerun()`); `🔒 Выйти / Сменить смену` only when PIN auth is enabled.
3. **Engineering expander** — `st.expander("🛠️ Инженерная диагностика", expanded=False)` keeps Gemini/Ollama radio, key-pool ping, `mask_secret()` preview, session UUID, and the WMS 5-column contract.

### 3. Duplicate reset buttons removed
Header `🧹 Очистить сессию` and sidebar `🧹 Сбросить` / `🔄 Новый заказ` collapsed into one operator action. `test_app_ui_has_logout_button` still matches because the label contains `🔒 Выйти`.

### 4. LLM provider still drives the pipeline
The radio lives inside the collapsed expander. `load_pipeline(provider)` reads `st.session_state["sidebar_llm_provider"]` *before* the widget renders so a Coolify rerun uses the last chosen provider without exposing the control on the operator surface.

---

## Test Results

```
pytest tests/ -q
284 passed, 1 warning in 42.66s
```

CLI:

```
python scripts/run_order.py "data/orders/order_transfering_01_09.xls"
Всего строк: 384
Авто-сопоставлено (MATCHED_AUTO): 377 (98.2%)
В карантине (QUARANTINE): 7 (1.8%)
Восстановлено заводских штрихкодов: 346 шт.
[Profiler] 22.23s (Parse=0.42s, Exact=1.12s, FAISS=15.89s, LLM=3.45s, Excel=0.16s)
```

### New / updated tests (`tests/test_ui_contracts.py`)

| Test | Result |
|------|--------|
| `test_app_ui_has_no_server_shutdown_control` (was `test_app_ui_has_shutdown_button`) | ✅ inverted contract |
| `TestSessionIsolation.test_app_ui_has_clear_session_button` | ✅ now asserts `Начать новый заказ` |
| `TestOperatorSidebar` (6 tests) | ✅ expander, labels, diagnostics, no duplicates, catalog format |

---

## Challenges & Caveats

1. **Inverted source-level contract** — Sprint 8.16 required `Завершить работу сервера` + `_shutdown_server` + `SIGTERM`/`os._exit` in `app_ui.py`. That test would have failed the lockdown. It is now a negative assertion. Matching cascade was not touched.

2. **Streamlit widget-before-render** — Catalog status is above the expander, but `st.radio` is inside it. Provider is taken from `session_state["sidebar_llm_provider"]` first so `load_pipeline` uses the correct backend on every rerun.

3. **TZ path vs actual file** — The brief named `src/app_ui.py`; the Streamlit entrypoint remains repo-root `app_ui.py`. Changes were applied there.

---

## Next Sprint Preview
- Optional: persist last LLM provider in a Coolify env default so the expander radio does not reset after a container restart.
- Optional: add a compact shift-summary caption (orders / places today) to the operator status block without opening History.
