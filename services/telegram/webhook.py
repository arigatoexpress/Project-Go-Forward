"""FastAPI webhook receiver and outbound alert helpers for Telegram.

Public API
----------
- ``telegram_router`` – FastAPI ``APIRouter`` that validates inbound Telegram
  updates at ``POST /api/v1/telegram/webhook``.
- ``send_deal_status_alert`` – fire-and-forget helper to notify the default chat
  when a deal status changes.
- ``send_partner_webhook_alert`` – fire-and-forget helper to mirror partner
  webhook events into Telegram.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from services.telegram.approval import handle_callback_query
from services.telegram.client import TelegramAPIError, TelegramClient
from services.telegram.config import TelegramConfig
from services.telegram.formatters import format_deal_alert_html

logger = logging.getLogger(__name__)

telegram_router = APIRouter(tags=["telegram"])


@telegram_router.post("/api/v1/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(default=""),
) -> dict[str, bool]:
    """Receive an update from Telegram.

    Validates the ``X-Telegram-Bot-Api-Secret-Token`` header when a webhook
    secret is configured, then dispatches callback queries for approval handling.
    """
    config = TelegramConfig.from_env()
    if config.webhook_secret and x_telegram_bot_api_secret_token != config.webhook_secret:
        raise HTTPException(status_code=401, detail="Invalid secret token")

    try:
        update = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    if update.get("callback_query"):
        try:
            client = TelegramClient(config)
            result = handle_callback_query(update, client=client)
            logger.info("Telegram callback query handled: %s", result)
            if result is not None:
                client.answer_callback_query(**result)
        except TelegramAPIError as exc:
            logger.warning("Failed to answer Telegram callback query: %s", exc)
        except Exception as exc:
            logger.warning("Telegram callback query handling failed: %s", exc)

    return {"ok": True}


def _normalise_deal(deal: Any) -> Any:
    """Ensure dict deals can be rendered by the HTML formatter."""
    return SimpleNamespace(**deal) if isinstance(deal, dict) else deal


def send_deal_status_alert(
    event: str,
    deal: Any,
    *,
    config: TelegramConfig | None = None,
    client: TelegramClient | None = None,
) -> dict[str, Any] | None:
    """Send a deal status change alert to the default Telegram chat.

    Returns ``None`` when Telegram is disabled or unconfigured.
    """
    cfg = config or TelegramConfig.from_env()
    if not cfg.enabled or not cfg.is_configured():
        return None

    cli = client or TelegramClient(cfg)
    text = format_deal_alert_html(_normalise_deal(deal), include_emoji=True)
    return cli.send_message(cfg.chat_id, text, parse_mode="HTML")


def send_partner_webhook_alert(
    event: str,
    payload: dict[str, Any],
    *,
    config: TelegramConfig | None = None,
    client: TelegramClient | None = None,
) -> dict[str, Any] | None:
    """Mirror a partner webhook event into the default Telegram chat.

    Returns ``None`` when Telegram is disabled or unconfigured.
    """
    cfg = config or TelegramConfig.from_env()
    if not cfg.enabled or not cfg.is_configured():
        return None

    cli = client or TelegramClient(cfg)
    body = json.dumps(payload, indent=2, default=str)
    text = f"📡 Partner webhook received\n<b>Event:</b> {event}\n<pre>{body}</pre>"
    return cli.send_message(cfg.chat_id, text, parse_mode="HTML")
