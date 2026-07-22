"""User Activity Logging for Texas Home Outlet — non-admin interaction trail.

Persists structured records of user actions (contact, appointments, chat,
feedback) to Firestore collection ``user_activity_log`` so we can understand
conversion funnels and debug user journeys without relying on raw PII.

Schema (one Firestore document per call):
    {
        "timestamp":   ISO8601 UTC,
        "action":      str,    # contact.submit | appointment.book |
                               # appointment.cancel | chat.message |
                               # chat.callback_requested |
                               # feedback.submit | page.view
        "session_id":  str,    # user session or "anonymous"
        "ip":          str,    # extracted from Request
        "user_agent":  str,    # extracted from Request
        "details":     dict,   # action-specific metadata — NEVER raw PII
    }

Design notes:
  * No PII in details — only IDs, status, and action-specific metadata.
  * Logging failures are warn-and-swallow (must never block the user action).
  * Lazy Firestore client for cold-start resilience.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────

COLLECTION = "user_activity_log"

ALLOWED_ACTIONS: tuple[str, ...] = (
    "contact.submit",
    "appointment.book",
    "appointment.cancel",
    "chat.message",
    "chat.callback_requested",
    "feedback.submit",
    "inventory.search",
    "page.view",
    "document.download",
    "document.share",
)

# Keys that are PII-shaped — stripped from details even if passed accidentally.
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
        "message_text",
        "raw_text",
        "text_content",
    }
)

_MAX_DETAILS_BYTES = 4096


# ── Lazy Firestore client ─────────────────────────────────────────────────

_firestore_client = None


def _get_db():
    """Lazy-load a Firestore client. Returns None if unavailable."""
    global _firestore_client
    if _firestore_client is None:
        # Avoid hanging in test environments when no credentials are available.
        if "pytest" in sys.modules:
            _firestore_client = None
            return None
        try:
            from google.cloud import firestore

            project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "tho-ai-agent")
            _firestore_client = firestore.Client(project=project_id)
        except Exception as exc:  # pragma: no cover
            logger.warning("User activity log Firestore client unavailable: %s", exc)
            _firestore_client = None
    return _firestore_client


def _reset_client_for_tests() -> None:
    """Test hook: drop the cached client so tests can swap in a fake."""
    global _firestore_client
    _firestore_client = None


# ── Helpers ───────────────────────────────────────────────────────────────


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
    """Drop PII-shaped keys and cap value sizes."""
    if not details:
        return {}
    if not isinstance(details, dict):
        return {"_invalid": "details must be a dict"}

    cleaned: dict[str, Any] = {}
    for key, value in details.items():
        if not isinstance(key, str):
            continue
        if key.lower() in _PII_KEYS_DENYLIST:
            continue
        cleaned[key] = _coerce_safe(value)

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
    try:
        return str(value)[:500]
    except Exception:
        return "<unprintable>"


# ── Public API ─────────────────────────────────────────────────────────────


def log_user_action(
    action: str,
    session_id: str | None = None,
    details: dict[str, Any] | None = None,
    request: Any = None,
) -> None:
    """Persist a user-activity entry.

    Args:
        action: One of ALLOWED_ACTIONS. Unknown actions are still logged
            but emit a warning so we notice drift.
        session_id: User session identifier, or None for "anonymous".
        details: Optional metadata. PII keys are stripped before write.
        request: FastAPI Request for IP / user-agent extraction.

    Logging failures are warned and swallowed.
    """
    try:
        if action not in ALLOWED_ACTIONS:
            logger.warning("User activity log received unknown action=%s", action)

        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "action": (action or "")[:60],
            "session_id": (session_id or "anonymous")[:120],
            "ip": _client_ip_from_request(request)[:64],
            "user_agent": _user_agent_from_request(request),
            "details": _sanitize_details(details),
        }

        db = _get_db()
        if db is None:
            logger.warning("User activity log skipped (no Firestore client) action=%s", action)
            return

        db.collection(COLLECTION).add(entry)
    except Exception as exc:
        logger.warning(
            "User activity log write failed action=%s error=%s",
            action,
            exc,
        )


def query_user_activity(
    action: str | None = None,
    session_id: str | None = None,
    since: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return user-activity entries in reverse-chronological order.

    Filters compose with AND. ``since`` is an ISO8601 string. ``limit`` is
    clamped to [1, 500].
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
        collection = db.collection(COLLECTION)
        query: Any = collection
        if action:
            query = query.where("action", "==", action)
        if session_id:
            query = query.where("session_id", "==", session_id)
        if since:
            query = query.where("timestamp", ">=", since)

        try:
            stream = query.limit(limit * 4).stream()
        except Exception:
            stream = query.stream()

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
        logger.warning("User activity log query failed: %s", exc)
        return []
