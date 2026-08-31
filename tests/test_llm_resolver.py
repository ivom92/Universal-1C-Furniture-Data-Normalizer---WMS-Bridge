"""Tests for dual-engine LLM resolver and HybridMatcher LLM integration (Sprint 4)."""

from __future__ import annotations

import json
from typing import Optional
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.matcher.dynamic_vocab import DynamicVocabulary
from src.matcher.feature_extractor import FeatureExtractor
from src.matcher.hybrid_matcher import HybridMatcher
from src.matcher.llm_resolver import (
    LLMResolver,
    build_gemini_client,
    call_with_retry,
    gemini_models_list_url,
    is_gemini_model_not_found,
    is_key_failover_error,
    is_retryable_llm_error,
    is_retryable_llm_error_without_failover,
    parse_llm_json_response,
    resolve_gemini_base_url,
    sanitize_json_text,
)
from src.models import (
    CatalogEntity,
    LLMResolutionResponse,
    MatchCandidate,
    RawOrderBlock,
)


def _catalog_entity(
    *,
    nomenclature: str,
    nomenclature_code: str,
    packaging: Optional[str] = None,
    module: Optional[str] = None,
    label_model: Optional[str] = None,
    barcode: Optional[str] = None,
) -> CatalogEntity:
    return CatalogEntity.model_validate(
        {
            "Номенклатура": nomenclature,
            "НоменклатураКод": nomenclature_code,
            "Упаковка": packaging,
            "Модуль": module,
            "ЭтикеткаМодель": label_model,
            "Штрихкод": barcode,
        }
    )


def _raw_block(
    *,
    client_description: str,
    factory_alias: str = "",
    item_type: str = "Пачка",
    line_number: int = 1,
) -> RawOrderBlock:
    return RawOrderBlock(
        line_number=line_number,
        client_description=client_description,
        item_type=item_type,
        quantity=1,
        factory_alias=factory_alias or client_description,
        order_service_line="Продажи оптовые УРП_ test",
        excel_row_start=line_number,
    )


class FakeVectorStore:
    """Deterministic vector store stub returning fixed candidates."""

    def __init__(
        self,
        hits: list[tuple[CatalogEntity, float]],
        catalog: list[CatalogEntity] | None = None,
    ) -> None:
        self._hits = hits
        self._catalog = catalog if catalog is not None else []

    @property
    def catalog(self) -> list[CatalogEntity]:
        return list(self._catalog)

    def search(self, query_text: str, top_k: int = 20) -> list[tuple[CatalogEntity, float]]:
        return self._hits[:top_k]


class TestLLMResponseParsing:
    def test_llm_response_parsing(self) -> None:
        payload = {
            "selected_nomenclature_code": "00000097658",
            "confidence": 0.92,
            "reasoning": "Совпадают габариты и упаковка 1/1",
        }
        response = LLMResolutionResponse.model_validate(payload)

        assert response.selected_nomenclature_code == "00000097658"
        assert response.confidence == pytest.approx(0.92)
        assert "габариты" in response.reasoning

    def test_llm_response_null_code(self) -> None:
        payload = {
            "selected_nomenclature_code": None,
            "confidence": 0.0,
            "reasoning": "Ни один кандидат не подходит",
        }
        response = LLMResolutionResponse.model_validate(payload)
        assert response.selected_nomenclature_code is None

    def test_llm_response_string_null_literal(self) -> None:
        payload = {
            "selected_nomenclature_code": "null",
            "confidence": 0.0,
            "reasoning": "Отказ",
        }
        response = LLMResolutionResponse.model_validate(payload)
        assert response.selected_nomenclature_code is None


