"""Hybrid catalog matcher: hard constraints + FAISS vector search + confidence scoring."""

from __future__ import annotations

import re
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable

from src.matcher.exact_matcher import ExactCatalogMatcher
from src.matcher.feature_extractor import (
    FeatureExtractor,
    extract_package_ratio_from_text,
    extract_sub_brands,
)
from src.matcher.llm_resolver import LLMResolver, _TIMEOUT_REASONING
from src.matcher.vector_store import CatalogVectorStore
from src.matcher.token_normalizer import canonicalize_search_text, expand_furniture_abbreviations
from src.models import (
    CatalogEntity,
    ExtractedFeatures,
    LLMResolutionResponse,
    MatchCandidate,
    MatchDecision,
    MatchedOrderItem,
    RawOrderBlock,
)
from src.parsers.v8_loader import (
    build_article_index,
    build_hardware_type_index,
    extract_article_tokens,
    extract_hardware_types,
)
from src.preprocessor.normalizer import canonicalize_dimensions
from src.utils.logger import get_logger

logger = get_logger()


@dataclass
class MatcherStageTimings:
    parse: float = 0.0
    exact: float = 0.0
    faiss: float = 0.0
    llm: float = 0.0
    excel: float = 0.0

_AUTO_MATCH_THRESHOLD = 0.83
_HIGH_CONFIDENCE_SCORE = 0.88
_PACKAGING_BOOST = 0.03
_MODEL_BOOST = 0.03
_COLOR_BOOST = 0.02
_COLLISION_GAP = 0.03
_VECTOR_TOP_K = 30
_LLM_CANDIDATE_POOL = 5
_QUARANTINE_MISSING_CATALOG = "Отсутствует в каталоге фабрики"
_QUARANTINE_ARTICLE_NOT_FOUND = "Артикул не найден в каталоге v8"
_QUARANTINE_CUSTOM_SIZE = "Нестандартный заказной размер"
_QUARANTINE_LLM_TIMEOUT = "Таймаут LLM"
_UNIVERSAL_PACKAGING = frozenset({"1/1", "Ун1/1"})

MatchProgressCallback = Callable[[int, int, dict[str, int]], None]

_DIMENSION_PAIR_RE = re.compile(r"(\d+)x(\d+)")
_UNIVERSAL_PACKAGING_RE = re.compile(r"^Ун\s*(\d+/\d+)$", re.IGNORECASE)
_COMPATIBILITY_RE = re.compile(r"совместимость\s*:.*", re.IGNORECASE | re.DOTALL)
_FOR_CABINET_COMPAT_RE = re.compile(r"д/шкаф[а-яё]*\s+[^;,.]*", re.IGNORECASE)
_IMP_PREFIX_RE = re.compile(r"^IMP\s+ст\s+", re.IGNORECASE)
_PACKAGING_TAIL_RE = re.compile(r"упаковка\s+\S+", re.IGNORECASE)
_HARDWARE_HINT_RE = re.compile(
    r"планка|корнер|заглушка|плинтус|профиль|\bопора\b|"
    r"угол.{0,24}цокол|цокол.{0,24}угол|петл|\bручка\b|стяжк|\bнавес\b|светильник|"
    r"доводчик|направляющ|ящик с доводчиком|полкодерж",
    re.IGNORECASE,
)
_HANDLE_PAREN_RE = re.compile(r"\([^()]{0,80}ручк[^)]*\)", re.IGNORECASE)
_HARDWARE_SIZE_RE = re.compile(
    r"\d+\s*мм|\d+\s*гр|\d+[,.]?\d*\s*м\b",
    re.IGNORECASE,
)
_GENERIC_IDENTITY_TOKENS = frozenset({
    "система",
    "упаковка",
    "корпус",
    "фасад",
    "фасады",
    "фурнитура",
    "и",
    "к",
    "д",
    "шт",
    "навесная",
    "навесной",
    "стекло",
    "полировка",
    "комплект",
    "набор",
    "пр",
    "imp",
    "кат1",
    "кат",
    "для",
    "the",
    "оптовые",
    "продажи",
})
_DECOR_CODE_RE = re.compile(r"\b(\d{3,4}/[a-z]{1,3})\b", re.IGNORECASE)
_LINEAR_METER_RE = re.compile(r"(\d+[,.]?\d*)\s*м\b", re.IGNORECASE)
_CANDIDATE_WIDTH_RE = re.compile(r"\b(\d{3,4})\b")
_CUSTOM_SIZE_HINT_RE = re.compile(r"заказн|нестандарт|под\s+заказ", re.IGNORECASE)
_FACADE_FINISH_RE = re.compile(r"\(\s*(FE|SB|Д)\s*\)", re.IGNORECASE)
_WITHOUT_COLOR_RE = re.compile(r"\bбез\s+цвета\b", re.IGNORECASE)
_GLASS_OR_MIRROR_RE = re.compile(r"стекл|зеркал", re.IGNORECASE)
_CORPUS_MARKER_RE = re.compile(r"\(\s*корпус\s*\)|\bкорпус\b", re.IGNORECASE)
_EXPLICIT_DRAWER_ORDER_RE = re.compile(
    r"ящик\s+с\s+доводчиком|комплект\s+ящиков|направляющ",
    re.IGNORECASE,
)
_DRAWER_OR_SLIDE_ENTITY_RE = re.compile(
    r"ящик\s+с\s+доводчиком|комплект\s+ящиков|направляющ|(?<!\d)\bящик\b",
    re.IGNORECASE,
)
_PACKAGING_MISMATCH_REASON = "Package ratio mismatch"
_CORPUS_DRAWER_ISOLATION_REASON = "Corpus vs drawer/slide isolation"
_SUB_BRAND_CONFLICT_REASON = "Sub-brand conflict"
_SUB_BRAND_BASE_OUTRANKED_REASON = "Base series outranked by matched sub-brand"
_SUB_BRAND_VARIANT_OUTRANKED_REASON = "Sub-brand variant outranked by base series"
_COMPOSITE_COLOR_OUTRANKED_REASON = "Composite decor outranked by monochrome match"
_SUB_BRAND_MATCH_BOOST = 0.35
_SUB_BRAND_MISMATCH_PENALTY = 0.50
_COMPOSITE_COLOR_PENALTY = 0.10


