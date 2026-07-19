"""
Lead Nurture — Stale-Lead Re-Engagement Module.

Surfaces customers in ``LEAD`` status who have had no recorded activity for
``days_inactive`` days and sends them a single re-engagement email so they
don't quietly go cold.

Today's outbound email is event-driven (deal status, lead welcome, appointment
confirmation). This module adds the first non-event re-engagement loop.
It is **manual-trigger only** for now — an admin endpoint posts to it with
``dry_run=true`` first to preview the cohort, then ``dry_run=false`` to send.
A scheduled (Cloud Scheduler) cron is intentionally NOT wired in this PR;
that's a follow-up after ops approves the copy.

Field convention:
  Customer docs in Firestore use ``updated_at`` to track the last touch
  (see ``database/firestore_client.py``). We treat any of these (in priority
  order) as the "last contact" timestamp:
    1. ``last_contact_at`` (preferred; future schema)
    2. ``updated_at``      (current schema)
    3. ``created_at``      (fallback for never-updated leads)

Constraints (per parallel-work agreement with feat/email-doc-delivery):
  * We do NOT modify ``email_service.py``.
  * We import ``email_service.send_custom_email`` as a consumer only.
"""

from __future__ import annotations

import html as html_mod
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from database.rpc_timeout import FIRESTORE_RPC_TIMEOUT

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────

CUSTOMERS_COLLECTION = "customers"
NURTURE_LOG_COLLECTION = "nurture_log"

LEAD_STATUS = "LEAD"

# Cap on how many leads a single batch will email. Protects against runaway
# Resend bills if Firestore returns far more stale leads than expected.
DEFAULT_MAX_RESULTS = 50
MAX_RESULTS_HARD_CAP = 500

# CTA destination for re-engagement clicks. Lives in lead_nurture so the
# email_service template path doesn't need a new helper.
NURTURE_CTA_BASE = os.environ.get(
    "LEAD_NURTURE_CTA_URL",
    "https://www.texashomeoutlet.com",
)


# ── Lazy Firestore client (mirrors email_service.py / audit_log.py) ────────

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
            logger.warning("Lead nurture Firestore client unavailable: %s", exc)
            _firestore_client = None
    return _firestore_client


def _reset_client_for_tests() -> None:
    """Test hook: drop the cached client so tests can swap in a fake."""
    global _firestore_client
    _firestore_client = None


