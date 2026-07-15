"""The SINGLE send chokepoint for inbound-email replies.

Lane 3b of the inbound-email automation pipeline. Every outward reply — the
human-approved substantive drafts AND the fixed auto-acknowledgement — passes
through this module and nowhere else. No other reply-pipeline code may import
``email_service`` (regression-tested in ``tests/test_email_reply_sender.py``).

Gate stack, checked in order on EVERY send:

1. ``FF_EMAIL_REPLY_SEND``  — master kill-switch, default OFF. Nothing in
   this module can send while it is off.
2. Per-path gate:
   - approved-draft path: draft.status == ``approved`` (the state machine in
     ``email_reply_drafts`` guarantees a human decision produced that status)
     and a non-empty approved body.
   - auto-ack path: ``FF_EMAIL_AUTO_ACK`` (default OFF) — the only
     unsupervised send, fixed body-echo-free template only.
3. Daily cap (``EMAIL_REPLY_DAILY_CAP``, default 25) across both paths.
4. Exactly-once: success transitions the draft to terminal ``sent``; the
   auto-ack path is idempotent on Resend message-id via the same store.
5. Audit record (``audit_log.log_admin_action``) for every successful send.

Underneath all of this, ``email_service.send_email`` itself no-ops without
``RESEND_API_KEY`` — activation requires Ari to set the key AND flip the
flags in prod (see the activation runbook lane).
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

import tools.feature_flags as ff
from audit_log import log_admin_action
from email_reply_drafts import (
    STATUS_APPROVED,
    STATUS_SENT,
    create_draft,
    get_draft,
    transition,
)
from email_reply_generator import render_ack
from email_service import send_email

logger = logging.getLogger(__name__)

FLAG_MASTER = "EMAIL_REPLY_SEND"
FLAG_AUTO_ACK = "EMAIL_AUTO_ACK"

_DEFAULT_DAILY_CAP = 25

# In-process backstop counter {date: count}. Per-instance, deliberately
# conservative: restarts reset it, scale-out multiplies it by instance count,
# and the cap default is small enough that neither matters for safety.
_daily_sent: dict[str, int] = {}


def _reset_daily_counter_for_tests() -> None:
    _daily_sent.clear()


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _daily_cap() -> int:
    try:
        return max(0, int(os.environ.get("EMAIL_REPLY_DAILY_CAP", _DEFAULT_DAILY_CAP)))
    except (TypeError, ValueError):
        return _DEFAULT_DAILY_CAP


def _cap_reached() -> bool:
    return _daily_sent.get(_today(), 0) >= _daily_cap()


def _count_send() -> None:
    _daily_sent[_today()] = _daily_sent.get(_today(), 0) + 1


def _refuse(error: str, **extra) -> dict:
    result = {"success": False, "error": error}
    result.update(extra)
    return result


def send_approved_reply(draft_id: str, actor: str) -> dict:
    """Send a human-approved reply draft. The ONLY path for substantive replies.

    Requires ``FF_EMAIL_REPLY_SEND`` + draft status ``approved`` + non-empty
    body + daily-cap headroom. Success transitions the draft to ``sent``
    (terminal — retries refuse); failure leaves it ``approved`` (retryable).
    """
    if not ff.is_enabled(FLAG_MASTER, default=False):
        return _refuse("EMAIL_REPLY_SEND flag is off — reply sending disabled")
    draft = get_draft(draft_id)
    if draft is None:
        return _refuse(f"draft not found: {draft_id}")
    if draft.status != STATUS_APPROVED:
        return _refuse(
            f"draft is {draft.status!r}, not approved — refusing to send", draft_id=draft_id
        )
    if not (draft.draft_body or "").strip():
        return _refuse("approved draft has empty body — refusing to send", draft_id=draft_id)
    if _cap_reached():
        return _refuse(f"daily reply cap reached ({_daily_cap()})", draft_id=draft_id)

    html_body = "<br>\n".join(
        line for line in (draft.draft_body.replace("\r", "").split("\n"))
    )
    result = send_email(
        to=draft.sender,
        subject=f"Re: {draft.subject}"[:220] if draft.subject else "Re: your message",
        html=f'<div style="font-family: Arial, sans-serif; white-space: normal;">{html_body}</div>',
        text=draft.draft_body,
        email_type="inbound_reply",
        related_id=draft.draft_id,
    )
    if not result.get("success"):
        logger.warning(
            "Approved reply send failed draft=%s error=%s", draft_id, result.get("error")
        )
        return _refuse(str(result.get("error") or "send failed"), draft_id=draft_id)

    _count_send()
    transition(draft.draft_id, STATUS_SENT, actor=f"system:sender({actor})")
    log_admin_action(
        actor=actor,
        action="email.send",
        target_type="email",
        target_id=draft.draft_id,
        details={"kind": "reply_draft", "triage_label": draft.triage_label},
    )
    return {"success": True, "draft_id": draft.draft_id, "message_id": result.get("message_id")}


def send_auto_ack(message_id: str, to: str, subject: str) -> dict:
    """Send the fixed acknowledgement template — the ONLY unsupervised send.

    Requires BOTH ``FF_EMAIL_REPLY_SEND`` and ``FF_EMAIL_AUTO_ACK`` (each
    default OFF) plus daily-cap headroom. Idempotent on ``message_id``: the
    ack is recorded in the draft store (label ``safe_ack``) and driven
    through the same one-way state machine, so a webhook retry can never
    double-ack.
    """
    if not ff.is_enabled(FLAG_MASTER, default=False):
        return _refuse("EMAIL_REPLY_SEND flag is off — reply sending disabled")
    if not ff.is_enabled(FLAG_AUTO_ACK, default=False):
        return _refuse("EMAIL_AUTO_ACK flag is off — auto-ack disabled")
    if _cap_reached():
        return _refuse(f"daily reply cap reached ({_daily_cap()})")

    record, created = create_draft(
        message_id=message_id,
        sender=to,
        subject=subject,
        triage_label="safe_ack",
        draft_body="(fixed acknowledgement template — see email_reply_generator.render_ack)",
    )
    if record is None:
        return _refuse("draft store unavailable — refusing to ack without idempotency record")
    if not created or record.status != "pending":
        return _refuse(f"ack already handled for this message (status={record.status})")

    rendered = render_ack(subject)
    result = send_email(
        to=to,
        subject=rendered["subject"],
        html=rendered["html"],
        text=rendered["text"],
        email_type="inbound_auto_ack",
        related_id=record.draft_id,
    )
    if not result.get("success"):
        logger.warning("Auto-ack send failed message=%s error=%s", message_id, result.get("error"))
        return _refuse(str(result.get("error") or "send failed"))

    _count_send()
    transition(record.draft_id, STATUS_APPROVED, actor="system:auto_ack(FF_EMAIL_AUTO_ACK)")
    transition(record.draft_id, STATUS_SENT, actor="system:auto_ack(FF_EMAIL_AUTO_ACK)")
    log_admin_action(
        actor="system:auto_ack",
        action="email.send",
        target_type="email",
        target_id=record.draft_id,
        details={"kind": "auto_ack"},
    )
    return {"success": True, "draft_id": record.draft_id, "message_id": result.get("message_id")}
