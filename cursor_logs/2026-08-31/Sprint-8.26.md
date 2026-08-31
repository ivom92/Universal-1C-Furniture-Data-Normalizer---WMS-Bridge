# Sprint 8.26 — Production Web Hardening & PIN Auth
**Date:** 2026-08-31  
**Status:** ✅ DONE — 278 tests pass (248 baseline + 30 new)

---

## Scope
Protect the publicly-exposed `wms-bridge.duckdns.org` from unauthorized access and bot abuse.
Harden Streamlit WebSocket/CORS/XSRF config for stable operation behind Traefik / Coolify.

---

## Files Created / Changed

| File | Action | Description |
|------|--------|-------------|
| `src/config.py` | **CREATED** | Pydantic v2 `AppConfig` + `get_config()` with `WAREHOUSE_PIN` field |
| `src/utils/auth.py` | **CREATED** | `verify_pin`, `is_auth_required`, `BruteForceProtector` |
| `app_ui.py` | **MODIFIED** | Auth gate in `main()`, `_render_pin_screen()`, logout button in sidebar |
| `.streamlit/config.toml` | **MODIFIED** | Added `maxUploadSize = 50`, `enableXsrfProtection = true` |
| `tests/test_auth.py` | **CREATED** | 30 new tests for all auth logic |

---

## Key Design Decisions

### 1. `src/config.py` — Pydantic v2 with `str_strip_whitespace`
- `AppConfig` uses `ConfigDict(populate_by_name=True, str_strip_whitespace=True)`.
- Strip is critical: a whitespace-only `WAREHOUSE_PIN` must be treated as "not set" (no auth).
- `get_config()` instantiates fresh from `os.environ` each call — no module-level caching that would break `monkeypatch` in tests.

### 2. `src/utils/auth.py` — Zero Streamlit Dependency
- `verify_pin(input_pin, target_pin)` uses `hmac.compare_digest` — constant-time comparison prevents timing-based side-channel attacks.
- `is_auth_required()` reads `get_config().warehouse_pin` — a single source of truth.
- `BruteForceProtector` is a plain Python class stored in `st.session_state["_auth_protector"]`; it uses `time.monotonic()` for lockout tracking. No Streamlit imports inside the module.
- Lockout is **session-scoped**: one browser tab cannot lock another.

### 3. `app_ui.py` — Auth Gate Before Any Rendering
- The gate runs immediately after `st.set_page_config()`, before the `with st.sidebar:` block and all tabs.
- If `is_auth_required()` and not `st.session_state["authenticated"]` → `_render_pin_screen()` + `st.stop()` — sidebar and content never render.
- `_render_pin_screen()` uses a Streamlit `st.form` (prevents double-submit), shows remaining attempt count, and auto-refreshes (`time.sleep(1)` + `st.rerun()`) during active lockout.
- Logout button added to sidebar only when `is_auth_required()` is True (hidden in local dev / offline mode).

### 4. `.streamlit/config.toml` — Production Hardening
- `enableXsrfProtection = true` — re-enabled for reverse-proxy deployments.
- `maxUploadSize = 50` — caps uploads at 50 MB to prevent quota abuse.
- `primaryColor` normalized to lowercase `#2e7d32` (cosmetic consistency).

### 5. Graceful No-Auth Mode
- When `WAREHOUSE_PIN` is empty (local dev, CI, offline): `is_auth_required()` returns `False`, the gate is completely bypassed. All 248 baseline tests continue to run without any authentication friction.

---

## Test Results

```
pytest tests/ -q
278 passed, 1 warning in 56.72s
```

### New tests (`tests/test_auth.py`) — 30 tests

| Group | Tests | Result |
|-------|-------|--------|
| `TestVerifyPin` | 7 | ✅ |
| `test_timing_attack_safety` | 1 | ✅ |
| `TestIsAuthRequired` | 5 | ✅ |
| `TestBruteForceProtector` | 10 | ✅ |
| Regression / contract checks | 7 | ✅ |

---

## Challenges & Caveats

1. **Pydantic `str_strip_whitespace` not set by default** — `AppConfig` initially let `"   "` pass as a truthy PIN. Fixed by adding `str_strip_whitespace=True` to `ConfigDict`. Caught by the test `test_whitespace_only_pin_returns_false` (1 initial failure, fixed immediately).

2. **Streamlit countdown auto-refresh** — `_render_pin_screen()` calls `time.sleep(1)` + `st.rerun()` during lockout to update the countdown display. This consumes one server thread for 1 second per locked session — acceptable for a warehouse-scale deployment with limited concurrent users.

3. **XSRF re-enabled** — The previous value was `enableXsrfProtection = false`. Changing to `true` is correct for the Traefik/Coolify proxy setup but requires the proxy to forward the `Origin` header correctly (Traefik does this by default with `passHostHeader = true`).

4. **`monkeypatch` and `get_config()`** — Using fresh `os.environ.get()` in `get_config()` (no module-level cache) means `monkeypatch.setenv/delenv` works correctly in all tests without any `importlib.reload()` dance.

---

## Next Sprint Preview

- Sprint 8.27: Dockerfile `WAREHOUSE_PIN` ARG / ENV injection for Coolify `docker-compose.yml`.
- Sprint 8.28: Rate limiting at Traefik middleware level (fallback for bots bypassing the PIN screen).
