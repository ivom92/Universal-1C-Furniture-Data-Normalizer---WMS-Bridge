"""Tests for src.utils.auth — PIN authentication and brute-force protection.

Sprint 8.26 — Production Web Hardening & PIN Auth
"""

from __future__ import annotations

import inspect
import time

import pytest

from src.utils.auth import BruteForceProtector, is_auth_required, verify_pin


# ---------------------------------------------------------------------------
# verify_pin
# ---------------------------------------------------------------------------


class TestVerifyPin:
    """Unit tests for verify_pin()."""

    def test_correct_pin_returns_true(self) -> None:
        assert verify_pin("7788", "7788") is True

    def test_wrong_pin_returns_false(self) -> None:
        assert verify_pin("0000", "7788") is False

    def test_empty_pin_mismatch(self) -> None:
        assert verify_pin("", "7788") is False

    def test_empty_target_and_empty_input(self) -> None:
        """Both empty — compare_digest returns True (edge-case for disabled-auth path)."""
        assert verify_pin("", "") is True

    def test_whitespace_stripped_before_compare(self) -> None:
        assert verify_pin(" 7788 ", "7788") is True
        assert verify_pin("7788", " 7788 ") is True

    def test_case_sensitive(self) -> None:
        assert verify_pin("ABCD", "abcd") is False

    def test_numeric_pins(self) -> None:
        assert verify_pin("123456", "123456") is True
        assert verify_pin("123456", "123457") is False


# ---------------------------------------------------------------------------
# Timing-attack safety
# ---------------------------------------------------------------------------


def test_timing_attack_safety() -> None:
    """verify_pin MUST use hmac.compare_digest — not == — to prevent timing attacks."""
    source = inspect.getsource(verify_pin)
    assert "hmac.compare_digest" in source, (
        "verify_pin must use hmac.compare_digest for constant-time comparison"
    )


# ---------------------------------------------------------------------------
# is_auth_required
# ---------------------------------------------------------------------------


class TestIsAuthRequired:
    def test_pin_not_set_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("WAREHOUSE_PIN", raising=False)
        assert is_auth_required() is False

    def test_empty_pin_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WAREHOUSE_PIN", "")
        assert is_auth_required() is False

    def test_whitespace_only_pin_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WAREHOUSE_PIN", "   ")
        # Pydantic strips the value; empty after strip → falsy → auth not required
        # This test documents intentional behavior: a whitespace-only PIN is invalid.
        # (str.strip on "   " == "" → bool("") == False)
        assert is_auth_required() is False

    def test_set_pin_returns_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WAREHOUSE_PIN", "7788")
        assert is_auth_required() is True

    def test_any_non_empty_pin_returns_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WAREHOUSE_PIN", "0")
        assert is_auth_required() is True


# ---------------------------------------------------------------------------
# BruteForceProtector
# ---------------------------------------------------------------------------


