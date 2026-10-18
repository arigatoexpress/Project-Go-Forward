"""Coverage mop-up for core seams: caching, auth.session, telegram_gate, mira_notify.

Chases the remaining coverable branches in the four modules (measured baselines
on Python 3.11 from the worktree):

- caching.py:        31 missing (52%) — Redis client lifecycle, redis-backed
                     get/set/delete, import-fallback branch.
- auth/session.py:   16 missing (80%) — Cloud Run secret validation, K_SERVICE
                     branch, dev-only secret fallback, malformed session and
                     challenge payloads.
- telegram_gate.py:  17 missing (81%) — _post_telegram network shim (patched,
                     never real network), notify refusal/failure paths,
                     IllegalTransitionError race + outer catch-all.
- mira_notify.py:    19 missing (51%) — send_mira_notification config refusal,
                     Telegram POST (patched urlopen), send failure, endpoint.

All Telegram / Redis access is patched at module namespace. No network.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from test_email_reply_drafts import FakeDB  # noqa: E402  (fixture helper)

import caching  # noqa: E402
import email_reply_drafts as drafts  # noqa: E402
import mira_notify  # noqa: E402
import telegram_gate as gate  # noqa: E402
from auth import session as auth_session  # noqa: E402
from email_reply_drafts import IllegalTransitionError  # noqa: E402

GOOD_SECRET = "x" * 40  # 40 bytes, independent of the admin pin hash


# ────────────────────────────────────────────────────────────────────────────
# caching.py
# ────────────────────────────────────────────────────────────────────────────


class _FakeRedis:
    """In-memory stand-in for redis.Redis with injectable failures."""

    def __init__(self, fail_ping=False, fail_ops=None, **kwargs):
        self.kwargs = kwargs
        self.store: dict[str, str] = {}
        self.fail_ping = fail_ping
        self.fail_ops: set[str] = set(fail_ops or ())

    def ping(self):
        if self.fail_ping:
            raise ConnectionError("no redis here")
        return True

    def get(self, key):
        if "get" in self.fail_ops:
            raise ConnectionError("get boom")
        return self.store.get(key)

    def setex(self, key, ttl, value):
        if "setex" in self.fail_ops:
            raise ConnectionError("setex boom")
        self.store[key] = value

    def delete(self, key):
        if "delete" in self.fail_ops:
            raise ConnectionError("delete boom")
        self.store.pop(key, None)


def _install_fake_redis(monkeypatch, client: _FakeRedis):
    """Point caching.get_redis_client() at the fake without touching network."""
    fake_mod = types.ModuleType("redis")
    fake_mod.Redis = lambda **kwargs: client  # noqa: E731
    monkeypatch.setattr(caching, "redis", fake_mod)
    monkeypatch.setenv("REDIS_HOST", "127.0.0.1")
    caching._redis_client = None


@pytest.fixture()
def caching_clean(monkeypatch):
    monkeypatch.setattr(caching, "_redis_client", None)
    monkeypatch.delenv("REDIS_HOST", raising=False)
    caching.clear_local_cache()
    yield
    caching.clear_local_cache()
    caching._redis_client = None


class TestCachingImportFallback:
    def test_redis_import_error_sets_module_to_none(self):
        """Lines 21-22: `import redis` failing must not break the module."""
        real_redis = sys.modules.get("redis")
        sys.modules["redis"] = None  # forces ImportError on `import redis`
        importlib.reload(caching)
        try:
            assert caching.redis is None
            caching._redis_client = None
            assert caching.get_redis_client() is None
            assert caching.cache_get("anything") is None
        finally:
            if real_redis is not None:
                sys.modules["redis"] = real_redis
            else:
                sys.modules.pop("redis", None)
            importlib.reload(caching)
            caching._redis_client = None
            caching.clear_local_cache()


class TestCachingRedisClientLifecycle:
    def test_connects_and_caches_client(self, caching_clean, monkeypatch):
        """Lines 48-55 plus 40: successful connect, then cached return."""
        client = _FakeRedis()
        _install_fake_redis(monkeypatch, client)
        assert caching.get_redis_client() is client
        # Second call must short-circuit on the cached client (line 40)
        # even if the environment later goes away.
        monkeypatch.delenv("REDIS_HOST", raising=False)
        assert caching.get_redis_client() is client

    def test_ping_failure_returns_none_and_resets(self, caching_clean, monkeypatch):
        """Lines 56-59: ping failure → warn, reset, return None."""
        client = _FakeRedis(fail_ping=True)
        _install_fake_redis(monkeypatch, client)
        assert caching.get_redis_client() is None
        assert caching._redis_client is None

    def test_no_host_and_no_module_returns_none(self, caching_clean, monkeypatch):
        """Line 46 already covered elsewhere; sanity-check the guard anyway."""
        monkeypatch.setattr(caching, "redis", None)
        monkeypatch.setenv("REDIS_HOST", "127.0.0.1")
        assert caching.get_redis_client() is None


class TestCachingRedisBackedOps:
    def test_cache_get_hits_redis(self, caching_clean, monkeypatch):
        """Lines 70-73: value served from Redis when present."""
        client = _FakeRedis()
        client.store["k"] = json.dumps({"a": 1})
        _install_fake_redis(monkeypatch, client)
        assert caching.cache_get("k") == {"a": 1}

    def test_cache_get_redis_error_falls_back_to_local(
        self, caching_clean, monkeypatch
    ):
        """Lines 74-75: Redis get raising → warn, fall through to local."""
        client = _FakeRedis(fail_ops={"get"})
        _install_fake_redis(monkeypatch, client)
        caching.cache_set("k", "local-value")  # seeds the local cache
        assert caching.cache_get("k") == "local-value"

    def test_cache_set_writes_redis_and_local(self, caching_clean, monkeypatch):
        """Lines 96-99: setex path."""
        client = _FakeRedis()
        _install_fake_redis(monkeypatch, client)
        assert caching.cache_set("k", {"v": 2}, ttl_seconds=60) is True
        assert json.loads(client.store["k"]) == {"v": 2}
        assert caching.cache_get("k") == {"v": 2}

    def test_cache_set_redis_error_still_updates_local(
        self, caching_clean, monkeypatch
    ):
        """Lines 99-100: setex raising → warn, local backup still written."""
        client = _FakeRedis(fail_ops={"setex"})
        _install_fake_redis(monkeypatch, client)
        assert caching.cache_set("k", "backup") is True
        assert client.store == {}
        assert caching.cache_get("k") == "backup"

    def test_cache_delete_clears_both_caches(self, caching_clean, monkeypatch):
        """Lines 113-116 + 120-121: redis delete plus local delete."""
        client = _FakeRedis()
        _install_fake_redis(monkeypatch, client)
        caching.cache_set("k", "v")
        client.store["k"] = '"v"'
        caching.cache_delete("k")
        assert "k" not in client.store
        assert caching.cache_get("k") is None

    def test_cache_delete_redis_error_still_clears_local(
        self, caching_clean, monkeypatch
    ):
        """Lines 117-118: redis delete raising → warn, local still cleared.

        The surviving Redis copy is served afterwards — that is the intended
        "local is only a backup" semantics, not a bug.
        """
        client = _FakeRedis(fail_ops={"delete"})
        _install_fake_redis(monkeypatch, client)
        caching.cache_set("k", "v")
        caching.cache_delete("k")
        assert "k" not in caching._local_cache
        assert client.store.get("k") == '"v"'
        assert caching.cache_get("k") == "v"


# ────────────────────────────────────────────────────────────────────────────
# auth/session.py
# ────────────────────────────────────────────────────────────────────────────


class TestValidateCloudRunSessionSecret:
    def test_missing_secret_rejected(self):
        with pytest.raises(RuntimeError, match="Cloud Run security"):
            auth_session.validate_cloud_run_session_secret(None, admin_pin_hash="abc")

    def test_short_secret_rejected(self):
        with pytest.raises(RuntimeError, match="Cloud Run security"):
            auth_session.validate_cloud_run_session_secret(
                "too-short", admin_pin_hash="abc"
            )

    def test_secret_equal_to_admin_pin_hash_rejected(self):
        pin_hash = "a" * 64
        with pytest.raises(RuntimeError, match="Cloud Run security"):
            auth_session.validate_cloud_run_session_secret(
                pin_hash, admin_pin_hash=pin_hash
            )

    def test_legacy_derived_secret_rejected(self):
        pin_hash = "b" * 64
        legacy = hashlib.sha256(f"tho-session-v2-{pin_hash}".encode()).hexdigest()
        with pytest.raises(RuntimeError, match="Cloud Run security"):
            auth_session.validate_cloud_run_session_secret(
                legacy, admin_pin_hash=pin_hash
            )

    def test_independent_secret_accepted(self):
        assert (
            auth_session.validate_cloud_run_session_secret(
                GOOD_SECRET, admin_pin_hash="c" * 64
            )
            == GOOD_SECRET
        )


class TestSessionManagerBranches:
    def test_k_service_enforces_cloud_secret(self, monkeypatch):
        """Line 69: on Cloud Run an invalid secret fails closed at __init__."""
        monkeypatch.setenv("K_SERVICE", "tho-api")
        monkeypatch.delenv("ADMIN_SESSION_SECRET", raising=False)
        with pytest.raises(RuntimeError, match="Cloud Run security"):
            auth_session.SessionManager()
        monkeypatch.setenv("ADMIN_SESSION_SECRET", GOOD_SECRET)
        mgr = auth_session.SessionManager()
        tok = mgr.issue_session("user-1")
        assert mgr.verify_session(tok) == {"user_id": "user-1"}

    def test_dev_fallback_secret_when_unset(self, monkeypatch):
        """Line 75: no K_SERVICE and no env secret → random dev secret."""
        monkeypatch.delenv("K_SERVICE", raising=False)
        monkeypatch.delenv("ADMIN_SESSION_SECRET", raising=False)
        mgr = auth_session.SessionManager()
        tok = mgr.issue_session("user-1")
        assert mgr.verify_session(tok) == {"user_id": "user-1"}

    def test_verify_session_rejects_payload_without_user_id(self):
        """Line 95: well-signed payload missing user_id → None."""
        mgr = auth_session.SessionManager(secret_key=GOOD_SECRET)
        bad = mgr._serializer.dumps({"role": "admin"})
        assert mgr.verify_session(bad) is None

    def test_verify_session_rejects_garbage_token(self):
        mgr = auth_session.SessionManager(secret_key=GOOD_SECRET)
        assert mgr.verify_session("not-a-token") is None
        assert mgr.verify_session(None) is None

    def test_unwrap_challenge_non_dict_payload(self):
        """Line 117: signed but non-dict challenge payload → None."""
        mgr = auth_session.SessionManager(secret_key=GOOD_SECRET)
        handle = mgr._challenge_serializer.dumps(["not", "a", "dict"])
        assert mgr.unwrap_challenge_payload(handle, flow="login") is None

    def test_unwrap_challenge_flow_mismatch(self):
        """Line 119: payload signed for a different flow → None."""
        mgr = auth_session.SessionManager(secret_key=GOOD_SECRET)
        handle = mgr.wrap_challenge(b"challenge-bytes", flow="login")
        assert mgr.unwrap_challenge_payload(handle, flow="register") is None

    def test_unwrap_challenge_missing_b64_field(self):
        """Lines 124-125: dict payload without challenge_b64 → None."""
        mgr = auth_session.SessionManager(secret_key=GOOD_SECRET)
        handle = mgr._challenge_serializer.dumps({"flow": "login"})
        assert mgr.unwrap_challenge_payload(handle, flow="login") is None
        assert mgr.unwrap_challenge(handle, flow="login") is None

    def test_challenge_roundtrip_still_works(self):
        mgr = auth_session.SessionManager(secret_key=GOOD_SECRET)
        challenge = auth_session.SessionManager.new_challenge_bytes()
        handle = mgr.wrap_challenge(challenge, flow="login", origin="test")
        payload = mgr.unwrap_challenge_payload(handle, flow="login")
        assert payload["challenge"] == challenge
        assert payload["origin"] == "test"
        assert mgr.unwrap_challenge(handle, flow="login") == challenge


# ────────────────────────────────────────────────────────────────────────────
# telegram_gate.py
# ────────────────────────────────────────────────────────────────────────────


CHAT_ID = "777001"


@pytest.fixture()
def fake_db(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(drafts, "_firestore_client", db)
    yield db
    drafts._reset_client_for_tests()


@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setenv("THO_TG_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("THO_TG_CHAT_ID", CHAT_ID)
    monkeypatch.setenv("FF_EMAIL_TG_GATE", "1")


def _pending_draft() -> str:
    draft, _ = drafts.create_draft(
        message_id="msg-c13",
        sender="customer@example.com",
        subject="Coverage question",
        triage_label="substantive",
        inbound_excerpt="Is the Bluebonnet still available?",
    )
    return draft.draft_id


def _callback(draft_id: str, verdict: str) -> dict:
    return {
        "callback_query": {
            "id": "cbq-c13",
            "from": {"id": 7},
            "message": {"chat": {"id": int(CHAT_ID)}},
            "data": f"draft:{verdict}:{draft_id}",
        }
    }


class TestPostTelegramShim:
    def test_no_token_short_circuits(self, monkeypatch):
        """Lines 74-75: no bot token → structured refusal, zero network."""
        monkeypatch.delenv("THO_TG_BOT_TOKEN", raising=False)
        result = gate._post_telegram("sendMessage", {})
        assert result == {"ok": False, "error": "no bot token"}

    def test_success_posts_and_parses_json(self, monkeypatch):
        """Lines 77-80 (200 branch): payload forwarded, JSON returned."""
        monkeypatch.setenv("THO_TG_BOT_TOKEN", "123:abc")
        calls: list[dict] = []

        class _Resp:
            status_code = 200

            @staticmethod
            def json():
                return {"ok": True, "result": {"message_id": 5}}

        def fake_post(url, json=None, timeout=None):
            calls.append({"url": url, "json": json, "timeout": timeout})
            return _Resp()

        monkeypatch.setattr("httpx.post", fake_post)
        result = gate._post_telegram("sendMessage", {"chat_id": CHAT_ID})
        assert result == {"ok": True, "result": {"message_id": 5}}
        assert calls[0]["url"] == "https://api.telegram.org/bot123:abc/sendMessage"
        assert calls[0]["timeout"] == 10

    def test_non_200_status_reported(self, monkeypatch):
        """Lines 80 (non-200 branch): HTTP error surfaced, never raised."""

        class _Resp:
            status_code = 500

            @staticmethod
            def json():  # pragma: no cover - must not be called on non-200
                raise AssertionError("json() must not be parsed on non-200")

        monkeypatch.setenv("THO_TG_BOT_TOKEN", "123:abc")
        monkeypatch.setattr("httpx.post", lambda *a, **k: _Resp())
        result = gate._post_telegram("sendMessage", {})
        assert result == {"ok": False, "error": "HTTP 500"}

    def test_exception_becomes_error_dict(self, monkeypatch):
        """Lines 81-83: transport failure logged, structured error returned."""
        monkeypatch.setenv("THO_TG_BOT_TOKEN", "123:abc")

        def boom(*a, **k):
            raise OSError("network down")

        monkeypatch.setattr("httpx.post", boom)
        result = gate._post_telegram("sendMessage", {})
        assert result["ok"] is False
        assert "network down" in result["error"]


class TestNotifyPendingDraftGaps:
    def test_none_draft_refused(self):
        """Line 95: None draft → structured refusal, no flag/env consulted."""
        assert gate.notify_pending_draft(None) == {
            "success": False,
            "error": "no draft",
        }

    def test_send_failure_surfaced(self, fake_db, configured, monkeypatch):
        """Line 125: Telegram API failure → success False with the error."""
        monkeypatch.setattr(
            gate, "_post_telegram", lambda method, payload: {"ok": False, "error": "boom"}
        )
        result = gate.notify_pending_draft(drafts.get_draft(_pending_draft()))
        assert result == {"success": False, "error": "boom"}


class TestHandleUpdateGaps:
    def test_illegal_transition_race_returns_already_decided(
        self, fake_db, configured, monkeypatch
    ):
        """Lines 194-195: pending at read time but transition refuses."""

        def refusing_transition(draft_id, new_status, actor):
            raise IllegalTransitionError("decided concurrently")

        monkeypatch.setattr(gate, "transition", refusing_transition)
        result = gate.handle_update(_callback(_pending_draft(), "approve"))
        assert result["status"] == "already_decided"

    def test_unexpected_exception_becomes_error_status(
        self, fake_db, configured, monkeypatch
    ):
        """Lines 196-198: the catch-all keeps the webhook from ever 500ing."""

        def boom(draft_id):
            raise RuntimeError("store exploded")

        monkeypatch.setattr(gate, "get_draft", boom)
        result = gate.handle_update(_callback("any-id", "approve"))
        assert result["status"] == "error"


# ────────────────────────────────────────────────────────────────────────────
# mira_notify.py
# ────────────────────────────────────────────────────────────────────────────


class _FakeUrlopenResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


def _configure_notify(monkeypatch, *, group="-100", fallback="", token="tok"):
    monkeypatch.setattr(mira_notify, "_GROUP", group)
    monkeypatch.setattr(mira_notify, "_FALLBACK", fallback)
    monkeypatch.setattr(mira_notify, "_TOKEN", token)


class TestSendMiraNotification:
    def test_no_chat_id_refused(self, monkeypatch):
        """Lines 30-32: neither MIRA_GROUP_ID nor fallback → refusal."""
        _configure_notify(monkeypatch, group="", fallback="")
        result = mira_notify.send_mira_notification(
            mira_notify.NotifyPayload(message="hi")
        )
        assert result["ok"] is False
        assert "MIRA_GROUP_ID" in result["error"]

    def test_no_token_refused(self, monkeypatch):
        """Lines 33-34: chat id present but token missing → refusal."""
        _configure_notify(monkeypatch, token="")
        result = mira_notify.send_mira_notification(
            mira_notify.NotifyPayload(message="hi")
        )
        assert result == {"ok": False, "error": "TELEGRAM_BOT_TOKEN not configured"}

    def test_posts_markdown_to_group(self, monkeypatch):
        """Lines 35-48 (happy path incl. action_url): POST body shape."""
        _configure_notify(monkeypatch, group="-100")
        requests: list[dict] = []

        def fake_urlopen(req, timeout=None):
            requests.append(req)
            return _FakeUrlopenResponse({"ok": True, "result": {"message_id": 1}})

        monkeypatch.setattr(mira_notify.request, "urlopen", fake_urlopen)
        payload = mira_notify.NotifyPayload(
            message="Deploy finished", level="warn", source="ci", action_url="https://x"
        )
        result = mira_notify.send_mira_notification(payload)
        assert result == {"ok": True, "result": {"message_id": 1}}
        req = requests[0]
        assert req.full_url == "https://api.telegram.org/bottok/sendMessage"
        body = json.loads(req.data.decode("utf-8"))
        assert body["chat_id"] == "-100"
        assert body["parse_mode"] == "Markdown"
        assert "*THO Alert — CI*" in body["text"]
        assert "Deploy finished" in body["text"]
        assert "[View →](https://x)" in body["text"]

    def test_fallback_chat_id_used_when_group_missing(self, monkeypatch):
        """Line 30: `_GROUP or _FALLBACK` prefers group, falls back."""
        _configure_notify(monkeypatch, group="", fallback="-999")
        seen: list[str] = []

        def fake_urlopen(req, timeout=None):
            seen.append(json.loads(req.data.decode("utf-8"))["chat_id"])
            return _FakeUrlopenResponse({"ok": True})

        monkeypatch.setattr(mira_notify.request, "urlopen", fake_urlopen)
        result = mira_notify.send_mira_notification(
            mira_notify.NotifyPayload(message="hi")
        )
        assert result["ok"] is True
        assert seen == ["-999"]

    def test_urlopen_failure_returns_send_failed(self, monkeypatch):
        """Lines 49-51: transport error → warning + structured failure."""

        def boom(req, timeout=None):
            raise OSError("telegram unreachable")

        _configure_notify(monkeypatch)
        monkeypatch.setattr(mira_notify.request, "urlopen", boom)
        result = mira_notify.send_mira_notification(
            mira_notify.NotifyPayload(message="hi")
        )
        assert result == {"ok": False, "error": "send_failed"}


class TestMiraNotifyEndpoint:
    def test_endpoint_wraps_send_result(self, monkeypatch):
        """Lines 57-58: POST /notify forwards the send result verbatim."""
        monkeypatch.setattr(
            mira_notify,
            "send_mira_notification",
            lambda payload: {"ok": True, "result": {"message_id": 9}},
        )
        payload = mira_notify.NotifyPayload(message="hello", level="info")
        result = asyncio.run(mira_notify.mira_notify(payload, None))
        assert result == {
            "ok": True,
            "telegram": {"ok": True, "result": {"message_id": 9}},
        }

    def test_endpoint_propagates_failure_flag(self, monkeypatch):
        monkeypatch.setattr(
            mira_notify,
            "send_mira_notification",
            lambda payload: {"ok": False, "error": "TELEGRAM_BOT_TOKEN not configured"},
        )
        payload = mira_notify.NotifyPayload(message="hello")
        result = asyncio.run(mira_notify.mira_notify(payload, None))
        assert result["ok"] is False
        assert "telegram" in result