# ── Helpers ────────────────────────────────────────────────────────────────


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _coerce_dt(value: Any) -> datetime | None:
    """Best-effort coerce a Firestore timestamp / ISO string / datetime to UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        # Tolerate trailing "Z" and missing tzinfo.
        s = value.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    # Firestore Timestamp objects expose ``.isoformat()`` / ``__str__``.
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        try:
            return _coerce_dt(iso())
        except Exception:
            return None
    return None


def _last_contact(customer: dict) -> datetime | None:
    """Pick the best available activity timestamp for a customer."""
    for key in ("last_contact_at", "updated_at", "created_at"):
        dt = _coerce_dt(customer.get(key))
        if dt is not None:
            return dt
    return None


def _first_name(full_name: str | None) -> str:
    if not full_name:
        return "Friend"
    parts = full_name.strip().split()
    return parts[0] if parts else "Friend"


def _utm_url(base: str, utm_campaign: str = "stale_14d") -> str:
    """Append re-engagement UTM parameters to the CTA URL."""
    sep = "&" if "?" in base else "?"
    params = f"utm_source=lead_nurture" f"&utm_medium=email" f"&utm_campaign={quote(utm_campaign)}"
    return f"{base}{sep}{params}"


# ── Public API ─────────────────────────────────────────────────────────────


def find_stale_leads(
    days_inactive: int = 14,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> list[dict]:
    """Return customers in LEAD status with no activity for ``days_inactive`` days.

    Filters applied:
      * ``status == "LEAD"``
      * email is set and looks like an email
      * last_contact (last_contact_at | updated_at | created_at) older than
        the threshold

    Returns at most ``max_results`` records (clamped to ``MAX_RESULTS_HARD_CAP``)
    sorted by oldest-contact first.
    """
    if days_inactive < 0:
        days_inactive = 0
    try:
        max_results = int(max_results)
    except (TypeError, ValueError):
        max_results = DEFAULT_MAX_RESULTS
    max_results = max(1, min(max_results, MAX_RESULTS_HARD_CAP))

    db = _get_db()
    if db is None:
        logger.warning("find_stale_leads: no Firestore client available")
        return []

    threshold = _now_utc() - timedelta(days=days_inactive)
    now = _now_utc()

    try:
        # Filter to LEAD status server-side. Activity-time comparisons are done
        # client-side because the timestamp field varies (last_contact_at /
        # updated_at / created_at) and may be a Firestore Timestamp or ISO str.
        query = db.collection(CUSTOMERS_COLLECTION).where("status", "==", LEAD_STATUS)
        # Pull a generous window so we can sort + cap locally.
        scan_limit = max(max_results * 4, 200)
        try:
            stream = query.limit(scan_limit).stream(timeout=FIRESTORE_RPC_TIMEOUT)
        except TypeError:
            # Some fakes don't support ``limit`` chaining — fall back.
            stream = query.stream(timeout=FIRESTORE_RPC_TIMEOUT)

        candidates: list[tuple[datetime, dict]] = []
        for doc in stream:
            data = doc.to_dict() or {}
            customer_id = data.get("id") or getattr(doc, "id", "")
            email = (data.get("email") or "").strip()
            if "@" not in email:
                continue
            last = _last_contact(data)
            if last is None:
                # No timestamp at all — treat as max-stale so it surfaces.
                last = datetime.min.replace(tzinfo=UTC)
            if last > threshold:
                continue

            days = max(0, (now - last).days)
            candidates.append(
                (
                    last,
                    {
                        "customer_id": customer_id,
                        "name": data.get("full_name") or "",
                        "email": email,
                        "last_contact_at": last.isoformat() if last.year > 1 else None,
                        "days_inactive": days,
                    },
                )
            )

        candidates.sort(key=lambda pair: pair[0])  # oldest first
        return [item for _, item in candidates[:max_results]]
    except Exception as exc:
        logger.warning("find_stale_leads failed: %s", exc)
        return []


def render_nurture_email(customer: dict) -> tuple[str, str]:
    """Return ``(subject, html_body)`` for the re-engagement email.

    Body matches the visual style of ``email_service._base_wrapper`` but is
    self-contained here so we don't have to add a helper to ``email_service.py``
    while the parallel email branch is in flight.
    """
    raw_first = _first_name(customer.get("name"))
    first_name = html_mod.escape(raw_first)

    subject = f"Still thinking about your new home, {raw_first}?"

    days_inactive = int(customer.get("days_inactive") or 0)
    days_phrase = (
        f"It's been about {days_inactive} days since we last connected"
        if days_inactive
        else "We wanted to circle back"
    )

    cta_url = _utm_url(NURTURE_CTA_BASE)
    cta_url_html = html_mod.escape(cta_url, quote=True)

    from config_loader import business_address as _cfg_business_address
    from config_loader import business_phone as _cfg_business_phone

    business_phone = _cfg_business_phone()
    business_address = _cfg_business_address()

    html_body = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; background: #ffffff;">
      <div style="background: #1e3a5f; padding: 24px; text-align: center;">
        <h1 style="color: #ffffff; margin: 0; font-size: 24px;">Texas Home Outlet</h1>
        <p style="color: #93c5fd; margin: 4px 0 0; font-size: 13px;">Your Dream Home Starts Here</p>
      </div>
      <div style="padding: 32px 24px;">
        <h2 style="color: #1e3a5f; margin-top: 0;">Still thinking about it, {first_name}?</h2>
        <p>Hi {first_name},</p>
        <p>{days_phrase}, and we wanted to make sure you didn't miss anything. New homes come on the lot every week — including some great pre-owned options under $50k.</p>

        <div style="background: #eff6ff; border-left: 4px solid #3b82f6; padding: 16px; margin: 20px 0; border-radius: 0 8px 8px 0;">
          <p style="margin: 0 0 8px; font-weight: 600; color: #1e3a5f;">A few easy ways to take the next step</p>
          <p style="margin: 4px 0; color: #374151;">Browse our latest inventory online</p>
          <p style="margin: 4px 0; color: #374151;">Reply to this email with what you're looking for</p>
          <p style="margin: 4px 0; color: #374151;">Or just give us a call — we're happy to help</p>
        </div>

        <p style="margin-top: 24px;">
          <a href="{cta_url_html}" style="display: inline-block; background: #3b82f6; color: white; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: 600;">
            See what's new
          </a>
        </p>

        <p style="color: #6b7280; font-size: 13px; margin-top: 20px;">
          If you'd rather not hear from us again, just reply with "unsubscribe" and we'll take you off the list.
        </p>
      </div>
      <div style="background: #f3f4f6; padding: 20px 24px; text-align: center; border-top: 1px solid #e5e7eb;">
        <p style="margin: 0; color: #6b7280; font-size: 12px;">
          Texas Home Outlet &bull; {business_address}<br>
          {business_phone} &bull; texashomeoutlet.com
        </p>
      </div>
    </div>
    """.strip()

    return subject, html_body


