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

BUSINESS_NAME = "Texas Home Outlet, Inc."
BUSINESS_PHONE = "(281) 324-3020"
BUSINESS_ADDRESS = "10685 FM 1960 East"
BUSINESS_CITY = "Huffman"
BUSINESS_STATE = "TX"
BUSINESS_ZIP = "77336"
BUSINESS_CITY_STATE_ZIP = "Huffman, TX 77336"


IDENTITY_FIELDS = {
    "serial_number_1": "Serial # 1",
    "serial_number_2": "Serial # 2",
    "label_number_1": "HUD label # 1",
    "label_number_2": "HUD label # 2",
    "hud_number": "HUD label",
}

REQUIRED_FIELD_LABELS = {
    "buyer_name": "Buyer name",
    "buyer_first_name": "Buyer first name",
    "buyer_last_name": "Buyer last name",
    "buyer_address": "Installation street address",
    "buyer_city": "Installation city",
    "buyer_county": "Installation county",
    "buyer_state": "Installation state",
    "buyer_zip": "Installation ZIP",
    "buyer_full_address": "Installation address",
    "buyer_city_state_zip": "Installation city/state/ZIP",
    "manufacturer": "Manufacturer",
    "manufacturer_address": "Manufacturer address",
    "manufacturer_city_state_zip": "Manufacturer city/state/ZIP",
    "manufacturer_full_address": "Manufacturer address",
    "model": "Model",
    "manufacturer_model": "Manufacturer/model",
    "manufacturer_model_hud": "Manufacturer/model/HUD label",
    "manufacturer_model_serial": "Manufacturer/model/serial",
    "serial_number_1": "Serial # 1",
    "serial_number_2": "Serial # 2",
    "label_number_1": "HUD label # 1",
    "label_number_2": "HUD label # 2",
    "date_of_manufacture": "Date of manufacture",
    "seller_name": "Seller name",
    "seller_address": "Seller address",
    "seller_phone": "Seller phone",
    "seller_city": "Seller city",
    "seller_state": "Seller state",
    "seller_zip": "Seller ZIP",
    "sales_price": "Sales price",
    "down_payment": "Down payment",
    "creditor_name": "Creditor name",
    "creditor_address": "Creditor address",
    "creditor_city_state_zip": "Creditor city/state/ZIP",
    "creditor_phone": "Creditor phone",
}

