"""Tests for database.firestore_timeouts — env parsing, defaults, call-time reads."""

import pytest

from database import firestore_timeouts as ft


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv(ft.ENV_TIMEOUT, raising=False)
    monkeypatch.delenv(ft.ENV_LONG_TIMEOUT, raising=False)


def test_defaults_when_unset():
    assert ft.firestore_timeout() == 10.0
    assert ft.firestore_long_timeout() == 60.0


def test_valid_env_override(monkeypatch):
    monkeypatch.setenv(ft.ENV_TIMEOUT, "2.5")
    monkeypatch.setenv(ft.ENV_LONG_TIMEOUT, "120")
    assert ft.firestore_timeout() == 2.5
    assert ft.firestore_long_timeout() == 120.0


@pytest.mark.parametrize("bad", ["", "abc", "0", "-5", "  ", "None", "1e-3x", "inf-nan"])
def test_invalid_env_degrades_to_default(monkeypatch, bad):
    monkeypatch.setenv(ft.ENV_TIMEOUT, bad)
    assert ft.firestore_timeout() == ft.DEFAULT_TIMEOUT_SECONDS


def test_invalid_long_timeout_degrades_to_default(monkeypatch):
    monkeypatch.setenv(ft.ENV_LONG_TIMEOUT, "0")
    assert ft.firestore_long_timeout() == ft.DEFAULT_LONG_TIMEOUT_SECONDS
    monkeypatch.setenv(ft.ENV_LONG_TIMEOUT, "-1")
    assert ft.firestore_long_timeout() == ft.DEFAULT_LONG_TIMEOUT_SECONDS


def test_whitespace_padded_value(monkeypatch):
    monkeypatch.setenv(ft.ENV_TIMEOUT, " 7.5 ")
    assert ft.firestore_timeout() == 7.5


def test_timeout_is_read_at_call_time(monkeypatch):
    """Operators can tune via env without restart; tests can monkeypatch."""
    assert ft.firestore_timeout() == 10.0
    monkeypatch.setenv(ft.ENV_TIMEOUT, "3")
    assert ft.firestore_timeout() == 3.0  # no import-time caching


def test_timeouts_are_independent(monkeypatch):
    monkeypatch.setenv(ft.ENV_TIMEOUT, "4")
    assert ft.firestore_timeout() == 4.0
    assert ft.firestore_long_timeout() == 60.0
