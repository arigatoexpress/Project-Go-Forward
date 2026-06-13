"""Telegram/Mira bot integration for THO notifications and approvals."""

from .approval import handle_callback_query, send_deal_approval_message
from .client import TelegramAPIError, TelegramClient
from .config import TelegramConfig
from .webhook import (
    send_deal_status_alert,
    send_partner_webhook_alert,
    telegram_router,
)

__all__ = [
    "TelegramAPIError",
    "TelegramClient",
    "TelegramConfig",
    "handle_callback_query",
    "send_deal_approval_message",
    "send_deal_status_alert",
    "send_partner_webhook_alert",
    "telegram_router",
]
