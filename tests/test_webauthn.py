"""
Tests for WebAuthn / Passkey authentication routes.

Uses mocks for Firestore and the py_webauthn verify functions so tests run
without a real authenticator, Firestore emulator, or network access.

Run: python -m pytest tests/test_webauthn.py -v
"""

import sys
import os
import json
import base64
import hashlib
import hmac
import struct
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure repo root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Provide a deterministic ADMIN_PIN_HASH so _jwt_secret() works without Secret Manager
_TEST_PIN_HASH = hashlib.sha256(b"000000").hexdigest()
os.environ.setdefault("ADMIN_PIN_HASH", _TEST_PIN_HASH)
os.environ.setdefault("WEBAUTHN_RP_ID", "localhost")
os.environ.setdefault("WEBAUTHN_ORIGINS", "http://localhost:5173")

import pytest

# Force the webauthn_routes module into sys.modules before any patch() call
# resolves targets like "auth.webauthn_routes._list_credentials".
import auth.webauthn_routes as _wauthn_module  # noqa: F401 — side-effect import

from fastapi import FastAPI
from fastapi.testclient import TestClient
from auth.webauthn_routes import router as _webauthn_router

# Shared test client (router imported once; routes don't mutate module state)
_TEST_APP = FastAPI()
_TEST_APP.include_router(_webauthn_router)
_CLIENT = TestClient(_TEST_APP)


def _make_app() -> TestClient:
    """Return the shared TestClient for all route tests."""
    return _CLIENT


