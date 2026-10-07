"""
Staff-reviewed e-sign flow.

Nothing goes to a buyer for signature unless a staff member previewed the exact
values and confirmed. The packet is built from the deal's own values, the money
lines must be filled in, the document-quality gate must pass, and the values
DocuSeal receives are read back from the filled PDF the staff member previewed.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any

from tools import feature_flags as ff
from tools.document_quality import enrich_document_data

AUTO_SEND_FLAG = "ESIGN_AUTO_SEND"

SIGNABLE_TEMPLATES: dict[str, str] = {
    "TMHA_SalesContract.pdf": "Sales Contract",
    "TMHA-SalesContractDepositAgreement.pdf": "Deposit Agreement",
}

STAGE_SIGNING_TEMPLATES: dict[str, str] = {
    "contract": "TMHA_SalesContract.pdf",
    "pending": "TMHA-SalesContractDepositAgreement.pdf",
}

# Money lines that, when a template prints them, must come out non-zero.
PRINTED_MONEY_LABELS: dict[str, str] = {
    "sales_price": "Sales price",
    "down_payment": "Down payment",
    "unpaid_balance": "Unpaid balance",
    "total_unpaid_balance": "Total unpaid balance",
    "max_financed": "Amount financed",
    "monthly_payment": "Monthly payment",
    "total_monthly_payment": "Total monthly payment",
    "total_payments": "Total of payments",
    "tax_escrow_payment": "Monthly tax escrow",
    "insurance_premium_monthly": "Monthly insurance escrow",
}

MAX_LOAN_TERM_MONTHS = 480
# Above this the APR is a decimal-point typo (e.g. 850 for 8.50), not a rate.
MAX_APR_PERCENT = Decimal("100")


def auto_send_enabled() -> bool:
    """Automatic sends on stage change / packet generation. Default OFF."""
    return ff.is_enabled(AUTO_SEND_FLAG, default=False)


def _amount(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).replace("$", "").replace(",", "").replace("%", "").strip()
    if not text:
        return None
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() else None


MONEY_INPUT_KEYS = (
    "sales_price",
    "down_payment",
    "apr",
    "loan_term",
    "monthly_payment",
    "tax_rate",
    "taxable_value",
    "annual_insurance",
)


def _finite_money_inputs(doc_data: dict[str, Any]) -> dict[str, Any]:
    """Drop NaN/Infinity money inputs (the payment engine raises on them)."""
    cleaned = dict(doc_data)
    for key in MONEY_INPUT_KEYS:
        value = cleaned.get(key)
        if value is not None and _amount(value) is None and str(value).strip():
            try:
                if not Decimal(str(value).strip()).is_finite():
                    cleaned[key] = None
            except (InvalidOperation, ValueError):
                pass
    return cleaned


def _positive(value: Any) -> bool:
    amount = _amount(value)
    return amount is not None and amount > 0


def _fmt_money(value: Any) -> str:
    amount = _amount(value)
    return "—" if amount is None else f"${amount:,.2f}"


def _problem(code: str, field: str | None, message: str) -> dict[str, Any]:
    return {"code": code, "field": field, "message": message}


def money_problems(doc_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Plain-English reasons the deal's money lines are not ready to sign."""
    doc_data = _finite_money_inputs(doc_data)
    enriched = enrich_document_data(doc_data)
    problems: list[dict[str, Any]] = []

    price_ok = _positive(doc_data.get("sales_price"))
    if not price_ok:
        problems.append(
            _problem(
                "missing_money_field",
                "sales_price",
                "Sales price is blank or $0. Enter the home's sale price on the deal.",
            )
        )
    if not _positive(doc_data.get("down_payment")):
        problems.append(
            _problem(
                "missing_money_field",
                "down_payment",
                "Down payment is blank or $0. Enter the down payment on the deal.",
            )
        )
    # Without APR and term the engine falls back to a 7.5% / 240-month guess,
    # so the payment would print but would not be the buyer's real payment.
    if not (_positive(doc_data.get("apr")) and _positive(doc_data.get("loan_term"))):
        problems.append(
            _problem(
                "missing_money_field",
                "monthly_payment",
                "Monthly payment can't be worked out because the APR or loan term "
                "is blank. Enter both on the deal.",
            )
        )
    elif not _whole_months(doc_data.get("loan_term")):
        # The engine reads only the leading digits of the term ("0.5" -> 240
        # fallback, "1e9" -> 1), so the payment would not match the printed term.
        problems.append(
            _problem(
                "invalid_money_field",
                "loan_term",
                f"Loan term must be a whole number of months from 1 to "
                f"{MAX_LOAN_TERM_MONTHS}. Fix the loan term on the deal.",
            )
        )
    elif _amount(doc_data.get("apr")) > MAX_APR_PERCENT:
        problems.append(
            _problem(
                "invalid_money_field",
                "apr",
                "APR looks wrong (over 100%). Enter it as a percent, e.g. 8.5.",
            )
        )
    elif price_ok and not _positive(enriched.get("monthly_payment")):
        problems.append(
            _problem(
                "missing_money_field",
                "monthly_payment",
                "Monthly payment works out to $0. Check the sales price, down "
                "payment, APR and loan term.",
            )
        )

    tax_ok = _positive(doc_data.get("tax_rate"))
    if not tax_ok:
        problems.append(
            _problem(
                "missing_money_field",
                "tax_rate",
                "Property tax rate is blank or 0%. Enter the tax rate on the deal.",
            )
        )
    insurance_ok = _positive(doc_data.get("annual_insurance"))
    if not insurance_ok:
        problems.append(
            _problem(
                "missing_money_field",
                "annual_insurance",
                "Insurance is blank or $0. Enter the annual insurance premium on the deal.",
            )
        )
    if tax_ok and price_ok and not _positive(enriched.get("tax_escrow_payment")):
        problems.append(
            _problem(
                "missing_money_field",
                "escrow",
                "Monthly tax escrow works out to $0. Check the tax rate and taxable value.",
            )
        )
    if insurance_ok and not _positive(enriched.get("insurance_premium_monthly")):
        problems.append(
            _problem(
                "missing_money_field",
                "escrow",
                "Monthly insurance escrow works out to $0. Check the annual insurance premium.",
            )
        )
    return problems