class HybridMatcher:
    """Cascade matcher combining hard filters and semantic vector similarity."""

    def __init__(
        self,
        vector_store: CatalogVectorStore,
        feature_extractor: FeatureExtractor,
        llm_resolver: LLMResolver | None = None,
    ) -> None:
        self._vector_store = vector_store
        self._feature_extractor = feature_extractor
        self._llm_resolver = llm_resolver
        catalog = getattr(vector_store, "catalog", None) or []
        self._article_index = build_article_index(catalog)
        self._hardware_type_index = build_hardware_type_index(catalog)
        self._catalog_by_code = {
            entity.nomenclature_code: entity for entity in catalog
        }
        self._nomenclature_slugs: dict[str, list[CatalogEntity]] = defaultdict(list)
        for entity in catalog:
            slug = _core_nomenclature_slug(entity.nomenclature)
            if slug:
                self._nomenclature_slugs[slug].append(entity)
        self._exact_matcher = ExactCatalogMatcher(catalog)
        self.stage_timings = MatcherStageTimings()

    def reset_stage_timings(self) -> None:
        self.stage_timings = MatcherStageTimings()

    def match_block(self, block: RawOrderBlock, *, apply_llm: bool = True) -> MatchDecision:
        row_i = block.line_number
        features = self._feature_extractor.extract_features(block)
        t_exact = time.perf_counter()
        lexical = self._try_lexical_match(block, features)
        self.stage_timings.exact += time.perf_counter() - t_exact
        if lexical is not None:
            entity = lexical.matched_entity
            sku = entity.nomenclature_code if entity is not None else "NO_BARCODE"
            name = entity.nomenclature if entity is not None else block.client_description
            logger.debug("[Matcher:Row #%s] EXACT MATCH SKU=%s Name='%s'", row_i, sku, name)
            return lexical

        query = _build_search_query(block)
        t_faiss = time.perf_counter()
        raw_hits = self._vector_store.search(query, top_k=_VECTOR_TOP_K)
        candidates = self._score_candidates(block, features, raw_hits, row_index=row_i)
        self.stage_timings.faiss += time.perf_counter() - t_faiss
        passed_candidates = [c for c in candidates if c.hard_filter_passed]
        passed_candidates.sort(key=lambda c: c.similarity_score, reverse=True)
        self._log_faiss_top(row_i, passed_candidates, candidates)

        if not passed_candidates:
            rescued = self._hardware_rescue_candidates(block, features)
            if rescued:
                passed_candidates = rescued
                candidates = rescued + candidates

        if _is_shelf_support_fittings(query):
            unique = _select_unique_hardware(
                query,
                [candidate.catalog_entity for candidate in passed_candidates],
                features,
            )
            if unique is not None:
                return MatchDecision(
                    raw_block=block,
                    extracted_features=features,
                    status="MATCHED_AUTO",
                    matched_entity=unique,
                    confidence_score=1.0,
                    candidates=passed_candidates[:_LLM_CANDIDATE_POOL],
                    match_method=_auto_match_method(unique),
                )
            passthrough = _hardware_passthrough_decision(block, features, candidates)
            if passthrough is not None:
                return passthrough

        if not passed_candidates:
            passthrough = _hardware_passthrough_decision(block, features, candidates)
            if passthrough is not None:
                return passthrough
            detail = _quarantine_status_detail(block, features, self._article_index)
            top1 = candidates[0] if candidates else None
            logger.warning(
                "[Matcher:Row #%s] QUARANTINE: Query='%s' Reason='%s' Top1=%s (Score=%.2f)",
                row_i,
                query,
                detail,
                top1.catalog_entity.nomenclature_code if top1 is not None else "—",
                top1.similarity_score if top1 is not None else 0.0,
            )
            return MatchDecision(
                raw_block=block,
                extracted_features=features,
                status="QUARANTINE",
                matched_entity=None,
                confidence_score=0.0,
                candidates=candidates[:_LLM_CANDIDATE_POOL],
                status_detail=detail,
            )

        top = passed_candidates[0]
        if self._should_auto_match(block, top, passed_candidates, features):
            return MatchDecision(
                raw_block=block,
                extracted_features=features,
                status="MATCHED_AUTO",
                matched_entity=top.catalog_entity,
                confidence_score=top.similarity_score,
                candidates=passed_candidates[:_LLM_CANDIDATE_POOL],
                match_method=_auto_match_method(top.catalog_entity),
            )

        return self._finalize_decision(
            MatchDecision(
                raw_block=block,
                extracted_features=features,
                status="NEEDS_LLM",
                matched_entity=None,
                confidence_score=top.similarity_score,
                candidates=passed_candidates[:_LLM_CANDIDATE_POOL],
            ),
            apply_llm=apply_llm,
        )

    def _invoke_llm_batch(
        self,
        jobs: list[tuple[RawOrderBlock, ExtractedFeatures, list[MatchCandidate]]],
    ) -> list[LLMResolutionResponse]:
        assert self._llm_resolver is not None
        batch_fn = getattr(self._llm_resolver, "resolve_candidates_batch", None)
        if callable(batch_fn):
            result = batch_fn(jobs)
            if isinstance(result, list) and len(result) == len(jobs):
                return result
        return [
            self._llm_resolver.resolve(block, features, candidates)
            for block, features, candidates in jobs
        ]

    def _lookup_catalog_entity(
        self,
        nomenclature_code: str,
        candidates: list[MatchCandidate],
    ) -> CatalogEntity | None:
        for candidate in candidates:
            if candidate.catalog_entity.nomenclature_code == nomenclature_code:
                return candidate.catalog_entity

        direct = self._catalog_by_code.get(nomenclature_code)
        if direct is not None:
            return direct

        stripped = nomenclature_code.lstrip("0")
        if not stripped:
            return None
        for catalog_code, entity in self._catalog_by_code.items():
            if catalog_code.lstrip("0") == stripped:
                return entity
        return None

    def _try_lexical_match(
        self,
        block: RawOrderBlock,
        features: ExtractedFeatures,
    ) -> MatchDecision | None:
        query = _order_match_text(block)
        articles = extract_article_tokens(query)
        entities: list[CatalogEntity] = []

        named = self._exact_matcher.exact_name_candidates(block.client_description)
        if named:
            entities = named
        elif articles:
            pools = [self._article_index.get(token, []) for token in articles]
            nonempty = [pool for pool in pools if pool]
            if nonempty:
                entities = _intersect_entities(nonempty)
        if not entities:
            entities = self._nomenclature_slug_candidates(block)
        if not entities:
            entities = self._dimension_identity_candidates(block, features)
        if not entities:
            entities = self._hardware_phrase_candidates(query)

        if not entities:
            return None

        entities = [
            entity
            for entity in entities
            if self._packaging_compatible(features.package_ratio, entity)
        ]
        type_tokens = _order_hardware_types(query)
        if type_tokens:
            typed = [
                entity
                for entity in entities
                if all(token in _entity_search_text(entity).lower() for token in type_tokens)
            ]
            if typed:
                entities = typed

        entities = _prefer_distinctive_overlap(query, entities)
        unique = _unique_by_code(entities)
        if len(unique) != 1:
            hardware_pick = _select_unique_hardware(query, unique, features)
            if hardware_pick is None:
                return None
            unique = [hardware_pick]

        entity = unique[0]
        passed, _reason = self._apply_hard_constraints(block, features, entity)
        if not passed:
            return None

        candidate = MatchCandidate(
            catalog_entity=entity,
            similarity_score=1.0,
            hard_filter_passed=True,
        )
        return MatchDecision(
            raw_block=block,
            extracted_features=features,
            status="MATCHED_AUTO",
            matched_entity=entity,
            confidence_score=1.0,
            candidates=[candidate],
            match_method=_lexical_match_method(entity),
        )

    def _nomenclature_slug_candidates(self, block: RawOrderBlock) -> list[CatalogEntity]:
        slug = _core_nomenclature_slug(block.client_description)
        if not slug:
            return []
        return _unique_by_code(list(self._nomenclature_slugs.get(slug, [])))

    def _dimension_identity_candidates(
        self,
        block: RawOrderBlock,
        features: ExtractedFeatures,
    ) -> list[CatalogEntity]:
        query = _order_match_text(block)
        return self._exact_matcher.candidates_for(query, features)

    def _hardware_phrase_candidates(self, query: str) -> list[CatalogEntity]:
        type_tokens = _order_hardware_types(query)
        if not type_tokens:
            return []

        pools = [self._hardware_type_index.get(token, []) for token in type_tokens]
        if not all(pools):
            return []
        entities = _intersect_entities(pools)
        extra_tokens = _hardware_modifier_tokens(query, type_tokens)
        if extra_tokens:
            entities = [
                entity
                for entity in entities
                if all(token in _entity_search_text(entity).lower() for token in extra_tokens)
            ]
        return entities

    def _finalize_decision(self, decision: MatchDecision, *, apply_llm: bool = True) -> MatchDecision:
        if apply_llm and decision.status == "NEEDS_LLM" and self._llm_resolver is not None:
            return self._apply_llm_resolution(decision)
        return decision

    def _apply_llm_resolution(self, decision: MatchDecision) -> MatchDecision:
        assert self._llm_resolver is not None
        if not decision.candidates:
            return MatchDecision(
                raw_block=decision.raw_block,
                extracted_features=decision.extracted_features,
                status="QUARANTINE",
                matched_entity=None,
                confidence_score=decision.confidence_score,
                candidates=decision.candidates,
                status_detail=_QUARANTINE_MISSING_CATALOG,
            )

        resolutions = self._invoke_llm_batch(
            [(decision.raw_block, decision.extracted_features, decision.candidates)]
        )
        return self._merge_llm_resolution(decision, resolutions[0])

    def _merge_llm_resolution(
        self,
        decision: MatchDecision,
        resolution: LLMResolutionResponse,
    ) -> MatchDecision:
        row_i = decision.raw_block.line_number
        provider = self._llm_resolver.provider if self._llm_resolver else "llm"
        query = _build_search_query(decision.raw_block)
        logger.debug(
            "[Matcher:Row #%s] LLM response SKU=%s Conf=%.2f Reason='%s'",
            row_i,
            resolution.selected_nomenclature_code,
            resolution.confidence,
            resolution.reasoning,
        )
        if resolution.reasoning == _TIMEOUT_REASONING:
            logger.warning(
                "[Matcher:Row #%s] QUARANTINE: Query='%s' Reason='%s' Top1=%s (Score=%.2f)",
                row_i,
                query,
                _QUARANTINE_LLM_TIMEOUT,
                decision.candidates[0].catalog_entity.nomenclature_code if decision.candidates else "—",
                decision.confidence_score,
            )
            return MatchDecision(
                raw_block=decision.raw_block,
                extracted_features=decision.extracted_features,
                status="QUARANTINE",
                matched_entity=None,
                confidence_score=decision.confidence_score,
                candidates=decision.candidates,
                match_method="LLM_TIMEOUT",
                status_detail=_QUARANTINE_LLM_TIMEOUT,
            )

        selected_code = resolution.selected_nomenclature_code
        if selected_code:
            normalized_code = str(selected_code).strip()
            entity = self._lookup_catalog_entity(normalized_code, decision.candidates)
            if entity is not None:
                passed, _reason = self._apply_hard_constraints(
                    decision.raw_block,
                    decision.extracted_features,
                    entity,
                )
                if not passed:
                    logger.debug(
                        "[Matcher:Row #%s] Rejected SKU=%s Reason='%s'",
                        row_i,
                        entity.nomenclature_code,
                        _reason,
                    )
                    entity = None
            if entity is not None:
                provider_label = provider.upper()
                logger.info(
                    "[Matcher:Row #%s] Low vector score (%.2f). Fallback to %s -> Decision: SKU=%s, Conf=%.2f",
                    row_i,
                    decision.confidence_score,
                    provider_label,
                    entity.nomenclature_code,
                    resolution.confidence,
                )
                return MatchDecision(
                    raw_block=decision.raw_block,
                    extracted_features=decision.extracted_features,
                    status="MATCHED_LLM",
                    matched_entity=entity,
                    confidence_score=resolution.confidence,
                    candidates=decision.candidates,
                    match_method=_llm_match_method(provider_label, entity),
                )

        logger.warning(
            "[Matcher:Row #%s] QUARANTINE: Query='%s' Reason='%s' Top1=%s (Score=%.2f)",
            row_i,
            query,
            _QUARANTINE_MISSING_CATALOG,
            decision.candidates[0].catalog_entity.nomenclature_code if decision.candidates else "—",
            decision.confidence_score,
        )
        return MatchDecision(
            raw_block=decision.raw_block,
            extracted_features=decision.extracted_features,
            status="QUARANTINE",
            matched_entity=None,
            confidence_score=decision.confidence_score,
            candidates=decision.candidates,
            status_detail=_QUARANTINE_MISSING_CATALOG,
        )

    def match_order_decisions(
        self,
        blocks: list[RawOrderBlock],
        progress_callback: MatchProgressCallback | None = None,
    ) -> list[MatchDecision]:
        """Match all blocks; LLM fallback runs in a thread pool with request dedupe."""
        total = len(blocks)
        decisions = [
            self.match_block(block, apply_llm=False) for block in blocks
        ]
        if progress_callback:
            progress_callback(total, total, _status_counts(decisions))

        if self._llm_resolver is None:
            return self._in_source_order(decisions)

        groups: dict[tuple, list[int]] = defaultdict(list)
        for index, decision in enumerate(decisions):
            if decision.status != "NEEDS_LLM":
                continue
            groups[_llm_job_key(decision)].append(index)

        if not groups:
            return self._in_source_order(decisions)

        t_llm = time.perf_counter()
        jobs: list[tuple[RawOrderBlock, ExtractedFeatures, list[MatchCandidate]]] = []
        keys_ordered: list[tuple] = []
        for key, indices in groups.items():
            template = decisions[indices[0]]
            if not template.candidates:
                quarantined = MatchDecision(
                    raw_block=template.raw_block,
                    extracted_features=template.extracted_features,
                    status="QUARANTINE",
                    matched_entity=None,
                    confidence_score=template.confidence_score,
                    candidates=template.candidates,
                    status_detail=_QUARANTINE_MISSING_CATALOG,
                )
                for index in indices:
                    decisions[index] = _copy_llm_outcome(decisions[index], quarantined)
                continue
            keys_ordered.append(key)
            jobs.append(
                (template.raw_block, template.extracted_features, template.candidates)
            )

        if not jobs:
            self.stage_timings.llm += time.perf_counter() - t_llm
            return self._in_source_order(decisions)

        resolutions = self._invoke_llm_batch(jobs)
        self.stage_timings.llm += time.perf_counter() - t_llm
        completed = 0
        for key, resolution in zip(keys_ordered, resolutions, strict=True):
            indices = groups[key]
            try:
                resolved = self._merge_llm_resolution(decisions[indices[0]], resolution)
            except Exception:
                template = decisions[indices[0]]
                resolved = MatchDecision(
                    raw_block=template.raw_block,
                    extracted_features=template.extracted_features,
                    status="QUARANTINE",
                    matched_entity=None,
                    confidence_score=template.confidence_score,
                    candidates=template.candidates,
                    match_method="LLM_TIMEOUT",
                    status_detail=_QUARANTINE_LLM_TIMEOUT,
                )
            for index in indices:
                decisions[index] = _copy_llm_outcome(decisions[index], resolved)
            completed += 1
            if progress_callback:
                progress_callback(completed, len(jobs), _status_counts(decisions))

        return self._in_source_order(decisions)

    @staticmethod
    def _in_source_order(decisions: list[MatchDecision]) -> list[MatchDecision]:
        """Restore 1C 7.7 row order after parallel LLM completion."""
        return sorted(decisions, key=lambda item: item.order_line_number)

    def diagnose_block(self, block: RawOrderBlock) -> dict:
        """Return a detailed diagnostic breakdown for matcher calibration."""
        features = self._feature_extractor.extract_features(block)
        query = _build_search_query(block)
        lexical = self._try_lexical_match(block, features)
        raw_hits = self._vector_store.search(query, top_k=_VECTOR_TOP_K)
        candidates = self._score_candidates(block, features, raw_hits, row_index=block.line_number)
        passed_candidates = [c for c in candidates if c.hard_filter_passed]
        passed_candidates.sort(key=lambda c: c.similarity_score, reverse=True)
        decision = lexical if lexical is not None else self.match_block(block)

        return {
            "block": block,
            "search_query": query,
            "features": features,
            "status": decision.status,
            "confidence_score": decision.confidence_score,
            "top_three": candidates[:3],
            "passed_count": len(passed_candidates),
            "rejection_reason": self._explain_rejection(
                block,
                features,
                candidates,
                passed_candidates,
                decision.status,
            ),
            "matched_entity": decision.matched_entity,
        }

    def match_order(
        self,
        blocks: list[RawOrderBlock],
        customer_name: str,
    ) -> list[MatchedOrderItem]:
        """Match all order blocks preserving zero-loss row count."""
        decisions = self.match_order_decisions(blocks)
        return [
            self._decision_to_matched_item(decision, customer_name)
            for decision in decisions
        ]

    def _should_auto_match(
        self,
        block: RawOrderBlock,
        top: MatchCandidate,
        passed_candidates: list[MatchCandidate],
        features: ExtractedFeatures,
    ) -> bool:
        if _has_high_confidence_tuple(block, features, top.catalog_entity):
            return True

        if not _distinctive_alignment(block, features, top.catalog_entity):
            return False

        if self._is_ambiguous_collision(top, passed_candidates, features):
            return False

        return top.similarity_score >= _AUTO_MATCH_THRESHOLD

    def _explain_rejection(
        self,
        block: RawOrderBlock,
        features: ExtractedFeatures,
        all_candidates: list[MatchCandidate],
        passed_candidates: list[MatchCandidate],
        status: str,
    ) -> str:
        if status == "MATCHED_AUTO":
            return "auto-matched"

        if not passed_candidates:
            reasons = [
                f"{candidate.penalty_reason}: {candidate.catalog_entity.nomenclature[:50]}"
                for candidate in all_candidates
                if not candidate.hard_filter_passed
            ][:3]
            detail = "; ".join(reasons) if reasons else "no vector hits"
            return f"no candidates passed hard filters (QUARANTINE) — {detail}"

        top = passed_candidates[0]
        score = top.similarity_score

        if not _distinctive_alignment(block, features, top.catalog_entity):
            return "distinctive product tokens/decor codes do not align with top candidate"

        if self._is_ambiguous_collision(top, passed_candidates, features):
            competitors = [
                candidate
                for candidate in passed_candidates[1:]
                if _entity_characteristic_key(candidate.catalog_entity)
                == _entity_characteristic_key(top.catalog_entity)
            ]
            if competitors:
                best = max(competitors, key=lambda candidate: candidate.similarity_score)
                gap = score - best.similarity_score
                return (
                    f"ambiguous collision among same-characteristic candidates "
                    f"(gap={gap:.3f} < {_COLLISION_GAP})"
                )
            return "ambiguous collision (dimension tie-breaker)"

        if score < _AUTO_MATCH_THRESHOLD:
            boost = self._compute_feature_boost(features, top.catalog_entity)
            return (
                f"score {score:.3f} below auto threshold {_AUTO_MATCH_THRESHOLD} "
                f"(feature boost={boost:.2f})"
            )

        return f"unknown rejection (status={status})"

    def _decision_to_matched_item(
        self,
        decision: MatchDecision,
        customer_name: str,
    ) -> MatchedOrderItem:
        block = decision.raw_block

        if decision.status == "MATCHED_AUTO":
            entity = decision.matched_entity
            if entity is not None:
                return MatchedOrderItem(
                    nomenclature=entity.nomenclature,
                    barcode=entity.barcode,
                    quantity=block.quantity,
                    customer_name=customer_name,
                    nomenclature_code=entity.nomenclature_code,
                    match_score=decision.confidence_score,
                    match_reason=decision.match_method or "vector_auto",
                    source_block=block,
                )
            return MatchedOrderItem(
                nomenclature=block.client_description,
                barcode=None,
                quantity=block.quantity,
                customer_name=customer_name,
                nomenclature_code=None,
                match_score=decision.confidence_score or None,
                match_reason=decision.match_method or "AUTO_NO_BARCODE",
                source_block=block,
            )

        if decision.status == "MATCHED_LLM" and decision.matched_entity is not None:
            entity = decision.matched_entity
            return MatchedOrderItem(
                nomenclature=entity.nomenclature,
                barcode=entity.barcode,
                quantity=block.quantity,
                customer_name=customer_name,
                nomenclature_code=entity.nomenclature_code,
                match_score=decision.confidence_score,
                match_reason=decision.match_method or "LLM",
                source_block=block,
            )

        fallback_name = block.client_description
        if decision.candidates:
            fallback_name = decision.candidates[0].catalog_entity.nomenclature

        return MatchedOrderItem(
            nomenclature=fallback_name,
            barcode=None,
            quantity=block.quantity,
            customer_name=customer_name,
            nomenclature_code=None,
            match_score=decision.confidence_score or None,
            match_reason=decision.status,
            source_block=block,
        )

    def _apply_hard_constraints(
        self,
        block: RawOrderBlock,
        features: ExtractedFeatures,
        entity: CatalogEntity,
    ) -> tuple[bool, str | None]:
        if not self._packaging_compatible(features.package_ratio, entity):
            return False, _PACKAGING_MISMATCH_REASON

        if not _sub_brand_compatible(features, entity):
            return False, _SUB_BRAND_CONFLICT_REASON

        query = _order_match_text(block)
        corpus_ok, corpus_reason = _corpus_drawer_compatible(query, entity)
        if not corpus_ok:
            return False, corpus_reason

        finish_ok, finish_reason = _facade_finish_compatible(query, _entity_search_text(entity))
        if not finish_ok:
            return False, finish_reason

        is_hardware = _is_hardware_query(query)

        if not is_hardware:
            dimension_ok, dimension_reason = self._dimensions_compatible(
                features.dimensions,
                _entity_search_text(entity),
                features.thicknesses,
                features.alternative_widths,
                require_exact_pairs=_is_cut_to_size_order(block, features),
            )
            if not dimension_ok:
                return False, dimension_reason

            if not self._glass_compatible(block, features, entity):
                return False, "glass/material mismatch"

        if not self._hardware_compatible(block, features, entity):
            return False, "hardware type/size/color mismatch"

        return True, None

    @staticmethod
    def _hardware_compatible(
        block: RawOrderBlock,
        features: ExtractedFeatures,
        entity: CatalogEntity,
    ) -> bool:
        query = _order_match_text(block)
        if not _is_hardware_query(query):
            return True
        return _hardware_entity_matches(query, features, entity)

    def _hardware_rescue_candidates(
        self,
        block: RawOrderBlock,
        features: ExtractedFeatures,
    ) -> list[MatchCandidate]:
        query = _order_match_text(block)
        if not _is_hardware_query(query):
            return []

        articles = extract_article_tokens(query)
        entities: list[CatalogEntity] = []
        if articles:
            pools = [self._article_index.get(token, []) for token in articles]
            nonempty = [pool for pool in pools if pool]
            if nonempty:
                entities = _intersect_entities(nonempty)
        if not entities:
            entities = self._hardware_phrase_candidates(query)
        if not entities:
            type_tokens = _order_hardware_types(query)
            pooled: list[CatalogEntity] = []
            for token in type_tokens:
                pooled.extend(self._hardware_type_index.get(token, []))
            entities = _unique_by_code(pooled)

        picked = _select_unique_hardware(query, entities, features)
        if picked is None:
            return []
        passed, _reason = self._apply_hard_constraints(block, features, picked)
        if not passed:
            return []
        return [
            MatchCandidate(
                catalog_entity=picked,
                similarity_score=1.0,
                hard_filter_passed=True,
            )
        ]

    def _score_candidates(
        self,
        block: RawOrderBlock,
        features: ExtractedFeatures,
        raw_hits: list[tuple[CatalogEntity, float]],
        row_index: int | None = None,
    ) -> list[MatchCandidate]:
        candidates: list[MatchCandidate] = []
        row_i = row_index if row_index is not None else block.line_number
        for entity, score in raw_hits:
            passed, penalty_reason = self._apply_hard_constraints(block, features, entity)
            ranked_score = score
            if passed:
                ranked_score = min(1.0, score + self._compute_feature_boost(features, entity))
                if _has_high_confidence_tuple(block, features, entity):
                    ranked_score = max(ranked_score, _HIGH_CONFIDENCE_SCORE)
            else:
                logger.debug(
                    "[Matcher:Row #%s] Rejected SKU=%s Reason='%s'",
                    row_i,
                    entity.nomenclature_code,
                    penalty_reason or "hard filter",
                )
            candidates.append(
                MatchCandidate(
                    catalog_entity=entity,
                    similarity_score=ranked_score,
                    hard_filter_passed=passed,
                    penalty_reason=penalty_reason,
                )
            )
        candidates = _apply_sub_brand_pool_barrier(candidates, features)
        candidates = _apply_color_palette_pool_barrier(candidates, features)
        return candidates

    @staticmethod
    def _log_faiss_top(
        row_i: int,
        passed_candidates: list[MatchCandidate],
        all_candidates: list[MatchCandidate],
    ) -> None:
        top = passed_candidates[0] if passed_candidates else (all_candidates[0] if all_candidates else None)
        if top is None:
            logger.debug("[Matcher:Row #%s] FAISS Top-1 SKU=— Score=0.000 | Barrier=no hits", row_i)
            return
        barrier = "PASS" if top.hard_filter_passed else (top.penalty_reason or "FAIL")
        logger.debug(
            "[Matcher:Row #%s] FAISS Top-1 SKU=%s Score=%.3f | Barrier=%s",
            row_i,
            top.catalog_entity.nomenclature_code,
            top.similarity_score,
            barrier,
        )

    @staticmethod
    def _compute_feature_boost(features: ExtractedFeatures, entity: CatalogEntity) -> float:
        """Add confidence when packaging, model and color already agree after hard filters."""
        boost = 0.0
        if _packaging_values_match(features.package_ratio, _entity_packaging_token(entity)):
            boost += _PACKAGING_BOOST
        if features.matched_models and (entity.label_model or "").strip():
            if _models_overlap(features.matched_models, entity.label_model or ""):
                boost += _MODEL_BOOST
        if features.matched_colors and (entity.color or "").strip():
            if _colors_overlap(features.matched_colors, entity.color or ""):
                boost += _COLOR_BOOST
        return boost

    @staticmethod
    def _packaging_compatible(
        block_ratio: str | None,
        entity: CatalogEntity,
    ) -> bool:
        if not block_ratio:
            return True
        candidate_packaging = _entity_packaging_token(entity)
        if not candidate_packaging:
            return not _is_multi_place_ratio(block_ratio)
        return _packaging_values_match(block_ratio, candidate_packaging)

    @staticmethod
    def _has_full_feature_match(
        block: RawOrderBlock,
        features: ExtractedFeatures,
        entity: CatalogEntity,
    ) -> bool:
        order_pairs = _extract_dimension_pairs_from_list(features.dimensions)
        if order_pairs:
            entity_pairs = _extract_dimension_pairs(_entity_search_text(entity))
            if not entity_pairs or not any(pair in entity_pairs for pair in order_pairs):
                return False

        if features.matched_models:
            label = (entity.label_model or "").strip()
            if label and not _models_overlap(features.matched_models, label):
                return False

        if features.matched_colors:
            entity_color = (entity.color or "").strip()
            if entity_color and not _colors_overlap(features.matched_colors, entity_color):
                return False

        entity_packaging = _entity_packaging_token(entity)
        if features.package_ratio and entity_packaging:
            if not _packaging_values_match(features.package_ratio, entity_packaging):
                return False

        return HybridMatcher._nomenclature_aligns_with_order(block, entity)

    @staticmethod
    def _nomenclature_aligns_with_order(block: RawOrderBlock, entity: CatalogEntity) -> bool:
        client_key = _normalize_product_slug(canonicalize_search_text(block.client_description))
        entity_key = _normalize_product_slug(_entity_search_text(entity))
        if not client_key or not entity_key:
            return True

        if client_key in entity_key or entity_key in client_key:
            return True

        client_tokens = set(_significant_tokens(client_key))
        entity_tokens = set(_significant_tokens(entity_key))
        if not client_tokens:
            return True

        overlap = len(client_tokens & entity_tokens) / len(client_tokens)
        return overlap >= 0.5

    @staticmethod
    def _dimensions_compatible(
        order_dimensions: list[str],
        candidate_text: str,
        order_thicknesses: list[str] | None = None,
        alternative_widths: list[int] | None = None,
        require_exact_pairs: bool = False,
    ) -> tuple[bool, str | None]:
        order_pairs = _extract_dimension_pairs_from_list(order_dimensions)
        linear_meters = _extract_linear_meters(order_dimensions)

        if alternative_widths:
            alt_ok, alt_reason = _alternative_widths_compatible(
                alternative_widths,
                candidate_text,
            )
            if not alt_ok:
                return False, alt_reason

        if linear_meters and order_thicknesses:
            candidate_lower = candidate_text.lower().replace(" ", "")
            for thickness in order_thicknesses:
                normalized_thickness = thickness.lower().replace(" ", "")
                if normalized_thickness not in candidate_lower:
                    return False, f"thickness mismatch: {thickness}"

            candidate_linear = _extract_linear_meters([candidate_text])
            if candidate_linear:
                order_value = linear_meters[0]
                if not any(abs(order_value - candidate) <= 0.05 for candidate in candidate_linear):
                    return (
                        False,
                        f"linear dimension mismatch: {order_value}м vs {candidate_linear}",
                    )

        if not order_pairs:
            return True, None

        if alternative_widths:
            return True, None

        candidate_pairs = _extract_dimension_pairs(candidate_text)
        if not candidate_pairs:
            if require_exact_pairs:
                return False, "custom size not in catalog"
            return True, None

        if require_exact_pairs and not any(pair in candidate_pairs for pair in order_pairs):
            return False, "custom size not in catalog"

        for order_pair in order_pairs:
            if order_pair in candidate_pairs:
                continue

            for candidate_pair in candidate_pairs:
                ow, oh = order_pair
                cw, ch = candidate_pair
                if (ow == cw and oh != ch) or (ow != cw and oh == ch):
                    return (
                        False,
                        f"dimension conflict: {ow}х{oh} vs {cw}х{ch}",
                    )

        return True, None

    @staticmethod
    def _is_ambiguous_collision(
        top: MatchCandidate,
        passed_candidates: list[MatchCandidate],
        features: ExtractedFeatures,
    ) -> bool:
        top_key = _entity_characteristic_key(top.catalog_entity)
        competitors = [
            candidate
            for candidate in passed_candidates[1:]
            if _entity_characteristic_key(candidate.catalog_entity) == top_key
        ]
        if not competitors:
            return False

        best_competitor = max(competitors, key=lambda candidate: candidate.similarity_score)
        gap = top.similarity_score - best_competitor.similarity_score
        if gap >= _COLLISION_GAP:
            return False

        order_pairs = _extract_dimension_pairs_from_list(features.dimensions)
        if not order_pairs:
            if features.alternative_widths:
                top_ok = _alternative_widths_compatible(
                    features.alternative_widths,
                    _entity_search_text(top.catalog_entity),
                )[0]
                competitor_ok = _alternative_widths_compatible(
                    features.alternative_widths,
                    _entity_search_text(best_competitor.catalog_entity),
                )[0]
                if top_ok and not competitor_ok:
                    return False
            return True

        top_pairs = _extract_dimension_pairs(_entity_search_text(top.catalog_entity))
        competitor_pairs = _extract_dimension_pairs(
            _entity_search_text(best_competitor.catalog_entity)
        )
        top_exact = any(pair in top_pairs for pair in order_pairs)
        competitor_exact = any(pair in competitor_pairs for pair in order_pairs)

        if top_exact and not competitor_exact:
            return False

        return True

    @staticmethod
    def _glass_compatible(
        block: RawOrderBlock,
        features: ExtractedFeatures,
        entity: CatalogEntity,
    ) -> bool:
        is_glass_order = block.item_type.strip().lower() == "стекло" or any(
            _is_glass_thickness(thickness) for thickness in features.thicknesses
        )
        if not is_glass_order:
            return True

        entity_text = _entity_search_text(entity).lower()
        if not _GLASS_OR_MIRROR_RE.search(entity_text):
            return False

        for thickness in features.thicknesses:
            if not _is_glass_thickness(thickness):
                continue
            normalized = thickness.lower().replace(" ", "")
            if normalized not in entity_text.replace(" ", ""):
                return False

        return True


