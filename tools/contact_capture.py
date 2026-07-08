"""Passive contact-capture backstop for the AI chat.

Tool-calls are unreliable — a visitor who simply *types* their phone or email
mid-conversation (e.g. after the agent asks "what's the best number to reach
you?") would otherwise never become an actionable lead. This scans the visitor's
own message, attaches any contact details to the session's lead (creating one if
needed), and alerts staff EXACTLY ONCE — the first time contact info appears.

This is capture-on-reach-out: the visitor volunteered their details in a
conversation asking for help. It is NOT passive visitor tracking.
"""

import logging
import re
import uuid

from lead_management import Lead

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# US phone: optional +1 / 1 prefix, then 3-3-4 digits with common separators.
_PHONE_RE = re.compile(r"(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}")


def extract_contact(text: str | None) -> tuple[str | None, str | None]:
    """Return (phone, email) volunteered in ``text`` — each None if absent."""
    if not text:
        return None, None

    email = None
    m = _EMAIL_RE.search(text)
    if m:
        email = m.group(0)

    # Strip any email before scanning for a phone so we don't pull digits out of
    # an address or domain.
    phone = None
    pm = _PHONE_RE.search(_EMAIL_RE.sub(" ", text))
    if pm:
        digits = re.sub(r"\D", "", pm.group(0))
        if len(digits) == 10 or (len(digits) == 11 and digits.startswith("1")):
            phone = pm.group(0).strip()

    return phone, email


async def capture_contact_from_message(
    text: str,
    session_id: str,
    user_id: str,
    *,
    lead_manager,
    notify,
):
    """Attach any phone/email the visitor typed to the session's lead and alert
    staff exactly once.

    Returns the lead (created or updated) or None when no contact info was
    present. The phone is stored as typed — ``LeadManager`` normalizes it to
    E.164 on persist, exactly as the contact form does. Never raises into the
    chat path.
    """
    phone, email = extract_contact(text)
    if not phone and not email:
        return None

    existing = await lead_manager.get_lead_by_session(session_id)
    # Only alert on the FIRST appearance of contact info for this session, so a
    # multi-message conversation never spams the team.
    had_contact = bool(existing and (existing.phone or existing.email))

    if existing:
        lead = existing
        if phone and not lead.phone:
            lead.phone = phone
        if email and not lead.email:
            lead.email = email
        await lead_manager.update_lead(lead)
    else:
        lead = Lead(
            lead_id=f"chat_{session_id[:8]}_{uuid.uuid4().hex[:6]}",
            user_id=user_id,
            session_id=session_id,
            phone=phone,
            email=email,
            source="chat",
        )
        await lead_manager.create_lead(lead)

    if not had_contact:
        try:
            notify(
                customer_name=lead.name or "Website chat visitor",
                phone=lead.phone or "(chat lead)",
                email=lead.email,
                source="chat",
            )
        except Exception as exc:  # a notify failure must never break the chat
            logger.warning("Chat lead staff-alert failed: %s", exc)

    return lead
