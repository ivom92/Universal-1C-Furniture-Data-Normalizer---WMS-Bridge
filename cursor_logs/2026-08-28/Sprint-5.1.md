# Sprint 5.1 — Streamlit Watcher Fix & High-Contrast Results Table

**Date:** 2026-08-28  
**Status:** Completed

## Scope

- Eliminate Streamlit file-watcher console spam (`torchvision` / `transformers` tracebacks)
- Redesign results table for dark-theme readability with native `st.dataframe` + `column_config`
- Add status filter tabs and improved quarantine warning block

## Files Created / Changed

| File | Action |
|------|--------|
| `.streamlit/config.toml` | **New** — server poll watcher, theme, folder blacklist |
| `app_ui.py` | Removed pandas Styler; tabs, column_config, quarantine UX |

## Key Design Decisions

1. **`fileWatcherType = "poll"`** — avoids Streamlit's aggressive module introspection that triggers `torchvision` import errors inside `venv/site-packages/transformers`.
2. **`folderWatchBlacklist`** — excludes `venv`, `.cache`, `__pycache__` from watch scope.
3. **Native dataframe rendering** — dropped low-contrast row background Styler; relies on theme `textColor = "#FAFAFA"` and Streamlit's built-in dark table styling.
4. **Status badges in-cell** — emoji prefixes (`🟢 Авто`, `🔵 LLM`, `🟡 Карантин`) for instant visual scanning without custom CSS.
5. **Dynamic filter tabs** — counts computed from actual `MatchDecision` statuses, not hardcoded.

## UI Changes

| Element | Before | After |
|---------|--------|-------|
| Table styling | pandas Styler pastel rows | Native `st.dataframe` + `column_config` |
| Status column | Plain text | Emoji badges |
| Filtering | None | 4 tabs (All / Auto / LLM / Quarantine) |
| Quarantine block | Generic list | Numbered items + warehouse handoff message |
| Download button | Plain label | `📥` icon + primary type |

## Test Results

```
pytest tests/ -v  →  54 passed (unchanged)
```

## Running the UI

```powershell
.\venv\Scripts\streamlit run app_ui.py
```

Console should be free of repeated `ModuleNotFoundError: No module named 'torchvision'` tracebacks.