def _log_nurture_send(
    customer_id: str,
    email: str,
    subject: str,
    send_result: dict | None,
) -> None:
    """Persist a record of a re-engagement send to ``nurture_log``."""
    db = _get_db()
    if db is None:
        return
    try:
        db.collection(NURTURE_LOG_COLLECTION).add(
            {
                "customer_id": customer_id,
                "email": email,
                "subject": subject,
                "sent_at": _now_utc().isoformat(),
                "send_success": bool((send_result or {}).get("success")),
                "send_message_id": (send_result or {}).get("message_id") or "",
            },
            timeout=FIRESTORE_RPC_TIMEOUT,
        )
    except Exception as exc:
        logger.warning("nurture_log write failed customer_id=%s error=%s", customer_id, exc)


def run_nurture_batch(
    dry_run: bool = True,
    days_inactive: int = 14,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> dict:
    """Find stale leads and (optionally) send re-engagement emails.

    Args:
        dry_run: If True (default), no emails are sent and no nurture_log
            entries are written. The cohort + a redacted preview are returned
            so an operator can sanity-check before flipping the switch.
        days_inactive: Threshold in days. Customers whose last activity is
            older than this and who have email set will be included.
        max_results: Hard cap on cohort size. Defaults to 50.

    Returns:
        ``{
            "dry_run": bool,
            "days_inactive": int,
            "cohort_size": int,
            "previews": [ {customer_id, name, email, days_inactive}, ... ],
            "sent": int,        # 0 when dry_run
            "failed": int,      # 0 when dry_run
        }``
    """
    leads = find_stale_leads(days_inactive=days_inactive, max_results=max_results)

    previews = [
        {
            "customer_id": lead["customer_id"],
            "name": lead.get("name") or "",
            "email": lead["email"],
            "days_inactive": lead.get("days_inactive") or 0,
        }
        for lead in leads[:10]  # preview at most 10 to keep the response small
    ]

    if dry_run:
        return {
            "dry_run": True,
            "days_inactive": days_inactive,
            "cohort_size": len(leads),
            "previews": previews,
            "sent": 0,
            "failed": 0,
        }

    # Non-dry-run path: import the email transport here so dry_run stays cheap
    # and so we don't import email_service at module-load (keeps tests light).
    from email_service import send_custom_email

    sent = 0
    failed = 0
    for lead in leads:
        subject, _html_body = render_nurture_email(lead)
        try:
            result = send_custom_email(
                to=lead["email"],
                customer_name=lead.get("name") or "",
                subject=subject,
                # send_custom_email wraps a plain-text "message" in its own
                # branded HTML. Our re-engagement copy is short so we ship the
                # text body and let send_custom_email handle escaping/wrapping.
                # Keeping the templating here means we don't add a new
                # template helper to email_service.py mid-merge.
                message=_nurture_plaintext(lead),
            )
        except Exception as exc:
            logger.warning(
                "send_custom_email raised for customer_id=%s error=%s",
                lead.get("customer_id"),
                exc,
            )
            result = {"success": False, "error": str(exc)}

        if (result or {}).get("success"):
            sent += 1
        else:
            failed += 1

        _log_nurture_send(
            customer_id=lead.get("customer_id") or "",
            email=lead["email"],
            subject=subject,
            send_result=result,
        )

    return {
        "dry_run": False,
        "days_inactive": days_inactive,
        "cohort_size": len(leads),
        "previews": previews,
        "sent": sent,
        "failed": failed,
    }


def _nurture_plaintext(customer: dict) -> str:
    """Plain-text re-engagement message used with ``send_custom_email``.

    ``send_custom_email`` does its own HTML-escaping and inserts a "Hi
    {first_name}," opener, so we deliberately skip both here.
    """
    days_inactive = int(customer.get("days_inactive") or 0)
    days_phrase = (
        f"It's been about {days_inactive} days since we last connected"
        if days_inactive
        else "We wanted to circle back"
    )
    cta_url = _utm_url(NURTURE_CTA_BASE)
    return (
        f"{days_phrase}, and we wanted to make sure you didn't miss anything. "
        "New homes come on the lot every week — including some great pre-owned "
        "options under $50k.\n\n"
        "A few easy ways to take the next step:\n"
        "- Browse our latest inventory online\n"
        "- Reply to this email with what you're looking for\n"
        "- Or just give us a call — we're happy to help\n\n"
        f"See what's new: {cta_url}\n\n"
        "If you'd rather not hear from us again, just reply with "
        '"unsubscribe" and we\'ll take you off the list.'
    )