def _copy_llm_outcome(source: MatchDecision, resolved: MatchDecision) -> MatchDecision:
    return MatchDecision(
        raw_block=source.raw_block,
        extracted_features=source.extracted_features,
        status=resolved.status,
        matched_entity=resolved.matched_entity,
        confidence_score=resolved.confidence_score,
        candidates=source.candidates,
        match_method=resolved.match_method,
        status_detail=resolved.status_detail,
    )


def _has_factory_barcode(entity: CatalogEntity) -> bool:
    return bool(entity.barcode and str(entity.barcode).strip())


def _auto_match_method(entity: CatalogEntity) -> str:
    return "vector_auto" if _has_factory_barcode(entity) else "AUTO_NO_BARCODE"


def _lexical_match_method(entity: CatalogEntity) -> str:
    return "exact_article" if _has_factory_barcode(entity) else "AUTO_NO_BARCODE"


def _llm_match_method(provider: str, entity: CatalogEntity) -> str:
    return f"LLM_{provider}" if _has_factory_barcode(entity) else "LLM_NO_BARCODE"


def _entity_packaging_token(entity: CatalogEntity) -> str | None:
    field = (entity.packaging or "").strip() or None
    from_name = extract_package_ratio_from_text(entity.nomenclature or "")
    return field or from_name


