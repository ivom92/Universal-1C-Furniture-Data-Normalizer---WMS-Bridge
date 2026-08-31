"""Full-cycle CLI: parse v7.7 order → hybrid match → LLM fallback → Rich report."""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

from src.adapters.wms_excel_adapter import WMSExcelAdapter
from src.matcher.dynamic_vocab import DynamicVocabulary
from src.matcher.feature_extractor import FeatureExtractor
from src.matcher.hybrid_matcher import HybridMatcher
from src.matcher.llm_resolver import LLMResolver
from src.matcher.vector_store import CatalogVectorStore
from src.pipeline import log_order_profiler, process_order
from src.parsers.v8_loader import load_catalog_v8
from src.utils.logger import console, get_logger
from src.utils.reporter import print_match_summary
from src.utils.telemetry import (
    collect_system_info,
    flush_telemetry,
    notify_error,
    notify_order_processed,
    notify_startup,
    order_stats,
)

CATALOG_PATH = PROJECT_ROOT / "data" / "catalog_v8.xlsx"
ORDERS_DIR = PROJECT_ROOT / "data" / "orders"
DEFAULT_ORDER_PATH = ORDERS_DIR / "order_ruban.xlsx"
OUTPUT_DIR = PROJECT_ROOT / "output"


def resolve_order_path(explicit: str | None = None) -> Path:
    """Resolve a v7.7 order path, accepting both .xlsx and .xls files."""
    if explicit:
        candidate = Path(explicit)
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        return candidate

    if DEFAULT_ORDER_PATH.exists():
        return DEFAULT_ORDER_PATH

    xlsx_files = sorted(ORDERS_DIR.glob("*.xlsx"))
    xls_files = sorted(ORDERS_DIR.glob("*.xls"))
    candidates = xlsx_files + xls_files
    if candidates:
        return candidates[0]
    return DEFAULT_ORDER_PATH


def main() -> None:
    order_name = "unknown"
    try:
        order_name = _run() or order_name
    except SystemExit:
        raise
    except Exception as exc:
        notify_error(
            f"CLI run_order: {exc}",
            traceback.format_exc(),
            filename=order_name,
        )
        flush_telemetry()
        raise
    flush_telemetry()


def _run() -> str:
    parser = argparse.ArgumentParser(description="Parse a 1C v7.7 order (.xlsx/.xls) and match against v8.")
    parser.add_argument(
        "order",
        nargs="?",
        default=None,
        help="Путь к отборочному листу (.xlsx или .xls). По умолчанию data/orders/order_ruban.xlsx",
    )
    args = parser.parse_args()
    order_path = resolve_order_path(args.order)

    if not CATALOG_PATH.exists():
        console.print(f"[red]Каталог не найден:[/red] {CATALOG_PATH}")
        raise SystemExit(1)
    if not order_path.exists():
        console.print(f"[red]Заказ не найден:[/red] {order_path}")
        raise SystemExit(1)

    console.print("[bold]1C Furniture Data Normalizer & WMS Bridge[/bold] — полный цикл")
    console.print(f"Каталог: {CATALOG_PATH.name} | Заказ: {order_path.name}")
    console.print()

    catalog = load_catalog_v8(CATALOG_PATH)
    notify_startup(collect_system_info(catalog_size=len(catalog)))
    get_logger().info("CLI order=%s catalog=%s", order_path.name, len(catalog))
    vocabulary = DynamicVocabulary(catalog)
    feature_extractor = FeatureExtractor(vocabulary)
    vector_store = CatalogVectorStore(cache_dir=str(PROJECT_ROOT / ".cache"))
    vector_store.build_or_load_index(catalog)

    llm_resolver = LLMResolver()
    console.print(
        f"[dim]LLM провайдер:[/dim] {llm_resolver.provider.upper()} "
        f"({'облако Gemini' if llm_resolver.provider == 'gemini' else 'локальный Ollama'})"
    )

    matcher = HybridMatcher(vector_store, feature_extractor, llm_resolver=llm_resolver)
    started = time.perf_counter()
    _doc_type, parsed, decisions = process_order(order_path, matcher, filename=order_path.name)
    blocks = parsed.blocks
    decisions = WMSExcelAdapter.sort_decisions(decisions)
    elapsed_sec = time.perf_counter() - started

    console.print(
        f"[dim]Заказчик:[/dim] {parsed.customer_name} | "
        f"[dim]Позиций:[/dim] {len(blocks)}"
    )
    console.print()

    if len(decisions) != len(blocks):
        console.print(
            f"[red]Zero-Loss нарушен:[/red] {len(blocks)} входных блоков → "
            f"{len(decisions)} решений"
        )
        raise SystemExit(1)

    print_match_summary(decisions, parsed.customer_name)
    notify_order_processed(
        order_path.name,
        order_stats(
            decisions,
            customer_name=parsed.customer_name,
            elapsed_sec=elapsed_sec,
            filename=order_path.name,
            checksum_mismatch=parsed.checksum_mismatch,
            declared_places=parsed.declared_places,
        ),
    )

    adapter = WMSExcelAdapter()
    filename = adapter.build_download_filename(parsed.customer_name)
    t_excel = time.perf_counter()
    wms_path = adapter.export(
        decisions,
        parsed.customer_name,
        OUTPUT_DIR / filename,
        source_name=order_path.name,
    )
    excel_sec = time.perf_counter() - t_excel
    timings = getattr(matcher, "stage_timings", None)
    if timings is not None:
        timings.excel = excel_sec
        log_order_profiler(order_path.name, timings, time.perf_counter() - started)
    console.print(f"[green]WMS Excel:[/green] {wms_path}")
    return order_path.name


if __name__ == "__main__":
    main()
