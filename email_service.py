"""
Email Service for Texas Home Outlet — Resend Integration

Plug-and-play email delivery. Currently uses Resend's test sender.
When domain admin access is available, swap RESEND_FROM to @texashomeoutlet.com.

Setup:
  1. Sign up at resend.com → get API key
  2. Set env var: RESEND_API_KEY=re_xxxxx
  3. For production: verify texashomeoutlet.com domain in Resend dashboard
"""

import html as html_mod
import os
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

TIMEZONE = ZoneInfo("America/Chicago")

# ── Config ──────────────────────────────────────────────────────
# Test sender works immediately — no domain verification needed
RESEND_FROM = os.environ.get("RESEND_FROM", "Texas Home Outlet <onboarding@resend.dev>")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
NOTIFICATION_EMAIL = os.environ.get("NOTIFICATION_EMAIL", "aribspector@gmail.com")
# Comma-separated list of team addresses that receive contact/appointment alerts.
TEAM_ALERT_EMAILS = os.environ.get("TEAM_ALERT_EMAILS", "sales@texashomeoutlet.com")
BUSINESS_PHONE = "(281) 324-3020"
BUSINESS_ADDRESS = "10685 FM 1960 East, Huffman, TX"
BUSINESS_HOURS = "Mon–Fri 9 AM–6 PM, Sat 9 AM–5 PM, Sun 12–3 PM (Central)"
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
        db.collection("email_log").add({
            "to": to,
            "subject": subject,
            "email_type": email_type,
            "related_id": related_id,
            "sent_at": datetime.now(TIMEZONE).isoformat(),
            "from": RESEND_FROM,
        })
    except Exception as e:
        logger.warning(f"Failed to log email activity: {e}")


# ── Send Email (core) ──────────────────────────────────────────

def send_email(
    to,
    subject: str,
    html: str,
    email_type: str = "general",
    related_id: str = None,
    attachments: list = None,
) -> dict:
    """
    Send an email via Resend.

    Args:
        to: Recipient address (str) or list of addresses.
        subject: Email subject line.
        html: HTML body content.
        email_type: Type for logging.
        related_id: Optional related entity ID.
        attachments: Optional list of dicts with keys "filename" and "content" (bytes).

    Returns:
        dict with success status and Resend message ID or error.
    """
    if not RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set — email not sent")
        return {
            "success": False,
            "error": "Email service not configured. Set RESEND_API_KEY environment variable.",
            "dry_run": True,
            "to": to,
            "subject": subject,
        }

    recipients = [to] if isinstance(to, str) else list(to)

    try:
        import resend
        resend.api_key = RESEND_API_KEY

        payload = {
            "from": RESEND_FROM,
            "to": recipients,
            "subject": subject,
            "html": html,
        }
        if attachments:
            payload["attachments"] = [
                {
                    "filename": a["filename"],
                    "content": list(a["content"]) if isinstance(a["content"], (bytes, bytearray)) else a["content"],
                }
                for a in attachments
            ]

        result = resend.Emails.send(payload)

        primary = recipients[0] if recipients else ""
        _log_email_activity(primary, subject, email_type, related_id)

        logger.info(f"Email sent: {email_type} to {recipients} (id: {result.get('id', 'unknown')})")
        return {
            "success": True,
            "message_id": result.get("id"),
            "to": to,
            "subject": subject,
        }

    except Exception as e:
        logger.error(f"Email send failed: {e}")
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
            "body": "Great news — your pre-qualification has been approved. The next step is to finalize your home selection and move to contract."
        },
        "contract": {
            "emoji": "📝",
            "title": "Contract Ready for Review",
            "body": "Your purchase contract is ready. Please review the documents and let us know if you have any questions."
        },
        "funded": {
            "emoji": "🎉",
            "title": "Funding Complete!",
            "body": "Congratulations! Your home financing has been funded. We're getting everything ready for delivery."
        },
        "complete": {
            "emoji": "🏠",
            "title": "Welcome Home!",
            "body": "Your home delivery and setup is complete. Welcome to the Texas Home Outlet family!"
        },
    }

    info = status_messages.get(new_status, {
        "emoji": "📋",
        "title": f"Deal Update: {new_status.title()}",
        "body": f"Your deal status has been updated to: {new_status}."
    })

    home_line = f'<p style="color: #374151; margin: 8px 0;"><strong>Home:</strong> {home_name}</p>' if home_name else ""

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