def _is_multi_place_ratio(ratio: str | None) -> bool:
    if not ratio:
        return False
    match = re.search(r"(\d+)\s*/\s*(\d+)", _normalize_packaging(ratio))
    if not match:
        return False
    return int(match.group(2)) > 1


def _packaging_values_match(block_ratio: str | None, candidate_packaging: str | None) -> bool:
    if not block_ratio or not candidate_packaging:
        return False
    normalized_block = _normalize_packaging(block_ratio)
    normalized_candidate = _normalize_packaging(candidate_packaging)
    if normalized_block == normalized_candidate:
        return True
    if _is_multi_place_ratio(normalized_block) or _is_multi_place_ratio(normalized_candidate):
        return False
    return normalized_block in _UNIVERSAL_PACKAGING and normalized_candidate in _UNIVERSAL_PACKAGING


def _entity_sub_brand_source(entity: CatalogEntity) -> str:
    """Prefer the collection/label field; fall back to nomenclature to avoid missing data."""
    label = (entity.label_model or "").strip()
    return label or (entity.nomenclature or "")


def _entity_sub_brands(entity: CatalogEntity) -> set[str]:
    return extract_sub_brands(_entity_sub_brand_source(entity))


def _sub_brand_compatible(features: ExtractedFeatures, entity: CatalogEntity) -> bool:
    """Rule 2 — reject candidates whose sub-brand conflicts with the query's (Равенна Роял != Равенна Тренд)."""
    query_sub_brands = features.sub_brands
    if not query_sub_brands:
        return True
    entity_sub_brands = _entity_sub_brands(entity)
    if not entity_sub_brands:
        return True
    return bool(entity_sub_brands & query_sub_brands)