def _whole_months(value: Any) -> bool:
    amount = _amount(value)
    return (
        amount is not None
        and amount == amount.to_integral_value()
        and 1 <= amount <= MAX_LOAN_TERM_MONTHS
    )


def money_summary(doc_data: dict[str, Any]) -> list[dict[str, str]]:
    """Payment lines staff hand-check before confirming a send."""
    doc_data = _finite_money_inputs(doc_data)
    enriched = enrich_document_data(doc_data)
    apr = _amount(doc_data.get("apr"))
    term = _amount(doc_data.get("loan_term"))
    return [
        {"label": "Sales price", "value": _fmt_money(doc_data.get("sales_price"))},
        {"label": "Down payment", "value": _fmt_money(doc_data.get("down_payment"))},
        {"label": "Amount financed", "value": _fmt_money(enriched.get("unpaid_balance"))},
        {"label": "APR", "value": "—" if apr is None else f"{apr.normalize()}%"},
        {"label": "Loan term", "value": "—" if term is None else f"{int(term)} months"},
        {
            "label": "Principal & interest",
            "value": _fmt_money(enriched.get("monthly_payment")),
        },
        {
            "label": "Monthly tax escrow",
            "value": _fmt_money(enriched.get("tax_escrow_payment")),
        },
        {
            "label": "Monthly insurance escrow",
            "value": _fmt_money(enriched.get("insurance_premium_monthly")),
        },
        {
            "label": "Total monthly payment",
            "value": _fmt_money(enriched.get("total_monthly_payment")),
        },
    ]


def read_filled_values(pdf_path: str) -> dict[str, Any]:
    """Field values filled into a generated PDF, keyed by AcroForm name.

    DocuSeal templates are uploaded from the same AcroForm PDFs, so these names
    are the DocuSeal field names. Some money lines (e.g. on the TMHA Sales
    Contract) are drawn as page overlays with the AcroForm field left empty;
    their values are recovered from the XFA datasets fill_pdf_form writes.
    """
    from pypdf import PdfReader

    reader = PdfReader(pdf_path)
    xfa_values = _read_xfa_values(reader)
    values: dict[str, Any] = {}
    # Read the page widgets, not reader.get_fields(): after fill_pdf_form the
    # AcroForm /Fields array still points at the template's unfilled objects.
    for page in reader.pages:
        for annot_ref in page.get("/Annots") or []:
            annot = annot_ref.get_object()
            if annot.get("/Subtype") != "/Widget":
                continue
            parent_ref = annot.get("/Parent")
            parent = parent_ref.get_object() if parent_ref is not None else None
            field = annot if "/T" in annot else parent
            if field is None:
                continue
            name = _qualified_field_name(field)
            raw = field.get("/V")
            if raw is None:
                raw = annot.get("/AS")
            text = str(raw).strip() if raw is not None else ""
            if text and text != "/Off":
                values[name] = True if text.startswith("/") else text
            elif field.get("/FT") == "/Tx" and name not in values:
                xfa_text = xfa_values.get(_short_field_name(name))
                if xfa_text:
                    values[name] = xfa_text
    return values


def _short_field_name(qualified: str) -> str:
    return qualified.split(".")[-1].split("[")[0]


def _read_xfa_values(reader: Any) -> dict[str, str]:
    import xml.etree.ElementTree as ET

    try:
        acroform = reader.trailer["/Root"].get("/AcroForm")
        xfa = acroform.get("/XFA") if acroform else None
        if not xfa:
            return {}
        for index in range(0, len(xfa) - 1, 2):
            if str(xfa[index]) != "datasets":
                continue
            root = ET.fromstring(xfa[index + 1].get_object().get_data())
            values: dict[str, str] = {}
            for element in root.iter():
                tag = element.tag.split("}")[-1]
                text = (element.text or "").strip()
                if text and len(element) == 0:
                    values.setdefault(tag, text)
            return values
    except Exception:  # noqa: BLE001 — a template without XFA just has no extras
        return {}
    return {}