# ── Admin Notifications ─────────────────────────────────────────

def notify_new_lead(customer_name: str, phone: str, email: str = None, source: str = "website") -> dict:
    """Notify owner when a new lead comes in."""
    if not NOTIFICATION_EMAIL:
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
        to=NOTIFICATION_EMAIL,
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
    """Notify owner when a new appointment is booked."""
    if not NOTIFICATION_EMAIL:
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
        to=NOTIFICATION_EMAIL,
        subject=f"Appointment: {customer_name} — {date} {time_slot}",
        html=_base_wrapper(content),
        email_type="admin_appointment_notification",
    )


# ── New Customer Confirmation + Team Alert Templates ────────────


def _team_alert_recipients() -> list:
    """Parse TEAM_ALERT_EMAILS env var into a list of addresses."""
    raw = os.environ.get("TEAM_ALERT_EMAILS", TEAM_ALERT_EMAILS)
    return [addr.strip() for addr in raw.split(",") if addr.strip()]


def _build_ics(summary: str, date_str: str, time_str: str, location: str = None) -> bytes | None:
    """Build a minimal .ics calendar invite. Returns bytes or None on failure."""
    try:
        from icalendar import Calendar, Event
        from datetime import timedelta
        from dateutil import parser as du_parser

        dt_start = du_parser.parse(f"{date_str} {time_str}", fuzzy=True)
        dt_end = dt_start + timedelta(hours=1)

        cal = Calendar()
        cal.add("prodid", "-//Texas Home Outlet//AI Agent//EN")
        cal.add("version", "2.0")
        cal.add("method", "REQUEST")

        event = Event()
        event.add("summary", summary)
        event.add("dtstart", dt_start)
        event.add("dtend", dt_end)
        if location:
            event.add("location", location)
        event.add("description", "Appointment with Texas Home Outlet")

        cal.add_component(event)
        return cal.to_ical()
    except Exception as e:
        logger.warning(f"ICS generation failed: {e}")
        return None


def contact_form_customer_confirmation(to: str, name: str, message_excerpt: str = "") -> dict:
    """Send a friendly confirmation to a customer who submitted the contact form."""
    esc = html_mod.escape
    first_name = esc(name.split()[0]) if name else "Friend"
    excerpt_html = (
        f'<p style="color:#6b7280;font-size:13px;font-style:italic;">'
        f'&ldquo;{esc(message_excerpt[:200])}&rdquo;</p>'
        if message_excerpt
        else ""
    )

    content = f"""
    <h2 style="color:#1e3a5f;margin-top:0;">Thanks for Reaching Out, {first_name}!</h2>
    <p>Hi {first_name},</p>
    <p>We got your message and a team member will follow up with you shortly — typically within a few
    hours during business hours.</p>
    {excerpt_html}
    <div style="background:#eff6ff;border-left:4px solid #3b82f6;padding:16px;margin:20px 0;border-radius:0 8px 8px 0;">
      <p style="margin:0 0 6px;font-weight:600;color:#1e3a5f;">Our Office Hours</p>
      <p style="margin:4px 0;color:#374151;">{esc(BUSINESS_HOURS)}</p>
      <p style="margin:8px 0 0;color:#374151;">📞 <strong>{BUSINESS_PHONE}</strong></p>
    </div>
    <p>To make sure our replies land in your inbox, save
    <strong>sales@texashomeoutlet.com</strong> to your contacts.</p>
    <p style="color:#6b7280;font-size:12px;margin-top:24px;">
      If you did not submit this form, you can ignore this email.<br>
      To stop receiving messages from Texas Home Outlet, reply with <strong>STOP</strong>.
    </p>
    """

    return send_email(
        to=to,
        subject=f"We got your message, {first_name} — Texas Home Outlet",
        html=_base_wrapper(content),
        email_type="contact_form_customer_confirmation",
    )


