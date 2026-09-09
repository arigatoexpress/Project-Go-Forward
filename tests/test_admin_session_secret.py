"""Production session signing must remain independent from the shared PIN."""

from __future__ import annotations

import hashlib
import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

PIN_HASH = "a" * 64
LEGACY_DERIVED_SECRET = hashlib.sha256(f"tho-session-v2-{PIN_HASH}".encode()).hexdigest()


def _cloud_run_import(*, session_secret: str | None):
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["K_SERVICE"] = "project-go-forward"
    env["ADMIN_PIN_HASH"] = PIN_HASH
    if session_secret is None:
        env.pop("ADMIN_SESSION_SECRET", None)
    else:
        env["ADMIN_SESSION_SECRET"] = session_secret
    return subprocess.run(
        [sys.executable, "-c", "import main"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize(
    "unsafe_secret",
    (None, "x", PIN_HASH, LEGACY_DERIVED_SECRET),
    ids=("missing", "one-byte", "pin-hash", "legacy-derived"),
)
def test_cloud_run_startup_rejects_missing_weak_or_pin_derived_session_secret(
    unsafe_secret,
):
    completed = _cloud_run_import(session_secret=unsafe_secret)

    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "ADMIN_SESSION_SECRET does not meet Cloud Run security requirements" in output
    if unsafe_secret and len(unsafe_secret) > 1:
        assert unsafe_secret not in output


def test_cloud_run_startup_accepts_independent_random_secret_of_at_least_32_utf8_bytes():
    session_secret = secrets.token_urlsafe(32)
    assert len(session_secret.encode("utf-8")) >= 32

    completed = _cloud_run_import(session_secret=session_secret)

    assert completed.returncode == 0, completed.stderr
    assert session_secret not in completed.stdout + completed.stderr
