"""Tests for warehouse readiness script (Gemini key pool awareness)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from scripts.check_system_health import _check_llm, _run_llm_deep_check


class TestCheckLlmGeminiKeys:
    def test_ok_with_gemini_api_keys_pool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "gemini")
        monkeypatch.setenv("GEMINI_API_KEYS", "key-one, key-two")
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.return_value = mock_response

        with patch("scripts.check_system_health.httpx.Client", return_value=mock_client):
            result = _check_llm()

        assert result.ok is True
        assert result.name == "Gemini API"
        assert "Найдено ключей: 2" in result.detail
        assert "gemini-3.5-flash-lite" in result.detail
        mock_client.get.assert_called_once()
        assert mock_client.get.call_args.kwargs["params"]["key"] == "key-one"

    def test_ok_with_single_gemini_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "gemini")
        monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
        monkeypatch.setenv("GEMINI_API_KEY", "solo-key")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.return_value = mock_response

        with patch("scripts.check_system_health.httpx.Client", return_value=mock_client):
            result = _check_llm()

        assert result.ok is True
        assert "Найдено ключей: 1" in result.detail

    def test_fail_when_no_gemini_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "gemini")
        monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        result = _check_llm()

        assert result.ok is False
        assert result.detail == "Не задан GEMINI_API_KEYS или GEMINI_API_KEY в .env"

    def test_fail_on_api_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "gemini")
        monkeypatch.setenv("GEMINI_API_KEY", "bad-key")

        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.return_value = mock_response

        with patch("scripts.check_system_health.httpx.Client", return_value=mock_client):
            result = _check_llm()

        assert result.ok is False
        assert "403" in result.detail

    @patch("scripts.check_system_health.LLMResolver")
    def test_ollama_provider_skips_gemini_keys(
        self,
        mock_resolver_cls: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        mock_resolver = MagicMock()
        mock_resolver.is_available.return_value = True
        mock_resolver.has_ollama_model.return_value = True
        mock_resolver.ollama_model = "qwen2.5:7b"
        mock_resolver.ollama_base_url = "http://localhost:11434"
        mock_resolver_cls.return_value = mock_resolver

        result = _check_llm()

        assert result.ok is True
        assert result.name == "LLM (Ollama)"


class TestLlmDeepCheck:
    @patch("scripts.test_gemini_connection.main")
    def test_deep_check_ok(self, mock_main: MagicMock) -> None:
        mock_main.side_effect = SystemExit(0)
        result = _run_llm_deep_check()
        assert result.ok is True
        assert result.name == "Gemini Deep Check"

    @patch("scripts.test_gemini_connection.main")
    def test_deep_check_fail(self, mock_main: MagicMock) -> None:
        mock_main.side_effect = SystemExit(1)
        result = _run_llm_deep_check()
        assert result.ok is False
        assert "кодом 1" in result.detail