class TestMockedLLMResolution:
    @patch.object(HybridMatcher, "_should_auto_match", return_value=False)
    def test_mocked_llm_resolution(
        self,
        _auto_match: MagicMock,
        feature_extractor: FeatureExtractor,
    ) -> None:
        entity_a = _catalog_entity(
            nomenclature="Фасад Плано 116х596х16 1/1",
            nomenclature_code="00000097658",
            packaging="1/1",
            module="116х596х16",
            label_model="Плано",
            barcode="2006000045445",
        )
        entity_b = _catalog_entity(
            nomenclature="Фасад Плано 140х596х16 1/1",
            nomenclature_code="00000097659",
            packaging="1/1",
            module="140х596х16",
            label_model="Плано",
            barcode="2006000045446",
        )

        mock_resolver = MagicMock(spec=LLMResolver)
        mock_resolver.provider = "gemini"
        mock_resolver.resolve.return_value = LLMResolutionResponse(
            selected_nomenclature_code="00000097658",
            confidence=0.88,
            reasoning="Точное совпадение 116х596",
        )

        matcher = HybridMatcher(
            FakeVectorStore([(entity_b, 0.91), (entity_a, 0.89)]),
            feature_extractor,
            llm_resolver=mock_resolver,
        )
        block = _raw_block(
            client_description="Фасад 116 x 596 x 16 Плано 1/1",
            factory_alias="Фасад д/выдвижного ящика Плано 116х596х16 1/1",
        )

        decision = matcher.match_block(block)

        assert decision.status == "MATCHED_LLM"
        assert decision.matched_entity is not None
        assert decision.matched_entity.nomenclature_code == "00000097658"
        assert decision.matched_entity.barcode == "2006000045445"
        assert decision.match_method == "LLM_GEMINI"
        assert decision.confidence_score == pytest.approx(0.88)
        mock_resolver.resolve.assert_called_once()


@pytest.fixture
def hybrid_matcher_with_failing_llm(vector_store, feature_extractor):
    failing_resolver = LLMResolver(provider="gemini", gemini_api_key="test-key")
    return HybridMatcher(
        vector_store,
        feature_extractor,
        llm_resolver=failing_resolver,
    ), failing_resolver


class TestGracefulDegradation:
    @patch("src.matcher.llm_resolver.LLMResolver._resolve_gemini")
    def test_graceful_degradation_on_api_error(
        self,
        mock_gemini: MagicMock,
        hybrid_matcher_with_failing_llm,
        order_ruban_parsed,
    ) -> None:
        mock_gemini.side_effect = ConnectionError("Network unreachable")
        matcher, _resolver = hybrid_matcher_with_failing_llm
        blocks = order_ruban_parsed.blocks
        customer_name = order_ruban_parsed.customer_name

        items = matcher.match_order(blocks, customer_name)

        assert len(items) == 55
        assert len(blocks) == len(items)

        status_counts = {"MATCHED_AUTO": 0, "MATCHED_LLM": 0, "QUARANTINE": 0, "NEEDS_LLM": 0}
        for item in items:
            reason = item.match_reason or "QUARANTINE"
            if reason in {"vector_auto", "AUTO_NO_BARCODE", "exact_article", "exact_article_no_barcode"}:
                status_counts["MATCHED_AUTO"] += 1
            elif reason and reason.startswith("LLM_"):
                status_counts["MATCHED_LLM"] += 1
            elif reason == "NEEDS_LLM":
                status_counts["NEEDS_LLM"] += 1
            else:
                status_counts["QUARANTINE"] += 1

        assert status_counts["MATCHED_LLM"] == 0
        assert status_counts["NEEDS_LLM"] == 0
        assert status_counts["MATCHED_AUTO"] + status_counts["QUARANTINE"] == 55

    @patch.object(HybridMatcher, "_should_auto_match", return_value=False)
    def test_resolver_returns_none_goes_to_quarantine(
        self,
        _auto_match: MagicMock,
        feature_extractor: FeatureExtractor,
    ) -> None:
        entity = _catalog_entity(
            nomenclature="Фасад Плано 116х596х16 1/1",
            nomenclature_code="00000097658",
            packaging="1/1",
            module="116х596х16",
            label_model="Плано",
        )
        entity_alt = _catalog_entity(
            nomenclature="Фасад Плано 140х596х16 1/1",
            nomenclature_code="00000097659",
            packaging="1/1",
            module="140х596х16",
            label_model="Плано",
        )

        mock_resolver = MagicMock(spec=LLMResolver)
        mock_resolver.provider = "ollama"
        mock_resolver.resolve.return_value = LLMResolutionResponse(
            selected_nomenclature_code=None,
            confidence=0.0,
            reasoning="LLM Fallback unavaliable",
        )

        matcher = HybridMatcher(
            FakeVectorStore([(entity_alt, 0.91), (entity, 0.89)]),
            feature_extractor,
            llm_resolver=mock_resolver,
        )
        block = _raw_block(
            client_description="Фасад 116 x 596 x 16 Плано 1/1",
            factory_alias="Фасад 116х596 1/1",
        )

        decision = matcher.match_block(block)

        assert decision.status == "QUARANTINE"
        assert decision.matched_entity is None