def contact_form_team_alert(
    name: str,
    phone: str,
    email: str = None,
    message: str = "",
    lead_id: str = None,
) -> dict:
    """Send a new-contact alert to the sales team distribution list."""
    recipients = _team_alert_recipients()
    if not recipients:
        return {"success": False, "error": "No team alert recipients configured"}

    esc = html_mod.escape
    crm_url = f"{PUBLIC_SITE_URL}/crm?lead_id={esc(lead_id or '')}" if lead_id else f"{PUBLIC_SITE_URL}/crm"
    email_line = f'<p style="margin:4px 0;color:#374151;"><strong>Email:</strong> {esc(email)}</p>' if email else ""
    message_block = (
        f'<div style="margin-top:12px;padding:12px;background:#f9fafb;border-radius:6px;">'
        f'<p style="margin:0 0 4px;font-weight:600;color:#374151;">Message:</p>'
        f'<p style="margin:0;color:#374151;">{esc(message[:500])}</p>'
        f"</div>"
        if message
        else ""
    )

    content = f"""
    <h2 style="color:#1e3a5f;margin-top:0;">&#128204; New Contact Form Submission</h2>
    <div style="background:#eff6ff;border-left:4px solid #3b82f6;padding:16px;margin:16px 0;border-radius:0 8px 8px 0;">
      <p style="margin:4px 0;color:#374151;"><strong>Name:</strong> {esc(name)}</p>
      <p style="margin:4px 0;color:#374151;"><strong>Phone:</strong> {esc(phone)}</p>
      {email_line}
      <p style="margin:4px 0;color:#6b7280;font-size:13px;">{datetime.now(TIMEZONE).strftime("%B %d, %Y at %I:%M %p CT")}</p>
    </div>
    {message_block}
    <p style="margin-top:20px;">
      <a href="{crm_url}" style="display:inline-block;background:#1e3a5f;color:white;padding:10px 20px;border-radius:6px;text-decoration:none;font-weight:600;">
        View Lead in CRM &rarr;
      </a>
    </p>
    """

    return send_email(
        to=recipients,
        subject=f"New Contact: {name} — {phone}",
        html=_base_wrapper(content),
        email_type="contact_form_team_alert",
        related_id=lead_id,
    )


