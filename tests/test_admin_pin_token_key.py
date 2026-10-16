"""The passkey router's PIN-token check must use the same server-held key as main.py."""

import base64
import hashlib
import hmac
import struct
import time

import pytest

from auth import routes

SCRYPT_VERIFIER = "scrypt$16384$8$1$c2FsdHNhbHRzYWx0c2FsdA==$ZGtkZGtkZGtkZGtkZGtkZGtkZGtkZGtkZGtkZGtkZGs="
SESSION_SECRET = "s" * 48


def _token(key: bytes, expires: int) -> str:
    payload = struct.pack(">Q", expires)
    sig = hmac.new(key, payload, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(payload + sig).decode().rstrip("=")


def _server_key(pin_hash: str) -> bytes:
    # Mirrors main._JWT_SECRET.
    return hmac.new(SESSION_SECRET.encode(), f"tho-pin-session-v2:{pin_hash}".encode(), hashlib.sha256).digest()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ADMIN_PIN_HASH", SCRYPT_VERIFIER)
    monkeypatch.setenv("ADMIN_SESSION_SECRET", SESSION_SECRET)


def test_token_with_legacy_derivation_is_rejected():
    legacy_key = hashlib.sha256(f"sapphire-jwt-{SCRYPT_VERIFIER[:16]}".encode()).digest()
    forged = _token(legacy_key, int(time.time()) + 3600)
    assert routes._verify_admin_pin_token(forged) is False


def test_token_minted_with_server_key_is_accepted():
    good = _token(_server_key(SCRYPT_VERIFIER), int(time.time()) + 3600)
    assert routes._verify_admin_pin_token(good) is True


def test_expired_token_is_rejected():
    old = _token(_server_key(SCRYPT_VERIFIER), int(time.time()) - 1)
    assert routes._verify_admin_pin_token(old) is False


def test_missing_session_secret_fails_closed(monkeypatch):
    good = _token(_server_key(SCRYPT_VERIFIER), int(time.time()) + 3600)
    monkeypatch.delenv("ADMIN_SESSION_SECRET")
    assert routes._verify_admin_pin_token(good) is False


def test_rotating_pin_hash_revokes_tokens(monkeypatch):
    good = _token(_server_key(SCRYPT_VERIFIER), int(time.time()) + 3600)
    monkeypatch.setenv("ADMIN_PIN_HASH", SCRYPT_VERIFIER.replace("c2Fsd", "b3Ro"))
    assert routes._verify_admin_pin_token(good) is False


def test_email_session_tokens_are_not_pin_admin():
    key = _server_key(SCRYPT_VERIFIER)
    email = b"staff@example.com"
    payload = bytes([2]) + struct.pack(">Q", int(time.time()) + 3600) + struct.pack(">H", len(email)) + email
    sig = hmac.new(key, payload, hashlib.sha256).digest()[:16]
    token = base64.urlsafe_b64encode(payload + sig).decode().rstrip("=")
    assert routes._verify_admin_pin_token(token) is False
