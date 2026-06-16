"""Env-gated Notion source for the Mira bridge endpoints.

The Mira bridge (`/api/v1/mira/installations/*`, `/api/v1/mira/feedback/*`)
defaults to reading aggregated, PII-redacted operational data from Firestore.
This module lets an operator point those same endpoints at Notion instead — a
"Delivery Tracker" database for installations/service requests and a
"CS survey" database for customer feedback — without changing the response
schema or aggregation logic in `mira_routes.py`.

Design
------
- **Env-gated.** Nothing happens unless the relevant env vars are set, read at
  call time (not import time) so tests and runtime config flips take effect:
    * ``NOTION_TOKEN``                     — internal integration token
    * ``NOTION_DELIVERY_TRACKER_DB_ID``    — installations source
    * ``NOTION_CS_SURVEY_DB_ID``           — feedback source
- **Direct REST, no SDK.** We hit the Notion REST API with ``httpx`` (already a
  transitive dependency) rather than adding ``notion-client`` — the project
  charter is "fewer dependencies."
- **Never raises outward.** On any HTTP/parse error we log via
  ``structured_logging`` and return ``[]``. The bridge must degrade gracefully
  and never 500; the caller falls back to Firestore or returns an empty source.
- **PII-redacted by contract.** ``fetch_*`` only surfaces the same non-PII keys
  the Firestore path produces (status / issue_type / warranty flags /
  contractor / rating / sentiment / source / created_at / deal_id). Customer
  names, emails, and phones in the Notion row are simply not mapped.

Notion property normalization
-----------------------------
A Notion page's ``properties`` is a dict of ``{name: property_object}`` where
each object is tagged by ``type`` (``title``, ``rich_text``, ``select``,
``status``, ``number``, ``date``, ``checkbox``). ``_prop_value`` flattens any of
those into a plain Python scalar. Property names are matched case-insensitively
and across a few common aliases so the operator's column naming has some slack.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import httpx

from structured_logging import logger as struct_logger

_NOTION_API = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"
_TIMEOUT_SECONDS = 8.0


# ---------------------------------------------------------------------------
# Configuration (read at call time, never at import time)
# ---------------------------------------------------------------------------
def _token() -> str:
    return (os.environ.get("NOTION_TOKEN") or "").strip()


def _delivery_tracker_db_id() -> str:
    return (os.environ.get("NOTION_DELIVERY_TRACKER_DB_ID") or "").strip()


def _cs_survey_db_id() -> str:
    return (os.environ.get("NOTION_CS_SURVEY_DB_ID") or "").strip()


# --- Command Center reader (config-driven, GCP-native, Mira-free) -------------
# Logical DB keys -> env-var fallback. DB ids are business config, NOT secrets,
# so they may live in config.yaml `notion.databases.<key>`; only NOTION_TOKEN is
# a credential. This is what lets the Ops Copilot read the staff's whole ops hub
# (replacing the Mira bridge) instead of just the two hard-coded DBs.
_DB_KEY_ENV = {
    "delivery_tracker": "NOTION_DELIVERY_TRACKER_DB_ID",
    "cs_survey": "NOTION_CS_SURVEY_DB_ID",
    "service_warranty": "NOTION_SERVICE_WARRANTY_DB_ID",
    "title": "NOTION_TITLE_DB_ID",
    "collections": "NOTION_COLLECTIONS_DB_ID",
    "insurance": "NOTION_INSURANCE_DB_ID",
    "team_tasks": "NOTION_TEAM_TASKS_DB_ID",
    "lead_pipeline": "NOTION_LEAD_PIPELINE_DB_ID",
}


def _config_db_id(db_key: str) -> str:
    """Notion DB id from config.yaml `notion.databases.<key>`, or '' (never raises)."""
    try:
        from config_loader import get_config

        dbs = ((get_config() or {}).get("notion") or {}).get("databases") or {}
        return str(dbs.get(db_key) or "").strip()
    except Exception:  # noqa: BLE001 — config block is optional; degrade to env
        return ""


def _db_id(db_key: str) -> str:
    """Resolve a Command Center DB id: config.yaml first, then env fallback."""
    cfg = _config_db_id(db_key)
    if cfg:
        return cfg
    env_name = _DB_KEY_ENV.get(db_key)
    return (os.environ.get(env_name) or "").strip() if env_name else ""


def is_command_center_enabled() -> bool:
    """Master flag for the Notion Command Center reader (default OFF).

    The reader ships dark; the Ops Copilot (the consumer) checks this before
    sourcing from Notion, so the whole feature flips on/off with one env var and
    reverts with no redeploy — same pattern as INVENTORY_SOURCE.
    """
    return (os.getenv("NOTION_COMMAND_CENTER", "off") or "off").strip().lower() in {
        "on", "true", "1", "yes",
    }


def is_installations_configured() -> bool:
    """True when both a token and the Delivery Tracker DB id are set."""
    return bool(_token() and _delivery_tracker_db_id())


def is_feedback_configured() -> bool:
    """True when both a token and the CS survey DB id are set."""
    return bool(_token() and _cs_survey_db_id())


# ---------------------------------------------------------------------------
# Notion property → flat scalar normalization
# ---------------------------------------------------------------------------
def _rich_text_plain(blocks: Any) -> str | None:
    """Join the ``plain_text`` of a Notion rich_text / title array."""
    if not isinstance(blocks, list):
        return None
    parts = [b.get("plain_text", "") for b in blocks if isinstance(b, dict)]
    text = "".join(parts).strip()
    return text or None


def _prop_value(prop: Any) -> Any:
    """Flatten a single Notion property object into a plain Python scalar.

    Returns ``None`` for empty/unsupported properties so callers can apply
    their own defaults (mirroring the Firestore ``data.get(...)`` behavior).
    """
    if not isinstance(prop, dict):
        return None
    ptype = prop.get("type")
    if ptype == "title":
        return _rich_text_plain(prop.get("title"))
    if ptype == "rich_text":
        return _rich_text_plain(prop.get("rich_text"))
    if ptype == "select":
        sel = prop.get("select")
        return sel.get("name") if isinstance(sel, dict) else None
    if ptype == "status":
        st = prop.get("status")
        return st.get("name") if isinstance(st, dict) else None
    if ptype == "number":
        return prop.get("number")
    if ptype == "checkbox":
        return bool(prop.get("checkbox"))
    if ptype == "date":
        d = prop.get("date")
        return d.get("start") if isinstance(d, dict) else None
    if ptype == "url":
        return prop.get("url")
    if ptype == "email":
        return prop.get("email")
    if ptype == "phone_number":
        return prop.get("phone_number")
    return None


def _find_prop(props: dict[str, Any], *names: str) -> Any:
    """Case-insensitive lookup of the first matching property, flattened.

    ``names`` is a list of acceptable column names / aliases. The first one
    present (case-insensitively) wins.
    """
    if not isinstance(props, dict):
        return None
    # Strip + lowercase so dirty Notion column names (trailing spaces, casing,
    # duplicates) still match. On a duplicate-after-normalization, the last wins.
    lowered = {k.strip().lower(): v for k, v in props.items() if isinstance(k, str)}
    for name in names:
        key = name.strip().lower()
        if key in lowered:
            value = _prop_value(lowered[key])
            if value is not None:
                return value
    return None


def _normalize_created_at(raw: Any, fallback: str | None) -> str:
    """Coerce a Notion date/created_time string to an ISO-8601 UTC string.

    Mirrors ``mira_routes._parse_timestamp`` output: a tz-aware ISO string.
    Falls back to the page's ``created_time`` and finally to ``now``.
    """
    candidate = raw if isinstance(raw, str) and raw.strip() else fallback
    if isinstance(candidate, str) and candidate.strip():
        s = candidate.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC).isoformat()
        except ValueError:
            pass
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _query_database(db_id: str, limit: int) -> list[dict[str, Any]]:
    """POST /databases/{db_id}/query and return the raw ``results`` list.

    Never raises: on any error logs and returns ``[]``.
    """
    token = _token()
    if not token or not db_id:
        return []
    url = f"{_NOTION_API}/databases/{db_id}/query"
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": _NOTION_VERSION,
        "Content-Type": "application/json",
    }
    # Notion caps page_size at 100; clamp the requested limit into [1, 100].
    page_size = max(1, min(int(limit), 100))
    payload = {"page_size": page_size}
    try:
        resp = httpx.post(url, headers=headers, json=payload, timeout=_TIMEOUT_SECONDS)
        resp.raise_for_status()
        body = resp.json()
        results = body.get("results", [])
        return results if isinstance(results, list) else []
    except Exception as e:  # noqa: BLE001 — graceful degradation by contract
        struct_logger.error("notion query failed", db_id=db_id, error=str(e))
        return []


# ---------------------------------------------------------------------------
# Public fetchers — return Firestore-shaped flat dicts
# ---------------------------------------------------------------------------
def fetch_installations(limit: int = 50) -> list[dict[str, Any]]:
    """Fetch installation/service-request rows from the Notion Delivery Tracker.

    Returns flat dicts with the SAME keys the Firestore path produces in
    ``mira_routes.mira_installations_recent``::

        id, status, issue_type, is_warranty_claim, warranty_status,
        assigned_contractor, created_at (ISO str), deal_id (optional)

    Customer PII columns are intentionally not mapped. Returns ``[]`` on error
    or when unconfigured.
    """
    if not is_installations_configured():
        return []
    rows: list[dict[str, Any]] = []
    for page in _query_database(_delivery_tracker_db_id(), limit):
        if not isinstance(page, dict):
            continue
        props = page.get("properties", {})
        created_at = _normalize_created_at(
            _find_prop(props, "created_at", "Created At", "Created", "Date"),
            page.get("created_time"),
        )
        item: dict[str, Any] = {
            "id": page.get("id", ""),
            "status": _find_prop(props, "status", "Status") or "UNKNOWN",
            "issue_type": _find_prop(props, "issue_type", "Issue Type", "Type") or "UNKNOWN",
            "is_warranty_claim": bool(
                _find_prop(props, "is_warranty_claim", "Warranty Claim", "Is Warranty Claim")
            ),
            "warranty_status": _find_prop(props, "warranty_status", "Warranty Status"),
            "assigned_contractor": _find_prop(
                props, "assigned_contractor", "Assigned Contractor", "Contractor"
            ),
            "created_at": created_at,
        }
        deal_id = _find_prop(props, "deal_id", "Deal ID", "Deal")
        if deal_id:
            item["deal_id"] = deal_id
        rows.append(item)
    return rows


def fetch_feedback(limit: int = 50) -> list[dict[str, Any]]:
    """Fetch customer-feedback rows from the Notion CS survey database.

    Returns flat dicts with the SAME keys the Firestore path produces in
    ``mira_routes.mira_feedback_recent``::

        id, rating, sentiment, source, created_at (ISO str), deal_id (optional)

    Free-text comments and customer PII columns are intentionally not mapped.
    Returns ``[]`` on error or when unconfigured.
    """
    if not is_feedback_configured():
        return []
    rows: list[dict[str, Any]] = []
    for page in _query_database(_cs_survey_db_id(), limit):
        if not isinstance(page, dict):
            continue
        props = page.get("properties", {})
        created_at = _normalize_created_at(
            _find_prop(props, "created_at", "Created At", "Created", "Date"),
            page.get("created_time"),
        )
        rating = _find_prop(props, "rating", "Rating", "Score")
        item: dict[str, Any] = {
            "id": page.get("id", ""),
            "rating": rating,
            "sentiment": _find_prop(props, "sentiment", "Sentiment"),
            "source": _find_prop(props, "source", "Source") or "UNKNOWN",
            "created_at": created_at,
        }
        deal_id = _find_prop(props, "deal_id", "Deal ID", "Deal")
        if deal_id:
            item["deal_id"] = deal_id
        rows.append(item)
    return rows


# Status/phase column names across the Command Center DBs (Title='Status',
# Delivery='Current Phase', Collections/Insurance='Payment Status',
# CS-survey='Outreach Status', Lead Pipeline='Pipeline Stage', ...). _find_prop
# is case-insensitive and tolerates trailing-space/duplicate column names.
_STATUS_ALIASES = (
    "status",
    "Current Phase",
    "Phase",
    "Pipeline Stage",
    "Payment Status",
    "Outreach Status",
    "Outreach Stage",
)


def fetch_status_counts(
    db_key: str, *, limit: int = 100, status_aliases: tuple[str, ...] = _STATUS_ALIASES
) -> dict[str, int]:
    """Count rows by their status/phase in a Command Center DB. PII-free by construction.

    This is the generic, GCP-native reader the Ops Copilot uses to answer
    "where is my delivery / title / service / collections" from the staff's real
    system. It reads ONLY the status-like column (via case-insensitive alias
    matching) and never any other property — so no customer identity, dollar
    amount, or free-text comment can enter the result, regardless of how the
    operator names or adds columns. Returns ``{status_value: count}``; ``{}`` when
    unconfigured (no token / no db id) or on any error (the underlying query
    never raises).
    """
    db_id = _db_id(db_key)
    if not _token() or not db_id:
        return {}
    counts: dict[str, int] = {}
    for page in _query_database(db_id, limit):
        if not isinstance(page, dict):
            continue
        status = _find_prop(page.get("properties", {}), *status_aliases) or "UNKNOWN"
        key = str(status)
        counts[key] = counts.get(key, 0) + 1
    return counts
