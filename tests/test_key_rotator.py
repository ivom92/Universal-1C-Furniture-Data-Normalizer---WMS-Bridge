"""Tests for Gemini API key pool rotation and parsing."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from src.matcher.key_rotator import KeyPool, parse_gemini_api_keys


class TestParseGeminiApiKeys:
    def test_parse_comma_separated_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GEMINI_API_KEYS", "key1, key2 , key3")
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        assert parse_gemini_api_keys() == ["key1", "key2", "key3"]

    def test_fallback_to_single_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
        monkeypatch.setenv("GEMINI_API_KEY", "solo-key")
        assert parse_gemini_api_keys() == ["solo-key"]

    def test_explicit_key_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GEMINI_API_KEYS", "env1,env2")
        assert parse_gemini_api_keys(explicit_key="override") == ["override"]

    def test_empty_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        assert parse_gemini_api_keys() == []


class TestKeyPoolRoundRobin:
    def test_round_robin_cycles_keys(self) -> None:
        pool = KeyPool(["alpha", "beta", "gamma"])
        assert pool.get_next_key() == "alpha"
        assert pool.get_next_key() == "beta"
        assert pool.get_next_key() == "gamma"
        assert pool.get_next_key() == "alpha"

    def test_unavailable_when_empty(self) -> None:
        pool = KeyPool([])
        assert pool.is_available is False
        assert pool.get_next_key() is None

    def test_mark_exhausted_skips_key_until_cooldown_expires(self) -> None:
        pool = KeyPool(["key-a", "key-b"], cooldown_seconds=60)
        pool.mark_exhausted("key-a")
        assert pool.get_next_key() == "key-b"
        assert pool.get_next_key() == "key-b"

    def test_mark_exhausted_recovers_after_cooldown(self) -> None:
        pool = KeyPool(["key-a", "key-b"], cooldown_seconds=1)
        pool.mark_exhausted("key-a", cooldown_seconds=0)
        assert pool.get_next_key() == "key-a"

    @patch("src.matcher.key_rotator.time.monotonic")
    def test_cooldown_expires_and_key_returns(self, mock_monotonic) -> None:
        mock_monotonic.side_effect = [100.0, 100.0, 161.0, 161.0]
        pool = KeyPool(["key-a", "key-b"], cooldown_seconds=60)
        pool.mark_exhausted("key-a")
        assert pool.get_next_key() == "key-b"
        assert pool.get_next_key() == "key-a"

    def test_from_env_uses_explicit_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GEMINI_API_KEYS", "env-key")
        pool = KeyPool.from_env(explicit_key="ctor-key")
        assert pool.keys == ["ctor-key"]

    @patch("src.matcher.key_rotator.logger")
    def test_mark_exhausted_logs_warning(self, mock_logger) -> None:
        pool = KeyPool(["abcdefghij"])
        pool.mark_exhausted("abcdefghij", cooldown_seconds=60)
        mock_logger.warning.assert_called_once()
        message = mock_logger.warning.call_args.args[0]
        assert "...ghij" in mock_logger.warning.call_args.args[1] or "ghij" in str(mock_logger.warning.call_args)


class TestKeyPoolTestConnection:
    def test_empty_pool_reports_missing_keys(self) -> None:
        result = KeyPool([]).test_connection()
        assert result.ok is False
        assert result.total == 0
        assert "не заданы" in result.message.lower()

    @patch("src.llm.gemini_client.probe_gemini_models_list")
    def test_all_keys_active(self, mock_probe) -> None:
        mock_probe.return_value.status_code = 200
        pool = KeyPool(["key-a", "key-b"])
        result = pool.test_connection(base_url="https://proxy.example.com")
        assert result.ok is True
        assert result.active == 2
        assert result.total == 2
        assert "Все 2 ключей активны" in result.message
        assert mock_probe.call_count == 2

    @patch("src.llm.gemini_client.probe_gemini_models_list")
    def test_partial_key_failure(self, mock_probe) -> None:
        class _Response:
            def __init__(self, status_code: int) -> None:
                self.status_code = status_code

        mock_probe.side_effect = [_Response(200), _Response(403)]
        pool = KeyPool(["good-key", "bad-key"])
        result = pool.test_connection()
        assert result.ok is False
        assert result.active == 1
        assert "1 из 2" in result.message
