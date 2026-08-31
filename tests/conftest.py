"""Pytest configuration and shared session-scoped catalog / FAISS fixtures."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "data"
CATALOG_V8_PATH = DATA_DIR / "catalog_v8.xlsx"
ORDER_RUBAN_PATH = DATA_DIR / "orders" / "order_ruban.xlsx"
PYTEST_FAISS_CACHE = PROJECT_ROOT / ".cache_pytest"


@pytest.fixture(scope="session", autouse=True)
def _block_live_gemini_http():
    """Fail fast if a test accidentally constructs a real Gemini client."""

    def _blocked(*_args, **_kwargs):
        raise RuntimeError("Live Gemini HTTP is blocked in pytest; mock LLMResolver instead")

    with patch("src.matcher.llm_resolver.LLMResolver._gemini_client", side_effect=_blocked):
        yield


@pytest.fixture(scope="session")
def catalog_v8():
    if not CATALOG_V8_PATH.exists():
        pytest.skip("Real catalog file is missing in data/")
    from src.parsers.v8_loader import load_catalog_v8

    return load_catalog_v8(CATALOG_V8_PATH)


@pytest.fixture(scope="session")
def vocabulary(catalog_v8):
    from src.matcher.dynamic_vocab import DynamicVocabulary

    return DynamicVocabulary(catalog_v8)


@pytest.fixture(scope="session")
def feature_extractor(vocabulary):
    from src.matcher.feature_extractor import FeatureExtractor

    return FeatureExtractor(vocabulary)


@pytest.fixture(scope="session")
def vector_store(catalog_v8):
    from src.matcher.vector_store import CatalogVectorStore

    store = CatalogVectorStore(cache_dir=str(PYTEST_FAISS_CACHE))
    store.build_or_load_index(catalog_v8)
    return store


@pytest.fixture(scope="session")
def hybrid_matcher(vector_store, feature_extractor):
    from src.matcher.hybrid_matcher import HybridMatcher

    return HybridMatcher(vector_store, feature_extractor)


@pytest.fixture(scope="session")
def order_ruban_parsed():
    if not ORDER_RUBAN_PATH.exists():
        pytest.skip("Real order file is missing in data/orders/")
    from src.parsers.v7_parser import parse_v7_order

    return parse_v7_order(ORDER_RUBAN_PATH)
