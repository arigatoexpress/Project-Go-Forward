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
- Dedup is BY PHONE against any already-captured lead (e.g. one the agent's
  save_lead tool persisted this same turn), so one customer is never
  double-captured / double-alerted — while the backstop STILL fires when
  save_lead was called but failed to persist (no such lead exists to match), so
  a high-intent lead is never silently lost.
"""

import asyncio
import logging
import re
import uuid
from datetime import UTC, datetime

from lead_management import Lead, normalize_phone

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


def apply_utm(lead, utm) -> bool:
    """Backfill first-party UTM/referrer onto ``lead`` where the field is empty.

    Returns True if anything changed. This is what makes a chat-sourced lead
    attributable to the paid campaign that drove the visit — the chat tool
    (save_lead) can't see the request, so the /run handler passes the UTM here.
    """
    changed = False
    for key, value in (utm or {}).items():
        if value and not getattr(lead, key, None):
            setattr(lead, key, value)
            changed = True
    return changed


async def capture_explicit_contact(
    *,
    name: str,
    phone: str,
    email: str | None,
    session_id: str,
    user_id: str,
    lead_manager,
    utm: dict | None = None,
):
    """Persist an explicitly consented chat callback request without sending.

    Prefer the session's anonymous lead so its home-search context survives the
    handoff. If no session lead exists, dedupe by normalized phone before
    creating a new record. Staff action happens through the CRM response queue;
    this helper intentionally sends no email, SMS, or other outward message.
    """
    normalized_phone = normalize_phone(phone)
    session_lead = await lead_manager.get_lead_by_session(session_id)
    context_lead = session_lead
    matched_by_session = bool(session_lead and session_lead.status == "new")
    lead = session_lead if matched_by_session else None
    if lead is None and session_lead is None:
        phone_lead = await lead_manager.get_lead_by_phone(normalized_phone)
        if phone_lead and phone_lead.status == "new":
            lead = phone_lead

    consent_at = datetime.now(UTC).isoformat()
    if lead is None:
        lead = Lead(
            lead_id=f"chat_callback_{uuid.uuid4().hex[:12]}",
            user_id=user_id,
            session_id=session_id,
            source="chat",
            name=name,
            phone=normalized_phone,
            email=email or None,
            priority="high",
            triage_reason="callback_requested",
            triage_notes="Visitor explicitly requested a callback from website chat.",
            contact_consent_at=consent_at,
            contact_consent_source="chat_callback",
            bedrooms=getattr(context_lead, "bedrooms", None),
            bathrooms=getattr(context_lead, "bathrooms", None),
            budget_max=getattr(context_lead, "budget_max", None),
            home_type=getattr(context_lead, "home_type", None),
            homes_viewed=list(getattr(context_lead, "homes_viewed", None) or []),
            appointment_requested=bool(getattr(context_lead, "appointment_requested", False)),
            financing_discussed=bool(getattr(context_lead, "financing_discussed", False)),
            **{k: v for k, v in (utm or {}).items() if v},
        )
        return await lead_manager.create_lead(lead)

    # The browser owns its session, so correcting that session's contact fields
    # is safe. A phone-only dedupe is weaker: fill blanks but never overwrite an
    # existing person's name/email based only on knowledge of their number.
    if matched_by_session or not lead.name:
        lead.name = name or lead.name
    if matched_by_session or not lead.phone:
        lead.phone = normalized_phone or lead.phone
    if email and (matched_by_session or not lead.email):
        lead.email = email
    lead.priority = "high"
    lead.triage_reason = "callback_requested"
    if not lead.triage_notes:
        lead.triage_notes = "Visitor explicitly requested a callback from website chat."
    lead.contact_consent_at = consent_at
    lead.contact_consent_source = "chat_callback"
    apply_utm(lead, utm)
    return await lead_manager.update_lead(lead)


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


async def capture_contact_from_message(
    text: str,
    session_id: str,
    user_id: str,
    *,
    lead_manager,
    notify,
    utm: dict | None = None,
    notify_timeout: float = NOTIFY_TIMEOUT,
):
    """Attach any phone/email the visitor typed to the session's lead and alert
    staff exactly once.

    Returns the lead (created, updated, or the pre-existing deduped one) or None
    when no contact info was present. Never raises into the chat path; the staff
    alert is offloaded to a thread and time-bounded so a slow send cannot stall
    the event loop.
    """
    phone, email = extract_contact(text)
    if not phone and not email:
        return None

    # Dedup by phone: if a lead already carries this number (e.g. the agent's
    # save_lead tool captured + alerted it earlier this same turn, or on a prior
    # turn), do not create a duplicate or re-alert. This keeps the backstop a
    # true safety net — exactly once per contact — while STILL firing when
    # save_lead was called but FAILED to persist (no matching lead exists).
    if phone:
        dup = await lead_manager.get_lead_by_phone(normalize_phone(phone))
        if dup is not None:
            # Already captured + alerted (e.g. by save_lead this same turn, or on
            # a prior turn/session). Don't create a duplicate or re-alert — but DO
            # merge any newly-volunteered detail (e.g. an email given alongside a
            # known number) so a contact detail is never silently dropped.
            changed = False
            if email and not dup.email:
                dup.email = email
                changed = True
            if apply_utm(dup, utm):
                changed = True
            if changed:
                await lead_manager.update_lead(dup)
            return dup

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
        apply_utm(lead, utm)
        await lead_manager.update_lead(lead)
    else:
        lead = Lead(
            lead_id=f"chat_{session_id[:8]}_{uuid.uuid4().hex[:6]}",
            user_id=user_id,
            session_id=session_id,
            phone=phone,
            email=email,
            source="chat",
            **{k: v for k, v in (utm or {}).items() if v},
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
