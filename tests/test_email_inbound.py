"""AI agent email inbox — webhook security + triage.

Inbound email is untrusted: the endpoint must be feature-flagged off without a
secret, reject bad signatures, drop non-allowlisted senders, and only triage
allowlisted mail into a CRM lead (never auto-reply).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

_SECRET = "whsec_" + base64.b64encode(b"super-secret-key").decode()


def _sig(svix_id: str, ts: str, body: bytes) -> str:
    key = base64.b64decode(_SECRET.split("_", 1)[1])
    signed = f"{svix_id}.{ts}.".encode() + body
    return "v1," + base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()


@pytest.fixture
def tc():
    import main

    return TestClient(main.app, raise_server_exceptions=False), main


# ─── helper units ───────────────────────────────────────────────────────────


def test_verify_svix_signature_valid_and_invalid():
    import main

    ts = str(int(time.time()))
    body = b'{"hello":"world"}'
    good = _sig("msg_1", ts, body)
    h = {"svix-id": "msg_1", "svix-timestamp": ts, "svix-signature": good}
    assert main._verify_svix_signature(_SECRET, h, body) is True
    # tampered body
    assert main._verify_svix_signature(_SECRET, h, b'{"hello":"evil"}') is False
    # bad signature
    assert main._verify_svix_signature(
        _SECRET, {**h, "svix-signature": "v1,deadbeef"}, body
    ) is False
    # stale timestamp (replay)
    old = str(int(time.time()) - 9999)
    assert main._verify_svix_signature(
        _SECRET, {"svix-id": "m", "svix-timestamp": old, "svix-signature": _sig("m", old, body)}, body
    ) is False


def test_inbound_sender_allowlist(monkeypatch):
    import main

    monkeypatch.setattr(main, "_INBOUND_ALLOWLIST", {"vip@example.com", "@trusted.com"})
    assert main._inbound_sender_allowed("vip@example.com") is True
    assert main._inbound_sender_allowed("VIP <vip@example.com>") is True
    assert main._inbound_sender_allowed("anyone@trusted.com") is True  # domain entry
    assert main._inbound_sender_allowed("stranger@evil.com") is False
    monkeypatch.setattr(main, "_INBOUND_ALLOWLIST", set())
    assert main._inbound_sender_allowed("vip@example.com") is False  # fail closed


# ─── endpoint ───────────────────────────────────────────────────────────────


def test_inbound_disabled_without_secret(tc, monkeypatch):
    client, main = tc
    monkeypatch.delenv("RESEND_WEBHOOK_SECRET", raising=False)
    r = client.post("/api/email/inbound", content=b"{}")
    assert r.status_code == 200 and r.json()["status"] == "disabled"


def test_inbound_rejects_bad_signature(tc, monkeypatch):
    client, main = tc
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", _SECRET)
    r = client.post(
        "/api/email/inbound",
        content=b"{}",
        headers={"svix-id": "x", "svix-timestamp": str(int(time.time())), "svix-signature": "v1,bad"},
    )
    assert r.status_code == 401


def test_inbound_drops_non_allowlisted(tc, monkeypatch):
    client, main = tc
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", _SECRET)
    monkeypatch.setattr(main, "_INBOUND_ALLOWLIST", {"vip@example.com"})
    body = json.dumps(
        {"type": "email.received", "data": {"from": "stranger@evil.com", "subject": "hi"}}
    ).encode()
    ts, sid = str(int(time.time())), "msg_drop"
    r = client.post(
        "/api/email/inbound",
        content=body,
        headers={"svix-id": sid, "svix-timestamp": ts, "svix-signature": _sig(sid, ts, body)},
    )
    assert r.status_code == 200 and r.json()["status"] == "dropped"


def test_inbound_processes_allowlisted(tc, monkeypatch):
    client, main = tc
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", _SECRET)
    monkeypatch.setattr(main, "_INBOUND_ALLOWLIST", {"vip@example.com"})

    created = {}

    async def fake_create(lead):
        created["lead"] = lead
        return lead

    notified = {}
    monkeypatch.setattr(main.lead_manager, "create_lead", fake_create)
    monkeypatch.setattr(main, "notify_new_lead", lambda **k: notified.update(k) or {"success": True})

    body = json.dumps(
        {"type": "email.received", "data": {"from": "VIP <vip@example.com>", "subject": "Need a quote"}}
    ).encode()
    ts, sid = str(int(time.time())), "msg_ok"
    r = client.post(
        "/api/email/inbound",
        content=body,
        headers={"svix-id": sid, "svix-timestamp": ts, "svix-signature": _sig(sid, ts, body)},
    )
    assert r.status_code == 200 and r.json()["status"] == "processed"
    assert created["lead"].email == "vip@example.com"
    assert "Need a quote" in created["lead"].triage_notes
    assert notified.get("email") == "vip@example.com"
