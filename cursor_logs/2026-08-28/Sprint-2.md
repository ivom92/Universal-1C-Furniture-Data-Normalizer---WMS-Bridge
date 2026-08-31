# Sprint 2 — Development Log (2026-08-28)

## Scope
Feature extractor and dynamic knowledge base: UTF-8 logger, catalog-derived vocabulary, RegEx feature extraction, integration tests on 55 order blocks.

## Files Created
```
src/utils/
  __init__.py
  logger.py
src/matcher/
  __init__.py
  dynamic_vocab.py
  feature_extractor.py
tests/
  test_features.py
```

## `src/utils/logger.py`
- `sys.stdout.reconfigure(encoding='utf-8')` (+ stderr) to prevent `UnicodeEncodeError` on Windows cp1252 consoles.
- Shared `rich.console.Console` instance exported as `console`.

## `src/matcher/dynamic_vocab.py`
- `DynamicVocabulary(catalog: list[CatalogEntity])` builds at runtime:
  - `known_models` ← `label_model`
  - `known_colors` ← `color`
  - `known_materials` ← `filling` (Начинка)
  - `known_modules` ← `module`
  - `known_part_types` ← `label_type` + high-frequency first words from `nomenclature` (freq ≥ 15, excluding model names)
- Longest-match-first substring search with overlap prevention for vocabulary matching.

## `src/matcher/feature_extractor.py`
- `FeatureExtractor(vocabulary).extract_features(raw_block) -> ExtractedFeatures`
- **package_ratio:** `(\b\d+/\d+\b|Ун\d+/\d+)` with normalization (`Ун 1/1` → `Ун1/1`); fallback `Ун1/1` for `item_type=Стекло`, else `1/1`.
- **dimensions:** `\d+[,.]?\d*м` and `\d+(?:[хxX*×]\d+)+` with Latin `x` → `х`.
- **thicknesses:** `\d+мм`
- **matched_***: dynamic vocabulary lookups on combined `client_description + factory_alias` (any prefix: КДР, IMP, Пакет, or none).

## Pydantic Model Added (`src/models.py`)
- `ExtractedFeatures`: package_ratio, dimensions, thicknesses, matched_part_types/colors/models.

## Tests (`tests/test_features.py`)
- Package ratios: 1/1, 2/2, Ун1/1, 1/3.
- Dimensions: Latin `x`, linear meters `2,00м`, thickness `40мм`.
- Product types: столешница, Йорк Комод, Стекло 4мм, non-IMP alias prefix.
- Integration: all 55 blocks from `order_ruban.xlsx` have non-null `package_ratio`.

## Test Run
```bash
pytest tests/ -v
# 33 passed (17 Sprint 1 + 16 Sprint 2)
```

## Next Sprint (Preview)
- `vector_store.py` — multilingual-e5-small + FAISS indexing.
- Hard-constraint filter using extracted features.
- `llm_resolver.py` — Gemini / Ollama fallback.
