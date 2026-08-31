"""Warehouse readiness check: catalog, FAISS cache, LLM, and Python libs."""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

from rich.table import Table

from src.config import get_config
from src.llm.gemini_client import probe_gemini_models_list
from src.matcher.llm_resolver import LLMResolver
from src.utils.logger import console, get_logger

CATALOG_PATH = PROJECT_ROOT / "data" / "catalog_v8.xlsx"
CACHE_DIR = PROJECT_ROOT / ".cache"
FAISS_INDEX = CACHE_DIR / "catalog_faiss.index"
NUMPY_VECTORS = CACHE_DIR / "catalog_vectors.npy"
FAISS_META = CACHE_DIR / "catalog_meta.pkl"
EXPECTED_CATALOG_ROWS = 12880
CRITICAL_MODULES = (
    ("openpyxl", "openpyxl"),
    ("xlrd", "xlrd"),
    ("bs4", "beautifulsoup4"),
    ("sentence_transformers", "sentence-transformers"),
    ("streamlit", "streamlit"),
)


class CheckResult:
    def __init__(self, name: str, ok: bool, detail: str, *, critical: bool = True) -> None:
        self.name = name
        self.ok = ok
        self.detail = detail
        self.critical = critical


def _count_catalog_rows(path: Path) -> int:
    import openpyxl

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=False)
    try:
        worksheet = workbook.active
        count = 0
        for row in worksheet.iter_rows(min_row=2, values_only=True):
            if any(cell is not None and str(cell).strip() != "" for cell in row):
                count += 1
        return count
    finally:
        workbook.close()


def _check_catalog() -> CheckResult:
    if not CATALOG_PATH.exists():
        return CheckResult("Каталог 1С v8", False, f"Файл не найден: {CATALOG_PATH}")
    try:
        rows = _count_catalog_rows(CATALOG_PATH)
    except Exception as exc:
        return CheckResult("Каталог 1С v8", False, f"Не удалось прочитать: {exc}")
    if rows == EXPECTED_CATALOG_ROWS:
        return CheckResult("Каталог 1С v8", True, f"{CATALOG_PATH.name}: {rows} строк")
    return CheckResult(
        "Каталог 1С v8",
        True,
        f"{CATALOG_PATH.name}: {rows} строк (ожидалось {EXPECTED_CATALOG_ROWS})",
        critical=False,
    )


def _format_vector_count(count: int) -> str:
    return f"{count:,}".replace(",", " ")


def _check_vector_engine() -> CheckResult:
    if not FAISS_META.exists():
        return CheckResult(
            "Векторный движок",
            True,
            f"Нет файлов в {CACHE_DIR} — индекс построится при первом запуске",
            critical=False,
        )
    try:
        from src.matcher.vector_store import FAISS_AVAILABLE, read_faiss_index

        vectors = 0
        if FAISS_AVAILABLE and FAISS_INDEX.exists():
            index = read_faiss_index(FAISS_INDEX)
            vectors = int(index.ntotal)
            detail = f"FAISS C++ Engine ({_format_vector_count(vectors)} векторов)"
        elif NUMPY_VECTORS.exists():
            import numpy as np

            matrix = np.load(NUMPY_VECTORS, mmap_mode="r")
            vectors = int(matrix.shape[0])
            detail = f"NumPy Vector Engine ({_format_vector_count(vectors)} векторов, fallback)"
        elif FAISS_INDEX.exists():
            return CheckResult(
                "Векторный движок",
                True,
                "FAISS-индекс есть, но C++ DLL недоступен — выполните прогрев (--warm)",
                critical=False,
            )
        else:
            return CheckResult(
                "Векторный движок",
                True,
                f"Нет векторного кэша в {CACHE_DIR} — индекс построится при первом запуске",
                critical=False,
            )
    except Exception as exc:
        return CheckResult("Векторный движок", False, f"Индекс повреждён: {exc}")
    if vectors <= 0:
        return CheckResult("Векторный движок", False, "Пустой векторный индекс")
    if EXPECTED_CATALOG_ROWS and vectors != EXPECTED_CATALOG_ROWS:
        return CheckResult("Векторный движок", True, detail, critical=False)
    return CheckResult("Векторный движок", True, detail)


