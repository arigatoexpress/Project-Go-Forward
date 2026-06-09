"""
ESIGN consumer consent & disclosure for e-signature flows.

Federal ESIGN Act (15 U.S.C. §7001(c)) requires that, BEFORE a consumer signs or
receives legally-required disclosures electronically, the consumer affirmatively
consents after being told: (1) the right to receive a paper copy, (2) the right
to withdraw consent and any consequences, (3) the scope of the consent, (4) the
hardware/software needed to access and retain the records, and (5) how to update
their contact information. See docs/TX_MH_COMPLIANCE_RESEARCH.md §4.

We satisfy this by prepending a consent page as the FIRST document in the
DocuSeal signing packet, so the consumer's signature on it is captured in the
same audit trail as the rest of the closing documents. Generated as a PDF with
reportlab (already a dependency; see tools/document_tools.py).
"""

from __future__ import annotations

import base64
import io

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

DEFAULT_BUSINESS_NAME = "Texas Home Outlet"
CONSENT_DOCUMENT_NAME = "E-SIGN Consent & Disclosure"
CONSENT_TITLE = "Consent to Use Electronic Records and Signatures"

# Each tuple is (heading, body). Headings double as the required-element anchors
# the tests assert on, so keep the keywords (paper copy, withdraw, hardware) intact.
CONSENT_SECTIONS: list[tuple[str, str]] = [
    (
        "Your consent",
        "By signing below, you agree to receive and sign the documents in this "
        "transaction electronically, and you agree that your electronic signature "
        "and electronic records are legally binding, the same as a handwritten "
        "signature on paper.",
    ),
    (
        "Right to a paper copy",
        "You have the right to receive these documents on paper instead of "
        "electronically, at no charge. To request paper copies, contact us using "
        "the information below.",
    ),
    (
        "Right to withdraw consent",
        "You may withdraw your consent to do business electronically at any time "
        "before you sign. If you withdraw consent, we will provide the documents "
        "on paper; doing so will not affect the validity of documents you already "
        "signed electronically.",
    ),
    (
        "Scope of your consent",
        "Your consent applies to the documents in this signing request for this "
        "transaction only. We will ask for your consent again for future "
        "transactions.",
    ),
    (
        "Hardware and software you need",
        "To access and retain these records you need: a device with internet "
        "access; a current web browser; a valid email account; and the ability to "
        "view, download, and print or save PDF files. If our system requirements "
        "change in a way that affects your ability to access the records, we will "
        "notify you.",
    ),
    (
        "Keeping your contact information current",
        "Please keep your email address and contact information up to date with us "
        "so we can deliver these records to you. Contact us to update it.",
    ),
]


def _wrap(text: str, max_chars: int) -> list[str]:
    """Greedy word-wrap to a character width (reportlab has no auto-wrap)."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def build_consent_pdf_bytes(
    business_name: str = DEFAULT_BUSINESS_NAME,
    *,
    business_phone: str | None = None,
    business_address: str | None = None,
) -> bytes:
    """Render the ESIGN consent disclosure as a single-page PDF (bytes).

    Phone/address default to the canonical values from config.yaml (single
    source of truth) so the disclosure never drifts from the live business info.
    """
    from config_loader import business_address as _cfg_business_address
    from config_loader import business_phone as _cfg_business_phone

    if business_phone is None:
        business_phone = _cfg_business_phone()
    if business_address is None:
        business_address = _cfg_business_address()
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    left = inch
    right = width - inch
    max_chars = 92  # fits within the margins at 10pt Helvetica
    y = height - inch

    c.setFont("Helvetica-Bold", 15)
    c.drawString(left, y, CONSENT_TITLE)
    y -= 0.28 * inch
    c.setFont("Helvetica", 10)
    c.drawString(left, y, business_name)
    y -= 0.32 * inch

    for heading, body in CONSENT_SECTIONS:
        c.setFont("Helvetica-Bold", 11)
        c.drawString(left, y, heading)
        y -= 0.20 * inch
        c.setFont("Helvetica", 10)
        for line in _wrap(body, max_chars):
            c.drawString(left, y, line)
            y -= 0.165 * inch
        y -= 0.08 * inch

    # Contact line + signing prompt.
    y -= 0.05 * inch
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(left, y, f"Contact: {business_name} · {business_phone} · {business_address}")
    y -= 0.30 * inch
    c.setFont("Helvetica-Bold", 10)
    c.drawString(
        left, y,
        "I have read this notice and consent to use electronic records and signatures:",
    )
    # Signature/date line for the consumer (DocuSeal positions its own fields, but
    # this gives a visible acknowledgment line on the printed/saved copy).
    y -= 0.5 * inch
    c.setFont("Helvetica", 10)
    c.line(left, y, left + 2.6 * inch, y)
    c.line(right - 2.0 * inch, y, right, y)
    y -= 0.16 * inch
    c.drawString(left, y, "Signature")
    c.drawString(right - 2.0 * inch, y, "Date")

    c.showPage()
    c.save()
    return buffer.getvalue()


def build_consent_document(business_name: str = DEFAULT_BUSINESS_NAME) -> dict:
    """Return a DocuSeal ``documents[]`` entry (base64) for the consent page.

    Prepend this as the first element of a submission's ``documents`` array so the
    signer acknowledges ESIGN consent before the substantive documents.
    """
    pdf_bytes = build_consent_pdf_bytes(business_name)
    return {
        "name": CONSENT_DOCUMENT_NAME,
        "file": base64.b64encode(pdf_bytes).decode("ascii"),
    }
