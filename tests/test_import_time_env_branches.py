"""Subprocess coverage for import-time env branches in main.py.

These branches only execute in a fresh interpreter with a controlled
environment — under pytest, ``conftest.py`` always sets ``ADMIN_PIN_HASH`` and
``PYTEST_CURRENT_TEST`` is present, so they can never run in-process:

* main.py L1202 — local startup without ``ADMIN_PIN_HASH`` must fail loudly.
* main.py L1227-1230 — local startup derives ``ADMIN_SESSION_SECRET`` from the
  PIN hash so sessions survive restarts (Cloud Run must use an independent
  secret instead; see tests/test_admin_session_secret.py).
* main.py L10027-10028 — the ``__main__`` entrypoint reads ``PORT`` and hands
  off to uvicorn.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PIN_HASH = hashlib.sha256(b"4832").hexdigest()
EXPECTED_DERIVED_SECRET = hashlib.sha256(f"tho-session-v2-{PIN_HASH}".encode()).hexdigest()


def _clean_env(**overrides: str) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST")}
    env.pop("K_SERVICE", None)
    env.pop("ADMIN_PIN_HASH", None)
    env.pop("ADMIN_SESSION_SECRET", None)
    env.pop("SENTRY_DSN", None)
    env.update(overrides)
    return env


def _run_python(code: str, env: dict, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def test_local_import_without_admin_pin_hash_raises_runtimeerror():
    result = _run_python("import main", _clean_env())

    assert result.returncode != 0
    assert "Set ADMIN_PIN_HASH env var to run locally" in result.stderr


def test_local_import_derives_admin_session_secret_from_pin_hash():
    result = _run_python(
        "import main, os; print('DERIVED=' + os.environ['ADMIN_SESSION_SECRET'])",
        _clean_env(ADMIN_PIN_HASH=PIN_HASH),
    )

    assert result.returncode == 0, result.stderr
    assert f"DERIVED={EXPECTED_DERIVED_SECRET}" in result.stdout


def test_main_entrypoint_runs_uvicorn_on_configured_port():
    code = (
        "import runpy, sys, types\n"
        "calls = {}\n"
        "fake = types.ModuleType('uvicorn')\n"
        "fake.run = lambda app, host, port: calls.update(app=app, host=host, port=port)\n"
        "sys.modules['uvicorn'] = fake\n"
        "runpy.run_path('main.py', run_name='__main__')\n"
        "assert calls['host'] == '0.0.0.0', calls\n"
        "assert calls['port'] == 8123, calls\n"
        "assert calls['app'] is not None, calls\n"
        "print('ENTRYPOINT_OK')\n"
    )
    result = _run_python(code, _clean_env(ADMIN_PIN_HASH=PIN_HASH, PORT="8123"))

    assert result.returncode == 0, result.stderr
    assert "ENTRYPOINT_OK" in result.stdout
