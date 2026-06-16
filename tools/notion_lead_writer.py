"""Mirror a website-captured lead into the Notion Lead Pipeline (the staff CRM).

Server-side only — called from ``POST /api/contact`` AFTER the lead is saved to
Firestore (the system of record). It is never reachable from a public/partner
route or the browser. The Lead Pipeline is the salespeople's own contact CRM
(it has Email/Phone columns), so mirroring name/email/phone there is appropriate
— this is the only PII the integration writes, and only into the client's own
internal system, not anywhere public.

Safety properties:
- **Gated.** No-op unless ``NOTION_LEAD_SYNC`` is on AND the token + Lead Pipeline
  db id are configured. Default OFF.
- **Fire-and-forget.** Any failure logs a warning and is swallowed so it never
  affects the visitor's response (mirrors the existing ``lead_storage_failed``).
- **Schema-exact.** Targets the live property names inspected 2026-06-16 — incl.
  the trailing-space ``"Email "`` column — and writes only the clean
  ``"Phone Number"`` (phone_number), deliberately avoiding the two dirty
  duplicate phone columns (one text, one mistyped as a date). It does NOT write
  to ``"SSN / Tax ID"`` or any other sensitive column.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

import httpx

from structured_logging import logger as struct_logger
from tools.notion_client import _NOTION_API, _NOTION_VERSION, _TIMEOUT_SECONDS, _db_id, _token

log = logging.getLogger(__name__)

# EXACT live Lead Pipeline property names. "Email " has a real trailing space;
# we write only the clean phone_number column and skip the dirty duplicates.
_P_NAME = "Customer Name"   # title
_P_EMAIL = "Email "         # email  — trailing space is intentional (live schema)
_P_PHONE = "Phone Number"   # phone_number (NOT "Phone number " / "phone number ")
_P_STAGE = "Pipeline Stage"  # select
_P_NOTES = "Notes"          # rich_text — holds the source/date stamp + message
_NEW_LEAD_STAGE = "New Lead"


def is_lead_sync_enabled() -> bool:
    """True only when NOTION_LEAD_SYNC is on AND token + Lead Pipeline db id exist."""
    flag = (os.getenv("NOTION_LEAD_SYNC", "off") or "off").strip().lower() in {
        "on", "true", "1", "yes",
    }
    return flag and bool(_token() and _db_id("lead_pipeline"))


def build_lead_properties(
    name, *, email=None, phone=None, message=None, source="website", lead_id=None
) -> dict:
    """Build the Notion ``properties`` payload (pure; unit-tested for PII-safety)."""
    stamp = f"Website lead · {datetime.now(UTC).date().isoformat()} · source={source}"
    if lead_id:
        # The Firestore lead_id is the cross-system join key (app stays SoR for
        # the raw lead); stamping it makes the Notion row traceable back.
        stamp += f" · ref={lead_id}"
    note = stamp if not message else f"{stamp}\n\n{str(message)[:1500]}"
    props: dict = {
        _P_NAME: {"title": [{"text": {"content": (name or "Website Lead")[:200]}}]},
        _P_STAGE: {"select": {"name": _NEW_LEAD_STAGE}},
        _P_NOTES: {"rich_text": [{"text": {"content": note}}]},
    }
    if email:
        props[_P_EMAIL] = {"email": str(email)[:200]}
    if phone:
        props[_P_PHONE] = {"phone_number": str(phone)[:50]}
    return props


def write_lead(name, *, email=None, phone=None, message=None, source="website", lead_id=None) -> bool:
    """Create a Lead Pipeline row for a website lead. Never raises.

    Returns True on a 2xx create, False if disabled or on any error (logged as
    ``notion_lead_sync_failed`` so a Cloud Run log alert can fire).
    """
    if not is_lead_sync_enabled():
        return False
    payload = {
        "parent": {"database_id": _db_id("lead_pipeline")},
        "properties": build_lead_properties(
            name, email=email, phone=phone, message=message, source=source, lead_id=lead_id
        ),
    }
    headers = {
        "Authorization": f"Bearer {_token()}",
        "Notion-Version": _NOTION_VERSION,
        "Content-Type": "application/json",
    }
    try:
        resp = httpx.post(
            f"{_NOTION_API}/pages", headers=headers, json=payload, timeout=_TIMEOUT_SECONDS
        )
        resp.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001 — fire-and-forget; must never reach the visitor
        struct_logger.warning(
            "notion lead sync failed", event="notion_lead_sync_failed", error=str(e)
        )
        return False