REQUIRED_FIELD_ALIASES = {
    "buyer_name": ("buyer_name", "buyer_first_name", "buyer_last_name"),
    "buyer_full_address": ("buyer_full_address", "buyer_address"),
    "buyer_city_state_zip": ("buyer_city_state_zip", "buyer_city"),
    "mailing_full_address": ("mailing_full_address", "mailing_address"),
    "mailing_city_state_zip": ("mailing_city_state_zip", "mailing_city"),
    "manufacturer_model": ("manufacturer_model", "manufacturer", "model"),
    "manufacturer_model_hud": ("manufacturer_model_hud",),
    "manufacturer_model_serial": ("manufacturer_model_serial",),
    "manufacturer_full_address": ("manufacturer_full_address", "manufacturer_address"),
    "manufacturer_city_state_zip": ("manufacturer_city_state_zip", "manufacturer_city"),
    "seller_name": ("seller_name",),
    "seller_address": ("seller_address",),
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


def _join_non_empty(values: list[Any], separator: str = " ") -> str:
    clean = [_clean(value) for value in values if _has_value(value)]
    return separator.join(clean)


def _all_have_values(data: dict[str, Any], *fields: str) -> bool:
    return all(_has_value(data.get(field)) for field in fields)


def _any_have_values(data: dict[str, Any], *fields: str) -> bool:
    return any(_has_value(data.get(field)) for field in fields)


def _direct_name_has_value(value: Any) -> bool:
    return len([part for part in _clean(value).split() if part]) >= 2


def _direct_city_state_zip_has_value(value: Any) -> bool:
    return bool(
        re.search(
            r"^[A-Za-z][A-Za-z .'-]{2,},\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?$",
            _clean(value),
        )
    )


def _city_state_zip_has_value(
    data: dict[str, Any],
    composite_field: str,
    city_field: str,
    state_field: str,
    zip_field: str,
) -> bool:
    if _any_have_values(data, city_field, state_field, zip_field):
        return _all_have_values(data, city_field, state_field, zip_field)
    return _direct_city_state_zip_has_value(data.get(composite_field))


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
    enriched.setdefault("seller_city", BUSINESS_CITY)
    enriched.setdefault("seller_state", BUSINESS_STATE)
    enriched.setdefault("seller_zip", BUSINESS_ZIP)
    enriched.setdefault("seller_city_state_zip", BUSINESS_CITY_STATE_ZIP)

    if _is_blank(enriched.get("buyer_name")) and _all_have_values(
        enriched, "buyer_first_name", "buyer_last_name"
    ):
        buyer_name = _join_non_empty(
            [enriched.get("buyer_first_name"), enriched.get("buyer_last_name")]
        )
        if buyer_name:
            enriched["buyer_name"] = buyer_name

    if _is_blank(enriched.get("buyer_city_state_zip")) and _all_have_values(
        enriched, "buyer_city", "buyer_state", "buyer_zip"
    ):
        buyer_city_state_zip = _join_non_empty(
            [
                enriched.get("buyer_city"),
                _join_non_empty([enriched.get("buyer_state"), enriched.get("buyer_zip")]),
            ],
            ", ",
        )
        if buyer_city_state_zip:
            enriched["buyer_city_state_zip"] = buyer_city_state_zip

    if _is_blank(enriched.get("buyer_full_address")) and _has_value(
        enriched.get("buyer_address")
    ) and _city_state_zip_has_value(
        enriched, "buyer_city_state_zip", "buyer_city", "buyer_state", "buyer_zip"
    ):
        buyer_full_address = _join_non_empty(
            [enriched.get("buyer_address"), enriched.get("buyer_city_state_zip")],
            ", ",
        )
        if buyer_full_address:
            enriched["buyer_full_address"] = buyer_full_address

    if _is_blank(enriched.get("manufacturer_model")) and _all_have_values(
        enriched, "manufacturer", "model"
    ):
        manufacturer_model = _join_non_empty([enriched.get("manufacturer"), enriched.get("model")])
        if manufacturer_model:
            enriched["manufacturer_model"] = manufacturer_model

    if _is_blank(enriched.get("manufacturer_model_hud")) and _all_have_values(
        enriched, "manufacturer_model", "label_number_1"
    ):
        manufacturer_model_hud = _join_non_empty(
            [enriched.get("manufacturer_model"), enriched.get("label_number_1")],
            " / ",
        )
        if manufacturer_model_hud:
            enriched["manufacturer_model_hud"] = manufacturer_model_hud

    if _is_blank(enriched.get("manufacturer_model_serial")) and _all_have_values(
        enriched, "manufacturer_model", "serial_number_1"
    ):
        manufacturer_model_serial = _join_non_empty(
            [enriched.get("manufacturer_model"), enriched.get("serial_number_1")],
            " / ",
        )
        if manufacturer_model_serial:
            enriched["manufacturer_model_serial"] = manufacturer_model_serial

    if _is_blank(enriched.get("manufacturer_city_state_zip")) and _all_have_values(
        enriched, "manufacturer_city", "manufacturer_state", "manufacturer_zip"
    ):
        manufacturer_city_state_zip = _join_non_empty(
            [
                enriched.get("manufacturer_city"),
                _join_non_empty(
                    [enriched.get("manufacturer_state"), enriched.get("manufacturer_zip")]
                ),
            ],
            ", ",
        )
        if manufacturer_city_state_zip:
            enriched["manufacturer_city_state_zip"] = manufacturer_city_state_zip

    if _is_blank(enriched.get("manufacturer_full_address")) and _has_value(
        enriched.get("manufacturer_address")
    ) and _city_state_zip_has_value(
        enriched,
        "manufacturer_city_state_zip",
        "manufacturer_city",
        "manufacturer_state",
        "manufacturer_zip",
    ):
        manufacturer_full_address = _join_non_empty(
            [enriched.get("manufacturer_address"), enriched.get("manufacturer_city_state_zip")],
            ", ",
        )
        if manufacturer_full_address:
            enriched["manufacturer_full_address"] = manufacturer_full_address

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


def _required_field_has_value(data: dict[str, Any], field: str) -> bool:
    if field == "sales_price":
        amount = _decimal(data.get(field))
        return amount is not None and amount > 0
    if field == "buyer_name":
        if _any_have_values(data, "buyer_first_name", "buyer_last_name"):
            return _all_have_values(data, "buyer_first_name", "buyer_last_name")
        return _direct_name_has_value(data.get("buyer_name"))
    if field == "buyer_city_state_zip":
        return _city_state_zip_has_value(
            data, "buyer_city_state_zip", "buyer_city", "buyer_state", "buyer_zip"
        )
    if field == "buyer_full_address":
        if _any_have_values(data, "buyer_address", "buyer_city", "buyer_state", "buyer_zip"):
            return _has_value(data.get("buyer_address")) and _required_field_has_value(
                data, "buyer_city_state_zip"
            )
        return _has_value(data.get("buyer_full_address"))
    if field == "manufacturer_model":
        if _any_have_values(data, "manufacturer", "model"):
            return _all_have_values(data, "manufacturer", "model")
        return _has_value(data.get("manufacturer_model"))
    if field == "manufacturer_model_hud":
        return _required_field_has_value(data, "manufacturer_model") and _has_value(
            data.get("label_number_1")
        )
    if field == "manufacturer_model_serial":
        return _required_field_has_value(data, "manufacturer_model") and _has_value(
            data.get("serial_number_1")
        )
    if field == "manufacturer_city_state_zip":
        return _city_state_zip_has_value(
            data,
            "manufacturer_city_state_zip",
            "manufacturer_city",
            "manufacturer_state",
            "manufacturer_zip",
        )
    if field == "manufacturer_full_address":
        if _any_have_values(
            data,
            "manufacturer_address",
            "manufacturer_city",
            "manufacturer_state",
            "manufacturer_zip",
        ):
            return _has_value(data.get("manufacturer_address")) and _required_field_has_value(
                data, "manufacturer_city_state_zip"
            )
        return _has_value(data.get("manufacturer_full_address"))

    if _has_value(data.get(field)):
        return True

    aliases = REQUIRED_FIELD_ALIASES.get(field, ())
    return any(_has_value(data.get(alias)) for alias in aliases)


def validate_required_document_data(
    data: dict[str, Any],
    required_fields: list[str] | tuple[str, ...] | set[str] | None,
) -> list[DocumentQualityIssue]:
    """Validate template-declared required fields before PDF generation.

    The UI uses the same metadata, but this backend gate protects direct API
    callers and prevents a partial deal from producing official-looking PDFs.
    """
    enriched = enrich_document_data(data)
    issues: list[DocumentQualityIssue] = []
    seen: set[str] = set()

    for field in required_fields or []:
        if not field or field in seen:
            continue
        seen.add(field)
        if _required_field_has_value(enriched, field):
            continue
        label = REQUIRED_FIELD_LABELS.get(field, field.replace("_", " ").title())
        message = f"{label} is required before generating documents."
        if field == "sales_price":
            message = (
                "Sales price is required and must be greater than $0 before generating documents."
            )
        issues.append(
            DocumentQualityIssue(
                code="missing_required_field",
                field=field,
                message=message,
            )
        )

    return issues


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
    missing_fields = [
        issue.field for issue in issues if issue.code == "missing_required_field" and issue.field
    ]
    if missing_fields:
        missing_labels = [
            REQUIRED_FIELD_LABELS.get(field, field.replace("_", " ").title())
            for field in missing_fields
        ]
        first = f"Missing required fields: {', '.join(missing_labels)}"
    return {
        "success": False,
        "error": "missing_required_fields" if missing_fields else "quality_gate_failed",
        "message": first,
        "quality_issues": [issue.to_dict() for issue in issues],
        "missing_fields": missing_fields,
    }