def appointment_booked_customer_confirmation(
    to: str,
    name: str,
    date: str,
    time_slot: str,
    location: str = None,
    appointment_id: str = None,
) -> dict:
    """Send appointment confirmation to customer with an .ics calendar attachment."""
    esc = html_mod.escape
    first_name = esc(name.split()[0]) if name else "Friend"
    appt_location = location or BUSINESS_ADDRESS

    content = f"""
    <h2 style="color:#1e3a5f;margin-top:0;">Your Visit is Confirmed! &#127968;</h2>
    <p>Hi {first_name},</p>
    <p>We&#8217;re looking forward to meeting you. Your showroom appointment is all set.</p>
    <div style="background:#f0fdf4;border-left:4px solid #22c55e;padding:16px;margin:20px 0;border-radius:0 8px 8px 0;">
      <p style="margin:0 0 8px;font-weight:600;color:#166534;">Appointment Details</p>
      <p style="margin:4px 0;color:#374151;">&#128197; <strong>{esc(date)}</strong></p>
      <p style="margin:4px 0;color:#374151;">&#128336; <strong>{esc(time_slot)}</strong></p>
      <p style="margin:4px 0;color:#374151;">&#128205; {esc(appt_location)}</p>
    </div>
    <p><strong>What to bring:</strong> valid ID, proof of land ownership (if applicable), and any
    financing pre-approval letters. Ask for <strong>Ben or Mark</strong> when you arrive.</p>
    <p style="margin-top:20px;">
      <a href="tel:+12813243020" style="display:inline-block;background:#3b82f6;color:white;padding:10px 20px;border-radius:6px;text-decoration:none;font-weight:600;">
        Call Us: {BUSINESS_PHONE}
      </a>
    </p>
    <p style="color:#6b7280;font-size:12px;margin-top:20px;">
      Need to reschedule? Reply to this email or call us. The calendar invite attached to this email
      will add the appointment to your calendar automatically.<br><br>
      To stop receiving messages from Texas Home Outlet, reply with <strong>STOP</strong>.
    </p>
    """

    ics_bytes = _build_ics(
        summary="Texas Home Outlet Showroom Visit",
        date_str=date,
        time_str=time_slot,
        location=appt_location,
    )
    attachments = [{"filename": "appointment.ics", "content": ics_bytes}] if ics_bytes else None

    return send_email(
        to=to,
        subject=f"Appointment confirmed: {date} at {time_slot} — Texas Home Outlet",
        html=_base_wrapper(content),
        email_type="appointment_booked_customer_confirmation",
        related_id=appointment_id,
        attachments=attachments,
    )


def appointment_booked_team_alert(
    name: str,
    date: str,
    time_slot: str,
    contact_info: dict = None,
) -> dict:
    """Send a new-appointment alert to the sales team distribution list."""
    recipients = _team_alert_recipients()
    if not recipients:
        return {"success": False, "error": "No team alert recipients configured"}

    esc = html_mod.escape
    info = contact_info or {}
    phone = info.get("phone", "")
    email = info.get("email", "")
    notes = info.get("notes", "")
    appointment_id = info.get("appointment_id", "")

    crm_url = f"{PUBLIC_SITE_URL}/crm" if PUBLIC_SITE_URL else ""
    phone_line = f'<p style="margin:4px 0;color:#374151;"><strong>Phone:</strong> {esc(phone)}</p>' if phone else ""
    email_line = f'<p style="margin:4px 0;color:#374151;"><strong>Email:</strong> {esc(email)}</p>' if email else ""
    notes_line = f'<p style="margin:4px 0;color:#6b7280;"><strong>Notes:</strong> {esc(notes)}</p>' if notes else ""
    cta = (
        f'<p style="margin-top:20px;"><a href="{esc(crm_url)}" style="display:inline-block;background:#1e3a5f;'
        f'color:white;padding:10px 20px;border-radius:6px;text-decoration:none;font-weight:600;">'
        f"View in CRM &rarr;</a></p>"
        if crm_url
        else ""
    )

    content = f"""
    <h2 style="color:#1e3a5f;margin-top:0;">&#128197; New Appointment Booked</h2>
    <div style="background:#f0fdf4;border-left:4px solid #22c55e;padding:16px;margin:16px 0;border-radius:0 8px 8px 0;">
      <p style="margin:4px 0;color:#374151;"><strong>Customer:</strong> {esc(name)}</p>
      {phone_line}
      {email_line}
      <p style="margin:4px 0;color:#374151;"><strong>Date:</strong> {esc(date)}</p>
      <p style="margin:4px 0;color:#374151;"><strong>Time:</strong> {esc(time_slot)}</p>
      {notes_line}
      <p style="margin:8px 0 0;color:#6b7280;font-size:13px;">{datetime.now(TIMEZONE).strftime("%B %d, %Y at %I:%M %p CT")}</p>
    </div>
    {cta}
    """

    return send_email(
        to=recipients,
        subject=f"New Appointment: {name} — {date} {time_slot}",
        html=_base_wrapper(content),
        email_type="appointment_booked_team_alert",
        related_id=appointment_id or None,
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