def _apply_sub_brand_pool_barrier(
    candidates: list[MatchCandidate],
    features: ExtractedFeatures,
) -> list[MatchCandidate]:
    """Rule 1 / Rule 3 — rank sub-brand-aligned or pure-base candidates above the rest.

    Rule 1: query names a sub-brand (e.g. "Вайт"). If the candidate pool contains at
    least one entity carrying that sub-brand, exact-brand candidates are boosted and
    base-series candidates (no sub-brand at all) are penalized so they cannot win a
    near-tie against the more specific match.
    Rule 3: query names no sub-brand. If the pool contains a pure base-series entity,
    sub-branded variants are penalized so the plain collection wins ties.
    """
    passed_indices = [index for index, candidate in enumerate(candidates) if candidate.hard_filter_passed]
    if not passed_indices:
        return candidates

    entity_sub_brands = {index: _entity_sub_brands(candidates[index].catalog_entity) for index in passed_indices}
    query_sub_brands = features.sub_brands

    if query_sub_brands:
        aligned_exists = any(entity_sub_brands[index] & query_sub_brands for index in passed_indices)
        if not aligned_exists:
            return candidates
    else:
        base_exists = any(not entity_sub_brands[index] for index in passed_indices)
        if not base_exists:
            return candidates

    updated = list(candidates)
    for index in passed_indices:
        candidate = candidates[index]
        sub_brands = entity_sub_brands[index]
        if query_sub_brands:
            if sub_brands & query_sub_brands:
                new_score = min(1.0, candidate.similarity_score + _SUB_BRAND_MATCH_BOOST)
                updated[index] = candidate.model_copy(update={"similarity_score": new_score})
            elif not sub_brands:
                new_score = max(0.0, candidate.similarity_score - _SUB_BRAND_MISMATCH_PENALTY)
                updated[index] = candidate.model_copy(
                    update={
                        "similarity_score": new_score,
                        "penalty_reason": candidate.penalty_reason or _SUB_BRAND_BASE_OUTRANKED_REASON,
                    }
                )
        elif sub_brands:
            new_score = max(0.0, candidate.similarity_score - _SUB_BRAND_MISMATCH_PENALTY)
            updated[index] = candidate.model_copy(
                update={
                    "similarity_score": new_score,
                    "penalty_reason": candidate.penalty_reason or _SUB_BRAND_VARIANT_OUTRANKED_REASON,
                }
            )
    return updated


