"""Tests for email_reply_sender.py — the SINGLE send chokepoint.

Safety properties under test (these ARE the spec):

1. DEFAULT INERT: with no flags set, nothing sends. Ever.
2. Master flag EMAIL_REPLY_SEND must be ON for ANY reply send.
3. Approved-draft path additionally requires draft.status == approved; a
   pending/rejected/sent draft never sends.
4. Auto-ack path additionally requires EMAIL_AUTO_ACK; it is the ONLY
   unsupervised send and uses the fixed body-echo-free template.
5. Exactly-once: a successful send transitions the draft to `sent` (terminal),
   so a retry cannot double-send; auto-ack is idempotent on message-id.
6. Daily cap: sends beyond EMAIL_REPLY_DAILY_CAP are refused.
7. Every send attempt writes an audit record; send failures leave the draft
   in `approved` (retryable) and never mark it `sent`.

Run: python -m pytest tests/test_email_reply_sender.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from test_email_reply_drafts import FakeDB  # noqa: E402  (fixture helper)

import email_reply_drafts as drafts  # noqa: E402
import email_reply_sender as sender  # noqa: E402
from email_reply_drafts import (  # noqa: E402
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_SENT,
    create_draft,
    get_draft,
    set_draft_body,
    transition,
)


@pytest.fixture()
def fake_db(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(drafts, "_firestore_client", db)
    yield db
    drafts._reset_client_for_tests()


@pytest.fixture()
def sent_emails(monkeypatch):
    """Capture outbound sends; success by default."""
    calls: list[dict] = []

    def fake_send_email(to, subject, html, email_type="general", related_id=None, text=None):
        calls.append(
            {"to": to, "subject": subject, "html": html, "email_type": email_type,
             "related_id": related_id, "text": text}
        )
        return {"success": True, "message_id": f"resend-{len(calls)}"}

    monkeypatch.setattr(sender, "send_email", fake_send_email)
    return calls


@pytest.fixture(autouse=True)
def audit_calls(monkeypatch):
    """Always mock the audit sink: tests must NEVER touch a real Firestore
    client (slow network retries locally, and a risk of writing to the prod
    audit_log collection when gcloud creds are present)."""
    calls: list[dict] = []
    monkeypatch.setattr(
        sender, "log_admin_action", lambda **kw: calls.append(kw)
    )
    return calls


@pytest.fixture(autouse=True)
def reset_cap():
    sender._reset_daily_counter_for_tests()
    yield
    sender._reset_daily_counter_for_tests()


def _flags(monkeypatch, master=False, auto_ack=False):
    monkeypatch.setenv("FF_EMAIL_REPLY_SEND", "1" if master else "0")
    monkeypatch.setenv("FF_EMAIL_AUTO_ACK", "1" if auto_ack else "0")


def _approved_draft(body="Thanks — Mark will call you tomorrow."):
    draft, _ = create_draft(
        message_id="msg-appr",
        sender="customer@example.com",
        subject="Question about the Bluebonnet",
        triage_label="substantive",
    )
    set_draft_body(draft.draft_id, body)
    transition(draft.draft_id, STATUS_APPROVED, actor="ari")
    return get_draft(draft.draft_id)


# ─── Default inertness ──────────────────────────────────────────────────────


class TestDefaultInert:
    def test_approved_draft_does_not_send_with_flags_off(
        self, fake_db, sent_emails, audit_calls, monkeypatch
    ):
        _flags(monkeypatch, master=False)
        draft = _approved_draft()
        result = sender.send_approved_reply(draft.draft_id, actor="ari")
        assert result["success"] is False
        assert "EMAIL_REPLY_SEND" in result["error"]
        assert sent_emails == []
        assert get_draft(draft.draft_id).status == STATUS_APPROVED

    def test_auto_ack_does_not_send_with_flags_off(
        self, fake_db, sent_emails, monkeypatch
    ):
        _flags(monkeypatch, master=False, auto_ack=False)
        result = sender.send_auto_ack(
            message_id="msg-1", to="customer@example.com", subject="Hours?"
        )
        assert result["success"] is False
        assert sent_emails == []

    def test_auto_ack_needs_both_master_and_auto_ack_flag(
        self, fake_db, sent_emails, monkeypatch
    ):
        _flags(monkeypatch, master=True, auto_ack=False)
        result = sender.send_auto_ack(
            message_id="msg-1", to="customer@example.com", subject="Hours?"
        )
        assert result["success"] is False
        assert "EMAIL_AUTO_ACK" in result["error"]
        assert sent_emails == []

    def test_master_flag_alone_does_not_enable_ack(self, fake_db, sent_emails, monkeypatch):
        _flags(monkeypatch, master=False, auto_ack=True)
        result = sender.send_auto_ack(
            message_id="msg-1", to="customer@example.com", subject="Hours?"
        )
        assert result["success"] is False
        assert sent_emails == []


# ─── Approved-draft path ────────────────────────────────────────────────────


class TestApprovedDraftPath:
    def test_approved_draft_sends_once_and_marks_sent(
        self, fake_db, sent_emails, audit_calls, monkeypatch
    ):
        _flags(monkeypatch, master=True)
        draft = _approved_draft()
        result = sender.send_approved_reply(draft.draft_id, actor="ari")
        assert result["success"] is True
        assert len(sent_emails) == 1
        assert sent_emails[0]["to"] == "customer@example.com"
        assert "Mark will call you" in sent_emails[0]["html"]
        assert get_draft(draft.draft_id).status == STATUS_SENT
        # Retry refuses — sent is terminal.
        retry = sender.send_approved_reply(draft.draft_id, actor="ari")
        assert retry["success"] is False
        assert len(sent_emails) == 1

    @pytest.mark.parametrize("status_path", [[], [STATUS_REJECTED]])
    def test_non_approved_draft_never_sends(
        self, fake_db, sent_emails, monkeypatch, status_path
    ):
        _flags(monkeypatch, master=True)
        draft, _ = create_draft(
            message_id="msg-x", sender="c@e.com", subject="s", triage_label="substantive"
        )
        for step in status_path:
            transition(draft.draft_id, step, actor="ari")
        result = sender.send_approved_reply(draft.draft_id, actor="ari")
        assert result["success"] is False
        assert sent_emails == []

    def test_missing_draft_refuses(self, fake_db, sent_emails, monkeypatch):
        _flags(monkeypatch, master=True)
        result = sender.send_approved_reply("nope", actor="ari")
        assert result["success"] is False
        assert sent_emails == []

    def test_empty_body_refuses(self, fake_db, sent_emails, monkeypatch):
        _flags(monkeypatch, master=True)
        draft, _ = create_draft(
            message_id="msg-empty", sender="c@e.com", subject="s", triage_label="substantive"
        )
        transition(draft.draft_id, STATUS_APPROVED, actor="ari")
        result = sender.send_approved_reply(draft.draft_id, actor="ari")
        assert result["success"] is False
        assert sent_emails == []

    def test_send_failure_leaves_draft_approved(self, fake_db, monkeypatch, audit_calls):
        _flags(monkeypatch, master=True)
        monkeypatch.setattr(
            sender, "send_email", lambda **kw: {"success": False, "error": "boom"}
        )
        draft = _approved_draft()
        result = sender.send_approved_reply(draft.draft_id, actor="ari")
        assert result["success"] is False
        assert get_draft(draft.draft_id).status == STATUS_APPROVED  # retryable

    def test_audit_record_written_on_send(
        self, fake_db, sent_emails, audit_calls, monkeypatch
    ):
        _flags(monkeypatch, master=True)
        draft = _approved_draft()
        sender.send_approved_reply(draft.draft_id, actor="ari")
        assert len(audit_calls) == 1
        entry = audit_calls[0]
        assert entry["action"] == "email.send"
        assert entry["target_type"] == "email"
        assert entry["actor"] == "ari"
        assert entry["details"]["kind"] == "reply_draft"


# ─── Auto-ack path ──────────────────────────────────────────────────────────


class TestAutoAckPath:
    def test_ack_sends_fixed_template(self, fake_db, sent_emails, audit_calls, monkeypatch):
        _flags(monkeypatch, master=True, auto_ack=True)
        result = sender.send_auto_ack(
            message_id="msg-ack", to="customer@example.com", subject="Hours?"
        )
        assert result["success"] is True
        assert len(sent_emails) == 1
        assert "received" in sent_emails[0]["text"].lower()
        assert sent_emails[0]["email_type"] == "inbound_auto_ack"

    def test_ack_idempotent_on_message_id(self, fake_db, sent_emails, monkeypatch):
        _flags(monkeypatch, master=True, auto_ack=True)
        r1 = sender.send_auto_ack(message_id="msg-ack", to="c@e.com", subject="s")
        r2 = sender.send_auto_ack(message_id="msg-ack", to="c@e.com", subject="s")
        assert r1["success"] is True
        assert r2["success"] is False
        assert "already" in r2["error"].lower()
        assert len(sent_emails) == 1

    def test_ack_recorded_in_draft_store_as_sent(self, fake_db, sent_emails, monkeypatch):
        _flags(monkeypatch, master=True, auto_ack=True)
        sender.send_auto_ack(message_id="msg-ack", to="c@e.com", subject="s")
        records = drafts.list_drafts(status=STATUS_SENT)
        assert len(records) == 1
        assert records[0].triage_label == "safe_ack"
        assert "auto_ack" in records[0].decided_by

    def test_ack_audit_written(self, fake_db, sent_emails, audit_calls, monkeypatch):
        _flags(monkeypatch, master=True, auto_ack=True)
        sender.send_auto_ack(message_id="msg-ack", to="c@e.com", subject="s")
        assert len(audit_calls) == 1
        assert audit_calls[0]["details"]["kind"] == "auto_ack"


# ─── Daily cap ──────────────────────────────────────────────────────────────


class TestDailyCap:
    def test_cap_refuses_further_sends(self, fake_db, sent_emails, monkeypatch):
        _flags(monkeypatch, master=True, auto_ack=True)
        monkeypatch.setenv("EMAIL_REPLY_DAILY_CAP", "2")
        for i in range(2):
            r = sender.send_auto_ack(message_id=f"msg-{i}", to="c@e.com", subject="s")
            assert r["success"] is True
        blocked = sender.send_auto_ack(message_id="msg-over", to="c@e.com", subject="s")
        assert blocked["success"] is False
        assert "cap" in blocked["error"].lower()
        assert len(sent_emails) == 2

    def test_cap_applies_to_approved_path_too(self, fake_db, sent_emails, monkeypatch):
        _flags(monkeypatch, master=True)
        monkeypatch.setenv("EMAIL_REPLY_DAILY_CAP", "0")
        draft = _approved_draft()
        result = sender.send_approved_reply(draft.draft_id, actor="ari")
        assert result["success"] is False
        assert sent_emails == []
        assert get_draft(draft.draft_id).status == STATUS_APPROVED


# ─── Chokepoint invariant ───────────────────────────────────────────────────


class TestChokepointInvariant:
    def test_pipeline_modules_never_call_send_email_directly(self):
        """Only email_reply_sender may touch email_service in the reply pipeline."""
        root = Path(__file__).parent.parent
        for module in ("email_triage.py", "email_reply_drafts.py", "email_reply_generator.py"):
            source = (root / module).read_text()
            import_lines = [
                line for line in source.splitlines()
                if line.strip().startswith(("import ", "from "))
            ]
            assert not any("email_service" in line for line in import_lines), module
