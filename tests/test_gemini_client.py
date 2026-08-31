"""Tests for Gemini native API-key auth helpers (AQ. keys)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.llm.gemini_client import (
    build_gemini_client,
    gemini_auth_headers,
    gemini_auth_query_params,
    gemini_models_list_url,
    probe_gemini_models_list,
)


class TestGeminiAuthHelpers:
    def test_auth_headers_use_x_goog_api_key_not_bearer(self) -> None:
        key = "AQ.Ab8RN6JxufXiLiskODCEid-HRy6BOcgc1j2eQ0HIv1KiJqr9Rw"
        headers = gemini_auth_headers(key)
        assert headers == {"x-goog-api-key": key}
        assert "Authorization" not in headers
        assert not any("Bearer" in str(value) for value in headers.values())

    def test_auth_headers_strip_whitespace(self) -> None:
        headers = gemini_auth_headers("  test-key  ")
        assert headers == {"x-goog-api-key": "test-key"}

    def test_auth_query_params(self) -> None:
        assert gemini_auth_query_params("  test-key  ") == {"key": "test-key"}

    def test_models_list_url_direct(self) -> None:
        assert gemini_models_list_url() == (
            "https://generativelanguage.googleapis.com/v1beta/models"
        )

    def test_models_list_url_proxy_strips_trailing_slash(self) -> None:
        assert gemini_models_list_url("https://gemini-proxy.example.com/") == (
            "https://gemini-proxy.example.com/v1beta/models"
        )


class TestBuildGeminiClient:
    @patch("google.genai.Client")
    def test_client_uses_x_goog_api_key_in_http_options(self, mock_client: MagicMock) -> None:
        build_gemini_client(
            "AQ.test-key",
            timeout=25.0,
            base_url="https://gemini-proxy.example.com/",
        )
        kwargs = mock_client.call_args.kwargs
        assert kwargs["api_key"] == "AQ.test-key"
        http_options = kwargs["http_options"]
        assert http_options.headers["x-goog-api-key"] == "AQ.test-key"
        assert "Authorization" not in (http_options.headers or {})
        assert http_options.base_url == "https://gemini-proxy.example.com"
        assert http_options.timeout == 25000

    @patch("google.genai.Client")
    @patch("src.llm.gemini_client.resolve_gemini_base_url")
    def test_client_applies_configured_proxy_when_base_url_omitted(
        self,
        mock_resolve: MagicMock,
        mock_client: MagicMock,
    ) -> None:
        mock_resolve.return_value = "https://gemini-proxy.example.com"
        build_gemini_client("AQ.test-key", timeout=25.0)
        http_options = mock_client.call_args.kwargs["http_options"]
        assert http_options.base_url == "https://gemini-proxy.example.com"

    def test_build_gemini_client_requires_key(self) -> None:
        with pytest.raises(ValueError, match="required"):
            build_gemini_client(None, timeout=10.0)
        with pytest.raises(ValueError, match="required"):
            build_gemini_client("   ", timeout=10.0)


class TestProbeGeminiModelsList:
    @patch("src.llm.gemini_client.httpx.Client")
    def test_probe_sends_native_auth_not_bearer(self, mock_client_cls: MagicMock) -> None:
        mock_response = mock_client_cls.return_value.__enter__.return_value.get.return_value
        mock_response.status_code = 200

        response = probe_gemini_models_list(
            "AQ.test-key",
            base_url="https://proxy.example.com",
            timeout=5.0,
        )

        assert response.status_code == 200
        call_kwargs = mock_client_cls.return_value.__enter__.return_value.get.call_args.kwargs
        assert call_kwargs["headers"] == {"x-goog-api-key": "AQ.test-key"}
        assert call_kwargs.get("params") is None
        assert "Authorization" not in call_kwargs.get("headers", {})

    @patch("src.llm.gemini_client.httpx.Client")
    @patch("src.llm.gemini_client.resolve_gemini_base_url")
    def test_probe_uses_configured_proxy_by_default(
        self,
        mock_resolve: MagicMock,
        mock_client_cls: MagicMock,
    ) -> None:
        mock_resolve.return_value = "https://proxy.example.com"
        mock_response = mock_client_cls.return_value.__enter__.return_value.get.return_value
        mock_response.status_code = 200

        probe_gemini_models_list("AQ.test-key")

        mock_resolve.assert_called_once_with(None)
        url = mock_client_cls.return_value.__enter__.return_value.get.call_args.args[0]
        assert url == "https://proxy.example.com/v1beta/models"
