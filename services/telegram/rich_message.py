"""Rich Message builders and Telegram limit guards for the THO Telegram/Mira bot.

This module is intentionally independent of the rest of the Telegram service
so it can be imported and unit-tested without a live bot token or Firestore.
All Telegram Bot API hard limits are enforced by default; callers that need to
fit arbitrary text can use the ``truncate_*`` helpers before constructing a
message.

References
----------
* https://core.telegram.org/bots/api
* https://core.telegram.org/bots/api#june-11-2026 (Bot API 10.1 Rich Messages)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Self

logger = logging.getLogger(__name__)

# Telegram hard limits relevant to the message types we build.
MAX_TEXT_LENGTH = 4096
MAX_RICH_MESSAGE_LENGTH = 32768
MAX_RICH_BLOCKS = 500
MAX_RICH_NESTING = 16
MAX_CAPTION_LENGTH = 1024
MAX_CALLBACK_DATA_BYTES = 64
MAX_BUTTON_TEXT_LENGTH = 64
MAX_BUTTONS_PER_ROW = 8
MAX_INLINE_KEYBOARD_BUTTONS = 100
MAX_INLINE_KEYBOARD_ROWS = 100

_BLOCK_TAGS = frozenset(
    {
        "article",
        "blockquote",
        "br",
        "details",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "li",
        "ol",
        "p",
        "pre",
        "section",
        "summary",
        "table",
        "tbody",
        "thead",
        "tr",
        "ul",
    }
)


class RichMessageLimitError(ValueError):
    """Raised when a message would violate a Telegram hard limit."""


# ──────────────────────────────────────────────────────────────────────────────
# Text helpers
# ──────────────────────────────────────────────────────────────────────────────


def sanitize_text(text: str) -> str:
    """Return ``text`` with ``\\r`` removed.

    Telegram parse modes treat carriage returns inconsistently, so we normalize
    them away at the boundary of this module.
    """
    if not isinstance(text, str):
        raise TypeError(f"text must be str, got {type(text).__name__}")
    return text.replace("\r", "")


def truncate_text(text: str, limit: int = MAX_TEXT_LENGTH, *, suffix: str = "…") -> str:
    """Return ``text`` truncated to ``limit`` characters.

    The suffix is only appended when truncation actually happens.  Use this
    before constructing a message if the caller prefers truncation to raising.
    """
    text = sanitize_text(text)
    if len(text) <= limit:
        return text
    if limit <= len(suffix):
        return text[:limit]
    return text[: limit - len(suffix)] + suffix


def truncate_caption(text: str, limit: int = MAX_CAPTION_LENGTH, *, suffix: str = "…") -> str:
    """Return ``text`` truncated to caption limits."""
    return truncate_text(text, limit=limit, suffix=suffix)


def validate_text_length(text: str, limit: int = MAX_TEXT_LENGTH) -> None:
    """Raise ``RichMessageLimitError`` if ``text`` exceeds ``limit``."""
    if len(text) > limit:
        raise RichMessageLimitError(f"text length {len(text)} exceeds Telegram limit {limit}")


def validate_caption_length(text: str, limit: int = MAX_CAPTION_LENGTH) -> None:
    """Raise ``RichMessageLimitError`` if caption ``text`` exceeds ``limit``."""
    if len(text) > limit:
        raise RichMessageLimitError(f"caption length {len(text)} exceeds Telegram limit {limit}")


# ──────────────────────────────────────────────────────────────────────────────
# Rich Message limit guards
# ──────────────────────────────────────────────────────────────────────────────


def count_markdown_blocks(markdown: str) -> int:
    """Approximate the number of Rich Message blocks in a markdown string.

    Telegram counts headings, paragraphs, list items, table rows, code blocks,
    details blocks, etc.  This is a conservative heuristic: it may over-count,
    but it will not under-count enough to let an oversized message slip through.
    """
    text = sanitize_text(markdown)
    lines = text.split("\n")
    count = 0
    in_code = False
    in_paragraph = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            in_paragraph = False
            continue

        if stripped.startswith("```"):
            in_code = not in_code
            count += 1
            in_paragraph = False
            continue

        if in_code:
            continue

        if stripped.startswith(("#", ">", "-", "*", "+", "|")) or re.match(r"\d+\.", stripped):
            count += 1
            in_paragraph = False
        elif stripped.lower().startswith("<details"):
            count += 1
            in_paragraph = False
        else:
            if not in_paragraph:
                count += 1
                in_paragraph = True

    return count


def count_html_blocks(html: str) -> int:
    """Approximate the number of Rich Message blocks in an HTML string."""
    text = sanitize_text(html)
    tags = re.findall(r"</?([a-zA-Z0-9]+)", text)
    return sum(1 for tag in tags if tag.lower() in _BLOCK_TAGS)


def _max_html_block_depth(html: str) -> int:
    """Return the maximum nesting depth of block-level HTML tags."""
    text = sanitize_text(html)
    tokens = re.finditer(r"</?([a-zA-Z0-9]+)[^>]*>", text)
    stack: list[str] = []
    max_depth = 0

    for match in tokens:
        tag = match.group(1).lower()
        if tag not in _BLOCK_TAGS:
            continue

        raw = match.group(0)
        if raw.startswith("</"):
            if stack and stack[-1] == tag:
                stack.pop()
        elif tag not in ("br", "hr"):
            stack.append(tag)
            max_depth = max(max_depth, len(stack))

    return max_depth


def _max_markdown_nesting(markdown: str) -> int:
    """Approximate the maximum nesting depth of a markdown Rich Message."""
    text = sanitize_text(markdown)
    max_depth = 1
    in_code = False
    details_stack = 0

    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue

        if stripped.lower().startswith("<details"):
            details_stack += 1
            max_depth = max(max_depth, details_stack + 1)
        elif stripped.lower().startswith("</details>"):
            details_stack = max(0, details_stack - 1)
        else:
            # List nesting by indentation (one level per two spaces).
            match = re.match(r"^(\s*)[-*+]\s", line)
            if match:
                depth = len(match.group(1)) // 2 + 1
                max_depth = max(max_depth, depth)

    return max_depth


def validate_rich_message(rich_message: dict[str, str]) -> None:
    """Raise ``RichMessageLimitError`` if ``rich_message`` violates Bot API limits.

    ``rich_message`` must be ``{"html": ...}`` or ``{"markdown": ...}`` and
    nothing else.
    """
    if not isinstance(rich_message, dict):
        raise RichMessageLimitError("rich_message must be a dict")

    keys = set(rich_message.keys())
    if keys not in ({"html"}, {"markdown"}):
        raise RichMessageLimitError("rich_message must contain exactly one of 'html' or 'markdown'")

    fmt, content = next(iter(rich_message.items()))
    content = sanitize_text(content)

    if len(content) > MAX_RICH_MESSAGE_LENGTH:
        raise RichMessageLimitError(
            f"{fmt} length {len(content)} exceeds Telegram limit {MAX_RICH_MESSAGE_LENGTH}"
        )

    if fmt == "html":
        blocks = count_html_blocks(content)
        depth = _max_html_block_depth(content)
    else:
        blocks = count_markdown_blocks(content)
        depth = _max_markdown_nesting(content)

    if blocks > MAX_RICH_BLOCKS:
        raise RichMessageLimitError(
            f"{fmt} block count {blocks} exceeds Telegram limit {MAX_RICH_BLOCKS}"
        )

    if depth > MAX_RICH_NESTING:
        raise RichMessageLimitError(
            f"{fmt} nesting depth {depth} exceeds Telegram limit {MAX_RICH_NESTING}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Inline keyboard builders
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class InlineKeyboardButton:
    """One inline keyboard button.

    Exactly one of ``callback_data`` or ``url`` must be provided.
    ``callback_data`` is limited to 64 UTF-8 bytes.
    """

    text: str
    callback_data: str | None = None
    url: str | None = None

    def __post_init__(self) -> None:
        if not self.text:
            raise RichMessageLimitError("button text is required")
        if len(self.text) > MAX_BUTTON_TEXT_LENGTH:
            raise RichMessageLimitError(
                f"button text length {len(self.text)} exceeds {MAX_BUTTON_TEXT_LENGTH}"
            )

        actions = [a for a in (self.callback_data, self.url) if a is not None]
        if len(actions) != 1:
            raise RichMessageLimitError("button must specify exactly one of callback_data or url")

        if self.callback_data is not None:
            data_bytes = self.callback_data.encode("utf-8")
            if not data_bytes:
                raise RichMessageLimitError("callback_data cannot be empty")
            if len(data_bytes) > MAX_CALLBACK_DATA_BYTES:
                raise RichMessageLimitError(
                    f"callback_data byte length {len(data_bytes)} exceeds {MAX_CALLBACK_DATA_BYTES}"
                )

    def as_dict(self) -> dict[str, str]:
        """Return the Bot API ``InlineKeyboardButton`` object."""
        result: dict[str, str] = {"text": self.text}
        if self.callback_data is not None:
            result["callback_data"] = self.callback_data
        if self.url is not None:
            result["url"] = self.url
        return result


@dataclass
class InlineKeyboardMarkup:
    """Builds an ``InlineKeyboardMarkup`` payload row by row."""

    rows: list[list[InlineKeyboardButton]] = field(default_factory=list)

    def add_row(self, buttons: list[InlineKeyboardButton]) -> Self:
        """Append a row of buttons and enforce row/total limits."""
        if not buttons:
            raise RichMessageLimitError("keyboard row cannot be empty")
        if len(buttons) > MAX_BUTTONS_PER_ROW:
            raise RichMessageLimitError(
                f"row button count {len(buttons)} exceeds {MAX_BUTTONS_PER_ROW}"
            )

        self.rows.append(list(buttons))
        self._validate_totals()
        return self

    def add_button(self, button: InlineKeyboardButton, *, new_row: bool = False) -> Self:
        """Add a button, optionally forcing a new row."""
        if new_row or not self.rows:
            self.add_row([button])
        else:
            if len(self.rows[-1]) >= MAX_BUTTONS_PER_ROW:
                self.add_row([button])
            else:
                self.rows[-1].append(button)
                self._validate_totals()
        return self

    def _validate_totals(self) -> None:
        total = sum(len(row) for row in self.rows)
        if total > MAX_INLINE_KEYBOARD_BUTTONS:
            raise RichMessageLimitError(
                f"total inline keyboard buttons {total} exceeds {MAX_INLINE_KEYBOARD_BUTTONS}"
            )
        if len(self.rows) > MAX_INLINE_KEYBOARD_ROWS:
            raise RichMessageLimitError(
                f"total inline keyboard rows {len(self.rows)} exceeds {MAX_INLINE_KEYBOARD_ROWS}"
            )

    def as_dict(self) -> dict[str, Any]:
        """Return the Bot API ``InlineKeyboardMarkup`` object."""
        return {"inline_keyboard": [[btn.as_dict() for btn in row] for row in self.rows]}


# ──────────────────────────────────────────────────────────────────────────────
# Message builders
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class RichMessage:
    """Base fields shared by all outgoing Telegram message builders."""

    chat_id: int | str
    message_thread_id: int | None = None
    reply_markup: InlineKeyboardMarkup | None = None
    disable_notification: bool = False
    protect_content: bool = False

    def base_payload(self) -> dict[str, Any]:
        """Return the common payload keys."""
        payload: dict[str, Any] = {"chat_id": self.chat_id}
        if self.message_thread_id is not None:
            payload["message_thread_id"] = self.message_thread_id
        if self.reply_markup is not None:
            payload["reply_markup"] = self.reply_markup.as_dict()
        if self.disable_notification:
            payload["disable_notification"] = True
        if self.protect_content:
            payload["protect_content"] = True
        return payload


@dataclass
class RichTextMessage(RichMessage):
    """Builder for ``sendMessage`` or ``sendRichMessage``.

    If ``rich_message`` is supplied the payload targets ``sendRichMessage``;
    otherwise it targets ``sendMessage`` with optional ``parse_mode``.
    """

    text: str = ""
    parse_mode: Literal["HTML", "MarkdownV2", "Markdown"] | None = None
    rich_message: dict[str, str] | None = None

    @property
    def method(self) -> str:
        """The Bot API method this payload is intended for."""
        return "sendRichMessage" if self.rich_message is not None else "sendMessage"

    def to_payload(self) -> dict[str, Any]:
        """Build and validate the Bot API payload."""
        payload = self.base_payload()

        if self.rich_message is not None:
            validate_rich_message(self.rich_message)
            payload["rich_message"] = dict(self.rich_message)
            return payload

        text = sanitize_text(self.text)
        validate_text_length(text)
        payload["text"] = text
        if self.parse_mode:
            payload["parse_mode"] = self.parse_mode
        return payload


@dataclass
class RichPhotoMessage(RichMessage):
    """Builder for ``sendPhoto`` with an optional caption."""

    photo: str = ""
    caption: str = ""
    parse_mode: str | None = None

    def __post_init__(self) -> None:
        if not self.photo:
            raise RichMessageLimitError("photo is required")

    @property
    def method(self) -> str:
        return "sendPhoto"

    def to_payload(self) -> dict[str, Any]:
        payload = self.base_payload()
        payload["photo"] = self.photo

        if self.caption:
            caption = sanitize_text(self.caption)
            validate_caption_length(caption)
            payload["caption"] = caption
            if self.parse_mode:
                payload["parse_mode"] = self.parse_mode

        return payload


@dataclass
class RichDocumentMessage(RichMessage):
    """Builder for ``sendDocument`` with an optional caption."""

    document: str = ""
    caption: str = ""
    parse_mode: str | None = None

    def __post_init__(self) -> None:
        if not self.document:
            raise RichMessageLimitError("document is required")

    @property
    def method(self) -> str:
        return "sendDocument"

    def to_payload(self) -> dict[str, Any]:
        payload = self.base_payload()
        payload["document"] = self.document

        if self.caption:
            caption = sanitize_text(self.caption)
            validate_caption_length(caption)
            payload["caption"] = caption
            if self.parse_mode:
                payload["parse_mode"] = self.parse_mode

        return payload


@dataclass
class RichEditTextMessage(RichMessage):
    """Builder for ``editMessageText`` or ``editMessageRichMessage``.

    Either ``message_id`` (with ``chat_id``) or ``inline_message_id`` must be
    supplied.
    """

    message_id: int | None = None
    inline_message_id: str | None = None
    text: str = ""
    parse_mode: str | None = None
    rich_message: dict[str, str] | None = None

    def __post_init__(self) -> None:
        if self.message_id is None and not self.inline_message_id:
            raise RichMessageLimitError("Either message_id or inline_message_id is required")

    @property
    def method(self) -> str:
        return "editMessageText"

    def to_payload(self) -> dict[str, Any]:
        payload = self.base_payload()

        if self.inline_message_id:
            payload["inline_message_id"] = self.inline_message_id
        elif self.message_id is not None:
            payload["message_id"] = self.message_id

        if self.rich_message is not None:
            validate_rich_message(self.rich_message)
            payload["rich_message"] = dict(self.rich_message)
            return payload

        text = sanitize_text(self.text)
        validate_text_length(text)
        payload["text"] = text
        if self.parse_mode:
            payload["parse_mode"] = self.parse_mode
        return payload


__all__ = [
    "MAX_BUTTON_TEXT_LENGTH",
    "MAX_BUTTONS_PER_ROW",
    "MAX_CALLBACK_DATA_BYTES",
    "MAX_CAPTION_LENGTH",
    "MAX_INLINE_KEYBOARD_BUTTONS",
    "MAX_INLINE_KEYBOARD_ROWS",
    "MAX_RICH_BLOCKS",
    "MAX_RICH_MESSAGE_LENGTH",
    "MAX_RICH_NESTING",
    "MAX_TEXT_LENGTH",
    "InlineKeyboardButton",
    "InlineKeyboardMarkup",
    "RichDocumentMessage",
    "RichEditTextMessage",
    "RichMessage",
    "RichMessageLimitError",
    "RichPhotoMessage",
    "RichTextMessage",
    "count_html_blocks",
    "count_markdown_blocks",
    "sanitize_text",
    "truncate_caption",
    "truncate_text",
    "validate_caption_length",
    "validate_rich_message",
    "validate_text_length",
]
