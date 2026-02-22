"""
Email Service for Texas Home Outlet — Resend Integration

Plug-and-play email delivery. Currently uses Resend's test sender.
When domain admin access is available, swap RESEND_FROM to @texashomeoutlet.com.

Setup:
  1. Sign up at resend.com → get API key
  2. Set env var: RESEND_API_KEY=re_xxxxx
  3. For production: verify texashomeoutlet.com domain in Resend dashboard
"""

import os
import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

TIMEZONE = ZoneInfo("America/Chicago")

# ── Config ──────────────────────────────────────────────────────
# Test sender works immediately — no domain verification needed
RESEND_FROM = os.environ.get("RESEND_FROM", "Texas Home Outlet <onboarding@resend.dev>")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
BUSINESS_PHONE = "(281) 324-3020"
BUSINESS_ADDRESS = "10685 FM 1960 East, Huffman, TX"

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

def send_email(to: str, subject: str, html: str, email_type: str = "general", related_id: str = None) -> dict:
    """
    Send an email via Resend.

    Args:
        to: Recipient email address
        subject: Email subject line
        html: HTML body content
        email_type: Type for logging (appointment_confirmation, lead_followup, etc.)
        related_id: Optional related entity ID (appointment_id, lead_id, deal_id)

    Returns:
        dict with success status and Resend message ID or error
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

    try:
        import resend
        resend.api_key = RESEND_API_KEY

        result = resend.Emails.send({
            "from": RESEND_FROM,
            "to": [to],
            "subject": subject,
            "html": html,
        })

        _log_email_activity(to, subject, email_type, related_id)

        logger.info(f"Email sent: {email_type} to {to} (id: {result.get('id', 'unknown')})")
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
    first_name = customer_name.split()[0] if customer_name else "Friend"

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
    first_name = customer_name.split()[0] if customer_name else "Friend"

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
    first_name = customer_name.split()[0] if customer_name else "Friend"

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
    first_name = customer_name.split()[0] if customer_name else "Friend"

    # Convert plain text line breaks to HTML
    html_message = message.replace("\n", "<br>")

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
