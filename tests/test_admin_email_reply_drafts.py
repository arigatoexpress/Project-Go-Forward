"""Tests for the read-only admin endpoint /api/admin/email-reply-drafts.

The endpoint is a pure read surface over the ``email_reply_drafts`` store:
admin-only, optional ``status`` filter validated against the five draft
statuses (400 on anything else), ``limit`` passed through, and a soft
failure payload (``{"success": False, ...}`` with HTTP 200) when the store
blows up — exactly mirroring /api/admin/audit-log's behavior.

Run: python -m pytest tests/test_admin_email_reply_drafts.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from test_api_v1 import create_client  # noqa: E402  (test helper)

import email_reply_drafts  # noqa: E402

# ─── Helpers ───────────────────────────────────────────────────────────────


def _seed_drafts() -> list[email_reply_drafts.ReplyDraft]:
    """Three drafts across three statuses, as the store would return them."""
    base = dict(
        sender="customer@example.com",
        triage_label="substantive",
        rule_hits=["trigger:money"],
        inbound_excerpt="How much does the Bluebonnet model cost?",
        lead_id="email_abc123",
        draft_body="Thanks for reaching out — happy to help.",
        created_at="2026-06-01T00:00:00+00:00",
        updated_at="2026-06-01T00:00:00+00:00",
        decided_by="",
    )
    return [
        email_reply_drafts.ReplyDraft(
            draft_id="d-pending",
            message_id="msg-1",
            subject="Pricing question",
            status=email_reply_drafts.STATUS_PENDING,
            **base,
        ),
        email_reply_drafts.ReplyDraft(
            draft_id="d-approved",
            message_id="msg-2",
            subject="Tour request",
            status=email_reply_drafts.STATUS_APPROVED,
            decided_by="admin:abc123",
            **{k: v for k, v in base.items() if k != "decided_by"},
        ),
        email_reply_drafts.ReplyDraft(
            draft_id="d-rejected",
            message_id="msg-3",
            subject="Spammy pitch",
            status=email_reply_drafts.STATUS_REJECTED,
            decided_by="admin:abc123",
            **{k: v for k, v in base.items() if k != "decided_by"},
        ),
    ]


@pytest.fixture()
def fake_list_drafts(monkeypatch):
    """Swap the store's list_drafts for a recording fake.

    main.py imports ``email_reply_drafts`` lazily inside the handler, so
    patching the module attribute is the seam that always works.
    """
    calls: list[dict] = []

    def fake(status: str | None = None, limit: int = 50):
        calls.append({"status": status, "limit": limit})
        return _seed_drafts()

    monkeypatch.setattr(email_reply_drafts, "list_drafts", fake)
    return calls


def _admin_headers(main) -> dict:
    return {"X-Admin-Token": main._create_admin_token()}


# ─── Endpoint tests ────────────────────────────────────────────────────────


def test_email_reply_drafts_endpoint_requires_admin(monkeypatch, fake_list_drafts):
    client, _main, _db, _logger = create_client(monkeypatch)
    response = client.get("/api/admin/email-reply-drafts")
    assert response.status_code == 401
    # The store must not be touched for an unauthenticated request.
    assert fake_list_drafts == []


def test_email_reply_drafts_endpoint_returns_drafts(monkeypatch, fake_list_drafts):
    client, main, _db, _logger = create_client(monkeypatch)
    response = client.get("/api/admin/email-reply-drafts", headers=_admin_headers(main))
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["count"] == 3
    assert len(body["drafts"]) == 3
    # Drafts are serialized via to_dict — spot-check the public shape.
    first = body["drafts"][0]
    for key in (
        "draft_id",
        "message_id",
        "sender",
        "subject",
        "triage_label",
        "status",
        "rule_hits",
        "inbound_excerpt",
        "lead_id",
        "draft_body",
        "created_at",
        "updated_at",
        "decided_by",
    ):
        assert key in first, f"missing key {key}"
    assert first["draft_id"] == "d-pending"
    assert first["status"] == "pending"
    # Unfiltered call passes status=None through with the default limit.
    assert fake_list_drafts == [{"status": None, "limit": 50}]


def test_email_reply_drafts_endpoint_passes_status_filter(monkeypatch, fake_list_drafts):
    client, main, _db, _logger = create_client(monkeypatch)
    response = client.get(
        "/api/admin/email-reply-drafts",
        headers=_admin_headers(main),
        params={"status": "pending"},
    )
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert fake_list_drafts == [{"status": "pending", "limit": 50}]


def test_email_reply_drafts_endpoint_passes_limit(monkeypatch, fake_list_drafts):
    client, main, _db, _logger = create_client(monkeypatch)
    response = client.get(
        "/api/admin/email-reply-drafts",
        headers=_admin_headers(main),
        params={"limit": 5},
    )
    assert response.status_code == 200
    assert fake_list_drafts == [{"status": None, "limit": 5}]


def test_email_reply_drafts_endpoint_rejects_invalid_status(monkeypatch, fake_list_drafts):
    client, main, _db, _logger = create_client(monkeypatch)
    response = client.get(
        "/api/admin/email-reply-drafts",
        headers=_admin_headers(main),
        params={"status": "bogus"},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert "Invalid status" in body["error"]
    # The store must not be touched for a rejected filter value.
    assert fake_list_drafts == []


def test_email_reply_drafts_endpoint_store_failure_soft_fails(monkeypatch):
    """Mirror /api/admin/audit-log: store errors return success=False with 200."""
    client, main, _db, _logger = create_client(monkeypatch)

    def explode(status: str | None = None, limit: int = 50):
        raise RuntimeError("Firestore is down")

    monkeypatch.setattr(email_reply_drafts, "list_drafts", explode)
    response = client.get("/api/admin/email-reply-drafts", headers=_admin_headers(main))
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "Failed to load email reply drafts."
