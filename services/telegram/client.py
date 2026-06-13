"""Minimal Telegram Bot API client.

This module provides a small, dependency-light client used by the approval service
and outbound alert helpers.  The public method names are part of the agreed module
contract; keep them stable.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from services.telegram.config import TelegramConfig
from services.telegram.rich_message import InlineKeyboardMarkup, RichMessage, RichTextMessage

logger = logging.getLogger(__name__)


class TelegramAPIError(Exception):
    """Raised when the Telegram API returns a non-OK response or the request fails."""

    def __init__(self, message: str, response_data: dict | None = None):
        super().__init__(message)
        self.response_data = response_data or {}


class TelegramClient:
    """Telegram Bot API client backed by ``requests``.

    Parameters
    ----------
    config : TelegramConfig | None
        Token and destination settings.  If omitted, loaded from environment.
    session : requests.Session | None
        Optional ``requests.Session`` for testing or connection reuse.
    timeout : float
        Per-request timeout in seconds.  Default 15s.
    """

    def __init__(
        self,
        config: TelegramConfig | None = None,
        *,
        session: requests.Session | None = None,
        timeout: float = 15.0,
    ) -> None:
        if config is None:
            config = TelegramConfig.from_env()
        if not config.bot_token:
            raise ValueError("bot token is required")
        self.config = config
        self._session = session or requests.Session()
        self.timeout = timeout

    def _base_url(self) -> str:
        return f"https://api.telegram.org/bot{self.config.bot_token}/"

    def _call(
        self,
        method: str,
        *,
        params: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
        http_method: str = "POST",
    ) -> dict[str, Any]:
        """Call a Telegram Bot API method and return the ``result`` object."""
        url = f"{self._base_url()}{method}"
        try:
            if http_method.upper() == "GET":
                resp = self._session.get(url, params=params, timeout=self.timeout)
            else:
                resp = self._session.post(url, json=json_payload, params=params, timeout=self.timeout)
        except requests.exceptions.RequestException as exc:
            raise TelegramAPIError(f"Telegram request failed: {exc}") from exc

        try:
            data = resp.json()
        except ValueError as exc:
            raise TelegramAPIError(
                f"Invalid JSON response (HTTP {resp.status_code}): {resp.text[:200]}",
                response_data={"status_code": resp.status_code, "body": resp.text[:500]},
            ) from exc

        if not data.get("ok"):
            raise TelegramAPIError(
                f"Telegram API error: {data.get('description', f'HTTP {resp.status_code}')}",
                response_data=data,
            )
        return data.get("result", {})

    def get_me(self) -> dict[str, Any]:
        """Return basic information about the bot (``getMe``)."""
        return self._call("getMe", http_method="GET")

    def send_message(
        self,
        chat_id: str | int,
        text: str,
        *,
        parse_mode: str | None = "MarkdownV2",
        reply_markup: dict | InlineKeyboardMarkup | None = None,
        disable_web_page_preview: bool = False,
    ) -> dict[str, Any]:
        """Send a plain text message (``sendMessage``)."""
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode
        if isinstance(reply_markup, InlineKeyboardMarkup):
            payload["reply_markup"] = reply_markup.as_dict()
        elif reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self._call("sendMessage", json_payload=payload)

    def send_rich_message(
        self,
        chat_id: str | int,
        message: RichMessage | RichTextMessage | str | None = None,
        *,
        text: str | None = None,
        parse_mode: str | None = None,
        reply_markup: dict | InlineKeyboardMarkup | None = None,
    ) -> dict[str, Any]:
        """Send a rich message or plain text with inline keyboard markup.

        Supports both the contract form ``send_rich_message(chat_id, message_obj)``
        and the convenience form ``send_rich_message(chat_id, text, parse_mode=...,
        reply_markup=...)`` used by the approval service.
        """
        resolved_text = text
        resolved_parse_mode = parse_mode
        resolved_markup = reply_markup

        if isinstance(message, str):
            resolved_text = message
        elif isinstance(message, RichTextMessage):
            resolved_text = message.text
            resolved_parse_mode = message.parse_mode
            resolved_markup = message.reply_markup
        elif isinstance(message, RichMessage) and message.reply_markup is not None:
            resolved_markup = message.reply_markup

        if resolved_text is None:
            resolved_text = ""
        send_kwargs: dict[str, Any] = {"reply_markup": resolved_markup}
        if resolved_parse_mode is not None:
            send_kwargs["parse_mode"] = resolved_parse_mode
        return self.send_message(
            chat_id,
            resolved_text,
            **send_kwargs,
        )

    def edit_message_text(
        self,
        chat_id: str | int,
        message_id: int,
        text: str,
        *,
        parse_mode: str | None = "Markdown",
        reply_markup: dict | InlineKeyboardMarkup | None = None,
    ) -> dict[str, Any]:
        """Update the text and/or markup of an existing message."""
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode
        if isinstance(reply_markup, InlineKeyboardMarkup):
            payload["reply_markup"] = reply_markup.as_dict()
        elif reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self._call("editMessageText", json_payload=payload)

    def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        *,
        show_alert: bool = False,
    ) -> dict[str, Any]:
        """Acknowledge a callback query to remove the loading spinner."""
        payload: dict[str, Any] = {
            "callback_query_id": callback_query_id,
            "show_alert": show_alert,
        }
        if text is not None:
            payload["text"] = text
        return self._call("answerCallbackQuery", json_payload=payload)

    def set_webhook(
        self,
        url: str | None = None,
        secret_token: str | None = None,
        *,
        drop_pending_updates: bool | None = None,
    ) -> dict[str, Any]:
        """Register or remove the webhook endpoint with Telegram."""
        payload: dict[str, Any] = {}
        if url is not None:
            payload["url"] = url
        if secret_token is not None:
            payload["secret_token"] = secret_token
        if drop_pending_updates is not None:
            payload["drop_pending_updates"] = drop_pending_updates
        return self._call("setWebhook", json_payload=payload)

    def delete_webhook(
        self,
        *,
        drop_pending_updates: bool | None = None,
    ) -> dict[str, Any]:
        """Remove the webhook endpoint from Telegram."""
        payload: dict[str, Any] = {}
        if drop_pending_updates is not None:
            payload["drop_pending_updates"] = drop_pending_updates
        return self._call("deleteWebhook", json_payload=payload)
