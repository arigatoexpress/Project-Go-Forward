"""
Audit Logging Service for Texas Home Outlet — admin mutation trail.

Persists structured records of admin CRUD/document operations to Firestore
collection ``audit_log`` so we have a defensible trail of who changed what.

Schema (one Firestore document per call):
    {
        "timestamp":   ISO8601 UTC,
        "actor":       str,    # admin token id or "admin" for the shared PIN
        "action":      str,    # deal.create | deal.update | deal.delete |
                               # customer.update | document.generate |
                               # admin.login | admin.logout
        "target_type": str,    # "deal" | "customer" | "document" | "session"
        "target_id":   str,
        "ip":          str,    # extracted from Request when available
        "user_agent":  str,    # extracted from Request when available
        "details":     dict,   # IDs and field names only — NEVER raw PII
    }

Design notes:
  * Mutations only — reads are not logged (would dwarf the signal).
  * Mirrors the lazy Firestore-client pattern in ``email_service.py``.
  * Logging failures are warn-and-swallow: we MUST NOT block a legitimate
    admin action because the audit write blew up.
  * ``details`` is sanitized server-side: any PII-shaped keys are dropped to
    avoid accidental leakage even if a caller passes them.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from database.rpc_timeout import FIRESTORE_RPC_TIMEOUT

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────

AUDIT_COLLECTION = "audit_log"

# Allowed action names. Kept narrow so the collection stays queryable and a
# typo at a call site is loud instead of silent. Extend deliberately.
ALLOWED_ACTIONS: tuple[str, ...] = (
    "deal.create",
    "deal.update",
    "deal.delete",
    "customer.create",
    "customer.update",
    "document.generate",
    "document.esign_send",
    "document.esign_complete",
    "documents.share",
    "inventory.create",
    "inventory.update",
    "inventory.delete",
    "inventory.retire",
    "inventory.bulk_import",
    "inventory.photos_upload",
    "inventory.photos_reorder",
    "inventory.photo_delete",
    "lead.update",
    "lead.lifecycle_transition",
    "crm_task.create",
    "crm_task.update",
    "email.send",
    "admin.login",
    "admin.login_code.request",
    "admin.logout",
)

ALLOWED_TARGET_TYPES: tuple[str, ...] = (
    "deal",
    "customer",
    "document",
    "inventory",
    "lead",
    "crm_task",
    "email",
    "session",
)

# Keys that are PII-shaped — we strip them from `details` even if a caller
# accidentally passes them. The rule is: details carry IDs and field-name
# deltas only, never raw PII values.
_PII_KEYS_DENYLIST = frozenset(
    {
        "ssn",
        "ssn_hash",
        "ssn_masked",
        "buyer_ssn",
        "co_buyer_ssn",
        "full_name",
        "first_name",
        "last_name",
        "buyer_first_name",
        "buyer_last_name",
        "email",
        "buyer_email",
        "phone",
        "buyer_phone",
        "address",
        "street",
        "dob",
        "date_of_birth",
        "license_number",
        "drivers_license",
        "password",
        "pin",
        "token",
    }
)

# Maximum length of the details JSON — prevents a buggy caller from filling
# Firestore with megabyte-sized payloads.
_MAX_DETAILS_BYTES = 4096


# ── Lazy Firestore client (mirrors email_service.py) ───────────────────────

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
            logger.warning("Audit log Firestore client unavailable: %s", exc)
            _firestore_client = None
    return _firestore_client


def _reset_client_for_tests() -> None:
    """Test hook: drop the cached client so tests can swap in a fake."""
    global _firestore_client
    _firestore_client = None


# ── Helpers ────────────────────────────────────────────────────────────────


def _client_ip_from_request(request: Any) -> str:
    """Extract the real client IP, honoring Cloud Run's X-Forwarded-For."""
    if request is None:
        return ""
    try:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        client = getattr(request, "client", None)
        if client and getattr(client, "host", None):
            return client.host
    except Exception:
        return ""
    return ""


def _user_agent_from_request(request: Any) -> str:
    if request is None:
        return ""
    try:
        return (request.headers.get("user-agent", "") or "")[:300]
    except Exception:
        return ""


def _sanitize_details(details: dict[str, Any] | None) -> dict[str, Any]:
    """Drop PII-shaped keys and cap value sizes.

    The contract is: ``details`` describes the *shape* of a change (IDs,
    field-name lists, status transitions) — never raw PII values. We enforce
    that here so a careless call site can't leak.
    """
    if not details:
        return {}
    if not isinstance(details, dict):
        return {"_invalid": "details must be a dict"}

    cleaned: dict[str, Any] = {}
    for key, value in details.items():
        if not isinstance(key, str):
            continue
        if key.lower() in _PII_KEYS_DENYLIST:
            # Skip silently — call sites should never pass these, but if they
            # do we drop the value rather than write it.
            continue
        cleaned[key] = _coerce_safe(value)

    # Hard cap on total size — protects Firestore from runaway payloads.
    serialized = repr(cleaned)
    if len(serialized) > _MAX_DETAILS_BYTES:
        return {"_truncated": True, "size_bytes": len(serialized)}
    return cleaned


