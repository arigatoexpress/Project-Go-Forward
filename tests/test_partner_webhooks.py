"""Unit tests for tools/partner_webhooks.py.

No outbound HTTP; `requests.post` is monkeypatched with a fake. No Firestore;
a minimal fake `db` captures any `activities/` writes for assertions.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import partner_webhooks  # noqa: E402


# ─── helpers ────────────────────────────────────────────────────────────────


class FakeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


class FakeDocRef:
    def __init__(self, store: dict, doc_id: str):
        self._store = store
        self._doc_id = doc_id

    def set(self, data):
        self._store[self._doc_id] = dict(data)


class FakeCollection:
    def __init__(self, store: dict):
        self._store = store

    def document(self, doc_id: str):
        return FakeDocRef(self._store, doc_id)


class FakeDB:
    def __init__(self):
        self.activities: dict = {}

    def collection(self, name: str):
        assert name == "activities", f"unexpected collection {name}"
        return FakeCollection(self.activities)


@pytest.fixture
def clean_env(monkeypatch):
    """Strip any PARTNER_WEBHOOK_* env the test process inherited."""
    for name in list(os.environ):
        if name.startswith("PARTNER_WEBHOOK_"):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def fake_post(monkeypatch):
    calls: list = []

    def _post(url, data=None, headers=None, timeout=None):
        calls.append(
            {
                "url": url,
                "body": data,
                "headers": dict(headers or {}),
                "timeout": timeout,
            }
        )
        return FakeResponse(200, "")

    monkeypatch.setattr(partner_webhooks.requests, "post", _post)
    return calls


# ─── _get_partner_webhook_urls ──────────────────────────────────────────────


def test_no_partners_when_env_unset(clean_env):
    assert partner_webhooks._get_partner_webhook_urls() == {}


def test_partner_urls_parsed_from_env(monkeypatch, clean_env):
    monkeypatch.setenv("PARTNER_WEBHOOK_URL_ETAI", "https://example.com/etai ")
    monkeypatch.setenv("PARTNER_WEBHOOK_URL_N8N", "https://n8n.example.com/hook")
    monkeypatch.setenv("PARTNER_WEBHOOK_URL_EMPTY", "   ")  # whitespace-only skipped

    urls = partner_webhooks._get_partner_webhook_urls()

    assert urls == {
        "etai": "https://example.com/etai",
        "n8n": "https://n8n.example.com/hook",
    }


def test_unrelated_env_vars_ignored(monkeypatch, clean_env):
    monkeypatch.setenv("PARTNER_WEBHOOK_SIGNING_KEY", "secret")  # not a URL
    monkeypatch.setenv("THO_API_KEY", "key")
    assert partner_webhooks._get_partner_webhook_urls() == {}


# ─── HMAC signing ───────────────────────────────────────────────────────────


def test_sign_produces_expected_hmac_sha256():
    body = b'{"event":"deal.funded"}'
    key = "shared-secret"
    expected = f"sha256={hmac.new(key.encode(), body, hashlib.sha256).hexdigest()}"
    assert partner_webhooks._sign(body, key) == expected


# ─── dispatch_partner_event: happy path ─────────────────────────────────────


def test_dispatch_without_partners_returns_empty(clean_env, fake_post):
    result = partner_webhooks.dispatch_partner_event("deal.funded", {"deal_id": "d1"})
    assert result == []
    assert fake_post == []


def test_dispatch_to_single_partner(monkeypatch, clean_env, fake_post):
    monkeypatch.setenv("PARTNER_WEBHOOK_URL_ETAI", "https://example.com/etai")
    monkeypatch.setenv("PARTNER_WEBHOOK_SIGNING_KEY", "signing-secret")

    db = FakeDB()
    partners = partner_webhooks.dispatch_partner_event(
        "deal.funded",
        {"deal_id": "d1", "from": "approved", "to": "funded"},
        db=db,
        blocking=True,
    )

    assert partners == ["etai"]
    assert len(fake_post) == 1
    call = fake_post[0]
    assert call["url"] == "https://example.com/etai"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["headers"]["X-THO-Event"] == "deal.funded"
    assert call["headers"]["X-THO-Partner"] == "etai"
    assert call["headers"]["X-THO-Delivery"]

    # Signature valid
    signature = call["headers"]["X-THO-Signature"]
    assert signature.startswith("sha256=")
    expected = partner_webhooks._sign(call["body"], "signing-secret")
    assert signature == expected

    # Body shape
    body = json.loads(call["body"])
    assert body["event"] == "deal.funded"
    assert body["delivered_at"]
    assert body["idempotency_key"]
    assert body["data"]["deal_id"] == "d1"
    assert body["data"]["to"] == "funded"

    # Activities collection got a success row
    assert len(db.activities) == 1
    (activity,) = db.activities.values()
    assert activity["activity_type"] == "partner_webhook_delivery.deal.funded"
    assert activity["metadata"]["partner_id"] == "etai"
    assert activity["metadata"]["success"] is True
    assert activity["metadata"]["status_code"] == 200


def test_dispatch_to_multiple_partners(monkeypatch, clean_env, fake_post):
    monkeypatch.setenv("PARTNER_WEBHOOK_URL_ETAI", "https://a")
    monkeypatch.setenv("PARTNER_WEBHOOK_URL_N8N", "https://b")

    partners = partner_webhooks.dispatch_partner_event(
        "deal.status_changed", {"deal_id": "d"}, blocking=True
    )
    assert set(partners) == {"etai", "n8n"}
    assert {c["url"] for c in fake_post} == {"https://a", "https://b"}


# ─── dispatch_partner_event: failure modes ──────────────────────────────────


def test_non_2xx_response_logged_as_failure(monkeypatch, clean_env):
    monkeypatch.setenv("PARTNER_WEBHOOK_URL_ETAI", "https://example.com/etai")
    monkeypatch.setattr(
        partner_webhooks.requests,
        "post",
        lambda *a, **kw: FakeResponse(500, "bad gateway"),
    )

    db = FakeDB()
    partner_webhooks.dispatch_partner_event(
        "deal.funded", {"deal_id": "d1"}, db=db, blocking=True
    )

    (activity,) = db.activities.values()
    assert activity["metadata"]["success"] is False
    assert activity["metadata"]["status_code"] == 500
    assert "HTTP 500" in activity["metadata"]["error"]


def test_network_error_logged_as_failure(monkeypatch, clean_env):
    monkeypatch.setenv("PARTNER_WEBHOOK_URL_ETAI", "https://example.com/etai")

    def _raise(*a, **kw):
        raise partner_webhooks.requests.exceptions.ConnectionError("unreachable")

    monkeypatch.setattr(partner_webhooks.requests, "post", _raise)

    db = FakeDB()
    partner_webhooks.dispatch_partner_event(
        "deal.funded", {"deal_id": "d1"}, db=db, blocking=True
    )

    (activity,) = db.activities.values()
    assert activity["metadata"]["success"] is False
    assert activity["metadata"]["status_code"] is None
    assert "ConnectionError" in activity["metadata"]["error"]


def test_missing_signing_key_still_delivers_but_no_signature(monkeypatch, clean_env, fake_post):
    """If signing key unset, still deliver (so dev isn't blocked) — but omit signature header."""
    monkeypatch.setenv("PARTNER_WEBHOOK_URL_ETAI", "https://example.com/etai")
    # PARTNER_WEBHOOK_SIGNING_KEY intentionally unset

    partner_webhooks.dispatch_partner_event(
        "deal.funded", {"deal_id": "d1"}, blocking=True
    )

    assert len(fake_post) == 1
    assert "X-THO-Signature" not in fake_post[0]["headers"]


def test_body_pii_is_caller_responsibility(monkeypatch, clean_env, fake_post):
    """Dispatcher does NOT redact; the caller (main.py) is responsible.

    We verify this contract so a future refactor doesn't accidentally start
    stripping fields the caller intended to include.
    """
    monkeypatch.setenv("PARTNER_WEBHOOK_URL_ETAI", "https://example.com/etai")

    partner_webhooks.dispatch_partner_event(
        "test.event",
        {"arbitrary_field": "arbitrary_value"},
        blocking=True,
    )

    body = json.loads(fake_post[0]["body"])
    assert body["data"]["arbitrary_field"] == "arbitrary_value"
