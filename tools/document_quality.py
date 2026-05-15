"""Document Center quality gates.

These checks are intentionally conservative: it is better to block a packet and
tell the sales rep exactly what to fix than to produce a closing PDF with fake
serials, placeholder HUD labels, or partially-filled lender note pages.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


BUSINESS_NAME = "Texas Home Outlet"
BUSINESS_PHONE = "(281) 324-3020"
BUSINESS_ADDRESS = "10685 FM 1960 East"
BUSINESS_CITY_STATE_ZIP = "Huffman, TX 77336"


IDENTITY_FIELDS = {
    "serial_number_1": "Serial # 1",
    "serial_number_2": "Serial # 2",
    "label_number_1": "HUD label # 1",
    "label_number_2": "HUD label # 2",
    "hud_number": "HUD label",
}

PLACEHOLDER_ID_VALUES = {
    "123456",
    "1234567",
    "12345678",
    "12345677",
    "nta1234567",
    "nta1234568",
    "hud1234567",
    "hud1234568",
    "serial1234567",
    "serial1234568",
}

PLACEHOLDER_TEXT_MARKERS = (
    "placeholder",
    "sample",
    "dummy",
    "fake",
    "todo",
    "tbd",
    "irregula",
    "rregular",
    "dddddddddd",
    "$$00000000000000",
    "01234 6789",
    "xxxxxxxx",
)

PRODUCTION_BLOCKED_TEMPLATES = {
    "TMHA-TwoPartyContract.pdf": (
        "Manufactured Home Note/Security Agreement is not production-ready in "
        "Document Center yet; lender/note fields are not fully mapped."
    ),
    "TMHA-TwoPartyContract191220.pdf": (
        "Manufactured Home Note/Security Agreement (2019 rev) is not "
        "production-ready in Document Center yet; lender/note fields are not "
        "fully mapped."
    ),
}


@dataclass(frozen=True)
class DocumentQualityIssue:
    code: str
    message: str
    field: str | None = None
    template: str | None = None
    severity: str = "error"

    def to_dict(self) -> dict[str, str]:
        return {k: v for k, v in asdict(self).items() if v is not None}


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _has_value(value: Any) -> bool:
    return bool(_clean(value))


def _is_blank(value: Any) -> bool:
    return not _has_value(value)


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    text = str(value).replace("$", "").replace(",", "").replace("%", "").strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{value.quantize(Decimal('0.01')):,.2f}"


def _normalize_identifier(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", _clean(value).lower())


def is_placeholder_identifier(value: Any) -> bool:
    normalized = _normalize_identifier(value)
    if not normalized:
        return False
    if normalized in PLACEHOLDER_ID_VALUES:
        return True
    if normalized in {"0000000", "00000000", "9999999", "99999999"}:
        return True
    if re.fullmatch(r"(?:1234){2,}", normalized):
        return True
    return False


def is_placeholder_text(value: Any) -> bool:
    lowered = _clean(value).lower()
    return any(marker in lowered for marker in PLACEHOLDER_TEXT_MARKERS)


def enrich_document_data(data: dict[str, Any]) -> dict[str, Any]:
    """Return data with safe seller and computed aliases filled.

    This does not invent customer/home identifiers. It only fills dealership
    seller fields and deterministic financing aliases from fields already
    present in the form.
    """
    enriched = dict(data or {})

    enriched.setdefault("seller_name", BUSINESS_NAME)
    enriched.setdefault("seller_phone", BUSINESS_PHONE)
    enriched.setdefault("seller_address", BUSINESS_ADDRESS)
    enriched.setdefault("seller_city_state_zip", BUSINESS_CITY_STATE_ZIP)

    sales_price = _decimal(enriched.get("sales_price"))
    down_payment = _decimal(enriched.get("down_payment")) or Decimal("0")
    loan_amount = None
    if sales_price is not None:
        loan_amount = sales_price - down_payment
        for key in ("unpaid_balance", "total_unpaid_balance", "max_financed"):
            if _is_blank(enriched.get(key)):
                enriched[key] = _money(loan_amount)

    apr = _decimal(enriched.get("apr"))
    if apr is not None and _is_blank(enriched.get("interest_rate")):
        enriched["interest_rate"] = str(apr.normalize())

    monthly_payment = _decimal(enriched.get("monthly_payment"))
    if monthly_payment is not None and _is_blank(enriched.get("total_monthly_payment")):
        enriched["total_monthly_payment"] = _money(monthly_payment)

    return enriched


def validate_document_quality(
    data: dict[str, Any],
    *,
    templates: list[str] | tuple[str, ...] | None = None,
) -> list[DocumentQualityIssue]:
    """Fail-closed validation for generated PDF packet quality."""
    enriched = enrich_document_data(data)
    selected_templates = list(templates or [])
    issues: list[DocumentQualityIssue] = []

    for field, label in IDENTITY_FIELDS.items():
        value = enriched.get(field)
        if _is_blank(value):
            continue
        if is_placeholder_identifier(value):
            issues.append(
                DocumentQualityIssue(
                    code="placeholder_identifier",
                    field=field,
                    message=f"{label} looks like a fake placeholder. Enter the real value before generating documents.",
                )
            )

    for field, value in enriched.items():
        if not isinstance(value, str) or _is_blank(value):
            continue
        if is_placeholder_text(value):
            issues.append(
                DocumentQualityIssue(
                    code="placeholder_text",
                    field=field,
                    message=f"{field.replace('_', ' ').title()} contains placeholder text.",
                )
            )

    for template in selected_templates:
        reason = PRODUCTION_BLOCKED_TEMPLATES.get(template)
        if reason:
            issues.append(
                DocumentQualityIssue(
                    code="template_not_production_ready",
                    template=template,
                    message=reason,
                )
            )

    return issues


def quality_failure_response(issues: list[DocumentQualityIssue]) -> dict[str, Any]:
    first = issues[0].message if issues else "Document quality gate failed."
    return {
        "success": False,
        "error": "quality_gate_failed",
        "message": first,
        "quality_issues": [issue.to_dict() for issue in issues],
    }