class TestLLMResolverUnit:
    def test_empty_candidates_returns_none(self) -> None:
        resolver = LLMResolver(provider="gemini")
        block = _raw_block(client_description="Test item")
        features = FeatureExtractor(
            DynamicVocabulary([_catalog_entity(nomenclature="x", nomenclature_code="00000000001")])
        ).extract_features(block)

        response = resolver.resolve(block, features, [])

        assert response.selected_nomenclature_code is None
        assert response.confidence == 0.0

    @patch("src.matcher.llm_resolver.LLMResolver._resolve_gemini")
    def test_resolve_gemini_exception_graceful(
        self,
        mock_gemini: MagicMock,
        feature_extractor: FeatureExtractor,
    ) -> None:
        mock_gemini.side_effect = TimeoutError("API timeout")
        resolver = LLMResolver(provider="gemini", gemini_api_key="test-key")

        entity = _catalog_entity(
            nomenclature="Test 1/1",
            nomenclature_code="00000000001",
            packaging="1/1",
        )
        block = _raw_block(client_description="Test 1/1")
        features = feature_extractor.extract_features(block)
        candidates = [
            MatchCandidate(catalog_entity=entity, similarity_score=0.85, hard_filter_passed=True)
        ]

        response = resolver.resolve(block, features, candidates)

        assert response.selected_nomenclature_code is None
        assert response.reasoning == "LLM request timeout"


class TestOllamaJsonSanitization:
    def test_sanitize_strips_markdown_fence(self) -> None:
        raw = '```json\n{"selected_nomenclature_code": "00000097658", "confidence": 0.9, "reasoning": "ok"}\n```'
        cleaned = sanitize_json_text(raw)
        payload = json.loads(cleaned)
        assert payload["selected_nomenclature_code"] == "00000097658"

    def test_sanitize_extracts_json_from_prose(self) -> None:
        raw = (
            "Вот ответ:\n"
            '{"selected_nomenclature_code": "00000097658", "confidence": 0.85, "reasoning": "габариты"}'
            "\nНадеюсь, это помогло."
        )
        response = parse_llm_json_response(raw)
        assert response.selected_nomenclature_code == "00000097658"
        assert response.confidence == pytest.approx(0.85)

    def test_parse_llm_json_response_null_code(self) -> None:
        raw = '{"selected_nomenclature_code": null, "confidence": 0.0, "reasoning": "нет совпадений"}'
        response = parse_llm_json_response(raw)
        assert response.selected_nomenclature_code is None


