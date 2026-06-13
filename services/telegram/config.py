"""Environment-based configuration for the Telegram bot integration."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class TelegramConfig:
    """Runtime configuration loaded from environment variables.

    Environment variables
    ---------------------
    TELEGRAM_BOT_TOKEN : str
        Bot token from @BotFather, required for all API calls.
    TELEGRAM_CHAT_ID : str
        Default target chat id for deal alerts and approval requests.
    TELEGRAM_WEBHOOK_SECRET : str | None
        Secret token Telegram sends in ``X-Telegram-Bot-Api-Secret-Token``.
    TELEGRAM_WEBHOOK_URL : str | None
        Public HTTPS URL passed to ``setWebhook``.
    TELEGRAM_BASE_URL or PUBLIC_BASE_URL : str
        Public origin of the THO app used for ``View Deal`` links.
    TELEGRAM_ENABLED : bool
        Whether outbound messages are enabled. Defaults to False so the bot is
        fail-closed until explicitly turned on.
    """

    bot_token: str = ""
    chat_id: str = ""
    webhook_secret: str | None = None
    webhook_url: str | None = None
    base_url: str = ""
    enabled: bool = False

    def is_configured(self) -> bool:
        """Return ``True`` when the minimum required fields are present."""
        return bool(self.bot_token and self.chat_id)

    @classmethod
    def from_env(cls) -> TelegramConfig:
        """Load configuration from environment variables.

        ``TELEGRAM_ENABLED`` is considered truthy for ``1``, ``true``, or
        ``yes`` (case-insensitive). All other values default to False.
        """
        enabled_raw = (os.environ.get("TELEGRAM_ENABLED") or "").strip().lower()
        enabled = enabled_raw in {"1", "true", "yes"}

        return cls(
            bot_token=(os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip(),
            chat_id=(os.environ.get("TELEGRAM_CHAT_ID") or "").strip(),
            webhook_secret=_strip_or_none(os.environ.get("TELEGRAM_WEBHOOK_SECRET")),
            webhook_url=_strip_or_none(os.environ.get("TELEGRAM_WEBHOOK_URL")),
            base_url=(os.environ.get("TELEGRAM_BASE_URL") or os.environ.get("PUBLIC_BASE_URL") or "").strip(),
            enabled=enabled,
        )


def _strip_or_none(value: str | None) -> str | None:
    """Return a stripped, non-empty string or ``None``."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None
