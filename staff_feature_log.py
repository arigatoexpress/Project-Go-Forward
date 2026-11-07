"""Feature-specific Cloud Logging lines for staff tool writes.

Draft PR 496 (`auth/staff_audit.py`) already writes stdout JSON for sign-in,
sign-out, passkey ceremonies, staff HTML page views, and a generic
authenticated mutation (`event=staff.action` plus the path). This module does
not repeat those lines.

It runs from ``audit_log.log_admin_action`` and adds one line when that audit
record already names a tool write the generic path line does not describe:
photos, inventory, leads, documents, staff email, deals, customers, CRM tasks,
or team access. Cloud Run collects stdout into the ``tho-ai-agent``
``_Default`` bucket.

Each line is a fixed envelope plus an allowlist. Telemetry failures are
ignored so a logging error cannot block the staff write.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

LANE = "pgf"
SERVICE = "staff-admin"

# Ceremony and page-view lines belong to the staff-admin logger on PR 496.
_SKIP_ACTIONS = frozenset(
    {
        "admin.login",
        "admin.logout",
        "admin.login_code.request",
    }
)

_FEATURE_BY_ACTION = {
    "inventory.photos_upload": "photos",
    "inventory.photos_reorder": "photos",
    "inventory.photo_delete": "photos",
    "inventory.tour_set": "photos",
    "inventory.tour_clear": "photos",
    "inventory.create": "inventory",
    "inventory.update": "inventory",
    "inventory.delete": "inventory",
    "inventory.retire": "inventory",
    "inventory.bulk_import": "inventory",
    "lead.update": "leads",
    "lead.lifecycle_transition": "leads",
    "document.generate": "documents",
    "document.esign_send": "documents",
    "document.esign_complete": "documents",
    "document.esign_cancel": "documents",
    "documents.share": "documents",
    "document.email_delivery": "email",
    "email.send": "email",
    "deal.create": "deals",
    "deal.update": "deals",
    "deal.delete": "deals",
    "customer.create": "customers",
    "customer.update": "customers",
    "crm_task.create": "crm",
    "crm_task.update": "crm",
    "admin.staff_access.update": "team",
}

_MESSAGE_BY_FEATURE = {
    "photos": "Staff photo action",
    "inventory": "Staff inventory action",
    "leads": "Staff lead action",
    "documents": "Staff document action",
    "email": "Staff email action",
    "deals": "Staff deal action",
    "customers": "Staff customer action",
    "crm": "Staff CRM action",
    "team": "Staff team access action",
}

_TARGET_TYPES = frozenset(
    {
        "deal",
        "customer",
        "document",
        "inventory",
        "lead",
        "crm_task",
        "email",
        "session",
    }
)

_COUNT_KEYS = frozenset(
    {
        "field_count",
        "page_count",
        "template_count",
        "item_count",
        "imported",
        "updated",
        "error_count",
        "uploaded_count",
        "unattempted_count",
        "photo_count",
        "subject_len",
        "message_len",
        "ttl_hours",
    }
)
_BOOL_KEYS = frozenset(
    {
        "merge",
        "legacy_endpoint",
        "hard",
        "cleared",
        "changed",
        "delivered",
    }
)
_TOKEN_KEYS = frozenset(
    {
        "mode",
        "trigger",
        "kind",
        "triage_label",
        "status_from",
        "status_to",
        "from_status",
        "to_status",
        "staff_action",
        "priority",
        "doc_type",
        "delivery",
        "deal_id",
        "matterport_id",
        "email_type",
        "submission_id",
        "bucket",
    }
)
_LABEL_KEYS = frozenset({"template_name", "packet_name", "model_name"})
_LIST_KEYS = frozenset(
    {
        "fields",
        "documents_included",
        "override_keys",
        "submission_ids",
    }
)

_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_ ./:-]{0,79}$")
_ACTOR = re.compile(r"^(admin|email|partner|system)(:[A-Za-z0-9_.:-]{1,40})?$")
_TARGET_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_DUMP_MARKERS = ("traceback", "exception", "request body", "password", "pin=")


def _contact_or_dump(value: str) -> bool:
    if _EMAIL.search(value) or _PHONE.search(value) or _SSN.search(value):
        return True
    lowered = value.lower()
    return any(marker in lowered for marker in _DUMP_MARKERS)


def _clean_token(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not _TOKEN.fullmatch(text) or _contact_or_dump(text):
        return None
    return text


def _clean_label(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not _LABEL.fullmatch(text) or _contact_or_dump(text):
        return None
    return text


def _clean_count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0 or value > 1_000_000:
        return None
    return value


def _clean_list(value: Any) -> list[str] | None:
    if not isinstance(value, list | tuple):
        return None
    cleaned: list[str] = []
    for item in value[:30]:
        token = _clean_label(item)
        if token is not None:
            cleaned.append(token)
    return cleaned or None


def _allowlisted_details(details: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(details, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in details.items():
        if not isinstance(key, str):
            continue
        name = key.strip().lower()
        if name in _COUNT_KEYS:
            count = _clean_count(value)
            if count is not None:
                cleaned[name] = count
            continue
        if name in _BOOL_KEYS:
            if isinstance(value, bool):
                cleaned[name] = value
            continue
        if name in _TOKEN_KEYS:
            token = _clean_token(value)
            if token is not None:
                cleaned[name] = token
            continue
        if name in _LABEL_KEYS:
            label = _clean_label(value)
            if label is not None:
                cleaned[name] = label
            continue
        if name in _LIST_KEYS:
            items = _clean_list(value)
            if items is not None:
                cleaned[name] = items
    return cleaned


def _outcome(explicit: str, details: dict[str, Any]) -> str:
    if details.get("delivered") is False or details.get("delivery") == "failed":
        return "error"
    normalized = str(explicit or "").strip().lower()
    if normalized in {"error", "failure"}:
        return "error"
    return "ok"


def emit_staff_feature_event(
    *,
    action: str,
    actor: str = "",
    target_type: str = "",
    target_id: str = "",
    details: dict[str, Any] | None = None,
    outcome: str = "ok",
) -> None:
    """Write one feature line, or nothing for ceremonies and unknown actions."""
    try:
        event = str(action or "").strip()
        if event in _SKIP_ACTIONS:
            return
        feature = _FEATURE_BY_ACTION.get(event)
        if feature is None:
            return
        safe_details = _allowlisted_details(details)
        # Email fingerprints and generated filenames are not a document type.
        if feature == "email":
            safe_target = ""
        else:
            candidate = str(target_id or "").strip()
            safe_target = candidate if _TARGET_ID.fullmatch(candidate) else ""
            if safe_target and (
                "/" in safe_target
                or safe_target.lower().endswith(".pdf")
                or _contact_or_dump(safe_target)
            ):
                safe_target = ""
        result = _outcome(outcome, safe_details)
        payload: dict[str, Any] = {
            "severity": "WARNING" if result == "error" else "INFO",
            "message": _MESSAGE_BY_FEATURE[feature],
            "lane": LANE,
            "service": SERVICE,
            "event": event,
            "feature": feature,
            "outcome": result,
        }
        actor_ref = str(actor or "").strip()
        if _ACTOR.fullmatch(actor_ref) and "@" not in actor_ref:
            payload["actor_ref"] = actor_ref
        kind = str(target_type or "").strip()
        if kind in _TARGET_TYPES:
            payload["target_type"] = kind
        if safe_target and feature != "email":
            payload["target_id"] = safe_target
        payload.update(safe_details)
        sys.stdout.write(json.dumps(payload, separators=(",", ":"), ensure_ascii=True) + "\n")
        sys.stdout.flush()
    except Exception:
        return