def _is_composite_entity_color(entity: CatalogEntity) -> bool:
    color = (entity.color or "").strip()
    return "/" in color or "-" in color


def _apply_color_palette_pool_barrier(
    candidates: list[MatchCandidate],
    features: ExtractedFeatures,
) -> list[MatchCandidate]:
    """Prefer a monochrome decor (``Белый``) over a composite one (``Ателье светлый/Белый``)
    that only partially overlaps the requested color, when both are present in the pool."""
    if not features.matched_colors or features.is_composite_color:
        return candidates

    passed_indices = [index for index, candidate in enumerate(candidates) if candidate.hard_filter_passed]
    if not passed_indices:
        return candidates

    monochrome_exists = any(
        not _is_composite_entity_color(candidates[index].catalog_entity)
        and _colors_overlap(features.matched_colors, candidates[index].catalog_entity.color or "")
        for index in passed_indices
    )
    if not monochrome_exists:
        return candidates

    updated = list(candidates)
    for index in passed_indices:
        candidate = candidates[index]
        entity = candidate.catalog_entity
        if _is_composite_entity_color(entity) and _colors_overlap(features.matched_colors, entity.color or ""):
            new_score = max(0.0, candidate.similarity_score - _COMPOSITE_COLOR_PENALTY)
            updated[index] = candidate.model_copy(
                update={
                    "similarity_score": new_score,
                    "penalty_reason": candidate.penalty_reason or _COMPOSITE_COLOR_OUTRANKED_REASON,
                }
            )
    return updated


def _is_corpus_module_query(query: str) -> bool:
    return bool(_CORPUS_MARKER_RE.search(query))


def _is_explicit_drawer_order(query: str) -> bool:
    return bool(_EXPLICIT_DRAWER_ORDER_RE.search(query))


def _is_drawer_or_slide_entity(entity: CatalogEntity) -> bool:
    text = f"{entity.nomenclature} {entity.filling or ''}"
    return bool(_DRAWER_OR_SLIDE_ENTITY_RE.search(text))


def _corpus_drawer_compatible(query: str, entity: CatalogEntity) -> tuple[bool, str | None]:
    if not _is_corpus_module_query(query):
        return True, None
    if _is_explicit_drawer_order(query):
        return True, None
    if _is_drawer_or_slide_entity(entity):
        return False, _CORPUS_DRAWER_ISOLATION_REASON
    return True, None


def _llm_job_key(decision: MatchDecision) -> tuple:
    block = decision.raw_block
    codes = tuple(candidate.catalog_entity.nomenclature_code for candidate in decision.candidates)
    return (
        block.client_description.strip().lower(),
        (block.factory_alias or "").strip().lower(),
        block.item_type.strip().lower(),
        codes,
    )


def _status_counts(decisions: list[MatchDecision]) -> dict[str, int]:
    counts = {"MATCHED_AUTO": 0, "MATCHED_LLM": 0, "QUARANTINE": 0, "NEEDS_LLM": 0}
    for decision in decisions:
        counts[decision.status] = counts.get(decision.status, 0) + 1
    return counts


