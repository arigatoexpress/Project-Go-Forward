"""Tests for telegram_gate.py + POST /api/telegram/webhook — the human
approve/reject rail for email reply drafts.

Safety properties under test (these ARE the spec):

1. INERT WITHOUT CONFIG: no THO_TG_BOT_TOKEN/THO_TG_CHAT_ID → notify is a
   no-op; no THO_TG_WEBHOOK_SECRET → the webhook route answers "disabled" and
   processes nothing. No flag (FF_EMAIL_TG_GATE, default OFF) → same.
2. The gate NEVER sends email itself — approve only moves a draft to
   `approved` and then defers to the single chokepoint
   (email_reply_sender.send_approved_reply), which has its own flag stack.
   With send flags off, an approved draft stays approved and unsent.
3. Callbacks are accepted only from the configured chat id and only with the
   configured webhook secret header; anything else is rejected.
4. Replay of a decided callback is idempotent — it can never flip a decision
   or trigger anything twice.

Run: python -m pytest tests/test_telegram_gate.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from test_email_reply_drafts import FakeDB  # noqa: E402  (fixture helper)

import email_reply_drafts as drafts  # noqa: E402
import telegram_gate as gate  # noqa: E402
from email_reply_drafts import (  # noqa: E402
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    create_draft,
    get_draft,
)

CHAT_ID = "777001"


@pytest.fixture()
def fake_db(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(drafts, "_firestore_client", db)
    yield db
    drafts._reset_client_for_tests()


@pytest.fixture()
def tg_calls(monkeypatch):
    """Capture outbound Telegram API calls; success by default."""
    calls: list[dict] = []

    def fake_post(method: str, payload: dict) -> dict:
        calls.append({"method": method, "payload": payload})
        return {"ok": True}

    monkeypatch.setattr(gate, "_post_telegram", fake_post)
    return calls


@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setenv("THO_TG_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("THO_TG_CHAT_ID", CHAT_ID)
    monkeypatch.setenv("FF_EMAIL_TG_GATE", "1")


def _pending_draft() -> str:
    draft, _ = create_draft(
        message_id="msg-tg",
        sender="customer@example.com",
        subject="Question about the Bluebonnet",
        triage_label="substantive",
        inbound_excerpt="How much does it cost?",
    )
    return draft.draft_id


def _callback(draft_id: str, verdict: str, chat_id: str = CHAT_ID) -> dict:
    return {
        "callback_query": {
            "id": "cbq-1",
            "from": {"id": 42, "username": "ari"},
            "message": {"chat": {"id": int(chat_id)}},
            "data": f"draft:{verdict}:{draft_id}",
        }
    }


# ─── Inertness ──────────────────────────────────────────────────────────────


class TestInertWithoutConfig:
    def test_notify_noop_without_token(self, fake_db, tg_calls, monkeypatch):
        monkeypatch.delenv("THO_TG_BOT_TOKEN", raising=False)
        monkeypatch.delenv("THO_TG_CHAT_ID", raising=False)
        monkeypatch.setenv("FF_EMAIL_TG_GATE", "1")
        draft_id = _pending_draft()
        result = gate.notify_pending_draft(get_draft(draft_id))
        assert result["success"] is False
        assert tg_calls == []

    def test_notify_noop_with_flag_off(self, fake_db, tg_calls, monkeypatch):
        monkeypatch.setenv("THO_TG_BOT_TOKEN", "123:abc")
        monkeypatch.setenv("THO_TG_CHAT_ID", CHAT_ID)
        monkeypatch.setenv("FF_EMAIL_TG_GATE", "0")
        draft_id = _pending_draft()
        result = gate.notify_pending_draft(get_draft(draft_id))
        assert result["success"] is False
        assert "EMAIL_TG_GATE" in result["error"]
        assert tg_calls == []

    def test_is_configured(self, monkeypatch):
        monkeypatch.delenv("THO_TG_BOT_TOKEN", raising=False)
        monkeypatch.delenv("THO_TG_CHAT_ID", raising=False)
        assert gate.is_configured() is False
        monkeypatch.setenv("THO_TG_BOT_TOKEN", "123:abc")
        assert gate.is_configured() is False
        monkeypatch.setenv("THO_TG_CHAT_ID", CHAT_ID)
        assert gate.is_configured() is True


# ─── Notify card ────────────────────────────────────────────────────────────


class TestNotify:
    def test_card_payload_shape(self, fake_db, tg_calls, configured):
        draft_id = _pending_draft()
        result = gate.notify_pending_draft(get_draft(draft_id))
        assert result["success"] is True
        assert len(tg_calls) == 1
        call = tg_calls[0]
        assert call["method"] == "sendMessage"
        payload = call["payload"]
        assert payload["chat_id"] == CHAT_ID
        assert "customer@example.com" in payload["text"]
        buttons = payload["reply_markup"]["inline_keyboard"][0]
        datas = {b["callback_data"] for b in buttons}
        assert f"draft:approve:{draft_id}" in datas
        assert f"draft:reject:{draft_id}" in datas

    def test_notify_never_sends_email(self, fake_db, tg_calls, configured):
        """The gate module must not import the email service at all."""
        source = Path(gate.__file__).read_text()
        import_lines = [
            line for line in source.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        assert not any("email_service" in line for line in import_lines)


# ─── Callback handling ──────────────────────────────────────────────────────


class TestHandleUpdate:
    def test_approve_moves_draft_to_approved(self, fake_db, tg_calls, configured, monkeypatch):
        # Send flags OFF: approval must park the draft at approved, unsent.
        monkeypatch.setenv("FF_EMAIL_REPLY_SEND", "0")
        draft_id = _pending_draft()
        result = gate.handle_update(_callback(draft_id, "approve"))
        assert result["status"] == "approved"
        assert get_draft(draft_id).status == STATUS_APPROVED
        assert "telegram:" in get_draft(draft_id).decided_by
        # Chokepoint refused (flag off) — surfaced, not hidden.
        assert result["send"]["success"] is False

    def test_reject_moves_draft_to_rejected(self, fake_db, tg_calls, configured):
        draft_id = _pending_draft()
        result = gate.handle_update(_callback(draft_id, "reject"))
        assert result["status"] == "rejected"
        assert get_draft(draft_id).status == STATUS_REJECTED

    def test_wrong_chat_id_rejected(self, fake_db, tg_calls, configured):
        draft_id = _pending_draft()
        result = gate.handle_update(_callback(draft_id, "approve", chat_id="999999"))
        assert result["status"] == "unauthorized"
        assert get_draft(draft_id).status == STATUS_PENDING

    def test_replay_is_idempotent(self, fake_db, tg_calls, configured, monkeypatch):
        monkeypatch.setenv("FF_EMAIL_REPLY_SEND", "0")
        draft_id = _pending_draft()
        gate.handle_update(_callback(draft_id, "approve"))
        replay = gate.handle_update(_callback(draft_id, "approve"))
        assert replay["status"] == "already_decided"
        assert get_draft(draft_id).status == STATUS_APPROVED

    def test_reject_after_approve_cannot_flip(self, fake_db, tg_calls, configured, monkeypatch):
        monkeypatch.setenv("FF_EMAIL_REPLY_SEND", "0")
        draft_id = _pending_draft()
        gate.handle_update(_callback(draft_id, "approve"))
        flip = gate.handle_update(_callback(draft_id, "reject"))
        assert flip["status"] == "already_decided"
        assert get_draft(draft_id).status == STATUS_APPROVED

    def test_unknown_callback_data_ignored(self, fake_db, tg_calls, configured):
        result = gate.handle_update(_callback("x", "explode"))
        assert result["status"] == "ignored"

    def test_non_callback_update_ignored(self, fake_db, tg_calls, configured):
        assert gate.handle_update({"message": {"text": "hi"}})["status"] == "ignored"
        assert gate.handle_update({})["status"] == "ignored"
        assert gate.handle_update(None)["status"] == "ignored"

    def test_missing_draft_reported(self, fake_db, tg_calls, configured):
        result = gate.handle_update(_callback("nonexistent", "approve"))
        assert result["status"] == "not_found"


# ─── Webhook secret ─────────────────────────────────────────────────────────


class TestWebhookSecret:
    def test_fail_closed_without_secret(self, monkeypatch):
        monkeypatch.delenv("THO_TG_WEBHOOK_SECRET", raising=False)
        assert gate.verify_webhook_secret("anything") is False
        assert gate.verify_webhook_secret("") is False

    def test_matches_configured_secret(self, monkeypatch):
        monkeypatch.setenv("THO_TG_WEBHOOK_SECRET", "s3cret")
        assert gate.verify_webhook_secret("s3cret") is True
        assert gate.verify_webhook_secret("wrong") is False
        assert gate.verify_webhook_secret("") is False


# ─── Route ──────────────────────────────────────────────────────────────────


class TestWebhookRoute:
    @pytest.fixture()
    def tc(self):
        from fastapi.testclient import TestClient

        import main

        return TestClient(main.app, raise_server_exceptions=False)

    def test_disabled_without_secret_env(self, tc, monkeypatch):
        monkeypatch.delenv("THO_TG_WEBHOOK_SECRET", raising=False)
        r = tc.post("/api/telegram/webhook", json={"update_id": 1})
        assert r.status_code == 200
        assert r.json()["status"] == "disabled"

    def test_401_on_bad_secret_header(self, tc, monkeypatch):
        monkeypatch.setenv("THO_TG_WEBHOOK_SECRET", "s3cret")
        r = tc.post(
            "/api/telegram/webhook",
            json={"update_id": 1},
            headers={"x-telegram-bot-api-secret-token": "wrong"},
        )
        assert r.status_code == 401

    def test_valid_secret_reaches_handler(self, tc, monkeypatch, fake_db, tg_calls, configured):
        monkeypatch.setenv("THO_TG_WEBHOOK_SECRET", "s3cret")
        r = tc.post(
            "/api/telegram/webhook",
            json={"message": {"text": "hi"}},
            headers={"x-telegram-bot-api-secret-token": "s3cret"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "ignored"

    def test_bad_json_tolerated(self, tc, monkeypatch):
        monkeypatch.setenv("THO_TG_WEBHOOK_SECRET", "s3cret")
        r = tc.post(
            "/api/telegram/webhook",
            content=b"not json",
            headers={
                "x-telegram-bot-api-secret-token": "s3cret",
                "content-type": "application/json",
            },
        )
        assert r.status_code == 200
        assert r.json()["status"] == "ignored"