def _valid_admin_token() -> str:
    """Create a valid admin token using the same logic as the routes."""
    pin_hash = os.environ["ADMIN_PIN_HASH"]
    jwt_secret = hashlib.sha256(f"sapphire-jwt-{pin_hash[:16]}".encode()).digest()
    expires = int(time.time()) + 7200
    payload = struct.pack(">Q", expires)
    sig = hmac.new(jwt_secret, payload, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(payload + sig).decode().rstrip("=")


def _fake_cred_id() -> str:
    return base64.urlsafe_b64encode(b"test-cred-id-1234").decode().rstrip("=")


# ─── Login Begin ──────────────────────────────────────────────────────────────

class TestLoginBegin:
    def test_returns_challenge_id_and_options(self):
        with patch("auth.webauthn_routes._list_credentials", return_value=[]), \
             patch("auth.webauthn_routes._store_challenge", return_value="cid-abc"):
            client = _make_app()
            resp = client.post(
                "/api/auth/webauthn/login/begin",
                json={"user_id": "admin"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["challenge_id"] == "cid-abc"
        assert "options" in data
        assert "challenge" in data["options"]

    def test_no_credentials_still_returns_options(self):
        with patch("auth.webauthn_routes._list_credentials", return_value=[]), \
             patch("auth.webauthn_routes._store_challenge", return_value="cid-empty"):
            client = _make_app()
            resp = client.post("/api/auth/webauthn/login/begin", json={})
        assert resp.status_code == 200
        # allow_credentials empty → discoverable-credential mode
        options = resp.json()["options"]
        assert options.get("allowCredentials", []) == []

    def test_with_registered_credentials_populates_allow_list(self):
        fake_cred = {"credential_id": _fake_cred_id(), "label": "Test Key"}
        with patch("auth.webauthn_routes._list_credentials", return_value=[fake_cred]), \
             patch("auth.webauthn_routes._store_challenge", return_value="cid-with"):
            client = _make_app()
            resp = client.post("/api/auth/webauthn/login/begin", json={"user_id": "admin"})
        assert resp.status_code == 200
        options = resp.json()["options"]
        assert len(options.get("allowCredentials", [])) == 1


# ─── Register Begin ───────────────────────────────────────────────────────────

class TestRegisterBegin:
    def test_requires_admin_token(self):
        client = _make_app()
        resp = client.post("/api/auth/webauthn/register/begin", json={})
        assert resp.status_code == 401

    def test_invalid_token_rejected(self):
        client = _make_app()
        resp = client.post(
            "/api/auth/webauthn/register/begin",
            json={},
            headers={"X-Admin-Token": "not-a-valid-token"},
        )
        assert resp.status_code == 401

    def test_valid_token_returns_options(self):
        token = _valid_admin_token()
        with patch("auth.webauthn_routes._list_credentials", return_value=[]), \
             patch("auth.webauthn_routes._store_challenge", return_value="reg-cid-789"):
            client = _make_app()
            resp = client.post(
                "/api/auth/webauthn/register/begin",
                json={"user_id": "admin", "label": "Mark's iPhone"},
                headers={"X-Admin-Token": token},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["challenge_id"] == "reg-cid-789"
        assert "options" in data
        assert "challenge" in data["options"]
        assert data["options"]["rp"]["id"] == "localhost"

    def test_bearer_token_also_accepted(self):
        token = _valid_admin_token()
        with patch("auth.webauthn_routes._list_credentials", return_value=[]), \
             patch("auth.webauthn_routes._store_challenge", return_value="reg-bearer"):
            client = _make_app()
            resp = client.post(
                "/api/auth/webauthn/register/begin",
                json={},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200


# ─── Login Complete ───────────────────────────────────────────────────────────

class TestLoginComplete:
    def test_expired_challenge_returns_400(self):
        with patch("auth.webauthn_routes._consume_challenge", return_value=None):
            client = _make_app()
            resp = client.post("/api/auth/webauthn/login/complete", json={
                "challenge_id": "expired-id",
                "credential": {"id": "fakecred", "type": "public-key"},
            })
        assert resp.status_code == 400
        assert "expired" in resp.json()["detail"].lower()

    def test_missing_credential_id_returns_400(self):
        challenge_bytes = os.urandom(32)
        with patch("auth.webauthn_routes._consume_challenge", return_value=challenge_bytes):
            client = _make_app()
            resp = client.post("/api/auth/webauthn/login/complete", json={
                "challenge_id": "valid-cid",
                "credential": {},
            })
        assert resp.status_code == 400

    def test_unknown_credential_returns_401(self):
        challenge_bytes = os.urandom(32)
        with patch("auth.webauthn_routes._consume_challenge", return_value=challenge_bytes), \
             patch("auth.webauthn_routes._get_credential", return_value=None):
            client = _make_app()
            resp = client.post("/api/auth/webauthn/login/complete", json={
                "challenge_id": "valid-cid",
                "credential": {"id": "no-such-cred"},
            })
        assert resp.status_code == 401

    def test_mocked_success_returns_valid_jwt(self):
        challenge_bytes = os.urandom(32)
        cred_id = _fake_cred_id()
        fake_public_key = b"\x04" + b"\xaa" * 64

        mock_stored = {
            "user_id": "admin",
            "public_key_bytes": base64.b64encode(fake_public_key).decode(),
            "sign_count": 5,
        }
        mock_verification = MagicMock()
        mock_verification.new_sign_count = 6

        with patch("auth.webauthn_routes._consume_challenge", return_value=challenge_bytes), \
             patch("auth.webauthn_routes._get_credential", return_value=mock_stored), \
             patch("auth.webauthn_routes._update_sign_count"), \
             patch("auth.webauthn_routes._parse_authentication_credential",
                   return_value=MagicMock()), \
             patch("webauthn.verify_authentication_response",
                   return_value=mock_verification):

            client = _make_app()
            resp = client.post("/api/auth/webauthn/login/complete", json={
                "challenge_id": "valid-cid",
                "credential": {
                    "id": cred_id,
                    "rawId": cred_id,
                    "type": "public-key",
                    "response": {
                        "clientDataJSON": base64.urlsafe_b64encode(b"{}").decode(),
                        "authenticatorData": base64.urlsafe_b64encode(b"\x00" * 37).decode(),
                        "signature": base64.urlsafe_b64encode(b"\x00" * 64).decode(),
                    },
                },
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "token" in data

        # Verify the returned token is a valid admin JWT (same logic as main.py)
        token = data["token"]
        padding = 4 - len(token) % 4
        if padding != 4:
            token += "=" * padding
        raw = base64.urlsafe_b64decode(token)
        assert len(raw) == 24, "Token must be 24 bytes (8 payload + 16 signature)"
        expires = struct.unpack(">Q", raw[:8])[0]
        assert expires > time.time(), "Token must not be already expired"


# ─── Register Complete ────────────────────────────────────────────────────────

class TestRegisterComplete:
    def test_expired_challenge_returns_400(self):
        token = _valid_admin_token()
        with patch("auth.webauthn_routes._consume_challenge", return_value=None):
            client = _make_app()
            resp = client.post(
                "/api/auth/webauthn/register/complete",
                json={"challenge_id": "expired", "credential": {}, "label": "Test"},
                headers={"X-Admin-Token": token},
            )
        assert resp.status_code == 400

    def test_mocked_success_saves_credential(self):
        token = _valid_admin_token()
        challenge_bytes = os.urandom(32)
        cred_id_bytes = b"new-cred-id-5678"

        mock_verification = MagicMock()
        mock_verification.credential_id = cred_id_bytes
        mock_verification.credential_public_key = b"\x04" + b"\xbb" * 64
        mock_verification.sign_count = 0
        mock_verification.fmt = "none"

        saved = {}

        def fake_save(cred_id, data):
            saved["cred_id"] = cred_id
            saved["data"] = data

        with patch("auth.webauthn_routes._consume_challenge", return_value=challenge_bytes), \
             patch("auth.webauthn_routes._parse_registration_credential",
                   return_value=MagicMock()), \
             patch("webauthn.verify_registration_response",
                   return_value=mock_verification), \
             patch("auth.webauthn_routes._save_credential", side_effect=fake_save):

            client = _make_app()
            resp = client.post(
                "/api/auth/webauthn/register/complete",
                json={
                    "challenge_id": "valid-cid",
                    "credential": {
                        "id": base64.urlsafe_b64encode(cred_id_bytes).decode(),
                        "type": "public-key",
                        "response": {
                            "clientDataJSON": base64.urlsafe_b64encode(b"{}").decode(),
                            "attestationObject": base64.urlsafe_b64encode(b"\x00" * 50).decode(),
                        },
                    },
                    "label": "Celeste's MacBook",
                },
                headers={"X-Admin-Token": token},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "credential_id" in data
        assert saved["data"]["label"] == "Celeste's MacBook"
        assert saved["data"]["user_id"] == "admin"


# ─── Credential Management ────────────────────────────────────────────────────

class TestCredentialManagement:
    def test_list_credentials_requires_auth(self):
        client = _make_app()
        resp = client.get("/api/auth/webauthn/credentials")
        assert resp.status_code == 401

    def test_list_credentials_returns_safe_list(self):
        token = _valid_admin_token()
        fake_creds = [
            {
                "credential_id": _fake_cred_id(),
                "label": "Ben's Android",
                "registered_at": "2026-01-01T00:00:00Z",
                "last_used_at": None,
                "transports": ["internal"],
                # public_key_bytes must NOT appear in response
                "public_key_bytes": "super-secret",
            }
        ]
        with patch("auth.webauthn_routes._list_credentials", return_value=fake_creds):
            client = _make_app()
            resp = client.get(
                "/api/auth/webauthn/credentials",
                headers={"X-Admin-Token": token},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["credentials"]) == 1
        cred = data["credentials"][0]
        assert cred["label"] == "Ben's Android"
        assert "public_key_bytes" not in cred

    def test_revoke_requires_auth(self):
        client = _make_app()
        resp = client.delete(f"/api/auth/webauthn/credentials/{_fake_cred_id()}")
        assert resp.status_code == 401

    def test_revoke_unknown_credential_returns_404(self):
        token = _valid_admin_token()
        with patch("auth.webauthn_routes._get_credential", return_value=None):
            client = _make_app()
            resp = client.delete(
                f"/api/auth/webauthn/credentials/no-such-id",
                headers={"X-Admin-Token": token},
            )
        assert resp.status_code == 404

    def test_revoke_known_credential_succeeds(self):
        token = _valid_admin_token()
        cred_id = _fake_cred_id()
        fake_stored = {"credential_id": cred_id, "user_id": "admin"}
        deleted = {}

        with patch("auth.webauthn_routes._get_credential", return_value=fake_stored), \
             patch("auth.webauthn_routes._delete_credential",
                   side_effect=lambda cid: deleted.update({"id": cid})):
            client = _make_app()
            resp = client.delete(
                f"/api/auth/webauthn/credentials/{cred_id}",
                headers={"X-Admin-Token": token},
            )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert deleted["id"] == cred_id


# ─── Token format compatibility ───────────────────────────────────────────────

class TestTokenCompatibility:
    """Verify that passkey-issued tokens are accepted by the same verify logic main.py uses."""

    def test_passkey_token_verifies_with_main_py_logic(self):
        from auth.webauthn_routes import _create_admin_token

        token = _create_admin_token()

        # Replicate main.py's _verify_admin_token exactly
        pin_hash = os.environ["ADMIN_PIN_HASH"]
        jwt_secret = hashlib.sha256(f"sapphire-jwt-{pin_hash[:16]}".encode()).digest()

        padding = 4 - len(token) % 4
        if padding != 4:
            token_padded = token + "=" * padding
        else:
            token_padded = token
        raw = base64.urlsafe_b64decode(token_padded)

        assert len(raw) == 24
        payload, sig = raw[:8], raw[8:]
        expected_sig = hmac.new(jwt_secret, payload, hashlib.sha256).digest()[:16]
        assert hmac.compare_digest(sig, expected_sig), "Signature mismatch"
        expires = struct.unpack(">Q", payload)[0]
        assert time.time() < expires, "Token expired immediately"
