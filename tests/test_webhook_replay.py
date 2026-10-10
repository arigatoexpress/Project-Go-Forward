"""Replay protection for outbound partner webhooks (v2 signature) and inbound DocuSeal events.

No network, no Firestore: requests.post and the app's eager imports are stubbed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import partner_webhooks as pw  # noqa: E402
from tools.webhook_replay import ReplayGuard, docuseal_event_key  # noqa: E402

KEY = "partner-signing-key"  # pragma: allowlist secret
BODY = b'{"event":"deal.funded","data":{"deal_id":"d1"}}'
NOW = 1_800_000_000


def _headers(**over):
    h = pw.signed_headers(
        "deal.funded", "etai", "11111111-2222-3333-4444-555555555555", BODY, KEY, now=NOW
    )
    h.update(over)
    return h


class FakeClock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


# ─── outbound v2 signature ─────────────────────────────────────────────────


def test_v2_round_trip_verifies():
    assert pw.verify_partner_webhook(_headers(), BODY, KEY, now=NOW + 10)


def test_header_names_are_case_insensitive():
    lowered = {k.lower(): v for k, v in _headers().items()}
    assert pw.verify_partner_webhook(lowered, BODY, KEY, now=NOW)


def test_legacy_body_signature_still_sent_and_unchanged():
    h = _headers()
    assert (
        h["X-THO-Signature"] == "sha256=" + hmac.new(KEY.encode(), BODY, hashlib.sha256).hexdigest()
    )


@pytest.mark.parametrize(
    "header", ["X-THO-Event", "X-THO-Partner", "X-THO-Delivery", "X-THO-Timestamp"]
)
def test_changing_any_signed_header_fails(header):
    value = str(NOW + 1) if header == "X-THO-Timestamp" else "tampered"
    assert not pw.verify_partner_webhook(_headers(**{header: value}), BODY, KEY, now=NOW)


def test_changing_body_fails():
    assert not pw.verify_partner_webhook(_headers(), BODY + b" ", KEY, now=NOW)


def test_wrong_key_fails():
    assert not pw.verify_partner_webhook(_headers(), BODY, "other-key", now=NOW)


@pytest.mark.parametrize("delta", [-301, 301, 86_400])
def test_timestamp_outside_window_fails(delta):
    assert not pw.verify_partner_webhook(_headers(), BODY, KEY, now=NOW + delta)


def test_non_numeric_timestamp_fails():
    assert not pw.verify_partner_webhook(_headers(**{"X-THO-Timestamp": "1e9"}), BODY, KEY, now=NOW)


def test_replayed_delivery_rejected_with_guard():
    guard = ReplayGuard(300)
    assert pw.verify_partner_webhook(_headers(), BODY, KEY, now=NOW, replay_guard=guard)
    assert not pw.verify_partner_webhook(_headers(), BODY, KEY, now=NOW + 5, replay_guard=guard)


def test_legacy_only_rejected_unless_opted_in():
    legacy = {
        k: v for k, v in _headers().items() if k not in ("X-THO-Signature-V2", "X-THO-Timestamp")
    }
    assert not pw.verify_partner_webhook(legacy, BODY, KEY, now=NOW)
    assert pw.verify_partner_webhook(legacy, BODY, KEY, now=NOW, allow_legacy=True)
    assert not pw.verify_partner_webhook(legacy, BODY + b"x", KEY, now=NOW, allow_legacy=True)


def test_empty_key_fails_closed():
    assert not pw.verify_partner_webhook(_headers(), BODY, "", now=NOW)


def test_newline_in_header_value_is_refused():
    with pytest.raises(ValueError):
        pw.signed_message_v2("1", "deal.funded\nX", "etai", "d", BODY)


def test_dispatch_sends_both_signatures(monkeypatch):
    calls = []

    class _Resp:
        status_code = 200
        text = ""

    def _post(url, data=None, headers=None, timeout=None):
        calls.append({"body": data, "headers": dict(headers or {})})
        return _Resp()

    pw._reset_unsigned_guard_for_tests()
    monkeypatch.setattr(pw.requests, "post", _post)
    for name in [n for n in __import__("os").environ if n.startswith("PARTNER_WEBHOOK_")]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PARTNER_WEBHOOK_URL_ETAI", "https://example.com/etai")
    monkeypatch.setenv("PARTNER_WEBHOOK_SIGNING_KEY", KEY)
    assert pw.dispatch_partner_event("deal.funded", {"deal_id": "d1"}, blocking=True) == ["etai"]
    (call,) = calls
    assert call["headers"]["X-THO-Signature"] == pw._sign(call["body"], KEY)
    assert pw.verify_partner_webhook(call["headers"], call["body"], KEY)


# ─── ReplayGuard ────────────────────────────────────────────────────────────


def test_guard_expires_after_ttl():
    clock = FakeClock(0)
    guard = ReplayGuard(60, clock=clock)
    assert guard.claim("a")
    assert not guard.claim("a")
    clock.t = 61
    assert guard.claim("a")


def test_guard_release_allows_retry():
    guard = ReplayGuard(60)
    assert guard.claim("a")
    guard.release("a")
    assert guard.claim("a")


def test_guard_is_bounded():
    guard = ReplayGuard(3600, max_entries=3)
    for key in "abcd":
        assert guard.claim(key)
    assert guard.claim("a")  # oldest evicted once over the cap


# ─── DocuSeal event key ─────────────────────────────────────────────────────

T0 = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


def _ds_event(ts=T0, event_id=42):
    return {
        "event_type": "form.completed",
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "data": {"id": event_id},
    }


def test_docuseal_key_uses_event_identity():
    ev = _ds_event()
    key, reason = docuseal_event_key(ev, json.dumps(ev).encode(), now=T0.timestamp())
    assert reason is None and key == f"docuseal:form.completed:42:{ev['timestamp']}"


def test_docuseal_stale_and_future_rejected():
    ev = _ds_event(T0 - timedelta(hours=73))
    assert docuseal_event_key(ev, b"", now=T0.timestamp()) == (None, "stale_timestamp")
    ev = _ds_event(T0 + timedelta(minutes=10))
    assert docuseal_event_key(ev, b"", now=T0.timestamp()) == (None, "future_timestamp")


def test_docuseal_invalid_timestamp_rejected():
    ev = {"event_type": "form.completed", "timestamp": "yesterday", "data": {"id": 1}}
    assert docuseal_event_key(ev, b"", now=T0.timestamp()) == (None, "invalid_timestamp")


def test_docuseal_without_timestamp_falls_back_to_body_hash():
    body = b'{"event_type":"form.completed","data":{"id":1}}'
    key, reason = docuseal_event_key(json.loads(body), body, now=T0.timestamp())
    assert reason is None and key == "docuseal:body:" + hashlib.sha256(body).hexdigest()


# ─── DocuSeal route ─────────────────────────────────────────────────────────


@pytest.fixture
def docuseal_client(monkeypatch):
    from tests.test_api_v1 import load_app

    main, _db, _logger = load_app(monkeypatch, tho_api_key="tho-secret")
    monkeypatch.setattr(main, "_DOCUSEAL_WEBHOOK_SECRET", "ds-secret")
    monkeypatch.setattr(main, "_DOCUSEAL_REPLAY_GUARD", ReplayGuard(3600))
    return TestClient(main.app)


def _post_signed(client, payload):
    body = json.dumps(payload).encode()
    sig = hmac.new(b"ds-secret", body, hashlib.sha256).hexdigest()
    return client.post(
        "/api/docuseal/webhook",
        content=body,
        headers={"X-Docuseal-Signature": sig, "Content-Type": "application/json"},
    )


def test_route_drops_exact_replay(docuseal_client):
    # No deal_id: the handler stops before any download or write.
    payload = _ds_event(datetime.now(UTC))
    first = _post_signed(docuseal_client, payload)
    assert first.status_code == 200 and first.json()["reason"] == "missing_document_url_or_deal_id"
    second = _post_signed(docuseal_client, payload)
    assert second.status_code == 200 and second.json() == {"status": "duplicate"}


def test_route_rejects_stale_event(docuseal_client):
    resp = _post_signed(docuseal_client, _ds_event(datetime.now(UTC) - timedelta(days=4)))
    assert resp.status_code == 200 and resp.json()["reason"] == "stale_timestamp"


def test_route_still_rejects_bad_signature(docuseal_client):
    resp = docuseal_client.post(
        "/api/docuseal/webhook", content=b"{}", headers={"X-Docuseal-Signature": "bad"}
    )
    assert resp.status_code == 401