class TestBruteForceProtector:
    def test_initial_state_not_locked(self) -> None:
        protector = BruteForceProtector()
        assert not protector.is_locked_out()
        assert protector.failed_attempts == 0
        assert protector.seconds_remaining() == 0

    def test_one_failure_does_not_lock(self) -> None:
        protector = BruteForceProtector()
        protector.record_failure()
        assert not protector.is_locked_out()
        assert protector.failed_attempts == 1

    def test_two_failures_do_not_lock(self) -> None:
        protector = BruteForceProtector()
        protector.record_failure()
        protector.record_failure()
        assert not protector.is_locked_out()
        assert protector.failed_attempts == 2

    def test_brute_force_lockout_after_three_failures(self) -> None:
        """Lockout activates on the 3rd consecutive failure."""
        protector = BruteForceProtector()
        protector.record_failure()
        protector.record_failure()
        protector.record_failure()
        assert protector.is_locked_out()
        assert protector.seconds_remaining() > 0

    def test_lockout_duration_approximately_correct(self) -> None:
        protector = BruteForceProtector(lockout_seconds=30)
        for _ in range(3):
            protector.record_failure()
        remaining = protector.seconds_remaining()
        assert 28 <= remaining <= 30, f"Expected ~30s, got {remaining}s"

    def test_success_resets_counter_and_clears_lockout(self) -> None:
        protector = BruteForceProtector()
        protector.record_failure()
        protector.record_failure()
        protector.record_success()
        assert not protector.is_locked_out()
        assert protector.failed_attempts == 0
        assert protector.seconds_remaining() == 0

    def test_success_after_lockout_clears_lockout(self) -> None:
        protector = BruteForceProtector()
        for _ in range(3):
            protector.record_failure()
        assert protector.is_locked_out()
        protector.record_success()
        assert not protector.is_locked_out()

    def test_custom_max_attempts(self) -> None:
        protector = BruteForceProtector(max_attempts=5, lockout_seconds=10)
        for i in range(4):
            protector.record_failure()
            assert not protector.is_locked_out(), f"Should not lock after {i + 1} failures"
        protector.record_failure()
        assert protector.is_locked_out()

    def test_lockout_expires(self) -> None:
        """Lockout with 1-second window expires naturally."""
        protector = BruteForceProtector(max_attempts=3, lockout_seconds=1)
        for _ in range(3):
            protector.record_failure()
        assert protector.is_locked_out()
        time.sleep(1.1)
        assert not protector.is_locked_out()
        assert protector.seconds_remaining() == 0

    def test_max_attempts_property(self) -> None:
        protector = BruteForceProtector(max_attempts=5)
        assert protector.max_attempts == 5


# ---------------------------------------------------------------------------
# Regression: auth module must not interfere with the existing test suite
# ---------------------------------------------------------------------------


def test_pin_not_set_allows_access(monkeypatch: pytest.MonkeyPatch) -> None:
    """When WAREHOUSE_PIN is absent, is_auth_required() is False — no gate in CI."""
    monkeypatch.delenv("WAREHOUSE_PIN", raising=False)
    assert is_auth_required() is False


def test_auth_module_importable_without_side_effects() -> None:
    """Importing auth must not raise and must not read stdin/stdout."""
    import importlib

    import src.utils.auth as auth_mod

    importlib.reload(auth_mod)
    assert callable(auth_mod.verify_pin)
    assert callable(auth_mod.is_auth_required)
    assert auth_mod.BruteForceProtector is not None


def test_config_module_importable(monkeypatch: pytest.MonkeyPatch) -> None:
    """src.config must expose AppConfig and get_config without errors."""
    monkeypatch.setenv("WAREHOUSE_PIN", "test_pin")
    from src.config import AppConfig, get_config

    cfg = get_config()
    assert isinstance(cfg, AppConfig)
    assert cfg.warehouse_pin == "test_pin"


def test_app_ui_imports_auth() -> None:
    """app_ui.py source must import the auth module — contract check."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app_ui.py").read_text(encoding="utf-8")
    assert "from src.utils.auth import" in src
    assert "is_auth_required" in src
    assert "verify_pin" in src
    assert "BruteForceProtector" in src


def test_app_ui_has_pin_screen() -> None:
    """app_ui.py must contain the PIN screen rendering function."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app_ui.py").read_text(encoding="utf-8")
    assert "_render_pin_screen" in src
    assert "🔐 Доступ к WMS Bridge" in src
    assert "authenticated" in src


def test_app_ui_has_logout_button() -> None:
    """app_ui.py must contain the logout button wired to clear session auth."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app_ui.py").read_text(encoding="utf-8")
    assert "🔒 Выйти" in src
    assert 'st.session_state["authenticated"] = False' in src


def test_streamlit_config_has_max_upload_size() -> None:
    """Production .streamlit/config.toml must include maxUploadSize."""
    from pathlib import Path

    config_path = Path(__file__).resolve().parents[1] / ".streamlit" / "config.toml"
    assert config_path.is_file()
    content = config_path.read_text(encoding="utf-8")
    assert "maxUploadSize" in content
    assert "enableXsrfProtection = true" in content
