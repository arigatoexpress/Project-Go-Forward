"""Tests for the admin-PIN verifier (main._verify_admin_pin_value).

The verifier must accept BOTH the new salted-scrypt hash and the legacy
unsalted SHA-256 hex hash (so a deployment can migrate without downtime), and
must fail closed on empty/missing/malformed input.
"""

import base64
import hashlib
import importlib.util
import os
from pathlib import Path

import main


def _scrypt_hash(pin: str, n: int = 16384, r: int = 8, p: int = 1, dklen: int = 32) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(
        pin.encode(), salt=salt, n=n, r=r, p=p, dklen=dklen, maxmem=128 * n * r + (1 << 20)
    )
    return f"scrypt${n}${r}${p}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def test_legacy_sha256_still_verifies(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_PIN_HASH", hashlib.sha256(b"4832").hexdigest())
    assert main._verify_admin_pin_value("4832") is True
    assert main._verify_admin_pin_value("0000") is False


def test_scrypt_verifies(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_PIN_HASH", _scrypt_hash("9176"))
    assert main._verify_admin_pin_value("9176") is True
    assert main._verify_admin_pin_value("9177") is False


def test_empty_pin_fails(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_PIN_HASH", hashlib.sha256(b"4832").hexdigest())
    assert main._verify_admin_pin_value("") is False


def test_unconfigured_hash_fails(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_PIN_HASH", "")
    assert main._verify_admin_pin_value("4832") is False


def test_malformed_scrypt_hash_fails(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_PIN_HASH", "scrypt$bad$format")
    assert main._verify_admin_pin_value("4832") is False


def test_generator_script_output_verifies(monkeypatch):
    """End-to-end: the generator's output must verify in the app."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "generate_admin_pin_hash.py"
    spec = importlib.util.spec_from_file_location("gen_pin", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    h = mod.make_hash("2468")
    assert h.startswith("scrypt$")
    monkeypatch.setattr(main, "ADMIN_PIN_HASH", h)
    assert main._verify_admin_pin_value("2468") is True
    assert main._verify_admin_pin_value("1357") is False
