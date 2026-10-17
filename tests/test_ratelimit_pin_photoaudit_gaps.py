"""Tests for four coverage-gap targets (Python-3.12 honest-coverage backlog).

Targets (see the 2026-10 3.12 re-measurement in docs/MAIN_COVERAGE_INVESTIGATION.md):

- ``_is_rate_limit_exempt_path`` — the legacy global limiter's skip rules
  (health probes, non-API GETs, static prefixes/extensions, API paths)
- ``_get_pin_attempts`` / ``_get_pin_global_attempts`` / ``_add_pin_attempt``
  — the PIN brute-force guards' Redis-backed reads/writes with in-memory
  fallback (direct unit tests; Redis supplied by a fake)
- ``GET /api/admin/inventory/photo-audit`` — the read-only photo dedup audit
  happy path and failure envelope
- ``GET /api/documents/templates/{name}/fields`` — found / not-found /
  failure envelopes of the SmartForm field lookup

Run: python -m pytest tests/test_ratelimit_pin_photoaudit_gaps.py -v
"""

from __future__ import annotations

import json
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

import caching  # noqa: E402
import tools  # noqa: E402  (already imported by main; parent package for stubs)


def _admin_headers(main) -> dict:
    return {"X-Admin-Token": main._create_admin_token()}


# ─── _is_rate_limit_exempt_path (direct) ───────────────────────────────────


class TestIsRateLimitExemptPath:
    @pytest.mark.parametrize(
        ("path", "method", "expected"),
        [
            ("/health", "GET", True),
            ("/healthz", "HEAD", True),
            ("/readyz/", "GET", True),
            ("/some/spa/page", "GET", True),  # non-API GET/HEAD
            ("/some/spa/page", "HEAD", True),
            ("/some/spa/page", "POST", False),  # non-API POST, no extension
            ("/assets/app.js", "POST", True),  # static prefix
            ("/workbox-abc123.js", "POST", True),  # workbox prefix
            ("/sw.js", "POST", True),  # static path member
            ("/favicon.ico", "POST", True),
            ("/style.css", "POST", True),  # extension match, non-API
            ("/IMAGE.PNG", "POST", True),  # extension match is case-insensitive
            ("/data.json", "POST", False),  # extension not in the list
            ("/api/contact", "GET", False),  # API paths never exempt
            ("/api", "GET", False),
            ("/api/", "POST", False),
        ],
    )
    def test_exemption_rules(self, monkeypatch, path, method, expected):
        _client, main, _db, _logger = create_client(monkeypatch)

        assert main._is_rate_limit_exempt_path(path, method) is expected


# ─── PIN attempt helpers (direct) ──────────────────────────────────────────


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
        self.calls.append(("setex", key, ttl))
        if self.fail:
            raise RuntimeError("redis down")
        self.store[key] = value


def _pin_client(monkeypatch, redis):
    _client, main, _db, _logger = create_client(monkeypatch)
    monkeypatch.setattr(caching, "get_redis_client", lambda: redis)
    monkeypatch.setattr(main, "_pin_attempts_fallback", {})
    monkeypatch.setattr(main, "_pin_global_attempts_fallback", [])
    return main


class TestPinAttemptHelpers:
    def test_get_pin_attempts_redis_hit(self, monkeypatch):
        redis = _FakeRedis()
        main = _pin_client(monkeypatch, redis)
        now = time.time()
        key = main._pin_attempts_key("1.2.3.4")
        fresh = [now - 1, now - 2]
        stale = now - (main.PIN_LOCKOUT_SECONDS + 10)
        redis.store[key] = json.dumps(fresh + [stale])

        attempts = main._get_pin_attempts("1.2.3.4")

        assert attempts == fresh  # stale entry filtered
        assert main._pin_attempts_fallback == {}  # redis path untouched

    def test_get_pin_attempts_redis_miss_falls_back_to_memory(self, monkeypatch):
        redis = _FakeRedis()
        main = _pin_client(monkeypatch, redis)
        now = time.time()
        main._pin_attempts_fallback["1.2.3.4"] = [now - 3]

        attempts = main._get_pin_attempts("1.2.3.4")

        assert attempts == [now - 3]

    def test_get_pin_attempts_redis_failure_falls_back_to_memory(self, monkeypatch):
        redis = _FakeRedis(fail=True)
        main = _pin_client(monkeypatch, redis)
        now = time.time()
        main._pin_attempts_fallback["1.2.3.4"] = [now - 4]

        attempts = main._get_pin_attempts("1.2.3.4")

        assert attempts == [now - 4]

    def test_get_pin_global_attempts_redis_hit_and_miss(self, monkeypatch):
        redis = _FakeRedis()
        main = _pin_client(monkeypatch, redis)
        now = time.time()
        fresh = now - 1
        stale = now - (main.PIN_GLOBAL_WINDOW_SECONDS + 10)
        redis.store[main.PIN_GLOBAL_KEY] = json.dumps([fresh, stale])

        assert main._get_pin_global_attempts() == [fresh]

        del redis.store[main.PIN_GLOBAL_KEY]
        main._pin_global_attempts_fallback.extend([now - 2])
        assert main._get_pin_global_attempts() == [now - 2]

    def test_add_pin_attempt_writes_redis_and_global_signal(self, monkeypatch):
        redis = _FakeRedis()
        main = _pin_client(monkeypatch, redis)
        now = time.time()

        main._add_pin_attempt("9.9.9.9", now)

        key = main._pin_attempts_key("9.9.9.9")
        assert json.loads(redis.store[key]) == [now]
        assert json.loads(redis.store[main.PIN_GLOBAL_KEY]) == [now]
        assert main._pin_attempts_fallback == {}  # redis present: memory not written

    def test_add_pin_attempt_redis_failure_writes_memory(self, monkeypatch):
        redis = _FakeRedis(fail=True)
        main = _pin_client(monkeypatch, redis)
        now = time.time()

        main._add_pin_attempt("9.9.9.9", now)

        assert main._pin_attempts_fallback["9.9.9.9"] == [now]
        # The global alert signal is still recorded via the in-memory path.
        assert main._pin_global_attempts_fallback == [now]


