"""Golden checks for suites whose documented standalone command must stay hermetic."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_api_fixture_restores_database_models_identity():
    """Keep the package attribute, module cache, and strict models in sync."""
    database_package = importlib.import_module("database")
    original_models = importlib.import_module("database.models")
    original_deal = original_models.Deal

    from tests.test_api_v1 import load_app

    with pytest.MonkeyPatch.context() as monkeypatch:
        load_app(monkeypatch)

    assert sys.modules["database.models"] is original_models
    assert database_package.models is original_models
    assert original_models.Deal is original_deal


def test_csrf_protection_fixture_loads_in_a_clean_process():
    """Catch fixture stubs that pass only after another suite primes imports."""
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            ("tests/test_csrf_protection.py::TestCSRFTokens::" "test_login_sets_csrf_cookie"),
            "-q",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
