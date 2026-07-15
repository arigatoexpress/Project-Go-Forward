"""THO-self-contained Telegram approve/reject rail for email reply drafts.

Lane 4 of the inbound-email automation pipeline. When a substantive inbound
email produces a pending draft, this module pushes an approve/reject card to
a private THO Telegram chat; the human decision drives the one-way draft
state machine. Mirrors the proposals→decision→audit shape of the personal
trading rail but shares ZERO code or infrastructure with it — everything here
is scoped to this repo and its own bot credentials.

Inert by construction (three independent switches, each default off/absent):

1. ``FF_EMAIL_TG_GATE``       — feature flag, default OFF.
2. ``THO_TG_BOT_TOKEN`` /
   ``THO_TG_CHAT_ID``         — bot credentials; absent → every call no-ops.
3. ``THO_TG_WEBHOOK_SECRET``  — inbound callback verification; absent → the
   webhook route answers "disabled" and processes nothing (fail closed).

Safety rules (enforced by ``tests/test_telegram_gate.py``):

- This module NEVER sends email. Approve only transitions the draft to
  ``approved`` and then defers to the single chokepoint
  (``email_reply_sender.send_approved_reply``), which has its own flag stack
  — with send flags off, an approved draft parks unsent.
- Callbacks are honored only from the configured chat id.
- Replays are idempotent: a decided draft can never be flipped or re-sent
  (``IllegalTransitionError`` → "already_decided").
"""

from __future__ import annotations

import hmac
import logging
import os

import tools.feature_flags as ff
from email_reply_drafts import (
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    IllegalTransitionError,
    ReplyDraft,
    get_draft,
    transition,
)

logger = logging.getLogger(__name__)

FLAG_TG_GATE = "EMAIL_TG_GATE"

_TELEGRAM_API = "https://api.telegram.org"
_CARD_EXCERPT_CHARS = 400


def _token() -> str:
    return os.environ.get("THO_TG_BOT_TOKEN", "").strip()


def _chat_id() -> str:
    return os.environ.get("THO_TG_CHAT_ID", "").strip()


def is_configured() -> bool:
    """True only when both bot token and chat id are present."""
    return bool(_token() and _chat_id())


def _post_telegram(method: str, payload: dict) -> dict:
    """POST one Telegram Bot API call. Failures are logged, never raised.

    Module-level so tests (and the route layer) can monkeypatch it; the ONLY
    network egress in this module.
    """
    token = _token()
    if not token:
        return {"ok": False, "error": "no bot token"}
    try:
        import httpx

        resp = httpx.post(f"{_TELEGRAM_API}/bot{token}/{method}", json=payload, timeout=10)
        return resp.json() if resp.status_code == 200 else {"ok": False, "error": f"HTTP {resp.status_code}"}
    except Exception as exc:
        logger.warning("Telegram API call failed method=%s error=%s", method, exc)
        return {"ok": False, "error": str(exc)}


# ── Outbound: approve/reject card ───────────────────────────────────────────


def notify_pending_draft(draft: ReplyDraft | None) -> dict:
    """Push an approve/reject card for a pending draft to the THO chat.

    No flag, no credentials, or no draft → structured refusal, zero network.
    """
    if draft is None:
        return {"success": False, "error": "no draft"}
    if not ff.is_enabled(FLAG_TG_GATE, default=False):
        return {"success": False, "error": "EMAIL_TG_GATE flag is off"}
    if not is_configured():
        logger.info("Telegram gate not configured — draft card skipped id=%s", draft.draft_id)
        return {"success": False, "error": "telegram gate not configured"}

    text = (
        "📧 Email reply draft needs review\n"
        f"From: {draft.sender}\n"
        f"Subject: {draft.subject}\n"
        f"Triage: {draft.triage_label} ({', '.join(draft.rule_hits[:5])})\n"
        f"Inbound: {draft.inbound_excerpt[:_CARD_EXCERPT_CHARS]}\n"
        "----\n"
        f"Draft reply:\n{(draft.draft_body or '(empty — edit in admin before approving)')[:_CARD_EXCERPT_CHARS]}"
    )
    payload = {
        "chat_id": _chat_id(),
        "text": text,
        "reply_markup": {
            "inline_keyboard": [
                [
                    {"text": "✅ Approve", "callback_data": f"draft:approve:{draft.draft_id}"},
                    {"text": "❌ Reject", "callback_data": f"draft:reject:{draft.draft_id}"},
                ]
            ]
        },
    }
    result = _post_telegram("sendMessage", payload)
    if not result.get("ok"):
        return {"success": False, "error": str(result.get("error") or "telegram send failed")}
    return {"success": True, "draft_id": draft.draft_id}


# ── Inbound: webhook secret + callback handling ─────────────────────────────


def verify_webhook_secret(header_value: str) -> bool:
    """Constant-time check of X-Telegram-Bot-Api-Secret-Token. Fail closed."""
    secret = os.environ.get("THO_TG_WEBHOOK_SECRET", "").strip()
    if not secret or not header_value:
        return False
    return hmac.compare_digest(secret, header_value)


def _answer_callback(callback_id: str, text: str) -> None:
    if callback_id:
        _post_telegram("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:190]})


def handle_update(update) -> dict:
    """Process one Telegram update. Only draft approve/reject callbacks from
    the configured chat act; everything else is ignored. Never raises."""
    try:
        if not isinstance(update, dict):
            return {"status": "ignored"}
        cbq = update.get("callback_query")
        if not isinstance(cbq, dict):
            return {"status": "ignored"}

        data = str(cbq.get("data") or "")
        parts = data.split(":", 2)
        if len(parts) != 3 or parts[0] != "draft" or parts[1] not in ("approve", "reject"):
            return {"status": "ignored"}
        verdict, draft_id = parts[1], parts[2]

        chat = ((cbq.get("message") or {}).get("chat") or {})
        if str(chat.get("id", "")) != _chat_id() or not _chat_id():
            logger.warning("Telegram callback from unauthorized chat id=%s", chat.get("id"))
            return {"status": "unauthorized"}

        callback_id = str(cbq.get("id") or "")
        actor = f"telegram:{(cbq.get('from') or {}).get('id', 'unknown')}"

        draft = get_draft(draft_id)
        if draft is None:
            _answer_callback(callback_id, "Draft not found")
            return {"status": "not_found"}
        if draft.status != STATUS_PENDING:
            _answer_callback(callback_id, f"Already decided: {draft.status}")
            return {"status": "already_decided", "draft_status": draft.status}

        try:
            if verdict == "approve":
                transition(draft_id, STATUS_APPROVED, actor=actor)
                # Defer to the single chokepoint. With FF_EMAIL_REPLY_SEND off
                # (default) this refuses and the draft parks at approved.
                from email_reply_sender import send_approved_reply

                send_result = send_approved_reply(draft_id, actor=actor)
                _answer_callback(
                    callback_id,
                    "Approved — sent" if send_result.get("success") else
                    f"Approved — not sent: {send_result.get('error', '')}",
                )
                return {"status": "approved", "send": send_result}
            transition(draft_id, STATUS_REJECTED, actor=actor)
            _answer_callback(callback_id, "Rejected — no reply will be sent")
            return {"status": "rejected"}
        except IllegalTransitionError:
            return {"status": "already_decided"}
    except Exception as exc:  # noqa: BLE001 — webhook must never 500 back to Telegram
        logger.warning("Telegram update handling failed: %s", exc)
        return {"status": "error"}
