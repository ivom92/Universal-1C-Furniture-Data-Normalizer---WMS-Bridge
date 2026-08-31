"""FAISS vector store with multilingual-e5-small embeddings and disk cache."""

from __future__ import annotations

import hashlib
import pickle
import threading
from pathlib import Path
from typing import Optional, Union

import numpy as np
from sentence_transformers import SentenceTransformer

from src.models import CatalogEntity
from src.preprocessor.normalizer import canonicalize_dimensions
from src.utils.logger import console, get_logger

logger = get_logger()

try:
    import faiss

    FAISS_AVAILABLE = True
except (ImportError, Exception):
    faiss = None  # type: ignore[assignment]
    FAISS_AVAILABLE = False

_CACHE_VERSION = 2
_INDEX_FILENAME = "catalog_faiss.index"
_NUMPY_VECTORS_FILENAME = "catalog_vectors.npy"
_META_FILENAME = "catalog_meta.pkl"
_PASSAGE_PREFIX = "passage: "
_QUERY_PREFIX = "query: "
_ENCODE_BATCH_SIZE = 64
_MODEL_LOCK = threading.Lock()
_SHARED_MODELS: dict[str, SentenceTransformer] = {}


def _normalize_l2(vectors: np.ndarray) -> None:
    """In-place L2 normalization compatible with FAISS IndexFlatIP."""
    if FAISS_AVAILABLE:
        faiss.normalize_L2(vectors)
        return
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    np.divide(vectors, np.maximum(norms, 1e-12), out=vectors)