def _check_llm() -> CheckResult:
    provider = os.environ.get("LLM_PROVIDER", "gemini").strip().lower()
    resolver = LLMResolver(provider=provider)
    if provider == "ollama":
        if not resolver.is_available():
            return CheckResult("LLM (Ollama)", False, f"Нет ответа {resolver.ollama_base_url}")
        if not resolver.has_ollama_model():
            return CheckResult(
                "LLM (Ollama)",
                False,
                f"Модель {resolver.ollama_model} не найдена в /api/tags",
            )
        return CheckResult("LLM (Ollama)", True, f"{resolver.ollama_model} на {resolver.ollama_base_url}")

    keys = get_config().gemini_api_keys
    if not keys:
        return CheckResult(
            "Gemini API",
            False,
            "Не задан GEMINI_API_KEYS или GEMINI_API_KEY в .env",
        )

    model = resolver.gemini_model
    proxy_url = resolver.gemini_base_url
    try:
        response = probe_gemini_models_list(keys[0], base_url=proxy_url, timeout=8.0)
        if response.status_code >= 400:
            return CheckResult(
                "Gemini API",
                False,
                f"API {response.status_code}: ключ или квота недоступны",
            )
        detail = f"Найдено ключей: {len(keys)} (модель: {model})"
        if proxy_url:
            detail += f", прокси: {proxy_url}"
        return CheckResult("Gemini API", True, detail)
    except Exception as exc:
        return CheckResult("Gemini API", False, f"Сеть: {exc}")


def _run_llm_deep_check() -> CheckResult:
    """Run full per-key Gemini diagnostic from ``test_gemini_connection``."""
    try:
        from scripts.test_gemini_connection import main as gemini_connection_main

        gemini_connection_main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        if code == 0:
            return CheckResult("Gemini Deep Check", True, "Пинг пула и JSON-контракт OK")
        return CheckResult("Gemini Deep Check", False, f"Диагностика завершилась с кодом {code}")
    except Exception as exc:
        return CheckResult("Gemini Deep Check", False, str(exc))
    return CheckResult("Gemini Deep Check", True, "Пинг пула и JSON-контракт OK")


def _check_libraries() -> list[CheckResult]:
    results: list[CheckResult] = []
    for module_name, display in CRITICAL_MODULES:
        try:
            importlib.import_module(module_name)
            results.append(CheckResult(f"Библиотека {display}", True, "импорт OK"))
        except Exception as exc:
            results.append(CheckResult(f"Библиотека {display}", False, str(exc)))
    py_ok = sys.version_info >= (3, 11)
    results.append(
        CheckResult(
            "Python",
            py_ok,
            f"{sys.version.split()[0]} (нужен 3.11+)",
        )
    )
    return results


def _warm_vector_cache() -> CheckResult:
    try:
        from src.parsers.v8_loader import load_catalog_v8
        from src.matcher.vector_store import CatalogVectorStore

        get_logger().info("Warming vector cache from %s", CATALOG_PATH)
        catalog = load_catalog_v8(CATALOG_PATH)
        store = CatalogVectorStore(cache_dir=str(CACHE_DIR))
        store.build_or_load_index(catalog)
        engine = store.engine_name
        return CheckResult(
            "Прогрев векторного кэша",
            True,
            f"{engine}: {len(catalog)} векторов в {CACHE_DIR.name}",
        )
    except Exception as exc:
        return CheckResult("Прогрев векторного кэша", False, str(exc))


def main() -> None:
    parser = argparse.ArgumentParser(description="Warehouse readiness check")
    parser.add_argument(
        "--warm",
        action="store_true",
        help="Построить или загрузить FAISS-индекс (прогрев кэша при установке)",
    )
    parser.add_argument(
        "--llm-deep",
        action="store_true",
        help="Глубокая проверка Gemini: пинг каждого ключа и JSON-контракт",
    )
    args = parser.parse_args()

    console.print("[bold]Проверка готовности склада[/bold] — 1C WMS Bridge")
    checks = [_check_catalog(), _check_vector_engine(), *_check_libraries(), _check_llm()]
    if args.warm:
        checks.append(_warm_vector_cache())
    if args.llm_deep:
        checks.append(_run_llm_deep_check())

    table = Table(title="Диагностика", show_lines=False)
    table.add_column("Проверка", style="bold")
    table.add_column("Статус")
    table.add_column("Детали")
    critical_fail = False
    warn_count = 0
    for item in checks:
        if item.ok and not item.critical:
            status = "[yellow]WARN[/yellow]"
            warn_count += 1
        elif item.ok:
            status = "[green]OK[/green]"
        else:
            status = "[red]FAIL[/red]"
            if item.critical:
                critical_fail = True
        table.add_row(item.name, status, item.detail)
    console.print(table)

    if critical_fail:
        console.print("[red]Система не готова к работе.[/red] Исправьте FAIL-пункты.")
        raise SystemExit(1)
    if warn_count:
        console.print("[yellow]Система готова с замечаниями (WARN).[/yellow]")
    else:
        console.print("[green]Система готова к работе.[/green]")


if __name__ == "__main__":
    main()
