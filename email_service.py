"""
Email Service for Texas Home Outlet — Resend Integration

Plug-and-play email delivery. Sender domain comes from the RESEND_FROM env
var; default points at the verified `noreply@texashomeoutlet.com` alias. The
domain MUST be verified in the Resend dashboard (DKIM/SPF/DMARC) before
deliverability is reliable.

Setup:
  1. Sign up at resend.com → get API key
  2. Set env var: RESEND_API_KEY=re_xxxxx
  3. Verify texashomeoutlet.com in Resend dashboard, then set:
        RESEND_FROM="Texas Home Outlet <noreply@texashomeoutlet.com>"
"""

import html as html_mod
import logging
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

TIMEZONE = ZoneInfo("America/Chicago")

# ── Config ──────────────────────────────────────────────────────
# Default points at the verified Texas Home Outlet domain. The domain must be
# verified in Resend (DKIM/SPF/DMARC) before send calls succeed in volume.
# Override with RESEND_FROM env var if a different alias is preferred.
RESEND_FROM = os.environ.get(
    "RESEND_FROM",
    "Texas Home Outlet <noreply@texashomeoutlet.com>",
)

# Hardening: maximum number of recipients allowed in a single send. This caps
# blast radius if a caller accidentally passes a huge staff/customer list.
MAX_RECIPIENTS = int(os.environ.get("EMAIL_MAX_RECIPIENTS", "50"))

# Loose but practical RFC 5322-ish regex. It rejects obvious garbage (missing
# @, no domain, invalid chars) while allowing the formats THO actually uses.
_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
)


def _sanitize_header_value(value: str) -> str:
    """Strip CR/LF to prevent SMTP header-injection via subject or addresses."""
    if value is None:
        return ""
    return str(value).replace("\r", "").replace("\n", "")


def _validate_email_address(addr: str) -> bool:
    """Return True if ``addr`` looks like a safe, deliverable email address."""
    if not addr:
        return False
    # Header-injection guard: reject addresses containing newlines outright.
    if "\r" in addr or "\n" in addr:
        return False
    local, _, domain = addr.rpartition("@")
    if not local or not domain:
        return False
    if "." not in domain:
        return False
    # Reject consecutive dots and trailing dot in domain.
    if ".." in domain or domain.endswith("."):
        return False
    return bool(_EMAIL_RE.match(addr))


def _extract_address(from_value: str) -> str:
    """Extract the bare email from a 'Display Name <addr>' style From value."""
    if not from_value:
        return ""
    from_value = from_value.strip()
    if from_value.endswith(">") and "<" in from_value:
        return from_value.split("<")[-1].rstrip(">").strip()
    return from_value


def _current_api_key() -> str:
    """Read RESEND_API_KEY at call time so env-var patches in tests are respected."""
    return os.environ.get("RESEND_API_KEY", "")


def _parse_recipients(raw: str) -> list:
    """Split a comma/semicolon-separated recipient string into a clean,
    de-duplicated list (order preserved, case-insensitive dedupe)."""
    if not raw:
        return []
    seen: set = set()
    out: list = []
    for part in raw.replace(";", ",").split(","):
        addr = part.strip()
        if addr and addr.lower() not in seen:
            seen.add(addr.lower())
            out.append(addr)
    return out


# Internal staff alert recipients (new leads + new appointments). These fan out
# to the whole THO team so no lead is missed — the old single aribspector@gmail.com
# fallback was a deleted account, so alerts were silently lost. Override with the
# NOTIFICATION_EMAIL env var as a comma-separated list (one or many addresses).
NOTIFICATION_EMAILS = _parse_recipients(
    os.environ.get(
        "NOTIFICATION_EMAIL",
        "ben@texashomeoutlet.com,lee@texashomeoutlet.com,"
        "celeste@texashomeoutlet.com,mark@texashomeoutlet.com",
    )
)
# Back-compat string view (joined) for any caller still referencing the scalar.
NOTIFICATION_EMAIL = ",".join(NOTIFICATION_EMAILS)
# Customer-facing emails say "reply to this email"; route those replies to a
# single monitored shared mailbox (not every staff inbox) and never the
# unattended noreply@ From address.
REPLY_TO = os.environ.get("REPLY_TO", "sales@texashomeoutlet.com")
# Business contact details come from config.yaml (single source of truth); a
# guard test enforces that these agree across modules.
from config_loader import business_address as _cfg_business_address  # noqa: E402
from config_loader import business_phone as _cfg_business_phone  # noqa: E402

