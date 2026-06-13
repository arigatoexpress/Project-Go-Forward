"""Unit tests for services.telegram.approval.

No outbound HTTP and no Firestore emulator: the Telegram client and Firestore
``db`` are both replaced with lightweight fakes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from database.models import Deal, DealStatus
from services.telegram import approval
from services.telegram.client import TelegramAPIError, TelegramClient
from services.telegram.config import TelegramConfig
from services.telegram.rich_message import InlineKeyboardMarkup

# ─── helpers ──────────────────────────────────────────────────────────────────


class FakeDocRef:
    def __init__(self, store: dict, doc_id: str):
        self._store = store
        self._doc_id = doc_id

    def set(self, data: dict) -> None:
        self._store[self._doc_id] = dict(data)

    def update(self, data: dict) -> None:
        existing = self._store.setdefault(self._doc_id, {})
        existing.update(data)


class FakeCollection:
    def __init__(self, store: dict):
        self._store = store

    def document(self, doc_id: str):
        return FakeDocRef(self._store, doc_id)


class FakeDB:
    def __init__(self):
        self.activities: dict = {}
        self.deals: dict = {}

    def collection(self, name: str):
        if name == "activities":
            return FakeCollection(self.activities)
        if name == "deals":
            return FakeCollection(self.deals)
        raise ValueError(f"unexpected collection {name}")


@dataclass
class FakeTelegramClient(TelegramClient):
    """In-memory Telegram client that records calls instead of calling Telegram."""

    sent_messages: list = field(default_factory=list)
    answered_queries: list = field(default_factory=list)
    edited_messages: list = field(default_factory=list)
    fail_next_send: bool = False
    fail_next_edit: bool = False

    def __post_init__(self):
        # TelegramClient.__init__ is bypassed by dataclass; ensure config exists.
        object.__setattr__(
            self, "config", TelegramConfig("", "", "", "", enabled=True)
        )

    def send_rich_message(
        self,
        chat_id: str | int,
        text: str,
        *,
        parse_mode: str = "MarkdownV2",
        reply_markup: dict | None = None,
    ) -> dict[str, Any]:
        if self.fail_next_send:
            raise TelegramAPIError("fake send failure")
        self.sent_messages.append(
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            }
        )
        return {"message_id": 12345}

    def send_message(self, chat_id, text, *, parse_mode=None, reply_markup=None):
        self.sent_messages.append(
            {"chat_id": chat_id, "text": text, "reply_markup": reply_markup}
        )
        return {"message_id": 12345}

    def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str | None = None,
        show_alert: bool = False,
    ) -> dict[str, Any]:
        self.answered_queries.append(
            {
                "callback_query_id": callback_query_id,
                "text": text,
                "show_alert": show_alert,
            }
        )
        return {"ok": True}

    def edit_message_text(
        self,
        chat_id: str | int,
        message_id: int,
        text: str,
        *,
        parse_mode: str | None = None,
        reply_markup: dict | None = None,
    ) -> dict[str, Any]:
        self.edited_messages.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            }
        )
        if self.fail_next_edit:
            raise TelegramAPIError("fake edit failure")
        return {"ok": True}


def make_deal(**overrides) -> Deal:
    defaults = {
        "id": "deal-123",
        "buyer_first_name": "Jane",
        "buyer_last_name": "Doe",
        "sales_price": 125000.0,
        "down_payment": 25000.0,
        "status": DealStatus.PENDING,
        "manufacturer": "Tru",
        "model": "Belton",
        "salesrep": "Alice",
    }
    defaults.update(overrides)
    return Deal(**defaults)


def make_callback_update(action: str, deal_id: str = "deal-123") -> dict[str, Any]:
    return {
        "update_id": 1,
        "callback_query": {
            "id": "query-1",
            "from": {"id": 987654321, "first_name": "Mira"},
            "message": {
                "message_id": 42,
                "chat": {"id": -1001234567890, "type": "group"},
            },
            "data": f"{action}:{deal_id}",
        },
    }


# ─── message building ─────────────────────────────────────────────────────────


def test_build_deal_approval_message():
    deal = make_deal()
    text, markup = approval.build_deal_approval_message(deal, "https://tho.example.com")

    assert "Jane Doe" in text
    assert "deal-123" in text
    assert isinstance(markup, InlineKeyboardMarkup)

    keyboard = markup.as_dict()["inline_keyboard"]
    assert len(keyboard) == 2
    assert keyboard[0][0]["text"] == "✅ Approve"
    assert keyboard[0][0]["callback_data"] == "approve:deal-123"
    assert keyboard[0][1]["text"] == "❌ Reject"
    assert keyboard[0][1]["callback_data"] == "reject:deal-123"
    assert keyboard[1][0]["text"] == "👁 View Deal"
    assert keyboard[1][0]["url"] == "https://tho.example.com/admin/deals/deal-123"


def test_build_deal_approval_message_accepts_dict():
    deal_dict = {
        "id": "deal-dict",
        "buyer_first_name": "Bob",
        "buyer_last_name": "Smith",
        "sales_price": 99000.0,
        "status": "pending",
    }
    text, _markup = approval.build_deal_approval_message(deal_dict, "https://tho.example.com")
    assert "Bob Smith" in text


def test_build_deal_approval_message_without_base_url_omits_view():
    deal = make_deal()
    _text, markup = approval.build_deal_approval_message(deal)
    keyboard = markup.as_dict()["inline_keyboard"]
    assert len(keyboard) == 1
    assert len(keyboard[0]) == 2


# ─── send approval message ────────────────────────────────────────────────────


def test_send_deal_approval_message_logs_activity():
    client = FakeTelegramClient()
    db = FakeDB()
    deal = make_deal()

    result = approval.send_deal_approval_message(
        client, deal, approver_chat_id="-1001", base_url="https://tho.example.com", db=db
    )

    assert result["message_id"] == 12345
    assert len(client.sent_messages) == 1
    call = client.sent_messages[0]
    assert call["chat_id"] == "-1001"
    assert call["parse_mode"] == "HTML"
    assert call["reply_markup"] is not None
    assert len(db.activities) == 1
    activity = list(db.activities.values())[0]
    assert activity["activity_type"] == approval.ACTIVITY_APPROVAL_SENT
    assert activity["deal_id"] == "deal-123"
    assert activity["metadata"]["success"] is True
    assert activity["metadata"]["message_id"] == 12345


def test_send_deal_approval_message_propagates_api_error():
    client = FakeTelegramClient()
    client.fail_next_send = True
    db = FakeDB()

    with pytest.raises(TelegramAPIError):
        approval.send_deal_approval_message(
            client, make_deal(), approver_chat_id="-1001", base_url="https://tho.example.com", db=db
        )

    # Failure is still logged.
    assert len(db.activities) == 1
    assert db.activities[list(db.activities.keys())[0]]["metadata"]["success"] is False


def test_send_deal_approval_message_requires_chat_id():
    client = FakeTelegramClient()
    client.config.chat_id = ""

    with pytest.raises(ValueError, match="chat_id is required"):
        approval.send_deal_approval_message(client, make_deal())


# ─── callback parsing ─────────────────────────────────────────────────────────


def test_parse_callback_query_success():
    update = make_callback_update("approve")
    action = approval.parse_callback_query(update)

    assert action.action == "approve"
    assert action.deal_id == "deal-123"
    assert action.query_id == "query-1"
    assert action.message_id == 42
    assert action.chat_id == -1001234567890
    assert action.from_user["id"] == 987654321


def test_parse_callback_query_legacy_decline_becomes_reject():
    update = make_callback_update("decline")
    action = approval.parse_callback_query(update)
    assert action.action == "reject"


def test_parse_callback_query_legacy_prefix():
    update = {
        "callback_query": {
            "id": "q1",
            "from": {},
            "message": {"message_id": 1, "chat": {"id": 1}},
            "data": "tho:deal:approve:legacy-deal",
        }
    }
    action = approval.parse_callback_query(update)
    assert action.action == "approve"
    assert action.deal_id == "legacy-deal"


@pytest.mark.parametrize(
    "update,expected",
    [
        ({}, "missing callback_query"),
        ({"callback_query": {}}, "Invalid callback_data format"),
        ({"callback_query": {"data": "approve"}}, "Invalid callback_data format"),
        ({"callback_query": {"data": ":deal"}}, "Invalid callback_data format"),
        ({"callback_query": {"data": "approve:"}}, "Invalid callback_data format"),
    ],
)
def test_parse_callback_query_invalid(update, expected):
    with pytest.raises(ValueError, match=expected):
        approval.parse_callback_query(update)


# ─── action dispatch ──────────────────────────────────────────────────────────


def test_handle_callback_query_approve_updates_deal_and_edits_message():
    client = FakeTelegramClient()
    db = FakeDB()
    update = make_callback_update("approve")

    result = approval.handle_callback_query(update, client=client, db=db)

    assert result == {
        "callback_query_id": "query-1",
        "text": "✅ Approved deal deal-123",
    }
    assert db.deals["deal-123"]["status"] == DealStatus.APPROVED.value
    assert len(client.edited_messages) == 1
    assert client.edited_messages[0]["text"] == "✅ Deal approved"

    assert len(db.activities) == 1
    activity = list(db.activities.values())[0]
    assert activity["activity_type"] == approval.ACTIVITY_APPROVAL_APPROVED
    assert activity["metadata"]["telegram_user_id"] == 987654321


def test_handle_callback_query_reject_updates_deal():
    client = FakeTelegramClient()
    db = FakeDB()
    update = make_callback_update("reject")

    result = approval.handle_callback_query(update, client=client, db=db)

    assert result["callback_query_id"] == "query-1"
    assert "Rejected" in result["text"]
    assert db.deals["deal-123"]["status"] == DealStatus.DENIED.value


def test_handle_callback_query_unknown_action_returns_none():
    client = FakeTelegramClient()
    update = make_callback_update("nope")

    result = approval.handle_callback_query(update, client=client)

    assert result is None


def test_handle_callback_query_parse_error_returns_none():
    client = FakeTelegramClient()
    result = approval.handle_callback_query({}, client=client)

    assert result is None


def test_handle_callback_query_continues_if_edit_fails():
    client = FakeTelegramClient()
    client.fail_next_edit = True
    db = FakeDB()
    update = make_callback_update("approve")

    result = approval.handle_callback_query(update, client=client, db=db)

    assert result["callback_query_id"] == "query-1"
    assert db.deals["deal-123"]["status"] == DealStatus.APPROVED.value
    assert len(client.edited_messages) == 1


# ─── activity logging ─────────────────────────────────────────────────────────


def test_log_telegram_activity_noop_without_db():
    # Should not raise when db is None.
    approval._log_telegram_activity(
        None,
        approval.ACTIVITY_APPROVAL_SENT,
        "test",
        deal_id="d1",
        success=True,
    )


def test_update_deal_status_noop_without_db():
    # Should not raise when db is None.
    assert approval._update_deal_status(None, "d1", DealStatus.APPROVED) is False
