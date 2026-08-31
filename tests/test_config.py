"""Tests for unified AppConfig (Sprint 8.29)."""

from __future__ import annotations

import pytest

from src.config import AppConfig, get_config


class TestAppConfig:
    def test_gemini_api_keys_parses_comma_separated_pool(self) -> None:
        config = AppConfig(GEMINI_API_KEYS="key-one, key-two , key-three")
        assert config.gemini_api_keys == ["key-one", "key-two", "key-three"]

    def test_gemini_api_keys_fallback_to_single_env_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
        monkeypatch.setenv("GEMINI_API_KEY", "solo-key")
        config = AppConfig()
        assert config.gemini_api_keys == ["solo-key"]

    def test_gemini_base_url_strips_trailing_slash(self) -> None:
        config = AppConfig(GEMINI_BASE_URL="https://gemini-proxy.example.com/")
        assert config.gemini_base_url == "https://gemini-proxy.example.com"

    def test_gemini_base_url_empty_string_becomes_none(self) -> None:
        config = AppConfig(GEMINI_BASE_URL="   ")
        assert config.gemini_base_url is None

    def test_get_config_reads_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GEMINI_API_KEYS", "a,b,c")
        monkeypatch.setenv("GEMINI_BASE_URL", "https://gemini-proxy-warehouse.mokshin17.workers.dev")
        monkeypatch.setenv("LLM_PROVIDER", "gemini")
        monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

        config = get_config()

        assert len(config.gemini_api_keys) == 3
        assert config.gemini_base_url == "https://gemini-proxy-warehouse.mokshin17.workers.dev"
        assert config.llm_provider == "gemini"
        assert config.gemini_model == "gemini-3.5-flash-lite"

    def test_get_config_google_genai_base_url_alias(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GEMINI_BASE_URL", raising=False)
        monkeypatch.setenv("GOOGLE_GENAI_BASE_URL", "https://alias-proxy.example.com/")

        config = get_config()

        assert config.gemini_base_url == "https://alias-proxy.example.com"
