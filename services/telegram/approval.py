"""Telegram/Mira deal approval service.

Sends an interactive approval message to the configured Telegram chat and
dispatches callback actions (approve / reject / view) back into the THO system.
Every action is logged to the Firestore ``activities/`` collection for audit.

Public API
----------
- :func:`send_deal_approval_message`  Send the initial approval prompt.
- :func:`parse_callback_query`        Extract action/deal_id from a callback.
- :func:`handle_callback_query`       Dispatch approve/reject/view actions and
                                      return an answer payload for Telegram.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from database.models import DealStatus
from services.telegram.client import TelegramAPIError, TelegramClient
from services.telegram.config import TelegramConfig
from services.telegram.formatters import format_deal_alert_html
from services.telegram.rich_message import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

# Callback action prefixes. Keep them short to stay within Telegram's
# 1-64 byte callback_data limit.
APPROVE_ACTION = "approve"
REJECT_ACTION = "reject"
DECLINE_ACTION = "decline"
VIEW_ACTION = "view"
ACTION_SEPARATOR = ":"

# Activity types logged to Firestore.
ACTIVITY_APPROVAL_SENT = "telegram.approval_sent"
ACTIVITY_APPROVAL_APPROVED = "telegram.approval_approved"
ACTIVITY_APPROVAL_REJECTED = "telegram.approval_rejected"
ACTIVITY_APPROVAL_VIEWED = "telegram.approval_viewed"
ACTIVITY_APPROVAL_ERROR = "telegram.approval_error"


@dataclass(frozen=True)
class CallbackAction:
    """Structured representation of an inline-keyboard callback."""

    action: str
    deal_id: str
    query_id: str
    message_id: int
    chat_id: int | str
    from_user: dict[str, Any]


def _deal_attr(deal: Any, name: str, default: Any = None) -> Any:
    """Read a deal attribute regardless of whether ``deal`` is an object or dict."""
    if isinstance(deal, dict):
        return deal.get(name, default)
    return getattr(deal, name, default)


def _deal_id_from(deal: Any) -> str:
    """Safely extract the deal id from a model or dict."""
    return str(_deal_attr(deal, "id") or "unknown")


def _deal_view_url(base_url: str, deal_id: str) -> str:
    """Build a partner-safe view-deal URL."""
    base = base_url.rstrip("/")
    return f"{base}/admin/deals/{deal_id}"


def _get_db() -> Any | None:
    """Return the Firestore client singleton from ``main.py`` if available.

    The import is deferred to avoid circular imports at module load time.
    """
    try:
        from main import _db  # type: ignore[import-not-found]

        return _db.db
    except Exception:
        return None


def build_deal_approval_message(
    deal: Any,
    base_url: str = "",
) -> tuple[str, InlineKeyboardMarkup]:
    """Build the approval prompt text and inline keyboard.

    Parameters
    ----------
    deal : Any
        The deal to present. Accepts either a Pydantic ``Deal`` or the raw
        Firestore dict returned by ``THODatabase.get_deal``.
    base_url : str
        Public origin of the THO admin app, used for the "View Deal" URL.

    Returns
    -------
    tuple[str, InlineKeyboardMarkup]
        HTML-formatted text and the inline keyboard markup.
    """
    deal_id = _deal_id_from(deal)
    text = format_deal_alert_html(deal, include_emoji=True)

    buttons = [
        InlineKeyboardButton(
            text="✅ Approve",
            callback_data=f"{APPROVE_ACTION}{ACTION_SEPARATOR}{deal_id}",
        ),
        InlineKeyboardButton(
            text="❌ Reject",
            callback_data=f"{REJECT_ACTION}{ACTION_SEPARATOR}{deal_id}",
        ),
    ]
    if base_url:
        buttons.append(
            InlineKeyboardButton(
                text="👁 View Deal",
                url=_deal_view_url(base_url, deal_id),
            )
        )

    markup = InlineKeyboardMarkup([buttons[:2]])
    if len(buttons) > 2:
        markup.rows.append([buttons[2]])

    return text, markup


def send_deal_approval_message(
    client: TelegramClient,
    deal: Any,
    *,
    approver_chat_id: str | int | None = None,
    base_url: str = "",
    db: Any | None = None,
) -> dict[str, Any]:
    """Send an interactive deal approval message to Telegram.

    Parameters
    ----------
    client : TelegramClient
        Configured Telegram client.
    deal : Any
        Deal to present.
    approver_chat_id : str | int | None
        Telegram chat identifier. Defaults to ``client.config.chat_id``.
    base_url : str
        Public origin used for the view-deal link.
    db : Any | None
        Firestore client (or fake). When provided, an activity row is written
        to ``activities/`` for audit.

    Returns
    -------
    dict[str, Any]
        Telegram API response ``result`` object.

    Raises
    ------
    TelegramAPIError
        Propagated from ``client.send_rich_message`` on non-OK responses.
    """
    chat_id = approver_chat_id or client.config.chat_id
    if not chat_id:
        raise ValueError("Approver chat_id is required")

    deal_id = _deal_id_from(deal)
    text, markup = build_deal_approval_message(deal, base_url)

    try:
        result = client.send_rich_message(
            chat_id,
            text,
            parse_mode="HTML",
            reply_markup=markup.as_dict(),
        )
    except TelegramAPIError:
        _log_telegram_activity(
            db,
            ACTIVITY_APPROVAL_SENT,
            f"Failed to send Telegram approval for deal {deal_id}",
            deal_id=deal_id,
            success=False,
        )
        raise

    message_id: int | None = None
    if isinstance(result, dict):
        message_id = result.get("message_id")

    _log_telegram_activity(
        db,
        ACTIVITY_APPROVAL_SENT,
        f"Telegram approval message sent for deal {deal_id}",
        deal_id=deal_id,
        success=True,
        metadata={
            "chat_id": str(chat_id),
            "message_id": message_id,
        },
    )
    return result


def parse_callback_query(update: dict[str, Any]) -> CallbackAction:
    """Parse a Telegram callback query update into a structured action.

    Supports both the compact ``action:deal_id`` format used by the approval
    keyboard and the legacy ``tho:deal:action:deal_id`` format.

    Parameters
    ----------
    update : dict
        Raw Telegram ``Update`` object.

    Returns
    -------
    CallbackAction
        The decoded action, deal_id, and contextual Telegram IDs.

    Raises
    ------
    ValueError
        If the update is missing callback_query or the payload is malformed.
    """
    callback_query = update.get("callback_query")
    if not isinstance(callback_query, dict):
        raise ValueError("Update missing callback_query")

    data = callback_query.get("data") or ""

    # Legacy support: tho:deal:approve:<deal_id>
    action: str | None = None
    deal_id: str | None = None
    if data.startswith("tho:deal:"):
        parts = data.split(":")
        if len(parts) >= 4:
            action = parts[2]
            deal_id = ":".join(parts[3:])
    elif ACTION_SEPARATOR in data:
        action, _, deal_id = data.partition(ACTION_SEPARATOR)

    if not action or not deal_id:
        raise ValueError(f"Invalid callback_data format: {data!r}")

    # Normalise legacy "decline" to "reject".
    if action == DECLINE_ACTION:
        action = REJECT_ACTION

    message = callback_query.get("message") or {}
    from_user = callback_query.get("from") or {}

    return CallbackAction(
        action=action,
        deal_id=deal_id,
        query_id=str(callback_query.get("id") or ""),
        message_id=int(message.get("message_id") or 0),
        chat_id=message.get("chat", {}).get("id") or 0,
        from_user=from_user,
    )


def handle_callback_query(
    update: dict[str, Any],
    *,
    client: TelegramClient | None = None,
    db: Any | None = None,
) -> dict[str, Any] | None:
    """Dispatch a Telegram callback query to the appropriate action handler.

    This is the primary entry point for the webhook router. It validates the
    callback, performs the business action (approve/reject), edits the original
    message when possible, logs the outcome to Firestore, and returns a dict
    suitable for ``client.answer_callback_query(**result)``.

    Parameters
    ----------
    update : dict
        Raw Telegram ``Update`` object.
    client : TelegramClient | None
        Telegram client for editing the original message. When omitted, a client
        is built from the environment.
    db : Any | None
        Firestore client (or fake). When omitted, the singleton from ``main.py``
        is used. Used to update deal status and log activity.

    Returns
    -------
    dict[str, Any] | None
        Payload for ``answerCallbackQuery`` on valid callbacks, or ``None`` for
        unknown/malformed callbacks so Telegram does not receive a noisy answer.
    """
    callback_query = (update or {}).get("callback_query")
    if not isinstance(callback_query, dict):
        return None

    query_id = callback_query.get("id")
    if not query_id:
        return None

    try:
        action = parse_callback_query(update)
    except ValueError as exc:
        logger.warning("Telegram callback parse failed: %s", exc)
        return None

    resolved_client = client or TelegramClient(TelegramConfig.from_env())
    resolved_db = db if db is not None else _get_db()

    if action.action not in _ACTION_HANDLERS:
        logger.warning("Unknown Telegram action: %s", action.action)
        return None

    handler = _ACTION_HANDLERS[action.action]
    try:
        answer = handler(resolved_client, action, db=resolved_db)
    except Exception as exc:
        logger.exception("Telegram approval action failed: %s", action.action)
        _log_telegram_activity(
            resolved_db,
            ACTIVITY_APPROVAL_ERROR,
            f"Telegram approval action {action.action} failed for deal {action.deal_id}",
            deal_id=action.deal_id,
            success=False,
            metadata={"error": str(exc), "action": action.action},
        )
        return {"callback_query_id": query_id, "text": "Action failed — please try again."}

    return answer


def _handle_approve(
    client: TelegramClient,
    action: CallbackAction,
    *,
    db: Any | None,
) -> dict[str, Any]:
    """Approve a deal via Telegram callback."""
    _update_deal_status(db, action.deal_id, DealStatus.APPROVED)
    _edit_message_status(client, action, "✅ Deal approved")
    _log_telegram_activity(
        db,
        ACTIVITY_APPROVAL_APPROVED,
        f"Deal {action.deal_id} approved via Telegram",
        deal_id=action.deal_id,
        success=True,
        metadata={"telegram_user_id": action.from_user.get("id")},
    )
    return {
        "callback_query_id": action.query_id,
        "text": f"✅ Approved deal {action.deal_id}",
    }


def _handle_reject(
    client: TelegramClient,
    action: CallbackAction,
    *,
    db: Any | None,
) -> dict[str, Any]:
    """Reject a deal via Telegram callback."""
    _update_deal_status(db, action.deal_id, DealStatus.DENIED)
    _edit_message_status(client, action, "❌ Deal rejected")
    _log_telegram_activity(
        db,
        ACTIVITY_APPROVAL_REJECTED,
        f"Deal {action.deal_id} rejected via Telegram",
        deal_id=action.deal_id,
        success=True,
        metadata={"telegram_user_id": action.from_user.get("id")},
    )
    return {
        "callback_query_id": action.query_id,
        "text": f"❌ Rejected deal {action.deal_id}",
    }


def _handle_view(
    client: TelegramClient,
    action: CallbackAction,
    *,
    db: Any | None,
) -> dict[str, Any]:
    """Record a "View Deal" button click.

    The View button uses a URL, so this handler is rarely invoked. It exists
    for cases where a callback-based view is used (e.g., deep-link previews).
    """
    _edit_message_status(client, action, "👁 Opening deal details...")
    _log_telegram_activity(
        db,
        ACTIVITY_APPROVAL_VIEWED,
        f"Deal {action.deal_id} viewed via Telegram",
        deal_id=action.deal_id,
        success=True,
        metadata={"telegram_user_id": action.from_user.get("id")},
    )
    return {
        "callback_query_id": action.query_id,
        "text": f"👁 View deal {action.deal_id}",
    }


_ACTION_HANDLERS: dict[str, Any] = {
    APPROVE_ACTION: _handle_approve,
    REJECT_ACTION: _handle_reject,
    VIEW_ACTION: _handle_view,
}


def _edit_message_status(
    client: TelegramClient,
    action: CallbackAction,
    confirmation_text: str,
) -> None:
    """Replace the original message text and remove the inline keyboard.

    Missing message context (e.g., some test payloads) is ignored gracefully.
    """
    if not action.message_id or not action.chat_id:
        return

    try:
        client.edit_message_text(
            chat_id=action.chat_id,
            message_id=action.message_id,
            text=confirmation_text,
            parse_mode="HTML",
        )
    except TelegramAPIError:
        logger.warning(
            "editMessageText failed for chat=%s message=%s",
            action.chat_id,
            action.message_id,
        )


def _update_deal_status(
    db: Any | None,
    deal_id: str,
    status: DealStatus,
) -> bool:
    """Update the deal status in Firestore when a db client is provided.

    If ``db`` is None the status change is skipped. This keeps local tests
    lightweight and avoids hard failures when Firestore is unavailable.
    """
    if db is None:
        return False

    try:
        db.collection("deals").document(deal_id).update(
            {"status": status.value, "updated_at": datetime.now(UTC)}
        )
        return True
    except Exception as exc:
        logger.warning("Failed to update deal %s status to %s: %s", deal_id, status, exc)
        raise


def _log_telegram_activity(
    db: Any | None,
    activity_type: str,
    description: str,
    *,
    deal_id: str,
    success: bool,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Persist a Telegram-related event to Firestore ``activities/``.

    Mirrors the activity schema used by ``tools/partner_webhooks`` and the
    partner webhook endpoint in ``main.py`` so downstream audit views remain
    consistent.
    """
    if db is None:
        return

    activity_id = str(uuid.uuid4())
    activity = {
        "id": activity_id,
        "activity_type": activity_type,
        "description": description,
        "deal_id": deal_id,
        "actor": "telegram_approval_bot",
        "metadata": {
            **(metadata or {}),
            "success": success,
        },
        "created_at": datetime.now(UTC).isoformat(),
    }

    try:
        db.collection("activities").document(activity_id).set(activity)
    except Exception as exc:
        logger.warning("activities/ log failed for Telegram approval: %s", exc)