def _distinctive_alignment(
    block: RawOrderBlock,
    features: ExtractedFeatures,
    entity: CatalogEntity,
) -> bool:
    combined_text = _order_match_text(block).lower()
    entity_text = _entity_search_text(entity).lower()
    query = _order_match_text(block)

    if _is_hardware_query(query):
        return _hardware_entity_matches(query, features, entity)

    decor_codes = [match.group(1).lower() for match in _DECOR_CODE_RE.finditer(combined_text)]
    for decor_code in decor_codes:
        if decor_code not in entity_text.replace(" ", ""):
            return False

    identity_tokens = _product_identity_tokens(block.client_description)
    if identity_tokens:
        matched = sum(1 for token in identity_tokens if token in entity_text)
        if matched / len(identity_tokens) < 0.75:
            return False

    specific_part_types = [
        part_type
        for part_type in features.matched_part_types
        if part_type.lower() not in _GENERIC_IDENTITY_TOKENS and len(part_type) >= 5
    ]
    for part_type in specific_part_types:
        if part_type.lower() not in entity_text:
            return False

    order_pairs = _extract_dimension_pairs_from_list(features.dimensions)
    if order_pairs:
        entity_pairs = _extract_dimension_pairs(entity_text)
        if features.alternative_widths:
            if not _alternative_widths_compatible(features.alternative_widths, entity_text)[0]:
                return False
        elif not entity_pairs or not any(pair in entity_pairs for pair in order_pairs):
            return False
    elif features.alternative_widths:
        if not _alternative_widths_compatible(features.alternative_widths, entity_text)[0]:
            return False

    return HybridMatcher._nomenclature_aligns_with_order(block, entity)


def _product_identity_tokens(client_description: str) -> list[str]:
    slug = _normalize_product_slug(canonicalize_search_text(client_description))
    tokens = [
        token
        for token in _significant_tokens(slug)
        if token not in _GENERIC_IDENTITY_TOKENS and len(token) >= 4
    ]
    return tokens


def _extract_linear_meters(dimensions: list[str]) -> list[float]:
    values: list[float] = []
    for dimension in dimensions:
        for match in _LINEAR_METER_RE.finditer(dimension):
            raw_value = match.group(1).replace(",", ".")
            values.append(float(raw_value))
    return values


def _build_search_query(block: RawOrderBlock) -> str:
    parts = [block.client_description.strip()]
    if block.factory_alias:
        cleaned_alias = _clean_factory_alias(block.factory_alias)
        client_lower = block.client_description.strip().lower()
        if (
            cleaned_alias
            and cleaned_alias.lower() != client_lower
            and _alias_supports_client(block.client_description, cleaned_alias)
        ):
            parts.append(cleaned_alias)
    return canonicalize_search_text(" ".join(part for part in parts if part).strip())


def _order_match_text(block: RawOrderBlock) -> str:
    alias = (block.factory_alias or "").strip()
    if alias and not _alias_supports_client(block.client_description, alias):
        alias = ""
    return f"{block.client_description} {alias}".strip()


def _alias_supports_client(client_description: str, alias: str) -> bool:
    client_tokens = set(_product_identity_tokens(client_description))
    if not client_tokens:
        return True
    alias_lower = canonicalize_search_text(alias).lower()
    matched = sum(1 for token in client_tokens if token in alias_lower)
    return matched / len(client_tokens) > 0.5


def _clean_factory_alias(alias: str) -> str:
    text = alias.strip()
    text = _COMPATIBILITY_RE.sub("", text)
    text = _FOR_CABINET_COMPAT_RE.sub("", text)
    text = _IMP_PREFIX_RE.sub("", text)
    text = text.replace("=", " ")
    return re.sub(r"\s+", " ", text).strip()


def _facade_finish_token(text: str) -> str | None:
    match = _FACADE_FINISH_RE.search(text)
    if match:
        return match.group(1).upper()
    return None


def _facade_finish_compatible(order_text: str, entity_text: str) -> tuple[bool, str | None]:
    order_token = _facade_finish_token(order_text)
    entity_token = _facade_finish_token(entity_text)
    if order_token == entity_token:
        return True, None
    return False, f"facade finish mismatch: {order_token} vs {entity_token}"


def _entity_search_text(entity: CatalogEntity) -> str:
    blob = " ".join(
        part
        for part in (
            entity.nomenclature,
            entity.module,
            entity.filling,
            entity.label_type,
            entity.color,
        )
        if part
    )
    return canonicalize_search_text(blob)


def _entity_characteristic_key(entity: CatalogEntity) -> tuple:
    return (
        (entity.label_model or "").strip().lower(),
        (entity.color or "").strip().lower(),
        tuple(sorted(_extract_dimension_pairs(_entity_search_text(entity)))),
        _normalize_product_slug(entity.nomenclature),
    )


def _core_nomenclature_slug(text: str) -> str:
    cleaned = _WITHOUT_COLOR_RE.sub(" ", text or "")
    return _normalize_product_slug(canonicalize_search_text(cleaned))


def _is_cut_to_size_order(block: RawOrderBlock, features: ExtractedFeatures) -> bool:
    if not _extract_dimension_pairs_from_list(features.dimensions):
        return False
    query = _order_match_text(block)
    if block.item_type.strip().lower() == "стекло":
        return True
    return bool(_GLASS_OR_MIRROR_RE.search(query))


def _normalize_product_slug(text: str) -> str:
    lowered = canonicalize_dimensions(text).lower()
    lowered = re.sub(r"(\d+)\s*x\s*(\d+)", r"\1x\2", lowered)
    lowered = _PACKAGING_TAIL_RE.sub("", lowered)
    lowered = re.sub(r"\d+/\d+/\d+", "", lowered)
    lowered = re.sub(r"\d+/\d+", "", lowered)
    lowered = re.sub(r"ун\s*\d+/\d+", "", lowered, flags=re.IGNORECASE)
    lowered = re.sub(r"\s+", " ", lowered).strip()
    return lowered


def _significant_tokens(text: str) -> list[str]:
    stopwords = {"система", "упаковка", "корпус", "и", "the"}
    tokens = re.findall(r"[а-яa-z0-9]+", text.lower())
    return [token for token in tokens if len(token) >= 3 and token not in stopwords]


def _models_overlap(matched_models: list[str], label_model: str) -> bool:
    label_lower = label_model.lower()
    for model in matched_models:
        model_lower = model.lower()
        if model_lower == label_lower or model_lower in label_lower or label_lower in model_lower:
            return True
    return False


def _colors_overlap(matched_colors: list[str], entity_color: str) -> bool:
    entity_lower = entity_color.lower()
    for color in matched_colors:
        color_lower = color.lower()
        if color_lower == entity_lower or color_lower in entity_lower or entity_lower in color_lower:
            return True
    return False


def _is_glass_thickness(thickness: str) -> bool:
    normalized = thickness.lower().replace(" ", "")
    return normalized == "4мм" or normalized.startswith("4мм/")


def _normalize_packaging(value: str) -> str:
    cleaned = re.sub(r"^упаковка\s+", "", value.strip(), flags=re.IGNORECASE)
    universal_match = _UNIVERSAL_PACKAGING_RE.match(cleaned)
    if universal_match:
        return f"Ун{universal_match.group(1)}"
    if cleaned.lower().startswith("ун") and "/" in cleaned:
        digits = re.sub(r"\s+", "", cleaned)
        if digits.lower().startswith("ун") and not digits.startswith("Ун"):
            return "Ун" + digits[2:]
        return digits
    return cleaned


def _extract_dimension_pairs(text: str) -> list[tuple[int, int]]:
    normalized = canonicalize_dimensions(text)
    pairs: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for match in _DIMENSION_PAIR_RE.finditer(normalized):
        pair = (int(match.group(1)), int(match.group(2)))
        if pair not in seen:
            seen.add(pair)
            pairs.append(pair)
    return pairs


def _extract_dimension_pairs_from_list(dimensions: list[str]) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for dimension in dimensions:
        for pair in _extract_dimension_pairs(dimension):
            if pair not in seen:
                seen.add(pair)
                pairs.append(pair)
    return pairs


