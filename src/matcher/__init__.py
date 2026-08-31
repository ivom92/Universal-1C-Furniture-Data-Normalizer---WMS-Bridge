"""Catalog matching engine."""

from src.matcher.dynamic_vocab import DynamicVocabulary
from src.matcher.feature_extractor import FeatureExtractor

__all__ = [
    "CatalogVectorStore",
    "DynamicVocabulary",
    "FeatureExtractor",
    "HybridMatcher",
]


def __getattr__(name: str):
    if name == "CatalogVectorStore":
        from src.matcher.vector_store import CatalogVectorStore

        return CatalogVectorStore
    if name == "HybridMatcher":
        from src.matcher.hybrid_matcher import HybridMatcher

        return HybridMatcher
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