BUSINESS_PHONE = _cfg_business_phone()
BUSINESS_ADDRESS = _cfg_business_address()
# Public site origin used to build absolute download URLs in document emails.
# Falls back to the empty string so callers must pass an already-resolved URL.
PUBLIC_SITE_URL = os.environ.get("PUBLIC_SITE_URL", "").rstrip("/")

# Activity log — stored in Firestore for CRM timeline
_firestore_client = None


def _get_db():
    global _firestore_client
    if _firestore_client is None:
        try:
            from google.cloud import firestore

            project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "tho-ai-agent")
            _firestore_client = firestore.Client(project=project_id)
        except Exception as e:
            logger.warning(f"Firestore not available for email logging: {e}")
    return _firestore_client


def _log_email_activity(to: str, subject: str, email_type: str, related_id: str = None):
    """Log email send to Firestore email_log collection for CRM activity timeline."""
    db = _get_db()
    if not db:
        return
    try:
        db.collection("email_log").add(
            {
                "to": to,
                "subject": subject,
                "email_type": email_type,
                "related_id": related_id,
                "sent_at": datetime.now(TIMEZONE).isoformat(),
                "from": _current_from(),
            }
        )
    except Exception as e:
        logger.warning(
            "Failed to log email activity (to=%s type=%s): %s", to, email_type, e
        )


# ── Send Email (core) ──────────────────────────────────────────


def _current_from() -> str:
    """Read RESEND_FROM at call time so env-var patches in tests are respected."""
    return os.environ.get("RESEND_FROM", RESEND_FROM)


def _html_to_text(html: str) -> str:
    """Cheap HTML→text fallback for clients that prefer plain text. Strips tags
    and unescapes entities. Not a perfect renderer but good enough for the
    short transactional templates we send."""
    import re

    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", html)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</(p|div|h[1-6]|li|tr)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = html_mod.unescape(text)
    # Collapse runs of blank lines.
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return text


def send_email(
    to,
    subject: str,
    html: str,
    email_type: str = "general",
    related_id: str = None,
    text: str = None,
) -> dict:
    """
    Send an email via Resend.

    Args:
        to: Recipient email address, or a list of addresses for a single send
            delivered to multiple recipients (e.g. internal staff alerts).
        subject: Email subject line
        html: HTML body content
        email_type: Type for logging (appointment_confirmation, lead_followup, etc.)
        related_id: Optional related entity ID (appointment_id, lead_id, deal_id)
        text: Optional plain-text body. If not supplied a fallback is derived
            from `html` so every send carries a multipart text alternative.

    Returns:
        dict with success status and Resend message ID or error
    """
    recipients = [to] if isinstance(to, str) else [r for r in to if r]

    # Validate and sanitize inputs before any network call or logging.
    invalid = [r for r in recipients if not _validate_email_address(r)]
    if invalid:
        return {
            "success": False,
            "error": f"Invalid recipient address(es): {', '.join(invalid[:3])}",
            "dry_run": True,
            "to": to,
            "subject": subject,
        }

    if len(recipients) > MAX_RECIPIENTS:
        return {
            "success": False,
            "error": f"Too many recipients: {len(recipients)} (max {MAX_RECIPIENTS})",
            "dry_run": True,
            "to": to,
            "subject": subject,
        }

    safe_subject = _sanitize_header_value(subject)
    if not safe_subject or not safe_subject.strip():
        return {
            "success": False,
            "error": "Subject is required",
            "dry_run": True,
            "to": to,
            "subject": subject,
        }

    sender = _current_from()
    sender_addr = _extract_address(sender)
    if sender and not _validate_email_address(sender_addr):
        return {
            "success": False,
            "error": f"Invalid sender address: {sender_addr}",
            "dry_run": True,
            "to": to,
            "subject": subject,
        }

    if REPLY_TO and not _validate_email_address(REPLY_TO):
        return {
            "success": False,
            "error": f"Invalid reply-to address: {REPLY_TO}",
            "dry_run": True,
            "to": to,
            "subject": subject,
        }

    to_display = ", ".join(recipients)
    api_key = _current_api_key()

    if not api_key:
        logger.warning("RESEND_API_KEY not set — email not sent")
        return {
            "success": False,
            "error": "Email service not configured. Set RESEND_API_KEY environment variable.",
            "dry_run": True,
            "to": to,
            "subject": subject,
        }

    try:
        import resend

        resend.api_key = api_key

        payload = {
            "from": sender,
            "to": recipients,
            "reply_to": REPLY_TO,
            "subject": safe_subject,
            "html": html,
            "text": text if text is not None else _html_to_text(html),
        }
        result = resend.Emails.send(payload)

        _log_email_activity(to_display, safe_subject, email_type, related_id)

        logger.info("Email sent: %s to %s (id: %s)", email_type, to_display, result.get("id", "unknown"))
        return {
            "success": True,
            "message_id": result.get("id"),
            "to": to,
            "subject": safe_subject,
        }

    except Exception as e:
        logger.error("Email send failed: %s", e)
        return {"success": False, "error": str(e)}


