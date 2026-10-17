"""Tests for three coverage-gap targets (Python-3.12 re-measurement backlog).

Targets (see docs/MAIN_COVERAGE_INVESTIGATION.md):

- ``POST /api/admin/email-code/request`` — one-time sign-in code issuance with
  enumeration-resistant envelopes, cooldown guard, delivery-failure rollback
- ``GET /api/customers/{customer_id}`` — single-customer fetch with SSN
  stripping (404 / 500 envelopes)
- ``_check_chat_rate_limit`` — Redis-backed chat rate limiter with in-memory
  fallback (direct unit tests)

The email-code route carries a 3/min slowapi cap shared per-process, so the
limiter is disabled per test via ``main.limiter.enabled`` (the endpoint's own
logic is what is under test). All store/allowlist/delivery dependencies are
monkeypatched on ``main`` globals; no Firestore, no network.

Run: python -m pytest tests/test_email_code_customer_ratelimit.py -v
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(Path(__file__).parent))
from test_api_v1 import create_client  # noqa: E402

import caching  # noqa: E402


def _admin_headers(main) -> dict:
    return {"X-Admin-Token": main._create_admin_token()}


def _email_code_client(monkeypatch):
    """Client with limiter disabled and the delivery/allowlist deps controlled."""
    client, main, fake_db, _logger = create_client(monkeypatch)
    monkeypatch.setattr(main.limiter, "enabled", False)
    monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
    monkeypatch.setattr(main, "log_user_action", lambda **kwargs: None)
    return client, main, fake_db


class _FakeStore:
    def __init__(self, existing=None):
        self.records: dict = {}
        self.deleted: list = []
        self._existing = existing

    def get(self, email):
        return self._existing

    def put(self, email, code_hash, expires_at, link_hash):
        self.records[email] = {
            "code_hash": code_hash,
            "expires_at": expires_at,
            "link_hash": link_hash,
        }

    def delete(self, email):
        self.deleted.append(email)
        if getattr(self, "fail_delete", False):
            raise RuntimeError("delete failed")
        self.records.pop(email, None)
        self._existing = None


class _ExistingCode:
    def __init__(self, created_at):
        self.created_at = created_at


class TestRequestAdminEmailCode:
    def test_malformed_json_returns_400(self, monkeypatch):
        client, _main, _db = _email_code_client(monkeypatch)

        resp = client.post(
            "/api/admin/email-code/request",
            content="{not json",
            headers={"Content-Type": "application/json"},
        )

        assert resp.status_code == 400
        assert resp.json() == {"success": False, "error": "Malformed JSON body."}

    def test_non_object_body_returns_400(self, monkeypatch):
        client, _main, _db = _email_code_client(monkeypatch)

        resp = client.post("/api/admin/email-code/request", json=["not", "object"])

        assert resp.status_code == 400
        assert resp.json() == {
            "success": False,
            "error": "Request body must be a JSON object.",
        }

    def test_delivery_not_configured_returns_uniform_503(self, monkeypatch):
        client, main, _db = _email_code_client(monkeypatch)
        monkeypatch.setattr(main, "email_delivery_configured", lambda: False)

        resp = client.post(
            "/api/admin/email-code/request", json={"email": "owner@texashomeoutlet.com"}
        )

        assert resp.status_code == 503
        body = resp.json()
        assert body["success"] is False
        assert "not turned on yet" in body["error"]

    def test_unauthorized_email_gets_generic_success_no_store_write(self, monkeypatch):
        client, main, _db = _email_code_client(monkeypatch)
        monkeypatch.setattr(main, "email_delivery_configured", lambda: True)
        monkeypatch.setattr(main, "is_allowed_admin_email", lambda email: False)
        store = _FakeStore()
        monkeypatch.setattr(main, "default_code_store", lambda: store)
        sent: list = []
        monkeypatch.setattr(
            main, "send_admin_login_code", lambda *args, **kwargs: sent.append(kwargs) or {"success": True}
        )

        resp = client.post(
            "/api/admin/email-code/request", json={"email": "stranger@example.com"}
        )

        assert resp.status_code == 200
        assert resp.json() == {"success": True}
        assert store.records == {}
        assert sent == []

    def test_store_unavailable_still_returns_generic_success(self, monkeypatch):
        client, main, _db = _email_code_client(monkeypatch)
        monkeypatch.setattr(main, "email_delivery_configured", lambda: True)
        monkeypatch.setattr(main, "is_allowed_admin_email", lambda email: True)

        def _boom():
            raise main.EmailLoginCodeStoreUnavailable("firestore down")

        monkeypatch.setattr(main, "default_code_store", _boom)

        resp = client.post(
            "/api/admin/email-code/request", json={"email": "owner@texashomeoutlet.com"}
        )

        assert resp.status_code == 200
        assert resp.json() == {"success": True}

    def test_resend_within_cooldown_suppressed(self, monkeypatch):
        client, main, _db = _email_code_client(monkeypatch)
        monkeypatch.setattr(main, "email_delivery_configured", lambda: True)
        monkeypatch.setattr(main, "is_allowed_admin_email", lambda email: True)
        store = _FakeStore(existing=_ExistingCode(created_at=time.time()))
        monkeypatch.setattr(main, "default_code_store", lambda: store)
        sent: list = []
        monkeypatch.setattr(
            main, "send_admin_login_code", lambda *args, **kwargs: sent.append(kwargs) or {"success": True}
        )

        resp = client.post(
            "/api/admin/email-code/request", json={"email": "owner@texashomeoutlet.com"}
        )

        assert resp.status_code == 200
        assert resp.json() == {"success": True}
        assert store.records == {}  # no new code minted
        assert sent == []

    def test_send_failure_rolls_back_code(self, monkeypatch):
        client, main, _db = _email_code_client(monkeypatch)
        monkeypatch.setattr(main, "email_delivery_configured", lambda: True)
        monkeypatch.setattr(main, "is_allowed_admin_email", lambda email: True)
        store = _FakeStore()
        monkeypatch.setattr(main, "default_code_store", lambda: store)
        monkeypatch.setattr(
            main, "send_admin_login_code", lambda *args, **kwargs: {"success": False}
        )
        monkeypatch.setattr(main, "generate_code", lambda: "123456")
        monkeypatch.setattr(main, "generate_link_token", lambda: "tok-abc")
        monkeypatch.setattr(main, "hash_code", lambda value: f"hashed:{value}")

        resp = client.post(
            "/api/admin/email-code/request", json={"email": "Owner@TexasHomeOutlet.com"}
        )

        assert resp.status_code == 200
        assert resp.json() == {"success": True}
        assert store.deleted == ["owner@texashomeoutlet.com"]  # email normalized
        assert store.records == {}

    def test_send_failure_with_failed_rollback_still_generic(self, monkeypatch):
        client, main, _db = _email_code_client(monkeypatch)
        monkeypatch.setattr(main, "email_delivery_configured", lambda: True)
        monkeypatch.setattr(main, "is_allowed_admin_email", lambda email: True)
        store = _FakeStore()
        store.fail_delete = True
        monkeypatch.setattr(main, "default_code_store", lambda: store)
        monkeypatch.setattr(
            main, "send_admin_login_code", lambda *args, **kwargs: {"success": False}
        )
        monkeypatch.setattr(main, "generate_code", lambda: "123456")
        monkeypatch.setattr(main, "generate_link_token", lambda: "tok-abc")
        monkeypatch.setattr(main, "hash_code", lambda value: f"hashed:{value}")

        resp = client.post(
            "/api/admin/email-code/request", json={"email": "owner@texashomeoutlet.com"}
        )

        assert resp.status_code == 200
        assert resp.json() == {"success": True}
        assert store.deleted == ["owner@texashomeoutlet.com"]  # rollback was attempted
        assert store.records  # but the rollback itself failed; still no 500

    def test_happy_path_stores_hashes_sends_and_audits(self, monkeypatch):
        client, main, _db = _email_code_client(monkeypatch)
        monkeypatch.setattr(main, "email_delivery_configured", lambda: True)
        monkeypatch.setattr(main, "is_allowed_admin_email", lambda email: True)
        store = _FakeStore()
        monkeypatch.setattr(main, "default_code_store", lambda: store)
        monkeypatch.setattr(main, "generate_code", lambda: "654321")
        monkeypatch.setattr(main, "generate_link_token", lambda: "tok-xyz")
        monkeypatch.setattr(main, "hash_code", lambda value: f"hashed:{value}")
        sent: list = []
        monkeypatch.setattr(
            main, "send_admin_login_code", lambda email, code, **kwargs: sent.append(
                {"email": email, "code": code, **kwargs}
            )
            or {"success": True}
        )
        audited: list = []
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: audited.append(kwargs))
        ttl = main.EMAIL_CODE_TTL_SECONDS

        resp = client.post(
            "/api/admin/email-code/request", json={"email": "owner@texashomeoutlet.com"}
        )

        assert resp.status_code == 200
        assert resp.json() == {"success": True}
        record = store.records["owner@texashomeoutlet.com"]
        assert record["code_hash"] == "hashed:654321"
        assert record["link_hash"] == "hashed:tok-xyz"
        assert record["expires_at"] == time.time() + ttl or abs(
            record["expires_at"] - (time.time() + ttl)
        ) < 5
        assert sent[0]["email"] == "owner@texashomeoutlet.com"
        assert sent[0]["code"] == "654321"
        assert "#" in sent[0]["link"] and "tok-xyz" in sent[0]["link"]
        assert audited and audited[0]["action"] == "admin.login_code.request"
        assert audited[0]["actor"].startswith("email:")


# ─── GET /api/customers/{customer_id} ─────────────────────────────────────


class TestGetCustomer:
    def test_existing_customer_returned_with_ssn_stripped(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)

        resp = client.get("/api/customers/cust-1", headers=_admin_headers(main))

        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "cust-1"
        assert body["full_name"] == "Alice Example"
        assert "ssn_hash" not in body
        assert "ssn_masked" in body

    def test_lookup_by_legacy_id(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)

        resp = client.get("/api/customers/legacy-1", headers=_admin_headers(main))

        assert resp.status_code == 200
        assert resp.json()["id"] == "cust-1"

    def test_missing_customer_returns_404(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)

        resp = client.get("/api/customers/cust-999", headers=_admin_headers(main))

        assert resp.status_code == 404
        assert resp.json()["message"] == "Customer not found"

    def test_database_failure_returns_500(self, monkeypatch):
        client, main, db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)

        def _boom(customer_id):
            raise RuntimeError("firestore down")

        monkeypatch.setattr(db, "get_customer", _boom)

        resp = client.get("/api/customers/cust-1", headers=_admin_headers(main))

        assert resp.status_code == 500
        assert resp.json()["message"] == "Failed to retrieve customer"


# ─── _check_chat_rate_limit (direct) ──────────────────────────────────────


class _FakeRedis:
    def __init__(self, fail=False):
        self.store: dict = {}
        self.fail = fail
        self.calls: list = []

    def get(self, key):
        self.calls.append(("get", key))
        if self.fail:
            raise RuntimeError("redis down")
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.calls.append(("setex", key))
        if self.fail:
            raise RuntimeError("redis down")
        self.store[key] = value


class TestCheckChatRateLimit:
    def _fresh_fallback(self, monkeypatch, main):
        fallback: dict = {}
        monkeypatch.setattr(main, "_chat_rate_limit_fallback", fallback)
        return fallback

    def test_allows_first_request_and_records_timestamp(self, monkeypatch):
        _client, main, _db, _logger = create_client(monkeypatch)
        self._fresh_fallback(monkeypatch, main)
        monkeypatch.setattr(caching, "get_redis_client", lambda: None)

        assert main._check_chat_rate_limit("1.2.3.4") is True
        assert len(main._chat_rate_limit_fallback["1.2.3.4"]) == 1

    def test_denies_when_window_full(self, monkeypatch):
        _client, main, _db, _logger = create_client(monkeypatch)
        fallback = self._fresh_fallback(monkeypatch, main)
        now = time.time()
        fallback["1.2.3.4"] = [now - i for i in range(main.CHAT_RATE_LIMIT_MAX_REQUESTS)]
        monkeypatch.setattr(caching, "get_redis_client", lambda: None)

        assert main._check_chat_rate_limit("1.2.3.4") is False
        assert len(fallback["1.2.3.4"]) == main.CHAT_RATE_LIMIT_MAX_REQUESTS

    def test_stale_entries_pruned_from_window(self, monkeypatch):
        _client, main, _db, _logger = create_client(monkeypatch)
        fallback = self._fresh_fallback(monkeypatch, main)
        old = time.time() - (main.CHAT_RATE_LIMIT_SECONDS + 10)
        fallback["1.2.3.4"] = [old, time.time()]
        monkeypatch.setattr(caching, "get_redis_client", lambda: None)

        assert main._check_chat_rate_limit("1.2.3.4") is True
        assert len(fallback["1.2.3.4"]) == 2  # stale pruned, new appended

    def test_redis_path_uses_shared_storage(self, monkeypatch):
        _client, main, _db, _logger = create_client(monkeypatch)
        self._fresh_fallback(monkeypatch, main)
        redis = _FakeRedis()
        monkeypatch.setattr(caching, "get_redis_client", lambda: redis)

        assert main._check_chat_rate_limit("9.9.9.9") is True
        assert main._check_chat_rate_limit("9.9.9.9") is True
        assert [op for op, _key in redis.calls] == ["get", "setex", "get", "setex"]
        assert main._chat_rate_limit_fallback == {}

    def test_redis_deny_when_window_full(self, monkeypatch):
        _client, main, _db, _logger = create_client(monkeypatch)
        self._fresh_fallback(monkeypatch, main)
        redis = _FakeRedis()
        now = time.time()
        key = "tho:chat_ratelimit:9.9.9.9"
        redis.store[key] = json.dumps(
            [now - i for i in range(main.CHAT_RATE_LIMIT_MAX_REQUESTS)]
        )
        monkeypatch.setattr(caching, "get_redis_client", lambda: redis)

        assert main._check_chat_rate_limit("9.9.9.9") is False
        assert ("setex", key) not in redis.calls  # denied: nothing appended
        assert main._chat_rate_limit_fallback == {}  # memory path untouched

    def test_redis_failure_falls_back_to_memory(self, monkeypatch):
        _client, main, _db, _logger = create_client(monkeypatch)
        self._fresh_fallback(monkeypatch, main)
        redis = _FakeRedis(fail=True)
        monkeypatch.setattr(caching, "get_redis_client", lambda: redis)

        assert main._check_chat_rate_limit("5.5.5.5") is True
        assert len(main._chat_rate_limit_fallback["5.5.5.5"]) == 1