def _coerce_safe(value: Any, _depth: int = 0) -> Any:
    """Recursively make a value Firestore-friendly and PII-stripped."""
    if _depth > 4:
        return "<truncated>"
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        # Cap individual string length so a bug can't blow up the doc.
        return value[:500]
    if isinstance(value, list | tuple):
        return [_coerce_safe(v, _depth + 1) for v in list(value)[:50]]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                continue
            if k.lower() in _PII_KEYS_DENYLIST:
                continue
            out[k] = _coerce_safe(v, _depth + 1)
        return out
    # Fallback: stringify unknown types.
    try:
        return str(value)[:500]
    except Exception:
        return "<unprintable>"


# ── Public API ─────────────────────────────────────────────────────────────


def log_admin_action(
    actor: str,
    action: str,
    target_type: str,
    target_id: str,
    details: dict[str, Any] | None = None,
    request: Any = None,
) -> None:
    """Persist an audit-log entry for an admin mutation.

    Args:
        actor: Token id, or ``"admin"`` for the shared PIN.
        action: One of ``ALLOWED_ACTIONS``. Unknown actions are still logged
            but emit a warn-level structured note so we notice drift.
        target_type: ``deal`` | ``customer`` | ``document`` | ``session``.
        target_id: Firestore doc id, filename, etc. ``""`` is acceptable for
            actions where there is no natural identifier (e.g. ``admin.login``
            uses the actor token preview).
        details: Optional metadata. IDs and field-name deltas only — PII keys
            are stripped before write.
        request: FastAPI ``Request`` for IP / user-agent extraction.

    Logging failures are warned and swallowed: we never block the originating
    request because the audit write failed.
    """
    try:
        if action not in ALLOWED_ACTIONS:
            logger.warning(
                "Audit log received unknown action=%s target_type=%s",
                action,
                target_type,
            )
        if target_type not in ALLOWED_TARGET_TYPES:
            logger.warning(
                "Audit log received unknown target_type=%s for action=%s",
                target_type,
                action,
            )

        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "actor": (actor or "admin")[:120],
            "action": (action or "")[:60],
            "target_type": (target_type or "")[:30],
            "target_id": (str(target_id) if target_id is not None else "")[:200],
            "ip": _client_ip_from_request(request)[:64],
            "user_agent": _user_agent_from_request(request),
            "details": _sanitize_details(details),
        }

        db = _get_db()
        if db is None:
            logger.warning("Audit log skipped (no Firestore client) action=%s", action)
            return

        db.collection(AUDIT_COLLECTION).add(entry, timeout=FIRESTORE_RPC_TIMEOUT)
    except Exception as exc:
        # NEVER raise — admin actions must complete even if the audit write
        # blows up. Surface the failure in structured logs so operators see
        # it in Cloud Logging.
        logger.warning(
            "Audit log write failed action=%s target_type=%s target_id=%s error=%s",
            action,
            target_type,
            target_id,
            exc,
        )


def query_audit_log(
    actor: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    since: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return audit-log entries in reverse-chronological order.

    Filters compose with AND. ``since`` is an ISO8601 string; entries with
    ``timestamp >= since`` are returned. ``limit`` is clamped to [1, 500].
    """
    try:
        requested = int(limit) if limit is not None else 100
    except (TypeError, ValueError):
        requested = 100
    limit = max(1, min(requested, 500))
    db = _get_db()
    if db is None:
        return []

    try:
        collection = db.collection(AUDIT_COLLECTION)
        query: Any = collection
        if actor:
            query = query.where("actor", "==", actor)
        if action:
            query = query.where("action", "==", action)
        if target_type:
            query = query.where("target_type", "==", target_type)
        if target_id:
            query = query.where("target_id", "==", target_id)
        if since:
            query = query.where("timestamp", ">=", since)

        # Pull more than we need so the in-memory sort is stable even when
        # Firestore can't honor an order_by alongside the inequality filters
        # we may have added. Then sort + cap locally.
        try:
            stream = query.limit(limit * 4).stream(timeout=FIRESTORE_RPC_TIMEOUT)
        except Exception:
            stream = query.stream(timeout=FIRESTORE_RPC_TIMEOUT)

        rows: list[dict[str, Any]] = []
        for snap in stream:
            try:
                data = snap.to_dict() or {}
            except Exception:
                continue
            if hasattr(snap, "id"):
                data.setdefault("id", snap.id)
            rows.append(data)

        rows.sort(key=lambda row: row.get("timestamp", ""), reverse=True)
        return rows[:limit]
    except Exception as exc:
        logger.warning("Audit log query failed: %s", exc)
        return []
