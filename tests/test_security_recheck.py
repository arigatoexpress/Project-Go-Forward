"""Focused tests for the 2026-10-07 security re-check code PR.

Covers silent email failures (H2), e-sign OUTPUT_DIR (M1), streamed body
size (M2), PII log redaction (M3/M3b), DocuSeal webhook URL gate (L3),
and in-memory rate-limit eviction (L6).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from pathlib import Path

import pytest
from fastapi.responses import JSONResponse

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.test_api_v1 import create_client  # noqa: E402
from tests.test_esign_review import _ready_doc_data  # noqa: E402
from tests.test_esign_staff_review_routes import Harness, _DocDeal  # noqa: E402


def _failed_send(**_kwargs):
    return {"success": False, "error": "provider_rejected"}


def test_welcome_email_false_success_is_logged_and_warned(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    monkeypatch.setattr(main, "send_lead_welcome", _failed_send)
    monkeypatch.setenv("NOTION_LEAD_SYNC", "off")
    response = client.post(
        "/api/contact",
        json={
            "name": "Synthetic Lead",
            "phone": "5125550123",
            "email": "lead@example.com",
        },
    )
    body = response.json()
    assert response.status_code == 200
    assert body["success"] is True
    assert "welcome_email_failed" in body.get("warnings", [])
    events = [e for e in main.struct_logger.entries if e.get("event") == "welcome_email_failed"]
    assert events and events[0]["level"] == "error"


def test_inbound_notify_false_success_logs_error(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", "dGVzdA==")  # base64 "test"
    monkeypatch.setattr(main, "_INBOUND_ALLOWLIST", {"vip@example.com"})
    monkeypatch.setattr(main, "notify_new_lead", _failed_send)

    body = json.dumps(
        {"type": "email.received", "data": {"from": "VIP <vip@example.com>", "subject": "hi"}}
    ).encode()
    # Use the real verifier by stubbing it; we only care about the notify check.
    monkeypatch.setattr(main, "_verify_svix_signature", lambda *_a, **_k: True)
    response = client.post("/api/email/inbound", content=body, headers={"svix-id": "x"})
    assert response.status_code == 200
    events = [e for e in main.struct_logger.entries if e.get("event") == "owner_notify_failed"]
    assert events and events[0]["level"] == "error"


def test_appointment_email_false_success_warns(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    import types

    monkeypatch.setattr(
        main, "Appointment", lambda **kw: types.SimpleNamespace(**kw), raising=False
    )

    async def ok_create(appt):
        return types.SimpleNamespace(
            to_dict=lambda: {"appointment_id": "appt_test"},
            **{k: getattr(appt, k, None) for k in ("email", "appointment_id", "notes")},
        )

    monkeypatch.setattr(main.appointment_manager, "create_appointment", ok_create)
    monkeypatch.setattr(main, "send_appointment_confirmation", _failed_send)
    monkeypatch.setattr(main, "notify_new_appointment", _failed_send)

    response = client.post(
        "/api/appointments",
        json={
            "name": "Synthetic Lead",
            "phone": "5125550123",
            "email": "lead@example.com",
            "date": "2026-10-08",
            "time_slot": "10:00 AM",
        },
    )
    body = response.json()
    assert response.status_code == 200
    assert body["success"] is True
    assert "appointment_confirmation_failed" in body.get("warnings", [])
    assert "owner_notify_failed" in body.get("warnings", [])
    events = {e.get("event") for e in main.struct_logger.entries}
    assert "appointment_confirmation_failed" in events
    assert "owner_notify_failed" in events


def test_chat_backstop_treats_false_success_as_failure(caplog):
    import asyncio

    from tests.test_chat_lead_capture import _FakeLeadManager
    from tools.contact_capture import capture_contact_from_message

    lm = _FakeLeadManager()
    with caplog.at_level("ERROR"):
        lead = asyncio.run(
            capture_contact_from_message(
                "call me at 281-324-3020",
                "sess-fail",
                "user1",
                lead_manager=lm,
                notify=lambda **_k: {"success": False},
            )
        )
    assert lead is not None
    assert any("chat_lead_notify_failed" in r.getMessage() for r in caplog.records)


def test_packet_esign_uses_output_dir_and_reports_dispatch(monkeypatch, tmp_path):
    h = Harness(monkeypatch, doc_data=_ready_doc_data())
    monkeypatch.setattr(h.main, "Deal", _DocDeal(h))
    monkeypatch.setattr(h.main, "validate_for_documents", lambda _data: {})
    monkeypatch.setattr(h.main, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(h.main.esign_review, "auto_send_enabled", lambda: True)
    monkeypatch.setattr(h.main.esign_review, "money_problems", lambda _d: [])
    monkeypatch.setattr(h.main, "_maybe_email_document", lambda **_kw: None)
    monkeypatch.setattr(
        h.main,
        "engine_generate_packet",
        lambda **_kw: {
            "success": True,
            "file_path": str(tmp_path / "packet.pdf"),
            "filename": "packet.pdf",
            "download_url": "/api/documents/download/packet.pdf",
            "message": "ok",
        },
    )

    async def fail_send(**kwargs):
        h.file_sent.append(kwargs)
        return {"success": False, "error": f"File not found: {kwargs['file_path']}"}

    monkeypatch.setattr(h.main, "docuseal_send_file_for_signature", fail_send)
    response = h.client.post(
        "/api/deals/deal-1/generate-packet",
        headers=h.headers,
        json={"packet_name": "standard_closing"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["esign_dispatched"] is False
    assert body["esign_error"] == "file_not_found"
    assert h.file_sent[0]["file_path"] == str(tmp_path / "packet.pdf")
    assert "generated_docs/packet.pdf" not in h.file_sent[0]["file_path"] or str(tmp_path) in (
        h.file_sent[0]["file_path"]
    )


@pytest.mark.asyncio
async def test_request_size_limit_chunked_oversize_returns_413(monkeypatch):
    import main

    monkeypatch.setattr(main, "MAX_REQUEST_BODY_BYTES", 64)
    seen = {"called": False}

    async def inner(scope, receive, send):
        seen["called"] = True
        await JSONResponse({"ok": True})(scope, receive, send)

    mw = main.RequestSizeLimitMiddleware(inner)
    chunks = [
        {"type": "http.request", "body": b"x" * 40, "more_body": True},
        {"type": "http.request", "body": b"y" * 40, "more_body": False},
    ]

    async def receive():
        return chunks.pop(0) if chunks else {"type": "http.disconnect"}

    sent = []

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/api/contact", "headers": []}
    await mw(scope, receive, send)
    assert seen["called"] is False
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 413


@pytest.mark.asyncio
async def test_request_size_limit_malformed_content_length_returns_400(monkeypatch):
    import main

    async def inner(scope, receive, send):
        raise AssertionError("inner app must not run")

    mw = main.RequestSizeLimitMiddleware(inner)
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/contact",
        "headers": [(b"content-length", b"not-a-number")],
    }
    await mw(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 400


def test_small_json_is_still_sanitized(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    monkeypatch.setenv("NOTION_LEAD_SYNC", "off")
    response = client.post(
        "/api/contact",
        json={"name": "<b>Alice</b>", "phone": "5125550123"},
    )
    assert response.status_code == 200
    assert main.lead_manager.leads[-1].name == "Alice"


def test_contact_and_inbound_logs_omit_name_and_email(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    monkeypatch.setenv("NOTION_LEAD_SYNC", "off")
    client.post(
        "/api/contact",
        json={
            "name": "Jordan Brooks",
            "phone": "5125550123",
            "email": "jordan.brooks@example.com",
        },
    )
    blob = json.dumps(main.struct_logger.entries)
    assert "Jordan Brooks" not in blob
    assert "jordan.brooks@example.com" not in blob
    contact_logs = [e for e in main.struct_logger.entries if e.get("message") == "Contact form submitted"]
    assert contact_logs
    assert contact_logs[0]["has_name"] is True

    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", "dGVzdA==")
    monkeypatch.setattr(main, "_INBOUND_ALLOWLIST", {"vip@example.com"})
    monkeypatch.setattr(main, "_verify_svix_signature", lambda *_a, **_k: True)
    inbound_body = json.dumps(
        {
            "type": "email.received",
            "data": {"from": "Stranger <stranger@evil.com>", "subject": "hi"},
        }
    ).encode()
    client.post("/api/email/inbound", content=inbound_body)
    blob = json.dumps(main.struct_logger.entries)
    assert "stranger@evil.com" not in blob
    dropped = [e for e in main.struct_logger.entries if "not allowlisted" in e.get("message", "")]
    assert dropped
    assert dropped[0].get("sender_domain") == "evil.com"


def test_docuseal_webhook_missing_fields_does_not_log_payload(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    secret = "webhook-secret"
    monkeypatch.setattr(main, "_DOCUSEAL_WEBHOOK_SECRET", secret)
    event = {
        "event_type": "form.completed",
        "data": {
            "id": "sub-99",
            "submitters": [{"email": "buyer@example.com", "name": "Jane Doe"}],
        },
    }
    raw = json.dumps(event).encode()
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    response = client.post(
        "/api/docuseal/webhook",
        content=raw,
        headers={"X-Docuseal-Signature": sig},
    )
    assert response.status_code == 200
    blob = json.dumps(main.struct_logger.entries)
    assert "Jane Doe" not in blob
    assert "buyer@example.com" not in blob
    missing = [
        e
        for e in main.struct_logger.entries
        if e.get("message") == "DocuSeal webhook missing document_url or deal_id"
    ]
    assert missing
    assert missing[0]["event_type"] == "form.completed"
    assert missing[0]["has_deal_id"] is False
    assert missing[0]["submission_id"] == "sub-99"


def test_docuseal_webhook_rejects_foreign_document_url(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    secret = "webhook-secret"
    monkeypatch.setattr(main, "_DOCUSEAL_WEBHOOK_SECRET", secret)
    monkeypatch.setattr(main, "_DOCUSEAL_API_URL", "https://sign.example.com")
    event = {
        "event_type": "form.completed",
        "data": {
            "id": "sub-1",
            "metadata": {"deal_id": "deal-1"},
            "documents": [{"url": "https://evil.example/doc.pdf"}],
        },
    }
    raw = json.dumps(event).encode()
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    response = client.post(
        "/api/docuseal/webhook",
        content=raw,
        headers={"X-Docuseal-Signature": sig},
    )
    assert response.json()["reason"] == "document_url_not_allowed"


def test_rate_bucket_eviction_drops_empty_and_oldest():
    import main

    buckets: dict[str, list[float]] = {"old": [1.0], "keep": [2.0]}
    main._commit_rate_bucket(buckets, "old", [], max_buckets=2)
    assert "old" not in buckets
    main._commit_rate_bucket(buckets, "new", [3.0], max_buckets=2)
    assert "keep" in buckets
    assert "new" in buckets
    main._commit_rate_bucket(buckets, "newer", [4.0], max_buckets=2)
    assert "keep" not in buckets
    assert list(buckets) == ["new", "newer"]


def test_chat_rate_limit_fallback_evicts_oldest(monkeypatch):
    import main

    monkeypatch.setattr(main, "CHAT_RATE_LIMIT_MAX_BUCKETS", 2)
    monkeypatch.setattr(main, "CHAT_RATE_LIMIT_MAX_REQUESTS", 10)
    main._chat_rate_limit_fallback.clear()
    assert main._check_chat_rate_limit("1.1.1.1") is True
    assert main._check_chat_rate_limit("2.2.2.2") is True
    assert main._check_chat_rate_limit("3.3.3.3") is True
    assert "1.1.1.1" not in main._chat_rate_limit_fallback
    assert set(main._chat_rate_limit_fallback) == {"2.2.2.2", "3.3.3.3"}