class NumpyVectorEngine:
    """Pure-NumPy cosine-similarity search over normalized embedding rows."""

    def __init__(self, vectors: np.ndarray) -> None:
        matrix = np.asarray(vectors, dtype=np.float32)
        if matrix.ndim != 2:
            raise ValueError(f"Expected 2-D vector matrix, got shape {matrix.shape}")
        self.vectors = matrix

    @property
    def ntotal(self) -> int:
        return int(self.vectors.shape[0])

    @property
    def d(self) -> int:
        return int(self.vectors.shape[1])

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Return (scores, indices) shaped like FAISS search output (1, k)."""
        scores = np.dot(self.vectors, query_vector.T).flatten()
        effective_k = min(top_k, len(scores))
        top_indices = np.argsort(scores)[::-1][:effective_k]
        top_scores = scores[top_indices]
        return top_scores.reshape(1, -1), top_indices.reshape(1, -1)

    def reconstruct(self, index: int) -> np.ndarray:
        return self.vectors[index].copy()


class CatalogVectorStore:
    """Local semantic index over the 1C v8 catalog using FAISS or NumPy fallback."""

    def __init__(
        self,
        cache_dir: str = ".cache",
        model_name: str = "intfloat/multilingual-e5-small",
    ) -> None:
        cache_path = Path(cache_dir)
        if not cache_path.is_absolute():
            project_root = Path(__file__).resolve().parents[2]
            cache_path = project_root / cache_path
        self._cache_dir = cache_path
        self._model_name = model_name
        self._index = None
        self._numpy_engine: Optional[NumpyVectorEngine] = None
        self._catalog: list[CatalogEntity] = []
        self._model: Optional[SentenceTransformer] = None
        self._search_cache: dict[tuple[str, int], list[tuple[CatalogEntity, float]]] = {}

    @property
    def catalog(self) -> list[CatalogEntity]:
        return list(self._catalog)

    @property
    def is_ready(self) -> bool:
        return self._vector_backend is not None and bool(self._catalog)

    @property
    def engine_name(self) -> str:
        if self._index is not None:
            return "FAISS C++ Engine"
        if self._numpy_engine is not None:
            return "NumPy Vector Engine"
        return "uninitialized"

    @property
    def vector_count(self) -> int:
        backend = self._vector_backend
        if backend is None:
            return 0
        return int(backend.ntotal)

    @property
    def _vector_backend(self) -> Optional[Union["faiss.IndexFlatIP", NumpyVectorEngine]]:
        if self._index is not None:
            return self._index
        return self._numpy_engine

    def build_or_load_index(
        self,
        catalog: list[CatalogEntity],
        force_rebuild: bool = False,
    ) -> None:
        """Build a new vector index or load a valid on-disk cache."""
        index_path = self._cache_dir / _INDEX_FILENAME
        meta_path = self._cache_dir / _META_FILENAME
        numpy_path = self._cache_dir / _NUMPY_VECTORS_FILENAME
        fingerprint = _catalog_fingerprint(catalog)
        self._search_cache.clear()

        if (
            not force_rebuild
            and meta_path.exists()
            and _cache_is_valid(meta_path, fingerprint, self._model_name, len(catalog))
            and (
                (FAISS_AVAILABLE and index_path.exists())
                or numpy_path.exists()
            )
        ):
            self._load_from_cache(index_path, meta_path, numpy_path)
            engine_label = self.engine_name
            console.print(
                f"[green]Loaded {engine_label} from cache[/green] "
                f"({len(self._catalog)} items, model={self._model_name})"
            )
            return

        engine_label = "FAISS index" if FAISS_AVAILABLE else "NumPy vector index"
        console.print(
            f"[yellow]Building {engine_label}[/yellow] "
            f"({len(catalog)} catalog items, model={self._model_name})..."
        )
        passages = [_passage_text(item) for item in catalog]
        model = self._get_model()
        embeddings = model.encode(
            passages,
            batch_size=_ENCODE_BATCH_SIZE,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=False,
        ).astype(np.float32)
        _normalize_l2(embeddings)

        self._catalog = list(catalog)
        if FAISS_AVAILABLE:
            index = faiss.IndexFlatIP(embeddings.shape[1])
            index.add(embeddings)
            self._index = index
            self._numpy_engine = None
        else:
            logger.info(
                "[VectorStore] FAISS недоступен, активирован сверхбыстрый NumPy Vector Engine"
            )
            self._numpy_engine = NumpyVectorEngine(embeddings)
            self._index = None

        self._save_to_cache(index_path, meta_path, numpy_path, fingerprint)
        console.print(
            f"[green]{engine_label} built and cached[/green] ({len(catalog)} items)"
        )

    def search(self, query_text: str, top_k: int = 20) -> list[tuple[CatalogEntity, float]]:
        """Return top-k catalog entities ranked by cosine similarity."""
        backend = self._vector_backend
        if backend is None or not self._catalog:
            raise RuntimeError("Vector index is not initialized. Call build_or_load_index() first.")

        cache_key = (query_text.strip(), top_k)
        cached = self._search_cache.get(cache_key)
        if cached is not None:
            return list(cached)

        prefixed_query = _QUERY_PREFIX + query_text.strip()
        model = self._get_model()
        query_vector = model.encode(
            [prefixed_query],
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=False,
        ).astype(np.float32)
        _normalize_l2(query_vector)

        effective_k = min(top_k, len(self._catalog))
        scores, indices = backend.search(query_vector, effective_k)

        results: list[tuple[CatalogEntity, float]] = []
        for idx, score in zip(indices[0], scores[0], strict=True):
            if idx < 0:
                continue
            results.append((self._catalog[int(idx)], float(score)))
        self._search_cache[cache_key] = list(results)
        return results

    def _get_model(self) -> SentenceTransformer:
        if self._model is not None:
            return self._model
        with _MODEL_LOCK:
            cached = _SHARED_MODELS.get(self._model_name)
            if cached is None:
                cached = SentenceTransformer(self._model_name, device="cpu")
                _SHARED_MODELS[self._model_name] = cached
            self._model = cached
        return self._model

    def save(self) -> bool:
        """Persist vector index + catalog metadata via Unicode-safe Python IO."""
        if self._vector_backend is None:
            logger.error("Cannot save vector index: index is not initialized")
            return False
        try:
            self._save_to_cache(
                self._cache_dir / _INDEX_FILENAME,
                self._cache_dir / _META_FILENAME,
                self._cache_dir / _NUMPY_VECTORS_FILENAME,
                _catalog_fingerprint(self._catalog),
            )
            return True
        except Exception:
            logger.exception("Failed to save vector index to %s", self._cache_dir)
            return False

    def load(self) -> bool:
        """Load vector index + catalog metadata via Unicode-safe Python IO."""
        index_path = self._cache_dir / _INDEX_FILENAME
        meta_path = self._cache_dir / _META_FILENAME
        numpy_path = self._cache_dir / _NUMPY_VECTORS_FILENAME
        if not meta_path.exists():
            logger.error("Vector cache metadata missing in %s", self._cache_dir)
            return False
        if not ((FAISS_AVAILABLE and index_path.exists()) or numpy_path.exists()):
            logger.error("Vector cache files missing in %s", self._cache_dir)
            return False
        try:
            self._load_from_cache(index_path, meta_path, numpy_path)
            return True
        except Exception:
            logger.exception("Failed to load vector index from %s", self._cache_dir)
            return False

    def _load_from_cache(
        self,
        index_path: Path,
        meta_path: Path,
        numpy_path: Path,
    ) -> None:
        with meta_path.open("rb") as handle:
            meta = pickle.load(handle)
        self._catalog = [
            CatalogEntity.model_validate(item) for item in meta["catalog"]
        ]

        if FAISS_AVAILABLE and index_path.exists():
            self._index = read_faiss_index(index_path)
            self._numpy_engine = None
            if not numpy_path.exists():
                np.save(numpy_path, self._export_vectors())
            return

        if not numpy_path.exists():
            raise FileNotFoundError(f"NumPy vector cache missing: {numpy_path}")

        vectors = np.load(numpy_path)
        self._numpy_engine = NumpyVectorEngine(vectors)
        self._index = None
        if not FAISS_AVAILABLE:
            logger.info(
                "[VectorStore] FAISS недоступен, активирован сверхбыстрый NumPy Vector Engine"
            )

    def _save_to_cache(
        self,
        index_path: Path,
        meta_path: Path,
        numpy_path: Path,
        fingerprint: str,
    ) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        vectors = self._export_vectors()
        np.save(numpy_path, vectors)
        if FAISS_AVAILABLE and self._index is not None:
            write_faiss_index(self._index, index_path)
        meta = {
            "version": _CACHE_VERSION,
            "model_name": self._model_name,
            "catalog_count": len(self._catalog),
            "fingerprint": fingerprint,
            "catalog": [item.model_dump(by_alias=True) for item in self._catalog],
        }
        with meta_path.open("wb") as handle:
            pickle.dump(meta, handle, protocol=pickle.HIGHEST_PROTOCOL)

    def _export_vectors(self) -> np.ndarray:
        backend = self._vector_backend
        if backend is None:
            raise RuntimeError("Vector index is not initialized")
        if isinstance(backend, NumpyVectorEngine):
            return backend.vectors
        return np.vstack([backend.reconstruct(i) for i in range(backend.ntotal)])


def write_faiss_index(index: "faiss.Index", index_path: Path) -> None:
    """Write a FAISS index using Python file objects (Windows UTF-8 paths)."""
    if not FAISS_AVAILABLE:
        raise RuntimeError("FAISS is not available on this system")
    blob = np.asarray(faiss.serialize_index(index), dtype=np.uint8)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with open(index_path, "wb") as handle:
        handle.write(blob.tobytes())


def read_faiss_index(index_path: Path) -> "faiss.Index":
    """Read a FAISS index using Python file objects (Windows UTF-8 paths)."""
    if not FAISS_AVAILABLE:
        raise RuntimeError("FAISS is not available on this system")
    with open(index_path, "rb") as handle:
        data = np.frombuffer(handle.read(), dtype=np.uint8).copy()
    return faiss.deserialize_index(data)


def _passage_text(item: CatalogEntity) -> str:
    parts = [
        item.nomenclature,
        item.label_model,
        item.module,
        item.color,
        item.filling,
        item.packaging,
    ]
    body = canonicalize_dimensions(" ".join(part for part in parts if part))
    return _PASSAGE_PREFIX + body


def _catalog_fingerprint(catalog: list[CatalogEntity]) -> str:
    codes = sorted(entity.nomenclature_code for entity in catalog)
    digest = hashlib.sha256("\n".join(codes).encode("utf-8")).hexdigest()
    return digest


def _cache_is_valid(
    meta_path: Path,
    fingerprint: str,
    model_name: str,
    catalog_count: int,
) -> bool:
    try:
        with meta_path.open("rb") as handle:
            meta = pickle.load(handle)
    except (OSError, pickle.UnpicklingError):
        return False

    return (
        meta.get("version") == _CACHE_VERSION
        and meta.get("model_name") == model_name
        and meta.get("catalog_count") == catalog_count
        and meta.get("fingerprint") == fingerprint
    )