def _qualified_field_name(field: Any) -> str:
    parts: list[str] = []
    node = field
    while node is not None:
        if "/T" in node:
            parts.append(str(node["/T"]))
        parent_ref = node.get("/Parent")
        node = parent_ref.get_object() if parent_ref is not None else None
    return ".".join(reversed(parts))


def printed_money_problems(
    template_name: str,
    values: dict[str, Any],
    doc_data: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Money lines on the filled PDF that are blank, $0, or disagree with the deal."""
    from config.field_map_loader import get_template_field_map

    field_map = get_template_field_map(template_name) or {}
    expected = enrich_document_data(doc_data) if doc_data is not None else {}
    label = SIGNABLE_TEMPLATES.get(template_name, template_name)
    problems: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pdf_field, data_key in field_map.items():
        money_label = PRINTED_MONEY_LABELS.get(data_key)
        if not money_label or data_key in seen:
            continue
        printed = _amount(values.get(pdf_field))
        if printed is None or printed <= 0:
            seen.add(data_key)
            problems.append(
                _problem(
                    "printed_money_blank",
                    data_key,
                    f"The {money_label} line on the {label} came out blank or $0. "
                    "Don't send it; ask the office to check the document setup.",
                )
            )
            continue
        want = _amount(expected.get(data_key))
        if want is not None and want.quantize(Decimal("0.01")) != printed.quantize(Decimal("0.01")):
            seen.add(data_key)
            problems.append(
                _problem(
                    "printed_money_mismatch",
                    data_key,
                    f"The {money_label} printed on the {label} ({_fmt_money(printed)}) "
                    f"doesn't match the deal ({_fmt_money(want)}). Don't send it; ask "
                    "the office to check the document setup.",
                )
            )
    return problems


def review_token(
    template_name: str,
    values: dict[str, Any],
    signer: tuple[str, str] = ("", ""),
) -> str:
    """Fingerprint of exactly what will be sent and to whom.

    The signer is hashed separately because the buyer's email (and on the
    Sales Contract, the name) is not a field on the PDF.
    """
    blob = json.dumps(
        {"template": template_name, "values": values, "signer": list(signer)},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def signer_from_deal(doc_data: dict[str, Any]) -> tuple[str, str]:
    email = str(doc_data.get("buyer_email") or "").strip()
    name = " ".join(
        part
        for part in (
            str(doc_data.get("buyer_first_name") or "").strip(),
            str(doc_data.get("buyer_last_name") or "").strip(),
        )
        if part
    )
    return email, name


def prepare_signing_packet(
    *,
    doc_data: dict[str, Any],
    template_name: str,
    generate: Callable[..., dict[str, Any]],
    read_values: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the signing document from the deal and decide if it may be sent.

    Returns ``ready`` plus the problems, the money summary for the review step,
    and (when built) the filled PDF, the values DocuSeal would receive and the
    review token the confirm step must echo back.
    """
    document_label = SIGNABLE_TEMPLATES.get(template_name)
    base: dict[str, Any] = {
        "ready": False,
        "template_name": template_name,
        "document_label": document_label or template_name,
        "problems": [],
        "money_summary": money_summary(doc_data),
    }
    if not document_label:
        base["problems"] = [
            _problem(
                "template_not_signable",
                None,
                f"{template_name} can't be sent for e-signature from the CRM.",
            )
        ]
        return base

    signer_email, signer_name = signer_from_deal(doc_data)
    base["signer_email"] = signer_email
    base["signer_name"] = signer_name or signer_email

    problems = money_problems(doc_data)
    if not signer_email:
        problems.insert(
            0,
            _problem("missing_signer", "buyer_email", "There's no buyer email on this deal."),
        )
    if problems:
        base["problems"] = problems
        return base

    result = generate(template_name=template_name, data=dict(doc_data), deal_id=None)
    if not result.get("success"):
        issues = result.get("quality_issues") or []
        base["problems"] = [
            _problem(issue.get("code", "quality_gate_failed"), issue.get("field"), issue["message"])
            for issue in issues
            if isinstance(issue, dict) and issue.get("message")
        ] or [
            _problem(
                "generation_failed",
                None,
                "The document could not be built: "
                + str(result.get("message") or result.get("error") or "unknown error"),
            )
        ]
        return base

    filename = result.get("filename")
    base["filename"] = filename
    base["download_url"] = f"/api/documents/download/{filename}" if filename else None

    values = (read_values or read_filled_values)(result["file_path"])
    problems = printed_money_problems(template_name, values, doc_data)
    if problems:
        base["problems"] = problems
        return base

    base.update(
        {
            "ready": True,
            "values": values,
            "review_token": review_token(
                template_name, values, (signer_email, base["signer_name"])
            ),
        }
    )
    return base


def public_view(prepared: dict[str, Any]) -> dict[str, Any]:
    """Prepared packet without the raw field values (they can include PII)."""
    return {key: value for key, value in prepared.items() if key != "values"}
