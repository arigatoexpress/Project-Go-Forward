"""Fixed-template rendering for inbound-email replies.

Lane 3a of the inbound-email automation pipeline. Two products:

- ``render_ack``            — the acknowledgement template, the ONLY content
                              the system may ever send unsupervised (and only
                              behind ``FF_EMAIL_REPLY_SEND`` +
                              ``FF_EMAIL_AUTO_ACK``, both default OFF).
- ``draft_substantive_body`` — an operator-editable skeleton for substantive
                              drafts; a human rewrites and approves it before
                              anything can send.

Safety rules (enforced by ``tests/test_email_reply_generator.py``):

1. ZERO body echo. ``render_ack`` does not accept the inbound body at all —
   injected email content cannot reach outbound mail through this template.
2. Deterministic: byte-identical output for identical input. No LLM, no
   network, no clock in the safe path. (Any future LLM-drafted substantive
   body keeps this module's interface and lands as its own reviewed lane.)
3. Subject is header-sanitized (CRLF stripped, length-capped) and
   HTML-escaped before interpolation.

This module renders strings only — it never sends and never imports the
email service.
"""

from __future__ import annotations

import html as _html
import re

from config_loader import business_name

_MAX_SUBJECT_CHARS = 200

_ACK_HTML_TEMPLATE = """\
<div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
  <h2 style="color: #1a365d;">We received your message</h2>
  <p>Thank you for reaching out to {business}{subject_clause}.</p>
  <p>A member of our team has your message and will follow up with you
  personally, usually within one business day.</p>
  <p>If you need us sooner, call <strong>(281) 324-3020</strong> or visit us
  at 10685 FM 1960 East, Huffman, TX 77336.</p>
  <p style="color: #718096; font-size: 13px;">This is an automated
  confirmation that your email arrived — a real person will reply next.</p>
</div>
"""

_ACK_TEXT_TEMPLATE = """\
We received your message

Thank you for reaching out to {business}{subject_clause}.

A member of our team has your message and will follow up with you personally,
usually within one business day.

If you need us sooner, call (281) 324-3020 or visit us at
10685 FM 1960 East, Huffman, TX 77336.

This is an automated confirmation that your email arrived — a real person
will reply next.
"""


def _sanitize_subject(subject) -> str:
    """Header-safe, length-capped subject. Non-strings degrade to ''."""
    if not isinstance(subject, str):
        subject = ""
    subject = re.sub(r"[\r\n\t]+", " ", subject).strip()
    return subject[:_MAX_SUBJECT_CHARS]


def render_ack(subject) -> dict:
    """Render the fixed acknowledgement reply.

    Accepts ONLY the inbound subject (never the body). Returns
    ``{"subject", "html", "text"}`` — deterministic for the same input.
    """
    business = business_name()
    clean = _sanitize_subject(subject)
    reply_subject = f"Re: {clean}" if clean else f"We received your message — {business}"
    if clean:
        html_clause = f' about "{_html.escape(clean)}"'
        text_clause = f' about "{clean}"'
    else:
        html_clause = ""
        text_clause = ""
    return {
        "subject": reply_subject[:_MAX_SUBJECT_CHARS + 20],
        "html": _ACK_HTML_TEMPLATE.format(business=_html.escape(business), subject_clause=html_clause),
        "text": _ACK_TEXT_TEMPLATE.format(business=business, subject_clause=text_clause),
    }


def draft_substantive_body(rule_hits: list[str] | None = None) -> str:
    """Skeleton reply body for a substantive inbound email.

    Deterministic template with explicit ``[operator fills this in]``
    placeholders — a human rewrites it in the review surface before approval.
    ``rule_hits`` are triage labels only (e.g. ``trigger:money``), never raw
    email content.
    """
    business = business_name()
    topics = sorted({h.split(":", 1)[-1] for h in (rule_hits or []) if h})
    topic_line = f"Detected topics: {', '.join(topics)}." if topics else "Detected topics: none."
    return (
        f"Hi [customer name],\n\n"
        f"Thank you for contacting {business}.\n\n"
        f"[operator fills this in — answer the customer's actual question here]\n\n"
        f"{topic_line}\n\n"
        f"Best regards,\n"
        f"[your name]\n"
        f"{business} — (281) 324-3020"
    )