def _alternative_widths_compatible(
    alternative_widths: list[int],
    candidate_text: str,
) -> tuple[bool, str | None]:
    candidate_widths = _extract_candidate_widths(candidate_text)
    if not candidate_widths:
        return True, None
    allowed = set(alternative_widths)
    for width in alternative_widths:
        allowed.add(width)
        if width >= 1000 and width % 10 == 0:
            allowed.add(width // 10)
        if width < 400:
            allowed.add(width * 10)
    if any(width in allowed for width in candidate_widths):
        return True, None
    return False, f"dimension mismatch: expected one of {alternative_widths}"


def _extract_candidate_widths(text: str) -> list[int]:
    stripped = re.sub(r"\b\d+/\d+\b", " ", text)
    values: list[int] = []
    seen: set[int] = set()
    for match in _CANDIDATE_WIDTH_RE.finditer(stripped):
        value = int(match.group(1))
        if value not in seen:
            seen.add(value)
            values.append(value)
    return values


def _quarantine_status_detail(
    block: RawOrderBlock,
    features: ExtractedFeatures,
    article_index: dict[str, list[CatalogEntity]],
) -> str:
    query = _order_match_text(block)
    articles = extract_article_tokens(query)
    if articles and not any(token in article_index for token in articles):
        return _QUARANTINE_ARTICLE_NOT_FOUND

    combined = query.lower()
    if _is_shelf_support_fittings(query):
        return _QUARANTINE_MISSING_CATALOG
    is_glass = block.item_type.strip().lower() == "стекло" or "стекло" in combined or "зеркал" in combined
    if is_glass and (features.dimensions or _CUSTOM_SIZE_HINT_RE.search(query)):
        return _QUARANTINE_CUSTOM_SIZE
    if _CUSTOM_SIZE_HINT_RE.search(query):
        return _QUARANTINE_CUSTOM_SIZE
    return _QUARANTINE_MISSING_CATALOG


def _intersect_entities(pools: list[list[CatalogEntity]]) -> list[CatalogEntity]:
    if not pools:
        return []
    code_sets = [
        {entity.nomenclature_code: entity for entity in pool}
        for pool in pools
    ]
    common_codes = set(code_sets[0])
    for mapping in code_sets[1:]:
        common_codes &= set(mapping)
    return [code_sets[0][code] for code in common_codes]


def _unique_by_code(entities: list[CatalogEntity]) -> list[CatalogEntity]:
    unique: dict[str, CatalogEntity] = {}
    for entity in entities:
        unique[entity.nomenclature_code] = entity
    return list(unique.values())


def _prefer_distinctive_overlap(query: str, entities: list[CatalogEntity]) -> list[CatalogEntity]:
    if len(entities) <= 1:
        return entities
    tokens = set(_distinctive_query_tokens(query))
    if not tokens:
        return entities
    scored: list[tuple[int, CatalogEntity]] = []
    for entity in entities:
        entity_tokens = set(_significant_tokens(_normalize_product_slug(_entity_search_text(entity))))
        scored.append((len(tokens & entity_tokens), entity))
    best = max(score for score, _entity in scored)
    if best <= 0:
        return entities
    winners = [entity for score, entity in scored if score == best]
    if len(winners) == 1:
        return winners
    return winners if best >= 2 else entities


def _distinctive_query_tokens(query: str) -> list[str]:
    slug = _normalize_product_slug(canonicalize_search_text(query))
    return [
        token
        for token in _significant_tokens(slug)
        if token not in _GENERIC_IDENTITY_TOKENS and len(token) >= 4
    ]


def _is_shelf_support_fittings(text: str) -> bool:
    """Glass-shelf support kits are warehouse fittings, not cut-to-size kitchen glass."""
    return bool(re.search(r"полкодерж", text, re.IGNORECASE))


def _is_hardware_query(text: str) -> bool:
    if _is_corpus_module_query(text) and not _is_explicit_drawer_order(text):
        return False
    if _is_shelf_support_fittings(text):
        return True
    stripped = _hardware_query_text(text)
    if _HARDWARE_HINT_RE.search(stripped):
        return True
    return bool(_order_hardware_types(text))


def _hardware_query_text(text: str) -> str:
    return _HANDLE_PAREN_RE.sub(" ", expand_furniture_abbreviations(text))


def _order_hardware_types(query: str) -> list[str]:
    return extract_hardware_types(_hardware_query_text(query))


def _hardware_type_in_text(token: str, text: str) -> bool:
    return (
        re.search(rf"(?<![а-яa-z0-9]){re.escape(token)}", text, re.IGNORECASE) is not None
    )


def _hardware_passthrough_decision(
    block: RawOrderBlock,
    features: ExtractedFeatures,
    candidates: list[MatchCandidate],
) -> MatchDecision | None:
    query = _order_match_text(block)
    if _is_corpus_module_query(query) and not _is_explicit_drawer_order(query):
        return None
    if not _is_hardware_query(query):
        return None
    detail = _quarantine_status_detail(block, features, {})
    if detail == _QUARANTINE_CUSTOM_SIZE:
        return None
    return MatchDecision(
        raw_block=block,
        extracted_features=features,
        status="MATCHED_AUTO",
        matched_entity=None,
        confidence_score=1.0,
        candidates=candidates[:_LLM_CANDIDATE_POOL],
        match_method="AUTO_NO_BARCODE",
    )


def _hardware_size_tokens(text: str) -> list[str]:
    return [re.sub(r"\s+", "", match.group(0)).lower() for match in _HARDWARE_SIZE_RE.finditer(text)]


def _hardware_modifier_tokens(query: str, type_tokens: list[str]) -> list[str]:
    type_set = set(type_tokens)
    size_tokens = {token for token in _hardware_size_tokens(query)}
    modifiers: list[str] = []
    for token in _distinctive_query_tokens(query):
        if token in type_set:
            continue
        compact = token.replace(" ", "")
        if compact in size_tokens or token in {"угловая", "угловой", "цоколя", "цоколь"}:
            modifiers.append(token)
    return modifiers


def _hardware_entity_matches(
    query: str,
    features: ExtractedFeatures,
    entity: CatalogEntity,
) -> bool:
    entity_text = _entity_search_text(entity).lower()
    entity_compact = entity_text.replace(" ", "")
    type_tokens = _order_hardware_types(query)
    if type_tokens and not all(_hardware_type_in_text(token, entity_text) for token in type_tokens):
        return False

    articles = extract_article_tokens(query)
    if articles:
        entity_articles = set(extract_article_tokens(_entity_search_text(entity)))
        if not any(token in entity_articles or token in entity_compact for token in articles):
            return False

    for size in _hardware_size_tokens(query):
        if size not in entity_compact:
            return False

    if features.matched_colors:
        entity_color = (entity.color or "").strip()
        in_field = bool(entity_color) and _colors_overlap(features.matched_colors, entity_color)
        in_text = any(color.lower() in entity_text for color in features.matched_colors)
        if not in_field and not in_text:
            return False
    return True


def _select_unique_hardware(
    query: str,
    entities: list[CatalogEntity],
    features: ExtractedFeatures,
) -> CatalogEntity | None:
    matched = [entity for entity in entities if _hardware_entity_matches(query, features, entity)]
    unique = _unique_by_code(matched)
    if len(unique) == 1:
        return unique[0]
    preferred = _prefer_distinctive_overlap(query, unique)
    unique = _unique_by_code(preferred)
    if len(unique) == 1:
        return unique[0]
    return None


def _has_high_confidence_tuple(
    block: RawOrderBlock,
    features: ExtractedFeatures,
    entity: CatalogEntity,
) -> bool:
    if not features.matched_models or not (entity.label_model or "").strip():
        return False
    if not _models_overlap(features.matched_models, entity.label_model or ""):
        return False
    if not _packaging_values_match(features.package_ratio, _entity_packaging_token(entity)):
        return False
    if not features.matched_colors or not (entity.color or "").strip():
        return False
    if not _colors_overlap(features.matched_colors, entity.color or ""):
        return False
    module = (entity.module or "").strip()
    if not module:
        return False
    query_lower = _order_match_text(block).lower()
    if module.lower() in query_lower:
        return True
    module_tokens = [token for token in _significant_tokens(module.lower()) if len(token) >= 2]
    return bool(module_tokens) and all(token in query_lower for token in module_tokens)
