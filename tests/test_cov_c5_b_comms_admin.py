"""Tests for comms/admin/auth coverage-gap targets (Python-3.12 honest-coverage campaign).

Targets (main.py @ 46c8afa, swarm B):

- ``get_appointment`` — outer exception envelope
- ``list_appointments_api`` — happy list + outer exception envelope
- ``send_email_api`` — required-field rejection + outer exception envelope
- ``get_email_log_api`` — happy list + outer exception envelope
- ``get_chat_history`` — session-not-found + outer exception envelope
- ``get_chat_sessions`` — happy list + outer exception envelope
- ``get_event_analytics`` — analytics-events read failure swallow + outer
  exception envelope
- ``_cached_email_liveness`` — fresh-cache hit (both re-checks) and the live
  provider call
- ``track_analytics_event`` — unparseable body still acks the beacon
- ``submit_chat_callback`` — bad-JSON 400, over-long phone, malformed email
- ``_verify_svix_signature`` — missing headers, non-numeric timestamp,
  undecodable secret
- ``verify_admin_pin`` — non-string PIN 400, unconfigured-hash 503
- ``check_admin_token`` — passkey-cookie session is valid
- ``get_admin_audit_log`` / ``get_user_activity`` — outer exception envelopes
- ``get_feature_flags`` / ``get_reviews_config`` — happy + exception envelopes
- ``review_redirect`` — tracking failure must never break the redirect
- ``admin_ops_snapshot`` — snapshot failure envelope
- ``list_admin_staff`` — staff-directory-unavailable 503
- ``_clear_pin_attempts`` — redis delete failure is swallowed, fallback popped
- ``_decode_admin_token`` — bad signature, wrong token version, zero email
  length, non-normalized email

``get_chat_analytics`` (2658-2660) is intentionally omitted: its try body only
builds and returns a literal dict, so the except branch is unreachable without
editing main.py.

Handlers are called directly with a minimal request stand-in where the suite's
``create_client`` body-schema swap would interfere; ``/api/admin/verify`` goes
through TestClient so the real request IP plumbing runs. Audit helpers are
no-op patched; per-function imports (``tools.*``, ``auth.staff_directory``,
``email_service.check_email_liveness``) are faked via ``sys.modules``.

Run: python -m pytest tests/test_cov_c5_b_comms_admin.py -v
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import struct
import sys
import threading
import time
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(Path(__file__).parent))
from test_api_v1 import create_client  # noqa: E402


class _Req:
    """Minimal request stand-in: ``await request.json()`` returns the payload."""

    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _client(monkeypatch):
    client, main, fake_db, _logger = create_client(monkeypatch)
    monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
    monkeypatch.setattr(main, "log_user_action", lambda **kwargs: None, raising=False)
    return client, main, fake_db


def _run(coro):
    return asyncio.run(coro)


def _err(resp):
    return resp.status_code, json.loads(resp.body.decode())


def _mint_token(main, payload: bytes) -> str:
    """Build a token exactly like ``_create_admin_token`` for an arbitrary payload."""
    sig = hmac.new(main._JWT_SECRET, payload, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(payload + sig).decode().rstrip("=")


def _stub_child_module(monkeypatch, dotted: str, fake: types.ModuleType):
    """Make ``from <dotted> import x`` resolve to the fake module."""
    parent_name, _, child_name = dotted.rpartition(".")
    monkeypatch.setitem(sys.modules, dotted, fake)
    parent = __import__(parent_name, fromlist=[child_name])
    monkeypatch.setattr(parent, child_name, fake, raising=False)


# ─── _decode_admin_token ────────────────────────────────────────────────────


class TestDecodeAdminToken:
    def test_valid_pin_session_round_trips(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        token = main._create_admin_token()
        decoded = main._decode_admin_token(token)
        assert decoded is not None
        assert decoded[1] is None  # shared PIN session carries no email

    def test_wrong_signature_returns_none(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        token = main._create_admin_token()
        # Flipping the first char perturbs the payload, so the HMAC no longer
        # matches and the decode fails at the signature check.
        tampered = ("A" if token[0] != "A" else "B") + token[1:]
        assert main._decode_admin_token(tampered) is None

    def test_wrong_email_token_version_returns_none(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        # 11-byte payload whose version byte is not _EMAIL_TOKEN_VERSION.
        payload = b"\x09" + b"\x00" * 10
        assert main._decode_admin_token(_mint_token(main, payload)) is None

    def test_zero_email_length_returns_none(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        expires = int(time.time()) + 3600
        payload = bytes([main._EMAIL_TOKEN_VERSION]) + struct.pack(">Q", expires) + struct.pack(
            ">H", 0
        )
        assert main._decode_admin_token(_mint_token(main, payload)) is None

    def test_non_normalized_email_returns_none(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        email = b"Staff@Example.com"  # uppercase → fails the lowercase strip check
        expires = int(time.time()) + 3600
        payload = (
            bytes([main._EMAIL_TOKEN_VERSION])
            + struct.pack(">Q", expires)
            + struct.pack(">H", len(email))
            + email
        )
        assert main._decode_admin_token(_mint_token(main, payload)) is None

    def test_empty_token_returns_none(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        assert main._decode_admin_token("") is None


# ─── _clear_pin_attempts ────────────────────────────────────────────────────


class TestClearPinAttempts:
    def test_redis_delete_failure_swallowed_and_fallback_popped(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        class _BoomRedis:
            def delete(self, key):
                raise RuntimeError("redis down")

        monkeypatch.setattr(main.caching, "get_redis_client", lambda: _BoomRedis())
        main._pin_attempts_fallback["9.9.9.9"] = [time.time()]

        main._clear_pin_attempts("9.9.9.9")  # must not raise

        assert "9.9.9.9" not in main._pin_attempts_fallback

    def test_no_redis_falls_back_to_memory_pop(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.setattr(main.caching, "get_redis_client", lambda: None)
        main._pin_attempts_fallback["8.8.8.8"] = [time.time()]

        main._clear_pin_attempts("8.8.8.8")

        assert "8.8.8.8" not in main._pin_attempts_fallback


# ─── _verify_svix_signature ─────────────────────────────────────────────────


class TestVerifySvixSignature:
    def test_missing_headers_rejected(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        assert main._verify_svix_signature("whsec_" + "a" * 16, {}, b"{}") is False

    def test_missing_secret_rejected(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        headers = {"svix-id": "msg_1", "svix-timestamp": str(int(time.time())), "svix-signature": "v1,x"}
        assert main._verify_svix_signature("", headers, b"{}") is False

    def test_non_numeric_timestamp_rejected(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        headers = {
            "svix-id": "msg_1",
            "svix-timestamp": "not-a-number",
            "svix-signature": "v1,x",
        }
        assert main._verify_svix_signature("whsec_" + "a" * 16, headers, b"{}") is False

    def test_undecodable_secret_rejected(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        headers = {
            "svix-id": "msg_1",
            "svix-timestamp": str(int(time.time())),
            "svix-signature": "v1,x",
        }
        # "a" is invalid base64 padding, so the key decode must fail closed.
        assert main._verify_svix_signature("whsec_a", headers, b"{}") is False

    def test_stale_timestamp_rejected(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        headers = {
            "svix-id": "msg_1",
            "svix-timestamp": str(int(time.time()) - 1000),  # outside the 5-min window
            "svix-signature": "v1,x",
        }
        assert main._verify_svix_signature("whsec_" + "a" * 16, headers, b"{}") is False

    def test_valid_signature_accepted(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        import base64 as _b64

        key = b"k" * 16
        secret = "whsec_" + _b64.b64encode(key).decode()
        svix_id, ts = "msg_1", str(int(time.time()))
        signed = f"{svix_id}.{ts}.".encode() + b"{}"
        expected = _b64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
        headers = {"svix-id": svix_id, "svix-timestamp": ts, "svix-signature": f"v1,{expected}"}
        assert main._verify_svix_signature(secret, headers, b"{}") is True


# ─── _cached_email_liveness ─────────────────────────────────────────────────


class TestCachedEmailLiveness:
    def test_fresh_cache_short_circuits_before_lock(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        sentinel = {"ok": True, "state": "fresh"}
        monkeypatch.setattr(
            main,
            "_EMAIL_LIVENESS_CACHE",
            {"checked_at": time.monotonic(), "status": sentinel},
        )
        # No lock needed: the first freshness check must return on its own.
        monkeypatch.setattr(main, "_EMAIL_LIVENESS_LOCK", None)

        assert main._cached_email_liveness() is sentinel

    def test_second_recheck_inside_lock_returns_cached(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        sentinel = {"ok": True, "state": "refreshed-by-peer"}
        cache = {"checked_at": 0.0, "status": sentinel}
        monkeypatch.setattr(main, "_EMAIL_LIVENESS_CACHE", cache)

        # First check sees a stale entry; entering the lock simulates a peer
        # thread that just refreshed it, so the in-lock re-check hits.
        class _PeerRefreshingLock:
            def __enter__(self):
                cache["checked_at"] = time.monotonic()
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(main, "_EMAIL_LIVENESS_LOCK", _PeerRefreshingLock())

        assert main._cached_email_liveness() is sentinel

    def test_stale_cache_calls_live_provider(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.setattr(
            main, "_EMAIL_LIVENESS_CACHE", {"checked_at": 0.0, "status": None}
        )
        monkeypatch.setattr(main, "_EMAIL_LIVENESS_LOCK", threading.Lock())
        email_mod = sys.modules["email_service"]
        live = {"ok": True, "state": "live"}
        monkeypatch.setattr(
            email_mod, "check_email_liveness", lambda: live, raising=False
        )

        assert main._cached_email_liveness() == live
        assert main._EMAIL_LIVENESS_CACHE["status"] is live

    def test_unreachable_provider_degrades_gracefully(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.setattr(
            main, "_EMAIL_LIVENESS_CACHE", {"checked_at": 0.0, "status": None}
        )
        monkeypatch.setattr(main, "_EMAIL_LIVENESS_LOCK", threading.Lock())
        # The stub email_service module has no check_email_liveness, so the
        # from-import raises and the health check degrades to unreachable.
        monkeypatch.delattr(sys.modules["email_service"], "check_email_liveness", raising=False)

        assert main._cached_email_liveness() == {"ok": False, "state": "unreachable"}


# ─── track_analytics_event ──────────────────────────────────────────────────


class TestTrackAnalyticsEvent:
    def test_unparseable_body_still_acks(self, monkeypatch):
        client, main, _db = _client(monkeypatch)
        resp = client.post(
            "/api/analytics",
            data="this is not json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_valid_event_stored(self, monkeypatch):
        client, main, _db = _client(monkeypatch)
        stored = []
        monkeypatch.setattr(main, "_store_analytics_event", lambda *a, **k: stored.append((a, k)))

        resp = client.post("/api/analytics", json={"event": "home_viewed"})

        assert resp.json() == {"ok": True}
        assert stored[0][0][0] == "home_viewed"


# ─── submit_chat_callback ───────────────────────────────────────────────────


class TestSubmitChatCallback:
    def _payload(self, **overrides):
        data = {
            "sessionId": "abcdefgh",
            "name": "Bob Buyer",
            "phone": "5125551234",
            "consent": True,
        }
        data.update(overrides)
        return data

    def test_bad_json_returns_400(self, monkeypatch):
        client, main, _db = _client(monkeypatch)
        resp = client.post(
            "/api/chat/contact",
            data="{not json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400
        assert resp.json() == {"success": False, "error": "Invalid request."}

    def test_overlong_phone_rejected(self, monkeypatch):
        client, main, _db = _client(monkeypatch)
        resp = client.post("/api/chat/contact", json=self._payload(phone="5" * 41))
        assert resp.status_code == 400
        assert resp.json() == {"success": False, "error": "Enter a valid 10-digit phone number."}

    def test_non_string_phone_rejected(self, monkeypatch):
        client, main, _db = _client(monkeypatch)
        resp = client.post("/api/chat/contact", json=self._payload(phone=5125551234))
        assert resp.status_code == 400
        assert resp.json() == {"success": False, "error": "Enter a valid 10-digit phone number."}

    def test_malformed_email_rejected(self, monkeypatch):
        client, main, _db = _client(monkeypatch)
        resp = client.post("/api/chat/contact", json=self._payload(email="not-an-email"))
        assert resp.status_code == 400
        assert resp.json() == {"success": False, "error": "Enter a valid email address."}


# ─── get_appointment / list_appointments_api ────────────────────────────────


class TestGetAppointment:
    def test_lookup_failure_envelope(self, monkeypatch):
        client, main, _db = _client(monkeypatch)

        async def boom(appointment_id):
            raise RuntimeError("calendar backend down")

        monkeypatch.setattr(main.appointment_manager, "get_appointment", boom, raising=False)

        resp = client.get("/api/appointments/appt-1", params={"phone": "1234"})

        assert resp.status_code == 200
        assert resp.json() == {"error": "Failed to load appointment details. Please try again."}


class TestListAppointmentsApi:
    def test_happy_list(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        async def fake_list(status=None, limit=100):
            return [types.SimpleNamespace(to_dict=lambda: {"id": "appt-1", "status": status})]

        monkeypatch.setattr(main.appointment_manager, "list_appointments", fake_list, raising=False)

        result = _run(main.list_appointments_api(status="confirmed", limit=10))

        assert result["success"] is True
        assert result["appointments"] == [{"id": "appt-1", "status": "confirmed"}]
        assert result["count"] == 1

    def test_failure_envelope(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        async def boom(status=None, limit=100):
            raise RuntimeError("firestore down")

        monkeypatch.setattr(main.appointment_manager, "list_appointments", boom, raising=False)

        result = _run(main.list_appointments_api())

        assert result == {"success": False, "error": "Failed to load appointments. Please try again."}


# ─── send_email_api / get_email_log_api ─────────────────────────────────────


class TestSendEmailApi:
    def test_required_fields_enforced(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        result = _run(main.send_email_api(_Req({"to": " ", "subject": "", "message": ""})))
        assert result == {"success": False, "error": "to, subject, and message are required"}

    def test_send_failure_envelope(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        def boom(**kwargs):
            raise RuntimeError("smtp unreachable")

        monkeypatch.setattr(main, "send_custom_email", boom)

        result = _run(
            main.send_email_api(_Req({"to": "a@b.com", "subject": "Hi", "message": "Body"}))
        )

        assert result == {"success": False, "error": "Failed to send email. Please try again."}

    def test_happy_send(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        sent = {"success": True, "id": "email-1"}
        monkeypatch.setattr(main, "send_custom_email", lambda **kwargs: sent)

        result = _run(
            main.send_email_api(
                _Req({"to": "a@b.com", "customer_name": "Bob", "subject": "Hi", "message": "Body"})
            )
        )

        assert result is sent


class TestGetEmailLogApi:
    def test_happy_list(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.setattr(main, "get_email_log", lambda **kwargs: [{"id": "e1"}])

        result = _run(main.get_email_log_api(limit=10, email_type="lead"))

        assert result == {"success": True, "emails": [{"id": "e1"}], "count": 1}

    def test_failure_envelope(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        def boom(**kwargs):
            raise RuntimeError("log store down")

        monkeypatch.setattr(main, "get_email_log", boom)

        result = _run(main.get_email_log_api())

        assert result == {"success": False, "error": "Failed to load email log. Please try again."}


# ─── get_chat_history / get_chat_sessions ───────────────────────────────────


def _fake_session(**overrides):
    data = {
        "session_id": "sess-1",
        "user_id": "user-1",
        "status": "active",
        "created_at": "2026-10-01T00:00:00",
        "updated_at": "2026-10-01T01:00:00",
        "messages": [types.SimpleNamespace(role="user", text="hello", timestamp="2026-10-01T00:05:00")],
        "lead_id": "lead-1",
    }
    data.update(overrides)
    data["get_summary"] = lambda: "user asked about homes"
    return types.SimpleNamespace(**data)


class TestGetChatHistory:
    def test_session_not_found(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        async def fake_get(session_id):
            return None

        monkeypatch.setattr(main.chat_history, "get_session", fake_get, raising=False)

        result = _run(main.get_chat_history("missing"))

        assert result == {"success": False, "error": "Session not found"}

    def test_failure_envelope(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        async def boom(session_id):
            raise RuntimeError("history store down")

        monkeypatch.setattr(main.chat_history, "get_session", boom, raising=False)

        result = _run(main.get_chat_history("sess-1"))

        assert result == {"success": False, "error": "Failed to load chat history"}

    def test_happy_history(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.setattr(
            main.chat_history,
            "get_session",
            lambda session_id: asyncio.to_thread(_fake_session),
            raising=False,
        )

        result = _run(main.get_chat_history("sess-1"))

        assert result["success"] is True
        assert result["session"]["session_id"] == "sess-1"
        assert result["session"]["message_count"] == 1
        assert result["session"]["messages"][0]["text"] == "hello"


class TestGetChatSessions:
    def test_happy_list(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        async def fake_recent(hours=24, limit=50):
            return [_fake_session()]

        monkeypatch.setattr(
            main.chat_history, "get_recent_sessions", fake_recent, raising=False
        )

        result = _run(main.get_chat_sessions(hours=48, limit=5))

        assert result["success"] is True
        assert result["count"] == 1
        assert result["sessions"][0]["summary"] == "user asked about homes"
        assert result["sessions"][0]["lead_id"] == "lead-1"

    def test_failure_envelope(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        async def boom(hours=24, limit=50):
            raise RuntimeError("sessions store down")

        monkeypatch.setattr(
            main.chat_history, "get_recent_sessions", boom, raising=False
        )

        result = _run(main.get_chat_sessions())

        assert result == {"success": False, "error": "Failed to load chat sessions"}


# ─── get_event_analytics ────────────────────────────────────────────────────


class TestGetEventAnalytics:
    def test_events_read_failure_swallowed(self, monkeypatch):
        _client_, main, fake_db = _client(monkeypatch)

        def boom(name):
            raise RuntimeError("firestore down")

        monkeypatch.setattr(fake_db.db, "collection", boom)

        result = _run(main.get_event_analytics(range="7d"))

        assert result["range"] == "7d"
        assert result["total_events"] == 0

    def test_outer_failure_envelope(self, monkeypatch):
        _client_, main, fake_db = _client(monkeypatch)
        fake_db.collections["analytics_events"] = {
            "e1": {"event": "home_viewed", "created_at": "2026-10-01T00:00:00"}
        }

        def boom(value, *, public=False):
            raise RuntimeError("canonicalizer exploded")

        monkeypatch.setattr(main, "_canonical_analytics_event", boom)

        result = _run(main.get_event_analytics(range="30d"))

        assert result == {"error": "Failed to load event analytics"}

    def test_happy_aggregation(self, monkeypatch):
        _client_, main, fake_db = _client(monkeypatch)
        journey = "j_" + "a" * 32  # must satisfy _JOURNEY_ID_RE
        fake_db.collections["analytics_events"] = {
            "e1": {
                "event": "home_viewed",
                "created_at": "2026-10-01T10:00:00",
                "props": {"home": "home-1"},
                "journey_id": journey,
            },
            "e2": {
                "event": "lead_captured",
                "created_at": "2026-10-02T10:00:00",
                "journey_id": journey,
            },
        }

        result = _run(main.get_event_analytics(range="all"))

        assert result["total_events"] == 2
        by_type = {row["event"]: row["count"] for row in result["by_type"]}
        assert by_type == {"home_viewed": 1, "lead_captured": 1}
        assert result["top_homes"] == [{"home": "home-1", "count": 1}]
        assert result["journey_funnel"]["eligible_journeys"] == 1
        assert result["journey_funnel"]["lead_journeys"] == 1


# ─── verify_admin_pin / check_admin_token ───────────────────────────────────


class TestVerifyAdminPin:
    def test_non_string_pin_rejected(self, monkeypatch):
        client, _main, _db = _client(monkeypatch)
        resp = client.post("/api/admin/verify", json={"pin": 1234})
        assert resp.status_code == 400
        assert resp.json() == {"success": False, "error": "PIN must be a string."}

    def test_unconfigured_pin_hash_fails_closed(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.setattr(main, "ADMIN_PIN_HASH", "")
        resp = _client_.post("/api/admin/verify", json={"pin": "4832"})
        assert resp.status_code == 503
        assert resp.json() == {"success": False, "error": "Admin auth not configured."}

    def test_correct_pin_still_logs_in(self, monkeypatch):
        client, _main, _db = _client(monkeypatch)
        resp = client.post("/api/admin/verify", json={"pin": "4832"})
        assert resp.status_code == 200
        assert resp.json()["success"] is True


class TestCheckAdminToken:
    def test_passkey_cookie_session_is_valid(self, monkeypatch):
        client, main, _db = _client(monkeypatch)
        monkeypatch.setattr(main, "_verify_passkey_cookie", lambda request: True)

        resp = client.get("/api/admin/check")

        assert resp.status_code == 200
        assert resp.json() == {"valid": True}

    def test_bogus_token_is_invalid(self, monkeypatch):
        client, main, _db = _client(monkeypatch)
        monkeypatch.setattr(main, "_verify_passkey_cookie", lambda request: False)

        resp = client.get("/api/admin/check", headers={"X-Admin-Token": "bogus"})

        assert resp.status_code == 200
        assert resp.json() == {"valid": False}


# ─── admin audit / user activity / feature flags / reviews config ───────────


class TestGetAdminAuditLog:
    def test_happy_list(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        entries = [{"actor": "admin:x", "action": "admin.login"}]
        monkeypatch.setattr(main, "query_audit_log", lambda **kwargs: entries)

        result = _run(main.get_admin_audit_log(action="admin.login", limit=10))

        assert result == {"success": True, "entries": entries, "count": 1}

    def test_failure_envelope(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        def boom(**kwargs):
            raise RuntimeError("audit store down")

        monkeypatch.setattr(main, "query_audit_log", boom)

        result = _run(main.get_admin_audit_log())

        assert result == {"success": False, "error": "Failed to load audit log."}


class TestGetUserActivity:
    def test_happy_list(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        entries = [{"action": "contact.submit", "session_id": "s1"}]
        monkeypatch.setattr(main, "query_user_activity", lambda **kwargs: entries)

        result = _run(main.get_user_activity(action="contact.submit", session_id="s1"))

        assert result == {"success": True, "entries": entries, "count": 1}

    def test_failure_envelope(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        def boom(**kwargs):
            raise RuntimeError("activity store down")

        monkeypatch.setattr(main, "query_user_activity", boom)

        result = _run(main.get_user_activity())

        assert result == {"success": False, "error": "Failed to load user activity."}


class TestGetFeatureFlags:
    def test_happy_list(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.setattr(main.feature_flags, "all_flags", lambda: {"FF_X": True})

        result = _run(main.get_feature_flags())

        assert result == {"success": True, "flags": {"FF_X": True}}

    def test_failure_envelope(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        def boom():
            raise RuntimeError("flag store down")

        monkeypatch.setattr(main.feature_flags, "all_flags", boom)

        result = _run(main.get_feature_flags())

        assert result == {"success": False, "error": "Failed to load feature flags."}


class TestGetReviewsConfig:
    def test_env_link_wins(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.setenv("GOOGLE_REVIEW_LINK", "https://review.example/link")

        result = _run(main.get_reviews_config())

        assert result == {"success": True, "enabled": True, "review_link": "https://review.example/link"}

    def test_failure_envelope(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.delenv("GOOGLE_REVIEW_LINK", raising=False)

        def boom(name, default=None):
            raise RuntimeError("flag store down")

        monkeypatch.setattr(main.feature_flags, "get_value", boom)

        result = _run(main.get_reviews_config())

        assert result == {"success": False, "error": "Failed to load reviews config."}


# ─── review_redirect ────────────────────────────────────────────────────────


class TestReviewRedirect:
    def test_tracking_failure_never_breaks_redirect(self, monkeypatch):
        client, main, _db = _client(monkeypatch)
        monkeypatch.setenv("GOOGLE_REVIEW_LINK", "https://review.example/link")

        def boom(*args, **kwargs):
            raise RuntimeError("analytics store down")

        monkeypatch.setattr(main, "_store_analytics_event", boom)

        resp = client.get("/review", params={"src": "qr-lot"}, follow_redirects=False)

        assert resp.status_code == 302
        assert resp.headers["location"] == "https://review.example/link"

    def test_tracking_failure_with_broken_logger_still_redirects(self, monkeypatch):
        client, main, _db = _client(monkeypatch)
        monkeypatch.setenv("GOOGLE_REVIEW_LINK", "https://review.example/link")

        def boom(*args, **kwargs):
            raise RuntimeError("analytics store down")

        monkeypatch.setattr(main, "_store_analytics_event", boom)
        # Even the failure telemetry must never break the redirect.
        monkeypatch.setattr(main.struct_logger, "warning", boom)

        resp = client.get("/review", params={"src": "qr-lot"}, follow_redirects=False)

        assert resp.status_code == 302
        assert resp.headers["location"] == "https://review.example/link"

    def test_redirect_records_src(self, monkeypatch):
        client, main, fake_db = _client(monkeypatch)
        monkeypatch.setenv("GOOGLE_REVIEW_LINK", "https://review.example/link")
        stored = []
        monkeypatch.setattr(main, "_store_analytics_event", lambda *a, **k: stored.append((a, k)))

        resp = client.get("/review", params={"src": "qr-lot"}, follow_redirects=False)

        assert resp.status_code == 302
        args, kwargs = stored[0]
        assert args == ("review_redirect", {"src": "qr-lot"})
        assert kwargs == {}


# ─── admin_ops_snapshot / list_admin_staff ──────────────────────────────────


class TestAdminOpsSnapshot:
    def test_failure_envelope(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        fake = types.ModuleType("tools.ops_copilot")

        async def boom():
            raise RuntimeError("snapshot backend down")

        fake.get_business_snapshot = boom
        _stub_child_module(monkeypatch, "tools.ops_copilot", fake)

        result = _run(main.admin_ops_snapshot())

        assert result == {"success": False, "error": "Snapshot unavailable."}

    def test_happy_snapshot(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        fake = types.ModuleType("tools.ops_copilot")
        snapshot = {"leads": {"total": 3}}

        async def fake_snapshot():
            return snapshot

        fake.get_business_snapshot = fake_snapshot
        _stub_child_module(monkeypatch, "tools.ops_copilot", fake)

        result = _run(main.admin_ops_snapshot())

        assert result == {"success": True, "snapshot": snapshot}


class TestListAdminStaff:
    def _stub_directory(self, monkeypatch):
        fake = types.ModuleType("auth.staff_directory")

        class StaffDirectoryUnavailable(Exception):
            pass

        fake.StaffDirectoryUnavailable = StaffDirectoryUnavailable
        _stub_child_module(monkeypatch, "auth.staff_directory", fake)
        return fake

    def test_directory_unavailable_503(self, monkeypatch):
        client, main, _db = _client(monkeypatch)
        fake = self._stub_directory(monkeypatch)

        def boom():
            raise fake.StaffDirectoryUnavailable("directory backend down")

        fake.list_overrides = boom

        resp = client.get("/api/admin/staff", headers={"X-Admin-Token": main._create_admin_token()})

        assert resp.status_code == 503
        assert resp.json() == {"success": False, "error": "Team list is unavailable right now."}

    def test_happy_list(self, monkeypatch):
        client, main, _db = _client(monkeypatch)
        fake = self._stub_directory(monkeypatch)
        fake.list_overrides = lambda: [
            types.SimpleNamespace(email="a@b.com", status="allowed"),
            types.SimpleNamespace(email="c@d.com", status="blocked"),
        ]
        monkeypatch.setattr(main, "allowed_staff_domains", lambda: {"texashomeoutlet.com"})
        monkeypatch.setattr(main, "allowed_owner_emails", lambda: {"owner@texashomeoutlet.com"})

        resp = client.get("/api/admin/staff", headers={"X-Admin-Token": main._create_admin_token()})

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["added"] == ["a@b.com"]
        assert body["blocked"] == ["c@d.com"]
        assert body["domains"] == ["texashomeoutlet.com"]
