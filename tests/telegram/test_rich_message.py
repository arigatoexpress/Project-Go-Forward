"""Unit tests for services.telegram.rich_message.

No outbound HTTP, no Firestore, no other Telegram service modules.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.telegram.rich_message import (  # noqa: E402
    MAX_BUTTONS_PER_ROW,
    MAX_CALLBACK_DATA_BYTES,
    MAX_CAPTION_LENGTH,
    MAX_INLINE_KEYBOARD_BUTTONS,
    MAX_INLINE_KEYBOARD_ROWS,
    MAX_RICH_MESSAGE_LENGTH,
    MAX_TEXT_LENGTH,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    RichDocumentMessage,
    RichEditTextMessage,
    RichMessageLimitError,
    RichPhotoMessage,
    RichTextMessage,
    count_markdown_blocks,
    sanitize_text,
    truncate_caption,
    truncate_text,
    validate_rich_message,
)

# ─── text helpers ─────────────────────────────────────────────────────────────


def test_sanitize_text_removes_carriage_returns():
    assert sanitize_text("line1\r\nline2") == "line1\nline2"
    assert sanitize_text("a\rb") == "ab"


def test_truncate_text():
    assert truncate_text("hello", 10) == "hello"
    assert len(truncate_text("x" * 100, 10)) == 10
    assert truncate_text("x" * 100, 10).endswith("…")


def test_truncate_caption():
    assert truncate_caption("short") == "short"
    assert len(truncate_caption("x" * (MAX_CAPTION_LENGTH + 20))) <= MAX_CAPTION_LENGTH


# ─── inline keyboard ──────────────────────────────────────────────────────────


def test_button_requires_text_and_single_action():
    InlineKeyboardButton(text="OK", callback_data="ok")
    InlineKeyboardButton(text="Open", url="https://example.com")

    with pytest.raises(RichMessageLimitError):
        InlineKeyboardButton(text="")
    with pytest.raises(RichMessageLimitError):
        InlineKeyboardButton(text="OK")
    with pytest.raises(RichMessageLimitError):
        InlineKeyboardButton(text="OK", callback_data="ok", url="https://example.com")


def test_button_text_length_limit():
    with pytest.raises(RichMessageLimitError):
        InlineKeyboardButton(text="x" * 65, callback_data="ok")


def test_callback_data_byte_limit():
    InlineKeyboardButton(text="OK", callback_data="x" * MAX_CALLBACK_DATA_BYTES)
    with pytest.raises(RichMessageLimitError):
        InlineKeyboardButton(text="OK", callback_data="x" * (MAX_CALLBACK_DATA_BYTES + 1))

    # multi-byte characters count as bytes, not characters
    with pytest.raises(RichMessageLimitError):
        InlineKeyboardButton(text="OK", callback_data="🔥" * 30)


def test_keyboard_add_row_and_payload():
    kb = (
        InlineKeyboardMarkup()
        .add_row(
            [
                InlineKeyboardButton(text="Approve", callback_data="deal:1:approve"),
                InlineKeyboardButton(text="Reject", callback_data="deal:1:reject"),
            ]
        )
        .add_button(
            InlineKeyboardButton(text="View", url="https://tho.example.com/d/1"), new_row=True
        )
    )

    assert kb.as_dict() == {
        "inline_keyboard": [
            [
                {"text": "Approve", "callback_data": "deal:1:approve"},
                {"text": "Reject", "callback_data": "deal:1:reject"},
            ],
            [{"text": "View", "url": "https://tho.example.com/d/1"}],
        ]
    }


def test_keyboard_row_button_limit():
    with pytest.raises(RichMessageLimitError):
        InlineKeyboardMarkup().add_row(
            [
                InlineKeyboardButton(text=str(i), callback_data=f"d:{i}")
                for i in range(MAX_BUTTONS_PER_ROW + 1)
            ]
        )


def test_keyboard_total_button_limit():
    kb = InlineKeyboardMarkup()
    for i in range(MAX_INLINE_KEYBOARD_BUTTONS):
        kb.add_button(InlineKeyboardButton(text="x", callback_data=f"d:{i}"))

    with pytest.raises(RichMessageLimitError):
        kb.add_button(InlineKeyboardButton(text="x", callback_data="d:extra"))


def test_keyboard_row_limit():
    kb = InlineKeyboardMarkup()
    for i in range(MAX_INLINE_KEYBOARD_ROWS):
        kb.add_row([InlineKeyboardButton(text="x", callback_data=f"r:{i}")])

    with pytest.raises(RichMessageLimitError):
        kb.add_row([InlineKeyboardButton(text="x", callback_data="r:extra")])


# ─── text / rich message builders ─────────────────────────────────────────────


def test_text_message_payload():
    msg = RichTextMessage(chat_id=-100123, text="hello", parse_mode="HTML")
    assert msg.to_payload() == {
        "chat_id": -100123,
        "text": "hello",
        "parse_mode": "HTML",
    }
    assert msg.method == "sendMessage"


def test_text_message_with_keyboard():
    kb = InlineKeyboardMarkup().add_row([InlineKeyboardButton(text="A", callback_data="a")])
    msg = RichTextMessage(chat_id=-100123, text="hello", reply_markup=kb)
    payload = msg.to_payload()
    assert payload["reply_markup"] == {"inline_keyboard": [[{"text": "A", "callback_data": "a"}]]}


def test_text_message_optional_flags():
    msg = RichTextMessage(
        chat_id=-100123,
        text="hello",
        message_thread_id=7,
        disable_notification=True,
        protect_content=True,
    )
    assert msg.to_payload() == {
        "chat_id": -100123,
        "message_thread_id": 7,
        "disable_notification": True,
        "protect_content": True,
        "text": "hello",
    }


def test_text_length_guard():
    RichTextMessage(chat_id=-1, text="x" * MAX_TEXT_LENGTH).to_payload()
    with pytest.raises(RichMessageLimitError):
        RichTextMessage(chat_id=-1, text="x" * (MAX_TEXT_LENGTH + 1)).to_payload()


def test_rich_message_payload():
    msg = RichTextMessage(chat_id=-1, rich_message={"markdown": "# Hello"})
    payload = msg.to_payload()
    assert payload["rich_message"] == {"markdown": "# Hello"}
    assert msg.method == "sendRichMessage"


def test_rich_message_requires_exactly_one_format():
    with pytest.raises(RichMessageLimitError):
        validate_rich_message({})
    with pytest.raises(RichMessageLimitError):
        validate_rich_message({"html": "x", "markdown": "y"})
    with pytest.raises(RichMessageLimitError):
        validate_rich_message({"html": "x", "extra": "y"})


def test_rich_message_length_guard():
    with pytest.raises(RichMessageLimitError):
        validate_rich_message({"markdown": "x" * (MAX_RICH_MESSAGE_LENGTH + 1)})


def test_rich_message_block_guard():
    # 501 list items is over the 500 block limit.
    markdown = "\n".join(f"- item {i}" for i in range(501))
    assert count_markdown_blocks(markdown) == 501
    with pytest.raises(RichMessageLimitError):
        validate_rich_message({"markdown": markdown})


# ─── media builders ───────────────────────────────────────────────────────────


def test_photo_message_payload():
    msg = RichPhotoMessage(
        chat_id=-1,
        photo="https://tho.example.com/photo.jpg",
        caption="Look at this",
        parse_mode="HTML",
    )
    assert msg.to_payload() == {
        "chat_id": -1,
        "photo": "https://tho.example.com/photo.jpg",
        "caption": "Look at this",
        "parse_mode": "HTML",
    }
    assert msg.method == "sendPhoto"


def test_photo_requires_photo():
    with pytest.raises(RichMessageLimitError):
        RichPhotoMessage(chat_id=-1)


def test_photo_caption_guard():
    with pytest.raises(RichMessageLimitError):
        RichPhotoMessage(
            chat_id=-1, photo="url", caption="x" * (MAX_CAPTION_LENGTH + 1)
        ).to_payload()


def test_document_message_payload():
    msg = RichDocumentMessage(chat_id=-1, document="file_id", caption="doc")
    assert msg.to_payload() == {
        "chat_id": -1,
        "document": "file_id",
        "caption": "doc",
    }
    assert msg.method == "sendDocument"


# ─── edit message builder ─────────────────────────────────────────────────────


def test_edit_text_payload():
    msg = RichEditTextMessage(chat_id=-1, message_id=42, text="updated", parse_mode="HTML")
    assert msg.to_payload() == {
        "chat_id": -1,
        "message_id": 42,
        "text": "updated",
        "parse_mode": "HTML",
    }
    assert msg.method == "editMessageText"


def test_edit_requires_target():
    with pytest.raises(RichMessageLimitError):
        RichEditTextMessage(chat_id=-1, text="updated")


def test_edit_rich_message_payload():
    msg = RichEditTextMessage(
        chat_id=-1,
        message_id=42,
        rich_message={"markdown": "# Updated"},
    )
    assert msg.to_payload()["rich_message"] == {"markdown": "# Updated"}
