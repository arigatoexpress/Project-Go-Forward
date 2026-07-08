"""Passive contact-capture backstop for the AI chat.

Tool-calls are unreliable — a visitor who simply *types* their phone or email
mid-conversation (e.g. after the agent asks "what's the best number to reach
you?") would otherwise never become an actionable lead. This scans the visitor's
own message, attaches any contact details to the session's lead (creating one if
needed), and alerts staff EXACTLY ONCE — the first time contact info appears.

This is capture-on-reach-out: the visitor volunteered their details in a
conversation asking for help. It is NOT passive visitor tracking.

Safety properties (hardened after adversarial review):
- Input to the regexes is length-capped (MAX_SCAN) and the email pattern is
  quantifier-bounded, so a huge chat message can't drive O(n^2) backtracking
  (ReDoS) on the async event loop.
- A bare digit-run (MLS #, serial, price) is only treated as a phone when it is
  phone-formatted or the message shows contact intent — avoids junk leads in a
  number-heavy real-estate chat.
- The staff alert is offloaded to a worker thread and time-bounded, so a slow or
  hung Resend send never stalls the chat worker.
"""

import asyncio
import logging
import re
import uuid

from lead_management import Lead

logger = logging.getLogger(__name__)

# Only ever scan this many leading characters of a chat message. Contact info a
# visitor types appears early; this bounds every regex below to O(MAX_SCAN).
MAX_SCAN = 2000
# Seconds to wait for the (synchronous, network) staff alert before giving up.
NOTIFY_TIMEOUT = 10.0

# Quantifier-bounded so it cannot backtrack catastrophically on adversarial input.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,255}\.[A-Za-z]{2,24}")
# US phone: optional +1 / 1 prefix, then 3-3-4 digits with common separators.
_PHONE_RE = re.compile(r"(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}")
# A run of digits is only a phone if it is phone-formatted (has a separator/paren/
# plus) OR the message shows contact intent — otherwise it's likely an MLS/serial
# number, a price, or a year.
_PHONE_SEP_RE = re.compile(r"[\s.()+\-]")
_CONTACT_CUE_RE = re.compile(
    r"\b(call|text|txt|phone|cell|mobile|reach|contact|dial|number|whatsapp)\b", re.I
)


def extract_contact(text: str | None) -> tuple[str | None, str | None]:
    """Return (phone, email) volunteered in ``text`` — each None if absent.

    Only the first ``MAX_SCAN`` characters are examined (ReDoS-safe)."""
    if not text:
        return None, None
    text = text[:MAX_SCAN]

    email = None
    m = _EMAIL_RE.search(text)
    if m:
        email = m.group(0)

    # Strip any email before scanning for a phone so we don't pull digits out of
    # an address or domain.
    phone = None
    pm = _PHONE_RE.search(_EMAIL_RE.sub(" ", text))
    if pm:
        raw = pm.group(0).strip()
        digits = re.sub(r"\D", "", raw)
        if len(digits) == 10 or (len(digits) == 11 and digits.startswith("1")):
            # Guard against number-soup: accept only if it's phone-formatted or
            # the visitor signalled contact intent.
            if _PHONE_SEP_RE.search(raw) or _CONTACT_CUE_RE.search(text):
                phone = raw

    return phone, email


def event_tool_names(event) -> set[str]:
    """Names of the tools the agent invoked in an ADK event (function_call parts).

    Used by /run to skip the passive backstop when the model already called
    ``save_lead`` this turn — otherwise one customer gets two alerts + two leads.
    Defensive against ADK shape changes: any missing attribute yields no name.
    """
    names: set[str] = set()
    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None) or []
    for part in parts:
        fc = getattr(part, "function_call", None)
        name = getattr(fc, "name", None)
        if name:
            names.add(name)
    return names


async def capture_contact_from_message(
    text: str,
    session_id: str,
    user_id: str,
    *,
    lead_manager,
    notify,
    notify_timeout: float = NOTIFY_TIMEOUT,
):
    """Attach any phone/email the visitor typed to the session's lead and alert
    staff exactly once.

    Returns the lead (created or updated) or None when no contact info was
    present. The phone is stored as typed — ``LeadManager`` normalizes it to
    E.164 on persist, exactly as the contact form does. Never raises into the
    chat path; the staff alert is offloaded to a thread and time-bounded so a
    slow send cannot stall the event loop.
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
        # notify() is a synchronous network send (Resend). Offload it off the
        # event loop and bound it, so a slow/hung send never freezes the chat
        # worker. A failure/timeout must never break the chat.
        try:
            await asyncio.wait_for(
                asyncio.to_thread(
                    notify,
                    customer_name=lead.name or "Website chat visitor",
                    phone=lead.phone or "(chat lead)",
                    email=lead.email,
                    source="chat",
                ),
                timeout=notify_timeout,
            )
        except Exception as exc:  # timeout or send error — never break the chat
            logger.warning("Chat lead staff-alert failed or timed out: %s", exc)

    return lead