# ─── GET /api/admin/inventory/photo-audit ──────────────────────────────────


class _FakeAuditDoc:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    def to_dict(self):
        return dict(self._data)


class _ScriptedInventoryCollection:
    def __init__(self, docs=(), stream_error=None):
        self.docs = list(docs)
        self.stream_error = stream_error
        self.limit_arg = None

    def limit(self, n):
        self.limit_arg = n
        return self

    def stream(self, timeout=None):
        if self.stream_error is not None:
            raise self.stream_error
        return iter(self.docs)


def _photo_audit_client(monkeypatch, collection, audit_report):
    client, main, fake_db, _logger = create_client(monkeypatch)
    monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
    monkeypatch.setattr(main, "log_user_action", lambda **kwargs: None)
    monkeypatch.setattr(
        main,
        "_db",
        types.SimpleNamespace(db=types.SimpleNamespace(collection=lambda name: collection)),
    )
    fake_module = types.ModuleType("tools.photo_dedup_audit")
    fake_module.run_photo_dedup_audit = lambda inventory: dict(audit_report)
    monkeypatch.setitem(sys.modules, "tools.photo_dedup_audit", fake_module)
    monkeypatch.setattr(tools, "photo_dedup_audit", fake_module, raising=False)
    return client, main, collection


class TestAdminInventoryPhotoAudit:
    def test_audit_returns_report(self, monkeypatch):
        collection = _ScriptedInventoryCollection(
            docs=[
                _FakeAuditDoc("home-1", {"image_url": "https://img/a.jpg"}),
                _FakeAuditDoc("home-2", {"image_url": "https://img/a.jpg"}),
            ]
        )
        client, main, collection = _photo_audit_client(
            monkeypatch, collection, {"duplicates": [["home-1", "home-2"]], "total_items": 2}
        )

        resp = client.get(
            "/api/admin/inventory/photo-audit?limit=100", headers=_admin_headers(main)
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["duplicates"] == [["home-1", "home-2"]]
        assert body["total_items"] == 2
        assert collection.limit_arg == 100

    def test_audit_failure_returns_generic_error(self, monkeypatch):
        collection = _ScriptedInventoryCollection(stream_error=RuntimeError("firestore down"))
        client, main, _collection = _photo_audit_client(monkeypatch, collection, {})

        resp = client.get("/api/admin/inventory/photo-audit", headers=_admin_headers(main))

        assert resp.status_code == 200
        assert resp.json() == {
            "success": False,
            "error": "Failed to run photo dedup audit.",
        }


# ─── GET /api/documents/templates/{name}/fields ────────────────────────────


class TestGetTemplateFields:
    def _client(self, monkeypatch, *, fields=None, error=None):
        client, main, fake_db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)

        def fake_lookup(template_name):
            if error is not None:
                raise error
            return fields

        monkeypatch.setattr(main, "engine_get_template_fields", fake_lookup, raising=False)
        return client, main

    def test_template_found_returns_fields(self, monkeypatch):
        client, main = self._client(monkeypatch, fields=[{"name": "buyer_name", "type": "text"}])

        resp = client.get(
            "/api/documents/templates/TMHA_SalesContract.pdf/fields",
            headers=_admin_headers(main),
        )

        assert resp.status_code == 200
        assert resp.json() == [{"name": "buyer_name", "type": "text"}]

    def test_unknown_template_returns_not_found(self, monkeypatch):
        client, main = self._client(monkeypatch, fields=None)

        resp = client.get(
            "/api/documents/templates/NOPE.pdf/fields", headers=_admin_headers(main)
        )

        assert resp.status_code == 200
        assert resp.json() == {"error": "Template 'NOPE.pdf' not found"}

    def test_lookup_failure_returns_generic_error(self, monkeypatch):
        client, main = self._client(monkeypatch, error=RuntimeError("engine down"))

        resp = client.get(
            "/api/documents/templates/TMHA_SalesContract.pdf/fields",
            headers=_admin_headers(main),
        )

        assert resp.status_code == 200
        assert resp.json() == {"error": "Failed to load template fields. Please try again."}
