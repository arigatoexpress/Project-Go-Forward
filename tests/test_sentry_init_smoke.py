"""Regression test: importing main with SENTRY_DSN set must not crash.

The Sentry init block at main.py module level is guarded by
``os.environ.get("SENTRY_DSN") and not os.environ.get("PYTEST_CURRENT_TEST")``
so it never runs under pytest. In production, with SENTRY_DSN set, it does —
and sentry-sdk 2.x's auto-enabled Starlette integration imports
``Jinja2Templates`` at ``sentry_sdk.init()`` time, raising ImportError when
jinja2 is not installed. That crashed the whole app at import
(requirements.txt now pins jinja2, and the init is wrapped so error tracking
can never take the site down).

This test boots a clean interpreter (no PYTEST_* env vars) with SENTRY_DSN
and ADMIN_PIN_HASH set and asserts the module imports and Sentry initializes.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _clean_env() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST")}
    env["SENTRY_DSN"] = "https://examplePublicKey@o0.ingest.sentry.io/0"
    env["ADMIN_PIN_HASH"] = hashlib.sha256(b"4832").hexdigest()
    return env


def test_import_main_with_sentry_dsn_initializes_sentry():
    result = subprocess.run(
        [sys.executable, "-c", "import main; print('IMPORT_OK')"],
        cwd=REPO_ROOT,
        env=_clean_env(),
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert result.returncode == 0, result.stderr
    assert "IMPORT_OK" in result.stdout
    assert "Sentry initialized" in result.stderr
    assert "Sentry initialization failed" not in result.stderr