# ── Email Templates ─────────────────────────────────────────────


def _base_wrapper(content: str) -> str:
    """Wrap email content in branded HTML template."""
    return f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; background: #ffffff;">
      <div style="background: #1e3a5f; padding: 24px; text-align: center;">
        <h1 style="color: #ffffff; margin: 0; font-size: 24px;">Texas Home Outlet</h1>
        <p style="color: #93c5fd; margin: 4px 0 0; font-size: 13px;">Your Dream Home Starts Here</p>
      </div>
      <div style="padding: 32px 24px;">
        {content}
      </div>
      <div style="background: #f3f4f6; padding: 20px 24px; text-align: center; border-top: 1px solid #e5e7eb;">
        <p style="margin: 0; color: #6b7280; font-size: 12px;">
          Texas Home Outlet &bull; {BUSINESS_ADDRESS}<br>
          {BUSINESS_PHONE} &bull; texashomeoutlet.com
        </p>
      </div>
    </div>
    """


def send_appointment_confirmation(
    to: str,
    customer_name: str,
    date: str,
    time_slot: str,
    appointment_id: str = None,
    notes: str = None,
) -> dict:
    """Send appointment confirmation email."""
    first_name = html_mod.escape(customer_name.split()[0]) if customer_name else "Friend"

    content = f"""
    <h2 style="color: #1e3a5f; margin-top: 0;">Your Visit is Confirmed!</h2>
    <p>Hi {first_name},</p>
    <p>Great news — your showroom visit is all set. We're looking forward to meeting you!</p>

    <div style="background: #eff6ff; border-left: 4px solid #3b82f6; padding: 16px; margin: 20px 0; border-radius: 0 8px 8px 0;">
      <p style="margin: 0 0 8px; font-weight: 600; color: #1e3a5f;">Appointment Details</p>
      <p style="margin: 4px 0; color: #374151;">📅 <strong>{date}</strong></p>
      <p style="margin: 4px 0; color: #374151;">🕐 <strong>{time_slot}</strong></p>
      <p style="margin: 4px 0; color: #374151;">📍 {BUSINESS_ADDRESS}</p>
      {f'<p style="margin: 8px 0 0; color: #6b7280; font-size: 13px;">Note: {notes}</p>' if notes else ''}
    </div>

    <p><strong>What to bring:</strong></p>
    <ul style="color: #374151;">
      <li>Valid ID</li>
      <li>Proof of land ownership (if applicable)</li>
      <li>Any financing pre-approval letters</li>
    </ul>

    <p>When you arrive, ask for <strong>Ben or Mark</strong> — they're ready to help!</p>

    <p style="margin-top: 24px;">
      <a href="tel:+12813243020" style="display: inline-block; background: #3b82f6; color: white; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: 600;">
        Call Us: {BUSINESS_PHONE}
      </a>
    </p>

    <p style="color: #6b7280; font-size: 13px; margin-top: 20px;">
      Need to reschedule? Just reply to this email or give us a call.
    </p>
    """

    return send_email(
        to=to,
        subject="Your Texas Home Outlet Visit is Confirmed! 🏠",
        html=_base_wrapper(content),
        email_type="appointment_confirmation",
        related_id=appointment_id,
    )


def send_lead_welcome(to: str, customer_name: str, lead_id: str = None) -> dict:
    """Send welcome email to new lead who submitted contact/quote form."""
    first_name = html_mod.escape(customer_name.split()[0]) if customer_name else "Friend"

    content = f"""
    <h2 style="color: #1e3a5f; margin-top: 0;">Thanks for Reaching Out!</h2>
    <p>Hi {first_name},</p>
    <p>We received your inquiry and one of our team members will be in touch shortly — usually within a few hours during business hours.</p>

    <p>In the meantime, here are some ways to explore:</p>

    <div style="background: #f0fdf4; border-left: 4px solid #22c55e; padding: 16px; margin: 20px 0; border-radius: 0 8px 8px 0;">
      <p style="margin: 0 0 8px; font-weight: 600; color: #166534;">While You Wait</p>
      <p style="margin: 4px 0; color: #374151;">🏠 Browse our full inventory online</p>
      <p style="margin: 4px 0; color: #374151;">📱 Chat with Tex, our AI assistant, 24/7</p>
      <p style="margin: 4px 0; color: #374151;">📅 Book a showroom visit if you haven't already</p>
    </div>

    <p>We have over 40 homes to choose from — new and pre-owned — starting under $50k.</p>

    <p style="color: #6b7280; font-size: 13px; margin-top: 20px;">
      Texas Home Outlet &bull; {BUSINESS_PHONE}
    </p>
    """

    return send_email(
        to=to,
        subject=f"Thanks for contacting Texas Home Outlet, {first_name}!",
        html=_base_wrapper(content),
        email_type="lead_welcome",
        related_id=lead_id,
    )


def send_deal_status_update(
    to: str,
    customer_name: str,
    deal_id: str,
    new_status: str,
    home_name: str = None,
) -> dict:
    """Send email when a deal status changes (approved, contract, funded, etc.)."""
    first_name = html_mod.escape(customer_name.split()[0]) if customer_name else "Friend"

    status_messages = {
        "approved": {
            "emoji": "✅",
            "title": "Your Application is Approved!",
            "body": "Great news — your pre-qualification has been approved. The next step is to finalize your home selection and move to contract.",
        },
        "contract": {
            "emoji": "📝",
            "title": "Contract Ready for Review",
            "body": "Your purchase contract is ready. Please review the documents and let us know if you have any questions.",
        },
        "funded": {
            "emoji": "🎉",
            "title": "Funding Complete!",
            "body": "Congratulations! Your home financing has been funded. We're getting everything ready for delivery.",
        },
        "complete": {
            "emoji": "🏠",
            "title": "Welcome Home!",
            "body": "Your home delivery and setup is complete. Welcome to the Texas Home Outlet family!",
        },
    }

    info = status_messages.get(
        new_status,
        {
            "emoji": "📋",
            "title": f"Deal Update: {new_status.title()}",
            "body": f"Your deal status has been updated to: {new_status}.",
        },
    )

    home_line = (
        f'<p style="color: #374151; margin: 8px 0;"><strong>Home:</strong> {home_name}</p>'
        if home_name
        else ""
    )

    content = f"""
    <h2 style="color: #1e3a5f; margin-top: 0;">{info['emoji']} {info['title']}</h2>
    <p>Hi {first_name},</p>
    <p>{info['body']}</p>
    {home_line}

    <p>If you have any questions, don't hesitate to reach out.</p>

    <p style="margin-top: 24px;">
      <a href="tel:+12813243020" style="display: inline-block; background: #3b82f6; color: white; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: 600;">
        Call Us: {BUSINESS_PHONE}
      </a>
    </p>
    """

    return send_email(
        to=to,
        subject=f"{info['emoji']} {info['title']} — Texas Home Outlet",
        html=_base_wrapper(content),
        email_type="deal_status_update",
        related_id=deal_id,
    )


def send_custom_email(to: str, customer_name: str, subject: str, message: str) -> dict:
    """Send a custom one-off email from the CRM."""
    first_name = html_mod.escape(customer_name.split()[0]) if customer_name else "Friend"

    # Convert plain text line breaks to HTML (escape first to prevent XSS)
    html_message = html_mod.escape(message).replace("\n", "<br>")

    content = f"""
    <p>Hi {first_name},</p>
    <div style="color: #374151; line-height: 1.6;">{html_message}</div>
    <p style="margin-top: 24px; color: #6b7280;">
      Warm regards,<br>
      <strong>Texas Home Outlet</strong><br>
      {BUSINESS_PHONE}
    </p>
    """

    return send_email(
        to=to,
        subject=subject,
        html=_base_wrapper(content),
        email_type="custom",
    )


def _humanize_doc_type(doc_type: str) -> str:
    """Render a document type slug like `sales_contract` as `Sales Contract`."""
    if not doc_type:
        return "document"
    cleaned = doc_type.replace("_", " ").replace("-", " ").strip()
    # Title-case while preserving common short acronyms (e.g. ID, TX).
    return (
        " ".join(
            word.upper()
            if len(word) <= 3 and word.isalpha() and word.isupper()
            else word.capitalize()
            for word in cleaned.split()
        )
        or "document"
    )


def send_document_email(
    to: str,
    customer_name: str,
    doc_filename: str,
    doc_type: str,
    download_url: str,
    deal_id: str = None,
) -> dict:
    """
    Email a customer a link to a freshly generated document.

    The download URL is included as a CTA button. For now the THO download
    endpoint is admin-authenticated, so the customer experience requires a
    login or a follow-up signed-URL endpoint. This helper sends the link
    regardless — callers decide whether to invoke it.

    Args:
        to: Customer email
        customer_name: Customer's full name (first name extracted for greeting)
        doc_filename: Filename of the generated PDF (shown in body)
        doc_type: Document slug (e.g. "sales_contract") used in subject + copy
        download_url: Absolute or relative download URL. Relative URLs are
            prefixed with PUBLIC_SITE_URL when that env var is set.
        deal_id: Optional related deal id, used as `related_id` in the
            email_log Firestore entry.

    Returns:
        dict (same shape as send_email).
    """
    first_name = html_mod.escape(customer_name.split()[0]) if customer_name else "Friend"
    pretty_type = _humanize_doc_type(doc_type)

    # Build absolute URL when caller passes a path-only URL.
    if download_url and download_url.startswith("/") and PUBLIC_SITE_URL:
        link = f"{PUBLIC_SITE_URL}{download_url}"
    else:
        link = download_url or ""
    safe_link = html_mod.escape(link, quote=True)
    safe_filename = html_mod.escape(doc_filename or "document.pdf")
    safe_type = html_mod.escape(pretty_type)

    cta_block = (
        f"""
        <p style="margin-top: 24px;">
          <a href="{safe_link}" style="display: inline-block; background: #3b82f6; color: white; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: 600;">
            Download Your Document
          </a>
        </p>
        <p style="color: #6b7280; font-size: 12px; word-break: break-all;">
          If the button does not work, copy this link into your browser:<br>
          <span>{safe_link}</span>
        </p>
        """
        if link
        else """
        <p style="color: #6b7280; font-size: 13px;">A team member will follow up shortly with your document.</p>
        """
    )

    content = f"""
    <h2 style="color: #1e3a5f; margin-top: 0;">Your {safe_type} is Ready</h2>
    <p>Hi {first_name},</p>
    <p>Your <strong>{safe_type}</strong> has been prepared and is ready for your review.</p>

    <div style="background: #eff6ff; border-left: 4px solid #3b82f6; padding: 16px; margin: 20px 0; border-radius: 0 8px 8px 0;">
      <p style="margin: 0 0 8px; font-weight: 600; color: #1e3a5f;">Document Details</p>
      <p style="margin: 4px 0; color: #374151;">📄 <strong>{safe_filename}</strong></p>
      <p style="margin: 4px 0; color: #374151;">Type: {safe_type}</p>
    </div>

    <p>Tap the button below to open the document. You may need to sign in to your Texas Home Outlet account to view it.</p>
    {cta_block}

    <p style="color: #6b7280; font-size: 13px; margin-top: 20px;">
      Questions? Reply to this email or call {BUSINESS_PHONE} and ask for Ben or Mark.
    </p>
    """

    plain_text = (
        f"Hi {first_name},\n\n"
        f"Your {pretty_type} ({doc_filename}) is ready.\n"
        f"Open it here: {link or '(link unavailable — please contact us)'}\n\n"
        f"You may need to sign in to your Texas Home Outlet account to view it.\n\n"
        f"Questions? Call {BUSINESS_PHONE} and ask for Ben or Mark.\n\n"
        f"— Texas Home Outlet"
    )

    return send_email(
        to=to,
        subject=f"Your {pretty_type} is ready, {first_name}",
        html=_base_wrapper(content),
        email_type="document_delivery",
        related_id=deal_id,
        text=plain_text,
    )


# ── Admin Notifications ─────────────────────────────────────────


def notify_new_lead(
    customer_name: str, phone: str, email: str = None, source: str = "website"
) -> dict:
    """Notify all THO staff when a new lead comes in."""
    if not NOTIFICATION_EMAILS:
        return {"success": False, "error": "No notification email configured"}

    esc = html_mod.escape
    content = f"""
    <h2 style="color: #1e3a5f; margin-top: 0;">New Lead Alert</h2>
    <div style="background: #eff6ff; border-left: 4px solid #3b82f6; padding: 16px; margin: 16px 0; border-radius: 0 8px 8px 0;">
      <p style="margin: 4px 0; color: #374151;"><strong>Name:</strong> {esc(customer_name)}</p>
      <p style="margin: 4px 0; color: #374151;"><strong>Phone:</strong> {esc(phone)}</p>
      {f'<p style="margin: 4px 0; color: #374151;"><strong>Email:</strong> {esc(email)}</p>' if email else ''}
      <p style="margin: 4px 0; color: #374151;"><strong>Source:</strong> {esc(source)}</p>
      <p style="margin: 4px 0; color: #6b7280; font-size: 13px;">{datetime.now(TIMEZONE).strftime("%B %d, %Y at %I:%M %p")}</p>
    </div>
    <p style="color: #374151;">Log into the <strong>CRM dashboard</strong> to follow up.</p>
    """

    return send_email(
        to=NOTIFICATION_EMAILS,
        subject=f"New Lead: {customer_name} — {phone}",
        html=_base_wrapper(content),
        email_type="admin_lead_notification",
    )


def notify_new_appointment(
    customer_name: str,
    phone: str,
    date: str,
    time_slot: str,
    email: str = None,
    notes: str = None,
) -> dict:
    """Notify all THO staff when a new appointment is booked."""
    if not NOTIFICATION_EMAILS:
        return {"success": False, "error": "No notification email configured"}

    esc = html_mod.escape
    content = f"""
    <h2 style="color: #1e3a5f; margin-top: 0;">New Appointment Booked</h2>
    <div style="background: #f0fdf4; border-left: 4px solid #22c55e; padding: 16px; margin: 16px 0; border-radius: 0 8px 8px 0;">
      <p style="margin: 4px 0; color: #374151;"><strong>Customer:</strong> {esc(customer_name)}</p>
      <p style="margin: 4px 0; color: #374151;"><strong>Phone:</strong> {esc(phone)}</p>
      {f'<p style="margin: 4px 0; color: #374151;"><strong>Email:</strong> {esc(email)}</p>' if email else ''}
      <p style="margin: 4px 0; color: #374151;"><strong>Date:</strong> {esc(date)}</p>
      <p style="margin: 4px 0; color: #374151;"><strong>Time:</strong> {esc(time_slot)}</p>
      {f'<p style="margin: 4px 0; color: #6b7280;"><strong>Notes:</strong> {esc(notes)}</p>' if notes else ''}
    </div>
    <p style="color: #374151;">Check the <strong>Appointments</strong> page in the CRM for details.</p>
    """

    return send_email(
        to=NOTIFICATION_EMAILS,
        subject=f"Appointment: {customer_name} — {date} {time_slot}",
        html=_base_wrapper(content),
        email_type="admin_appointment_notification",
    )


def get_email_log(limit: int = 50, email_type: str = None) -> list:
    """Retrieve email activity log from Firestore for CRM timeline."""
    db = _get_db()
    if not db:
        return []
    try:
        query = db.collection("email_log")
        if email_type:
            query = query.where("email_type", "==", email_type)
        query = query.order_by("sent_at", direction="DESCENDING").limit(limit)
        return [doc.to_dict() for doc in query.stream()]
    except Exception as e:
        logger.warning(f"Failed to retrieve email log: {e}")
        return []
