"""Tests for auth/session helper and readiness probe coverage gaps (swarm E).

Targets (main.py @ f65c537, swarm E — final tail):

- ``_create_admin_token`` — not-configured raise, malformed-email raise
- ``_maybe_slide_staff_session`` — a RuntimeError from the refresh mint is
  swallowed (session slide silently gives up)
- ``_verify_admin_token`` — expired token rejected, allowlist revocation
  for email sessions
- ``_verify_passkey_cookie`` — missing/unowned payload rejected, allowlist
  revocation for passkey sessions carrying an email
- ``healthz_detailed`` — ``email_not_configured`` warning branch
- ``_readyz_check_prompts`` — empty render raises
- ``_readyz_check_firestore`` — unavailable client raises
- ``readyz`` — inventory-source check failure is soft-handled (reported,
  never fails readiness)

Auth/token helpers are pure-ish and unit-tested directly with monkeypatched
time/allowlist deps; probe checks go through the real functions (or the real
TestClient route for ``/readyz`` / ``/healthz/detailed``). No main.py edits.

Run: python -m pytest tests/test_cov_c6_e_auth_probes.py -v
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import sys
import time
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(Path(__file__).parent))
from test_api_v1 import create_client  # noqa: E402


def _client(monkeypatch):
    client, main, fake_db, _logger = create_client(monkeypatch)
    monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
    monkeypatch.setattr(main, "log_user_action", lambda **kwargs: None, raising=False)
    return client, main, fake_db


def _mint_token(main, payload: bytes) -> str:
    """Build a token exactly like ``_create_admin_token`` for an arbitrary payload."""
    sig = hmac.new(main._JWT_SECRET, payload, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(payload + sig).decode().rstrip("=")


class _CookiesReq:
    """Minimal request stand-in carrying a cookie mapping."""

    def __init__(self, cookies: dict):
        self.cookies = cookies


# ─── _create_admin_token ────────────────────────────────────────────────────


class TestCreateAdminToken:
    def test_unconfigured_pin_hash_raises(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.setattr(main, "ADMIN_PIN_HASH", "")

        with pytest.raises(RuntimeError, match="Admin auth not configured"):
            main._create_admin_token()

    def test_malformed_email_raises(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        with pytest.raises(RuntimeError, match="Admin auth not configured"):
            main._create_admin_token(email="no-at-sign")

    def test_blank_email_raises(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        with pytest.raises(RuntimeError, match="Admin auth not configured"):
            main._create_admin_token(email="   ")

    def test_valid_email_token_round_trips(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        token = main._create_admin_token(email="Staff@Example.com")

        decoded = main._decode_admin_token(token)

        assert decoded is not None
        assert decoded[1] == "staff@example.com"  # normalized at mint time


# ─── _verify_admin_token ──────────────────────────────────────────────────


class TestVerifyAdminToken:
    def test_expired_token_rejected(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        expired = int(time.time()) - 10
        token = _mint_token(main, struct.pack(">Q", expired))

        assert main._verify_admin_token(token) is False

    def test_allowlist_revocation_rejects_email_session(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        token = main._create_admin_token(email="fired@texashomeoutlet.com")
        monkeypatch.setattr(main, "is_allowed_admin_email", lambda email: False)

        assert main._verify_admin_token(token) is False

    def test_allowed_email_session_accepted(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        token = main._create_admin_token(email="staff@texashomeoutlet.com")
        monkeypatch.setattr(main, "is_allowed_admin_email", lambda email: True)

        assert main._verify_admin_token(token) is True


# ─── _verify_passkey_cookie ───────────────────────────────────────────────


class TestVerifyPasskeyCookie:
    def _stub_manager(self, monkeypatch, main, payload):
        manager = types.SimpleNamespace(verify_session=lambda token: payload)
        monkeypatch.setattr(main, "_get_passkey_session_manager", lambda: manager)

    def _request(self, main):
        return _CookiesReq({main.PASSKEY_COOKIE_NAME: "passkey-token"})

    def test_no_cookie_rejected(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        assert main._verify_passkey_cookie(_CookiesReq({})) is False

    def test_unverified_payload_rejected(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        self._stub_manager(monkeypatch, main, None)

        assert main._verify_passkey_cookie(self._request(main)) is False

    def test_wrong_user_payload_rejected(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        self._stub_manager(monkeypatch, main, {"user_id": "someone-else"})

        assert main._verify_passkey_cookie(self._request(main)) is False

    def test_disallowed_email_rejected(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        self._stub_manager(
            monkeypatch, main, {"user_id": "admin", "email": "fired@texashomeoutlet.com"}
        )
        monkeypatch.setattr(main, "is_allowed_admin_email", lambda email: False)

        assert main._verify_passkey_cookie(self._request(main)) is False

    def test_admin_payload_without_email_accepted(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        self._stub_manager(monkeypatch, main, {"user_id": "admin"})

        assert main._verify_passkey_cookie(self._request(main)) is True


# ─── _maybe_slide_staff_session ───────────────────────────────────────────


class TestMaybeSlideStaffSession:
    def test_mint_failure_silently_skips_slide(self, monkeypatch):
        """The slide refresh must never break the response: if the token mint
        raises (e.g. config race), the middleware-grade helper just returns."""
        _client_, main, _db = _client(monkeypatch)
        from starlette.responses import Response

        expiring_soon = time.time() + 120  # well inside the slide window
        monkeypatch.setattr(main, "_verify_admin_token", lambda token: True)
        monkeypatch.setattr(
            main, "_decode_admin_token", lambda token: (int(expiring_soon), "staff@tho.com")
        )

        def boom(email=None):
            raise RuntimeError("Admin auth not configured")

        monkeypatch.setattr(main, "_create_admin_token", boom)

        request = _CookiesReq({"tho_admin_token": "old-token", "tho_csrf_token": "csrf"})
        response = Response(content="ok")

        main._maybe_slide_staff_session(request, response)  # must not raise

        # No fresh cookie may be set when the mint failed.
        assert "tho_admin_token" not in response.headers.get("set-cookie", "")

    def test_fresh_session_is_not_reissued(self, monkeypatch):
        """Sessions with most of their TTL remaining keep their original cookie."""
        _client_, main, _db = _client(monkeypatch)
        from starlette.responses import Response

        minted = main._create_admin_token(email="staff@tho.com")
        decoded = main._decode_admin_token(minted)
        assert decoded is not None
        monkeypatch.setattr(main, "_verify_admin_token", lambda token: True)

        request = _CookiesReq({"tho_admin_token": minted})
        response = Response(content="ok")

        main._maybe_slide_staff_session(request, response)

        assert "tho_admin_token" not in response.headers.get("set-cookie", "")

    def test_expiring_session_gets_fresh_cookie(self, monkeypatch):
        """The happy path: an old-but-valid session is re-signed and re-set."""
        _client_, main, _db = _client(monkeypatch)
        from starlette.responses import Response

        old_expires = int(time.time()) + main._STAFF_SESSION_SLIDE_AFTER - 60
        email_bytes = b"staff@tho.com"
        payload = (
            bytes([main._EMAIL_TOKEN_VERSION])
            + struct.pack(">Q", old_expires)
            + struct.pack(">H", len(email_bytes))
            + email_bytes
        )
        old_token = _mint_token(main, payload)
        monkeypatch.setattr(main, "_verify_admin_token", lambda token: True)

        request = _CookiesReq({"tho_admin_token": old_token, "tho_csrf_token": "csrf"})
        response = Response(content="ok")

        main._maybe_slide_staff_session(request, response)

        set_cookie = response.headers.get("set-cookie", "")
        assert "tho_admin_token=" in set_cookie
        # Starlette Response keeps no parsed cookie jar — pull the value from
        # the raw Set-Cookie header (first cookie pair, before the attributes).
        fresh = set_cookie.split("tho_admin_token=", 1)[1].split(";", 1)[0]
        assert fresh
        decoded_fresh = main._decode_admin_token(fresh)
        assert decoded_fresh is not None
        assert decoded_fresh[1] == "staff@tho.com"
        assert decoded_fresh[0] > old_expires


# ─── healthz_detailed ─────────────────────────────────────────────────────


class TestHealthzDetailed:
    def test_email_not_configured_warning(self, monkeypatch):
        client, main, _db = _client(monkeypatch)
        monkeypatch.setattr(
            main,
            "_cached_email_liveness",
            lambda: {"ok": False, "state": "not_configured"},
        )

        response = client.get(
            "/healthz/detailed",
            headers={"X-Admin-Token": main._create_admin_token()},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["dependencies"]["email"] == "not_configured"
        assert "email_not_configured" in body["warnings"]
        assert "email_key_rejected" not in body["warnings"]


# ─── readyz checks ────────────────────────────────────────────────────────


class TestReadyzChecks:
    def test_empty_prompt_render_raises(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.setattr(main, "render_prompt", lambda name: "   ")

        with pytest.raises(RuntimeError, match="rendered empty"):
            main._readyz_check_prompts()

    def test_unavailable_firestore_client_raises(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        fs_module = sys.modules["database.firestore_client"]
        monkeypatch.setattr(
            fs_module, "get_database", lambda: types.SimpleNamespace(db=None)
        )

        with pytest.raises(RuntimeError, match="firestore client unavailable"):
            main._readyz_check_firestore()

    def test_readyz_inventory_failure_is_soft_handled(self, monkeypatch):
        """A raising inventory check must not flap readiness: the probe stays
        200 and reports the failure under checks.inventory."""
        client, main, _db = _client(monkeypatch)

        def boom():
            raise RuntimeError("inventory backend exploded")

        monkeypatch.setattr(main, "_readyz_check_inventory_source", boom)

        response = client.get("/readyz")

        assert response.status_code == 200
        body = response.json()
        assert body["ready"] is True
        assert body["checks"]["inventory"] == {
            "ok": False,
            "error": "inventory backend exploded",
        }

    def test_readyz_inventory_error_is_truncated(self, monkeypatch):
        client, main, _db = _client(monkeypatch)

        def boom():
            raise RuntimeError("x" * 500)

        monkeypatch.setattr(main, "_readyz_check_inventory_source", boom)

        response = client.get("/readyz")

        assert response.status_code == 200
        error = response.json()["checks"]["inventory"]["error"]
        assert len(error) <= 300
