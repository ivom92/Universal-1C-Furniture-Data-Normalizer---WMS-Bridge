"""Unit tests for Gemini connection diagnostic script formatting."""

from __future__ import annotations

from scripts.test_gemini_connection import (
    KeyPingResult,
    _format_error_status,
    _format_ok_status,
    _interpret_gemini_error,
)


def test_format_ok_status() -> None:
    assert _format_ok_status(1, 240.4) == "🟢 Ключ #1: OK (Задержка 240мс)"


def test_format_error_status() -> None:
    assert _format_error_status(2, "401") == "🔴 Ключ #2: Ошибка (401)"


def test_interpret_gemini_error_unauthenticated() -> None:
    code, _msg = _interpret_gemini_error(Exception("401 UNAUTHENTICATED"))
    assert code == "401"


def test_key_ping_result_dataclass() -> None:
    result = KeyPingResult(index=1, ok=True, latency_ms=120.0)
    assert result.error_code is None
