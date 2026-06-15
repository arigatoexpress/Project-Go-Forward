"""Mira Notification Webhook — Forward system events to Telegram."""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from urllib import request

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from structured_logging import logger as struct_logger

router = APIRouter(prefix="/api/v1/mira", tags=["mira"])

_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
_FALLBACK = os.environ.get("KIMI_RELAY_CHAT_ID", "").strip()
_GROUP = os.environ.get("MIRA_GROUP_ID", "").strip()


class NotifyPayload(BaseModel):
    message: str = Field(..., description="Alert text (supports Markdown)")
    level: str = Field(default="info", pattern=r"^(info|warn|error|critical)$")
    source: str = Field(default="tho")
    action_url: str | None = Field(default=None)


def send_mira_notification(payload: NotifyPayload) -> dict:
    """Send a notification to the configured Mira Telegram group."""
    chat_id = _GROUP or _FALLBACK
    if not chat_id:
        return {"ok": False, "error": "MIRA_GROUP_ID not configured — set env var or KIMI_RELAY_CHAT_ID"}
    if not _TOKEN:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not configured"}
    text = f"*THO Alert — {payload.source.upper()}*\n\n{payload.message}"
    if payload.action_url:
        text += f"\n\n[View →]({payload.action_url})"
    text += f"\n\n_`{datetime.now(UTC).isoformat()}`_"
    body = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}).encode("utf-8")
    req = request.Request(
        f"https://api.telegram.org/bot{_TOKEN}/sendMessage",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        struct_logger.warning("mira telegram send failed", error=str(e))
        return {"ok": False, "error": "send_failed"}


@router.post("/notify")
async def mira_notify(payload: NotifyPayload, request: Request) -> dict:
    """Receive a system notification and forward it to the Telegram group."""
    result = send_mira_notification(payload)
    return {"ok": result.get("ok", False), "telegram": result}
