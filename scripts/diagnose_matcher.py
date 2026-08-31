"""Diagnostic script for hybrid matcher calibration on order_ruban.xlsx."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rich.table import Table

from src.matcher.dynamic_vocab import DynamicVocabulary
from src.matcher.feature_extractor import FeatureExtractor
from src.matcher.hybrid_matcher import HybridMatcher
from src.matcher.vector_store import CatalogVectorStore
from src.parsers.v7_parser import parse_v7_order
from src.parsers.v8_loader import load_catalog_v8
from src.utils.logger import console

CATALOG_PATH = PROJECT_ROOT / "data" / "catalog_v8.xlsx"
ORDERS_DIR = PROJECT_ROOT / "data" / "orders"
DEFAULT_ORDER_PATH = ORDERS_DIR / "order_ruban.xlsx"


def _resolve_order_path() -> Path:
    if len(sys.argv) > 1:
        candidate = Path(sys.argv[1])
        return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate
    if DEFAULT_ORDER_PATH.exists():
        return DEFAULT_ORDER_PATH
    candidates = sorted(ORDERS_DIR.glob("*.xlsx")) + sorted(ORDERS_DIR.glob("*.xls"))
    return candidates[0] if candidates else DEFAULT_ORDER_PATH


def _format_features(features) -> str:
    return (
        f"pkg={features.package_ratio or '-'} | "
        f"dims={', '.join(features.dimensions) or '-'} | "
        f"models={', '.join(features.matched_models[:3]) or '-'} | "
        f"colors={', '.join(features.matched_colors[:2]) or '-'}"
    )


def main() -> None:
    order_path = _resolve_order_path()
    if not CATALOG_PATH.exists():
        console.print(f"[red]Catalog not found:[/red] {CATALOG_PATH}")
        raise SystemExit(1)
    if not order_path.exists():
        console.print(f"[red]Order not found:[/red] {order_path}")
        raise SystemExit(1)

    catalog = load_catalog_v8(CATALOG_PATH)
    vocabulary = DynamicVocabulary(catalog)
    feature_extractor = FeatureExtractor(vocabulary)
    vector_store = CatalogVectorStore()
    vector_store.build_or_load_index(catalog)
    matcher = HybridMatcher(vector_store, feature_extractor)

    parsed = parse_v7_order(order_path)
    blocks = parsed.blocks

    status_counts = {"MATCHED_AUTO": 0, "NEEDS_LLM": 0, "QUARANTINE": 0}

    console.print(
        f"[bold]Matcher diagnostic[/bold] — {order_path.name} "
        f"({len(blocks)} blocks, customer={parsed.customer_name})"
    )
    console.print()

    for block in blocks:
        report = matcher.diagnose_block(block)
        status_counts[report["status"]] += 1

        console.rule(f"#{block.line_number} — {report['status']} (score={report['confidence_score']:.3f})")
        console.print(f"[cyan]Client:[/cyan] {block.client_description}")
        console.print(f"[cyan]Alias:[/cyan]  {block.factory_alias}")
        console.print(f"[cyan]Query:[/cyan]  {report['search_query']}")
        console.print(f"[cyan]Features:[/cyan] {_format_features(report['features'])}")

        table = Table(show_header=True, header_style="bold")
        table.add_column("#", width=3)
        table.add_column("Score", width=7)
        table.add_column("Hard filter", width=12)
        table.add_column("Nomenclature")

        for index, candidate in enumerate(report["top_three"], start=1):
            entity = candidate.catalog_entity
            filter_status = "PASS" if candidate.hard_filter_passed else candidate.penalty_reason or "FAIL"
            table.add_row(
                str(index),
                f"{candidate.similarity_score:.3f}",
                filter_status or "-",
                entity.nomenclature[:90],
            )

        console.print(table)
        console.print(f"[yellow]Passed hard filters:[/yellow] {report['passed_count']}")
        console.print(f"[yellow]Reason:[/yellow] {report['rejection_reason']}")

        if report["matched_entity"] is not None:
            entity = report["matched_entity"]
            console.print(
                f"[green]Matched:[/green] {entity.nomenclature[:90]} "
                f"(barcode={entity.barcode or '-'})"
            )
        console.print()

    total = len(blocks)
    auto_pct = status_counts["MATCHED_AUTO"] / total * 100
    console.print("[bold]Summary[/bold]")
    console.print(
        f"MATCHED_AUTO={status_counts['MATCHED_AUTO']} ({auto_pct:.1f}%), "
        f"NEEDS_LLM={status_counts['NEEDS_LLM']}, "
        f"QUARANTINE={status_counts['QUARANTINE']}"
    )

    if status_counts["MATCHED_AUTO"] < 40:
        console.print(
            f"[red]Target not met:[/red] MATCHED_AUTO >= 40 required, got {status_counts['MATCHED_AUTO']}"
        )
        raise SystemExit(1)

    console.print("[green]Target met:[/green] MATCHED_AUTO >= 40 (75%+)")


if __name__ == "__main__":
    main()
