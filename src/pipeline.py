"""Document-type routing: standard 1C picking lists vs soft-furniture transfers."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from src.matcher.hybrid_matcher import HybridMatcher
from src.models import ExtractedFeatures, MatchDecision, RawOrderBlock, V7ParseResult
from src.parsers.document_detector import DocumentType, DocumentTypeDetector
from src.parsers.document_splitter import parse_composite_order
from src.parsers.soft_furniture_parser import parse_soft_furniture_order
from src.parsers.v7_parser import V7Source, parse_v7_order
from src.utils.logger import get_logger

logger = get_logger()

MatchProgressCallback = Callable[[int, int, dict[str, int]], None]


@dataclass
class StageTimings:
    parse: float = 0.0
    exact: float = 0.0
    faiss: float = 0.0
    llm: float = 0.0
    excel: float = 0.0
    extra: dict[str, float] = field(default_factory=dict)


def log_order_profiler(filename: str, timings: StageTimings, total_time: float) -> None:
    logger.info(
        "[Profiler] Order '%s' processed in %.2fs "
        "(Parse=%.2fs, Exact=%.2fs, FAISS=%.2fs, LLM=%.2fs, Excel=%.2fs)",
        filename,
        total_time,
        timings.parse,
        timings.exact,
        timings.faiss,
        timings.llm,
        timings.excel,
    )


def parse_incoming_order(
    source: V7Source,
    filename: str | None = None,
) -> tuple[DocumentType, V7ParseResult]:
    """Detect document type and parse into the unified RawOrderBlock list."""
    detector = DocumentTypeDetector()
    doc_type = detector.detect(source, filename=filename)
    if doc_type == DocumentType.COMPOSITE_PICKING_LIST:
        return doc_type, parse_composite_order(source, filename=filename)
    if doc_type == DocumentType.SOFT_FURNITURE_TRANSFER:
        return doc_type, parse_soft_furniture_order(source, filename=filename)
    return doc_type, parse_v7_order(source, filename=filename)


def resolve_order_decisions(
    document_type: DocumentType,
    parsed: V7ParseResult,
    matcher: HybridMatcher,
    progress_callback: MatchProgressCallback | None = None,
) -> list[MatchDecision]:
    """Standard catalogs go through hybrid matching; soft furniture is passthrough."""
    if document_type == DocumentType.SOFT_FURNITURE_TRANSFER:
        return _soft_furniture_passthrough(parsed.blocks)
    if document_type == DocumentType.COMPOSITE_PICKING_LIST:
        return _composite_decisions(parsed.blocks, matcher, progress_callback=progress_callback)
    return matcher.match_order_decisions(
        parsed.blocks,
        progress_callback=progress_callback,
    )


def _composite_decisions(
    blocks: list[RawOrderBlock],
    matcher: HybridMatcher,
    progress_callback: MatchProgressCallback | None = None,
) -> list[MatchDecision]:
    """Standard (corpus) blocks go through hybrid matching; soft-furniture
    blocks bypass it — mirrors the 4-level cascade invariant per-section."""
    soft_blocks = [block for block in blocks if block.is_soft_furniture]
    standard_blocks = [block for block in blocks if not block.is_soft_furniture]
    decisions: list[MatchDecision] = []
    if standard_blocks:
        decisions.extend(
            matcher.match_order_decisions(
                standard_blocks,
                progress_callback=progress_callback,
            )
        )
    if soft_blocks:
        decisions.extend(_soft_furniture_passthrough(soft_blocks))
    return decisions


def process_order(
    source: V7Source,
    matcher: HybridMatcher,
    filename: str | None = None,
    progress_callback: MatchProgressCallback | None = None,
) -> tuple[DocumentType, V7ParseResult, list[MatchDecision]]:
    """Full parse + match cycle for CLI and UI."""
    started = time.perf_counter()
    reset_fn = getattr(type(matcher), "reset_stage_timings", None)
    if callable(reset_fn):
        matcher.reset_stage_timings()
    t0 = time.perf_counter()
    doc_type, parsed = parse_incoming_order(source, filename=filename)
    t_parse = time.perf_counter() - t0
    decisions = resolve_order_decisions(
        doc_type,
        parsed,
        matcher,
        progress_callback=progress_callback,
    )
    matcher_timings = getattr(matcher, "stage_timings", None)
    timings = StageTimings(
        parse=t_parse,
        exact=_safe_timing(matcher_timings, "exact"),
        faiss=_safe_timing(matcher_timings, "faiss"),
        llm=_safe_timing(matcher_timings, "llm"),
    )
    if hasattr(type(matcher), "stage_timings") or hasattr(matcher_timings, "parse"):
        try:
            matcher.stage_timings.parse = t_parse
        except (AttributeError, TypeError):
            pass
    log_order_profiler(filename or "unknown", timings, time.perf_counter() - started)
    return doc_type, parsed, decisions


def _safe_timing(timings: object, name: str) -> float:
    if timings is None:
        return 0.0
    value = getattr(timings, name, 0.0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _soft_furniture_passthrough(blocks: list[RawOrderBlock]) -> list[MatchDecision]:
    return [
        MatchDecision(
            raw_block=block,
            extracted_features=ExtractedFeatures(),
            status="MATCHED_AUTO",
            matched_entity=None,
            confidence_score=1.0,
            match_method="AUTO_NO_BARCODE",
        )
        for block in blocks
    ]
