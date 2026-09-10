"""Production session signing must remain independent from the shared PIN."""

from __future__ import annotations

import hashlib
import json
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


def _pin_session_worker(*, session_secret, pin_hash, token=None, production=True):
    """Exercise actual app signing/verifying in independent synthetic workers."""
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    if production:
        env["K_SERVICE"] = "synthetic-session-test"
    else:
        env.pop("K_SERVICE", None)
    env["ADMIN_PIN_HASH"] = pin_hash
    if session_secret is None:
        env.pop("ADMIN_SESSION_SECRET", None)
    else:
        env["ADMIN_SESSION_SECRET"] = session_secret
    env["SYNTHETIC_TEST_TOKEN"] = token or ""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, os, main; "
            "token = os.environ['SYNTHETIC_TEST_TOKEN']; "
            "result = main._verify_admin_token(token) if token else main._create_admin_token(); "
            "print('TEST_RESULT=' + json.dumps(result))",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, "Synthetic session worker failed"
    result_line = next(
        line for line in completed.stdout.splitlines() if line.startswith("TEST_RESULT=")
    )
    return json.loads(result_line.removeprefix("TEST_RESULT="))


def test_pin_sessions_share_across_workers_but_reject_wrong_or_rotated_secret():
    pin_hash = "scrypt$16384$8$1$" + "a" * 32 + "$" + "b" * 64
    secret = secrets.token_urlsafe(32)
    rotated = secrets.token_urlsafe(32)
    token = _pin_session_worker(session_secret=secret, pin_hash=pin_hash)
    assert _pin_session_worker(session_secret=secret, pin_hash=pin_hash, token=token) is True
    assert _pin_session_worker(session_secret=rotated, pin_hash=pin_hash, token=token) is False


def test_pin_verifier_rotation_revokes_sessions_even_with_matching_hash_format():
    secret = secrets.token_urlsafe(32)
    original = "scrypt$16384$8$1$" + "a" * 32 + "$" + "b" * 64
    rotated = "scrypt$16384$8$1$" + "c" * 32 + "$" + "d" * 64
    token = _pin_session_worker(session_secret=secret, pin_hash=original)
    assert _pin_session_worker(session_secret=secret, pin_hash=rotated, token=token) is False


def test_local_pin_sessions_remain_stable_across_restarts_without_configured_secret():
    token = _pin_session_worker(session_secret=None, pin_hash=PIN_HASH, production=False)
    assert (
        _pin_session_worker(session_secret=None, pin_hash=PIN_HASH, token=token, production=False)
        is True
    )