class TestOllamaHealthcheck:
    @patch("src.matcher.llm_resolver.httpx.Client")
    def test_is_available_true(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_client.__enter__.return_value = mock_client
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value = mock_client

        resolver = LLMResolver(provider="ollama")
        assert resolver.is_available() is True
        mock_client.get.assert_called_once_with("http://localhost:11434/api/tags")

    @patch("src.matcher.llm_resolver.httpx.Client")
    def test_is_available_false_on_timeout(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.side_effect = httpx.TimeoutException("timeout")
        mock_client_cls.return_value = mock_client

        resolver = LLMResolver(provider="ollama")
        assert resolver.is_available() is False

    @patch("src.matcher.llm_resolver.httpx.Client")
    def test_has_ollama_model(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "models": [{"name": "qwen2.5:7b"}, {"name": "llama3.2:3b"}]
        }
        mock_client.__enter__.return_value = mock_client
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value = mock_client

        resolver = LLMResolver(provider="ollama", ollama_model="qwen2.5:7b")
        assert resolver.has_ollama_model() is True
        assert resolver.has_ollama_model("llama3.2:3b") is True
        assert resolver.has_ollama_model("missing:7b") is False

    @patch("src.matcher.llm_resolver.httpx.Client")
    def test_resolve_ollama_sanitizes_markdown(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "response": (
                '```json\n'
                '{"selected_nomenclature_code": "00000097658", '
                '"confidence": 0.91, "reasoning": "совпадение"}\n'
                "```"
            )
        }
        mock_client.__enter__.return_value = mock_client
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        resolver = LLMResolver(provider="ollama")
        block = _raw_block(client_description="Test")
        features = FeatureExtractor(
            DynamicVocabulary([_catalog_entity(nomenclature="x", nomenclature_code="00000000001")])
        ).extract_features(block)
        entity = _catalog_entity(
            nomenclature="Test 1/1",
            nomenclature_code="00000097658",
            packaging="1/1",
        )
        candidates = [
            MatchCandidate(catalog_entity=entity, similarity_score=0.85, hard_filter_passed=True)
        ]

        response = resolver.resolve(block, features, candidates)

        assert response.selected_nomenclature_code == "00000097658"
        assert response.confidence == pytest.approx(0.91)
        post_payload = mock_client.post.call_args.kwargs["json"]
        assert post_payload["options"]["temperature"] == 0.0


class TestParallelLlmResolver:
    @patch.object(HybridMatcher, "_should_auto_match", return_value=False)
    def test_duplicate_rows_share_single_llm_call(
        self,
        _auto_match: MagicMock,
        feature_extractor: FeatureExtractor,
    ) -> None:
        entity = _catalog_entity(
            nomenclature="Аврора Кровать 140 1/2",
            nomenclature_code="00000011111",
            packaging="1/2",
            label_model="Аврора",
            barcode="2006000011111",
        )
        mock_resolver = MagicMock(spec=LLMResolver)
        mock_resolver.provider = "gemini"
        mock_resolver.max_workers = 8
        mock_resolver.resolve.return_value = LLMResolutionResponse(
            selected_nomenclature_code="00000011111",
            confidence=0.9,
            reasoning="дубликаты",
        )
        matcher = HybridMatcher(
            FakeVectorStore([(entity, 0.9)]),
            feature_extractor,
            llm_resolver=mock_resolver,
        )
        description = "Аврора Кровать 140 с основанием 1/2 дуб сонома/белый"
        blocks = [
            _raw_block(client_description=description, line_number=index)
            for index in (1, 2, 3)
        ]

        decisions = matcher.match_order_decisions(blocks)

        assert mock_resolver.resolve.call_count == 1
        assert len(decisions) == 3
        assert all(decision.status == "MATCHED_LLM" for decision in decisions)
        assert [decision.raw_block.line_number for decision in decisions] == [1, 2, 3]

    @patch.object(HybridMatcher, "_should_auto_match", return_value=False)
    def test_llm_timeout_goes_to_quarantine(
        self,
        _auto_match: MagicMock,
        feature_extractor: FeatureExtractor,
    ) -> None:
        from src.matcher.llm_resolver import _TIMEOUT_REASONING

        entity = _catalog_entity(
            nomenclature="Фасад Плано 116х596х16 1/1",
            nomenclature_code="00000097658",
            packaging="1/1",
            module="116х596х16",
            label_model="Плано",
        )
        mock_resolver = MagicMock(spec=LLMResolver)
        mock_resolver.provider = "gemini"
        mock_resolver.max_workers = 4
        mock_resolver.resolve.return_value = LLMResolutionResponse(
            selected_nomenclature_code=None,
            confidence=0.0,
            reasoning=_TIMEOUT_REASONING,
        )
        matcher = HybridMatcher(
            FakeVectorStore([(entity, 0.9)]),
            feature_extractor,
            llm_resolver=mock_resolver,
        )
        decision = matcher.match_order_decisions(
            [_raw_block(client_description="Фасад 116 x 596 x 16 Плано 1/1")]
        )[0]
        assert decision.status == "QUARANTINE"
        assert decision.status_detail == "Таймаут LLM"

    @patch("src.matcher.llm_resolver.LLMResolver._resolve_gemini_with_key")
    def test_gemini_config_disables_afc(self, mock_resolve_with_key: MagicMock) -> None:
        mock_resolve_with_key.return_value = LLMResolutionResponse(
            selected_nomenclature_code="00000000001",
            confidence=0.5,
            reasoning="ok",
        )

        resolver = LLMResolver(
            provider="gemini",
            gemini_api_key="test-key",
            gemini_model="gemini-2.5-flash-lite",
            timeout=25.0,
        )
        block = _raw_block(client_description="Test 1/1")
        features = FeatureExtractor(
            DynamicVocabulary([_catalog_entity(nomenclature="x", nomenclature_code="00000000001")])
        ).extract_features(block)
        entity = _catalog_entity(
            nomenclature="Test 1/1",
            nomenclature_code="00000000001",
            packaging="1/1",
        )
        candidates = [
            MatchCandidate(catalog_entity=entity, similarity_score=0.85, hard_filter_passed=True)
        ]

        resolver.resolve(block, features, candidates)

        mock_resolve_with_key.assert_called_once()
        assert mock_resolve_with_key.call_args.args[0] == "test-key"

    def test_resolver_cache_reuses_identical_payload(self, feature_extractor: FeatureExtractor) -> None:
        entity = _catalog_entity(
            nomenclature="Test 1/1",
            nomenclature_code="00000000001",
            packaging="1/1",
        )
        resolver = LLMResolver(provider="gemini", gemini_api_key="test-key")
        block = _raw_block(client_description="Test 1/1")
        features = feature_extractor.extract_features(block)
        candidates = [
            MatchCandidate(catalog_entity=entity, similarity_score=0.85, hard_filter_passed=True)
        ]
        with patch.object(
            resolver,
            "_resolve_gemini",
            return_value=LLMResolutionResponse(
                selected_nomenclature_code="00000000001",
                confidence=0.7,
                reasoning="cached",
            ),
        ) as mock_gemini:
            first = resolver.resolve(block, features, candidates)
            second = resolver.resolve(block, features, candidates)
        assert mock_gemini.call_count == 1
        assert first.selected_nomenclature_code == second.selected_nomenclature_code


class TestFlashLiteDefaultsAndRetry:
    def test_default_gemini_model_and_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        resolver = LLMResolver(provider="gemini", gemini_api_key="test-key")
        assert resolver.gemini_model == "gemini-3.5-flash-lite"
        assert resolver.timeout == 25.0

    def test_retryable_error_markers(self) -> None:
        assert is_retryable_llm_error(TimeoutError("deadline"))
        assert is_retryable_llm_error(RuntimeError("504 DEADLINE_EXCEEDED"))
        assert is_retryable_llm_error(RuntimeError("429 Too Many Requests"))
        assert not is_retryable_llm_error(ValueError("invalid json"))
        assert is_key_failover_error(RuntimeError("429 RESOURCE_EXHAUSTED"))
        assert is_key_failover_error(RuntimeError("403 PERMISSION_DENIED"))
        assert not is_retryable_llm_error_without_failover(RuntimeError("429 RESOURCE_EXHAUSTED"))
        assert is_retryable_llm_error_without_failover(RuntimeError("504 DEADLINE_EXCEEDED"))

    @patch("src.matcher.llm_resolver.time.sleep")
    def test_retry_once_on_504_then_succeeds(self, mock_sleep: MagicMock) -> None:
        calls = {"count": 0}

        def flaky() -> str:
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("504 DEADLINE_EXCEEDED")
            return "ok"

        assert call_with_retry(flaky) == "ok"
        assert calls["count"] == 2
        mock_sleep.assert_called_once_with(1.5)

    @patch("src.matcher.llm_resolver.LLMResolver._resolve_gemini_with_key")
    def test_failover_on_429_uses_second_key(
        self,
        mock_resolve_with_key: MagicMock,
        feature_extractor: FeatureExtractor,
    ) -> None:
        mock_resolve_with_key.side_effect = [
            RuntimeError("429 RESOURCE_EXHAUSTED"),
            LLMResolutionResponse(
                selected_nomenclature_code="00000000001",
                confidence=0.8,
                reasoning="failover ok",
            ),
        ]
        resolver = LLMResolver(
            provider="gemini",
            gemini_api_key="key-one,key-two",
        )
        entity = _catalog_entity(
            nomenclature="Test 1/1",
            nomenclature_code="00000000001",
            packaging="1/1",
        )
        block = _raw_block(client_description="Test 1/1")
        features = feature_extractor.extract_features(block)
        candidates = [
            MatchCandidate(catalog_entity=entity, similarity_score=0.85, hard_filter_passed=True)
        ]

        response = resolver.resolve(block, features, candidates)

        assert response.selected_nomenclature_code == "00000000001"
        assert mock_resolve_with_key.call_count == 2
        assert mock_resolve_with_key.call_args_list[0].args[0] in {"key-one", "key-two"}
        assert mock_resolve_with_key.call_args_list[1].args[0] in {"key-one", "key-two"}
        assert (
            mock_resolve_with_key.call_args_list[0].args[0]
            != mock_resolve_with_key.call_args_list[1].args[0]
        )


    @patch("src.matcher.llm_resolver.LLMResolver._gemini_client")
    def test_gemini_generate_config_disables_afc(self, mock_client_factory: MagicMock) -> None:
        mock_models = MagicMock()
        mock_response = MagicMock()
        mock_response.parsed = LLMResolutionResponse(
            selected_nomenclature_code="00000000001",
            confidence=0.5,
            reasoning="ok",
        )
        mock_response.text = None
        mock_models.generate_content.return_value = mock_response
        mock_client = MagicMock()
        mock_client.models = mock_models
        mock_client_factory.return_value = mock_client

        resolver = LLMResolver(
            provider="gemini",
            gemini_api_key="test-key",
            gemini_model="gemini-2.5-flash-lite",
            timeout=25.0,
        )
        resolver._generate_gemini("gemini-2.5-flash-lite", "prompt", api_key="test-key")

        config = mock_models.generate_content.call_args.kwargs["config"]
        assert config.tools == []
        assert config.automatic_function_calling.disable is True
        assert config.temperature == 0.0
        assert mock_models.generate_content.call_args.kwargs["model"] == "gemini-2.5-flash-lite"


class TestGeminiModelFallback:
    def test_not_found_detector(self) -> None:
        assert is_gemini_model_not_found(RuntimeError("404 NOT_FOUND"))
        assert not is_gemini_model_not_found(RuntimeError("429 RESOURCE_EXHAUSTED"))

    @patch("src.matcher.llm_resolver.LLMResolver._gemini_client")
    def test_404_retries_with_gemini_25_flash(
        self,
        mock_client_factory: MagicMock,
        feature_extractor: FeatureExtractor,
    ) -> None:
        mock_models = MagicMock()
        ok_response = MagicMock()
        ok_response.parsed = LLMResolutionResponse(
            selected_nomenclature_code="00000000001",
            confidence=0.8,
            reasoning="fallback",
        )
        ok_response.text = None

        def generate_content(*, model: str, contents: str, config: object):
            if model in {"gemini-2.5-flash-lite", "gemini-3.5-flash-lite"}:
                raise RuntimeError("404 NOT_FOUND")
            assert model == "gemini-2.5-flash"
            return ok_response

        mock_models.generate_content.side_effect = generate_content
        mock_client = MagicMock()
        mock_client.models = mock_models
        mock_client_factory.return_value = mock_client

        resolver = LLMResolver(
            provider="gemini",
            gemini_api_key="test-key",
            gemini_model="gemini-2.5-flash-lite",
        )
        entity = _catalog_entity(
            nomenclature="Test 1/1",
            nomenclature_code="00000000001",
            packaging="1/1",
        )
        block = _raw_block(client_description="Test 1/1")
        features = feature_extractor.extract_features(block)
        candidates = [
            MatchCandidate(catalog_entity=entity, similarity_score=0.85, hard_filter_passed=True)
        ]

        response = resolver.resolve(block, features, candidates)

        assert response.selected_nomenclature_code == "00000000001"
        assert resolver.gemini_model == "gemini-2.5-flash"
        models_used = [call.kwargs["model"] for call in mock_models.generate_content.call_args_list]
        assert models_used == [
            "gemini-2.5-flash-lite",
            "gemini-3.5-flash-lite",
            "gemini-2.5-flash",
        ]


class TestGeminiProxyBaseUrl:
    def test_gemini_client_with_custom_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GEMINI_BASE_URL", "https://gemini-proxy.example.com/")
        monkeypatch.delenv("GOOGLE_GENAI_BASE_URL", raising=False)
        assert resolve_gemini_base_url() == "https://gemini-proxy.example.com"
        resolver = LLMResolver(provider="gemini", gemini_api_key="test-key", timeout=25.0)
        assert resolver.gemini_base_url == "https://gemini-proxy.example.com"
        with patch("google.genai.Client") as mock_client:
            build_gemini_client(
                "test-key",
                timeout=25.0,
                base_url=resolver.gemini_base_url,
            )
        kwargs = mock_client.call_args.kwargs
        assert kwargs["api_key"] == "test-key"
        http_options = kwargs["http_options"]
        assert http_options.headers["x-goog-api-key"] == "test-key"
        assert "Authorization" not in (http_options.headers or {})
        assert http_options.base_url == "https://gemini-proxy.example.com"
        assert http_options.timeout == 25000

    def test_google_genai_base_url_alias(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GEMINI_BASE_URL", raising=False)
        monkeypatch.setenv("GOOGLE_GENAI_BASE_URL", "https://alias-proxy.example.com/")
        resolver = LLMResolver(provider="gemini", gemini_api_key="test-key")
        assert resolver.gemini_base_url == "https://alias-proxy.example.com"

    def test_trailing_slash_stripped_from_models_list_url(self) -> None:
        url = gemini_models_list_url("https://gemini-proxy.example.com/")
        assert url == "https://gemini-proxy.example.com/v1beta/models"
        assert "//v1beta" not in url.replace("https://", "")


class TestLlmCascadeCandidates:
    @patch.object(HybridMatcher, "_should_auto_match", return_value=False)
    def test_needs_llm_passes_faiss_candidates_into_batch(
        self,
        _auto_match: MagicMock,
        feature_extractor: FeatureExtractor,
    ) -> None:
        pack_half = _catalog_entity(
            nomenclature="Аврора Кровать 140 1/2 дуб сонома/белый",
            nomenclature_code="00000014012",
            packaging="1/2",
            label_model="Аврора",
            barcode="2006000014012",
        )
        pack_full = _catalog_entity(
            nomenclature="Аврора Кровать 140 2/2 дуб сонома/белый",
            nomenclature_code="00000014022",
            packaging="2/2",
            label_model="Аврора",
            barcode="2006000014022",
        )
        scandi = _catalog_entity(
            nomenclature="Лацио Сканди Шкаф 1/1",
            nomenclature_code="00000033001",
            packaging="1/1",
            label_model="Лацио Сканди",
            barcode="2006000033001",
        )
        captured: list[list[MatchCandidate]] = []

        def batch(jobs):
            captured.extend(job[2] for job in jobs)
            selected = jobs[0][2][0].catalog_entity.nomenclature_code
            return [
                LLMResolutionResponse(
                    selected_nomenclature_code=selected,
                    confidence=0.93,
                    reasoning="упаковка и модель",
                )
                for _job in jobs
            ]

        mock_resolver = MagicMock(spec=LLMResolver)
        mock_resolver.provider = "gemini"
        mock_resolver.max_workers = 8
        mock_resolver.resolve_candidates_batch.side_effect = batch
        matcher = HybridMatcher(
            FakeVectorStore([(pack_full, 0.88), (pack_half, 0.87), (scandi, 0.70)]),
            feature_extractor,
            llm_resolver=mock_resolver,
        )
        block = _raw_block(
            client_description="Аврора Кровать 140 с основанием 1/2 дуб сонома/белый",
            factory_alias="Аврора Кровать 140 1/2",
        )

        decision = matcher.match_order_decisions([block])[0]

        mock_resolver.resolve_candidates_batch.assert_called_once()
        assert captured
        passed_codes = [candidate.catalog_entity.nomenclature_code for candidate in captured[0]]
        assert "00000014012" in passed_codes
        assert "00000014022" not in passed_codes
        assert decision.status == "MATCHED_LLM"
        assert decision.matched_entity is not None
        assert decision.matched_entity.nomenclature_code == "00000014012"
        assert decision.matched_entity.barcode == "2006000014012"

    @patch.object(HybridMatcher, "_should_auto_match", return_value=False)
    def test_llm_code_resolves_from_full_v8_catalog(
        self,
        _auto_match: MagicMock,
        feature_extractor: FeatureExtractor,
    ) -> None:
        faiss_hit = _catalog_entity(
            nomenclature="Лацио Сканди Комод 1/1",
            nomenclature_code="00000033010",
            packaging="1/1",
            label_model="Лацио Сканди",
            barcode="2006000033010",
        )
        catalog_only = _catalog_entity(
            nomenclature="Лацио Сканди Шкаф 2ств 1/1",
            nomenclature_code="00000033099",
            packaging="1/1",
            label_model="Лацио Сканди",
            barcode="2006000033099",
        )
        mock_resolver = MagicMock(spec=LLMResolver)
        mock_resolver.provider = "gemini"
        mock_resolver.resolve_candidates_batch.return_value = [
            LLMResolutionResponse(
                selected_nomenclature_code="00000033099",
                confidence=0.9,
                reasoning="шкаф из каталога v8",
            )
        ]
        matcher = HybridMatcher(
            FakeVectorStore([(faiss_hit, 0.84)], catalog=[faiss_hit, catalog_only]),
            feature_extractor,
            llm_resolver=mock_resolver,
        )
        block = _raw_block(
            client_description="Лацио Сканди шкаф двухстворчатый без цвета",
            factory_alias="Лацио Сканди Шкаф",
        )

        decision = matcher.match_block(block)

        assert decision.status == "MATCHED_LLM"
        assert decision.matched_entity is not None
        assert decision.matched_entity.nomenclature_code == "00000033099"
        assert decision.matched_entity.barcode == "2006000033099"
