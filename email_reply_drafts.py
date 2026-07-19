"""Firestore-backed store for human-review email reply drafts.

Lane 2 of the inbound-email automation pipeline (spec: the golden triage set
in ``tests/test_email_triage.py`` + the state machine in
``tests/test_email_reply_drafts.py``). Substantive inbound email gets a draft
here; a human approves or rejects it via a gated review surface. This module
is a pure store — it has **no send capability** and never imports
``email_service``. The single send chokepoint (a later lane) is the only code
allowed to act on an ``approved`` draft.

State machine (illegal transitions raise ``IllegalTransitionError``):

    pending  → approved | rejected | expired
    approved → sent
    rejected / expired / sent are terminal

Idempotency: the Firestore doc id is a deterministic hash of the Resend
message-id, so webhook retries can never create a second draft (and therefore
can never cause a second send downstream).

Storage: collection ``email_reply_drafts`` (lazy client, mirrors
``audit_log.py``). Store failures degrade to ``None``/warn — except state-
machine violations, which are always loud.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from database.firestore_timeouts import firestore_timeout

logger = logging.getLogger(__name__)

DRAFTS_COLLECTION = "email_reply_drafts"

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_SENT = "sent"
STATUS_EXPIRED = "expired"

# The state machine IS the safety property: there is no path to `sent` that
# does not pass through an explicit human `approved`.
_LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_PENDING: frozenset({STATUS_APPROVED, STATUS_REJECTED, STATUS_EXPIRED}),
    STATUS_APPROVED: frozenset({STATUS_SENT}),
    STATUS_REJECTED: frozenset(),
    STATUS_SENT: frozenset(),
    STATUS_EXPIRED: frozenset(),
}

# Cap the stored inbound excerpt: reviewers need context, not an archive.
MAX_EXCERPT_CHARS = 2000
_MAX_FIELD_CHARS = 300


class IllegalTransitionError(ValueError):
    """Raised on any draft status change the state machine does not allow."""


@dataclass(frozen=True)
class ReplyDraft:
    """One reviewable reply draft for an inbound email."""

    draft_id: str
    message_id: str
    sender: str
    subject: str
    triage_label: str
    status: str = STATUS_PENDING
    rule_hits: list[str] = field(default_factory=list)
    inbound_excerpt: str = ""
    lead_id: str = ""
    draft_body: str = ""
    created_at: str = ""
    updated_at: str = ""
    decided_by: str = ""


# ── Lazy Firestore client (mirrors audit_log.py) ────────────────────────────

_firestore_client = None


def _get_db():
    """Lazy-load a Firestore client. Returns None if unavailable."""
    global _firestore_client
    if _firestore_client is None:
        try:
            from google.cloud import firestore

            project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "tho-ai-agent")
            _firestore_client = firestore.Client(project=project_id)
        except Exception as exc:  # pragma: no cover - import-time guard
            logger.warning("Reply-draft Firestore client unavailable: %s", exc)
            _firestore_client = None
    return _firestore_client


def _reset_client_for_tests() -> None:
    """Test hook: drop the cached client so tests can swap in a fake."""
    global _firestore_client
    _firestore_client = None


# ── Helpers ──────────────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _doc_id_for_message(message_id: str) -> str:
    """Deterministic Firestore doc id for a Resend message-id (idempotency key)."""
    return hashlib.sha256(message_id.encode("utf-8", errors="replace")).hexdigest()[:32]


def _from_doc(doc_id: str, data: dict) -> ReplyDraft:
    return ReplyDraft(
        draft_id=doc_id,
        message_id=str(data.get("message_id", "")),
        sender=str(data.get("sender", "")),
        subject=str(data.get("subject", "")),
        triage_label=str(data.get("triage_label", "")),
        status=str(data.get("status", STATUS_PENDING)),
        rule_hits=list(data.get("rule_hits") or []),
        inbound_excerpt=str(data.get("inbound_excerpt", "")),
        lead_id=str(data.get("lead_id", "")),
        draft_body=str(data.get("draft_body", "")),
        created_at=str(data.get("created_at", "")),
        updated_at=str(data.get("updated_at", "")),
        decided_by=str(data.get("decided_by", "")),
    )


# ── Public API ───────────────────────────────────────────────────────────────


def create_draft(
    message_id: str,
    sender: str,
    subject: str,
    triage_label: str,
    rule_hits: list[str] | None = None,
    inbound_excerpt: str = "",
    lead_id: str = "",
    draft_body: str = "",
) -> tuple[ReplyDraft | None, bool]:
    """Create a pending draft, idempotent on ``message_id``.

    Returns ``(draft, created)``. On a duplicate message-id the EXISTING
    draft is returned untouched with ``created=False`` — a webhook retry must
    never overwrite a draft a human may already be reviewing. Returns
    ``(None, False)`` when input is invalid or the store is unavailable.
    """
    if not message_id or not isinstance(message_id, str):
        logger.warning("Reply draft rejected: missing message_id")
        return None, False
    db = _get_db()
    if db is None:
        logger.warning("Reply draft skipped (no Firestore client)")
        return None, False
    try:
        doc_id = _doc_id_for_message(message_id)
        ref = db.collection(DRAFTS_COLLECTION).document(doc_id)
        snap = ref.get(timeout=firestore_timeout())
        if snap.exists:
            return _from_doc(doc_id, snap.to_dict() or {}), False
        now = _now()
        data = {
            "message_id": message_id[:_MAX_FIELD_CHARS],
            "sender": str(sender or "")[:_MAX_FIELD_CHARS],
            "subject": str(subject or "")[:_MAX_FIELD_CHARS],
            "triage_label": str(triage_label or "")[:60],
            "status": STATUS_PENDING,
            "rule_hits": [str(h)[:60] for h in (rule_hits or [])][:20],
            "inbound_excerpt": str(inbound_excerpt or "")[:MAX_EXCERPT_CHARS],
            "lead_id": str(lead_id or "")[:_MAX_FIELD_CHARS],
            "draft_body": str(draft_body or ""),
            "created_at": now,
            "updated_at": now,
            "decided_by": "",
        }
        ref.set(data, timeout=firestore_timeout())
        return _from_doc(doc_id, data), True
    except Exception as exc:
        logger.warning("Reply draft create failed: %s", exc)
        return None, False


def get_draft(draft_id: str) -> ReplyDraft | None:
    """Fetch one draft by id. None if missing or store unavailable."""
    db = _get_db()
    if db is None or not draft_id:
        return None
    try:
        snap = db.collection(DRAFTS_COLLECTION).document(draft_id).get(
            timeout=firestore_timeout()
        )
        if not snap.exists:
            return None
        return _from_doc(draft_id, snap.to_dict() or {})
    except Exception as exc:
        logger.warning("Reply draft read failed id=%s: %s", draft_id, exc)
        return None


def list_drafts(status: str | None = None, limit: int = 50) -> list[ReplyDraft]:
    """List drafts, optionally filtered by status. Empty list on any failure."""
    db = _get_db()
    if db is None:
        return []
    try:
        query = db.collection(DRAFTS_COLLECTION)
        if status:
            query = query.where("status", "==", status)
        query = query.limit(max(1, min(int(limit), 200)))
        return [
            _from_doc(snap.id, snap.to_dict() or {})
            for snap in query.stream(timeout=firestore_timeout())
        ]
    except Exception as exc:
        logger.warning("Reply draft list failed: %s", exc)
        return []


def transition(draft_id: str, new_status: str, actor: str) -> ReplyDraft | None:
    """Move a draft through the state machine.

    Raises ``IllegalTransitionError`` on any disallowed move — a bug in a
    caller must be loud, never a silent status overwrite. Returns ``None``
    when the draft is missing or the store is unavailable.
    """
    if new_status not in _LEGAL_TRANSITIONS:
        raise IllegalTransitionError(f"unknown draft status: {new_status!r}")
    db = _get_db()
    if db is None:
        logger.warning("Reply draft transition skipped (no Firestore client)")
        return None
    ref = db.collection(DRAFTS_COLLECTION).document(draft_id)
    snap = ref.get(timeout=firestore_timeout())
    if not snap.exists:
        logger.warning("Reply draft transition on missing draft id=%s", draft_id)
        return None
    data = snap.to_dict() or {}
    current = str(data.get("status", STATUS_PENDING))
    if new_status not in _LEGAL_TRANSITIONS.get(current, frozenset()):
        raise IllegalTransitionError(
            f"illegal draft transition {current!r} → {new_status!r} (id={draft_id})"
        )
    updates = {
        "status": new_status,
        "updated_at": _now(),
        "decided_by": str(actor or "")[:120],
    }
    ref.update(updates, timeout=firestore_timeout())
    data.update(updates)
    return _from_doc(draft_id, data)


def set_draft_body(draft_id: str, body: str) -> ReplyDraft | None:
    """Set the proposed reply body — allowed only while the draft is pending.

    Post-decision edits raise ``IllegalTransitionError``: what the human
    approved must be exactly what could ever be sent.
    """
    db = _get_db()
    if db is None:
        return None
    ref = db.collection(DRAFTS_COLLECTION).document(draft_id)
    snap = ref.get(timeout=firestore_timeout())
    if not snap.exists:
        return None
    data = snap.to_dict() or {}
    current = str(data.get("status", STATUS_PENDING))
    if current != STATUS_PENDING:
        raise IllegalTransitionError(
            f"draft body is immutable after decision (status={current!r}, id={draft_id})"
        )
    updates = {"draft_body": str(body or ""), "updated_at": _now()}
    ref.update(updates, timeout=firestore_timeout())
    data.update(updates)
    return _from_doc(draft_id, data)


def to_dict(draft: ReplyDraft) -> dict:
    """JSON-safe dict for API surfaces."""
    return asdict(draft)
