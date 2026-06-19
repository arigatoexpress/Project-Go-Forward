"""Document Center quality gates.

These checks are intentionally conservative: it is better to block a packet and
tell the sales rep exactly what to fix than to produce a closing PDF with fake
serials, placeholder HUD labels, or partially-filled lender note pages.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

# Registered legal entity of record for all closing documents. Per founding
# partner directive (2026-05-18): the company is "Prosperity Acquisitions, Inc.
# dba Texas Home Outlet" (no longer an LLC). Documents that do not name the
# correct INC entity are rejected and must be redone, so this is the seller /
# installer name baked into every generated contract and disclosure.
BUSINESS_NAME = "Prosperity Acquisitions, Inc. dba Texas Home Outlet"
BUSINESS_PHONE = "(281) 324-3020"
BUSINESS_ADDRESS = "10685 FM 1960 East"
BUSINESS_CITY = "Huffman"
BUSINESS_STATE = "TX"
BUSINESS_ZIP = "77336"
BUSINESS_CITY_STATE_ZIP = "Huffman, TX 77336"
BUSINESS_RBI = "35248"


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
    "no_of_sections": "Number of sections",
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
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _int(value: Any) -> int | None:
    text = _clean(value)
    if not text:
        return None
    match = re.search(r"\d+", text)
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def _compute_monthly_payment(
    amount_financed: Decimal,
    loan_term: int,
    apr: Decimal,
) -> Decimal | None:
    if loan_term <= 0:
        return None
    monthly_rate = (apr / Decimal("100")) / Decimal("12")
    if monthly_rate <= 0:
        return _round_money(amount_financed / Decimal(loan_term))
    power = (Decimal("1") + monthly_rate) ** loan_term
    return _round_money(amount_financed * monthly_rate * power / (power - Decimal("1")))


def normalize_section_count(value: Any) -> str:
    """Return the compact section count expected by tiny PDF fields."""
    text = _clean(value)
    if not text:
        return ""
    lowered = text.lower()
    word_counts = {
        "single": "1",
        "one": "1",
        "double": "2",
        "two": "2",
        "triple": "3",
        "three": "3",
        "quad": "4",
        "four": "4",
    }
    for word, count in word_counts.items():
        if re.search(rf"\b{word}\b", lowered):
            return count
    match = re.search(r"\b([1-4])\b", lowered)
    if match:
        return match.group(1)
    return text


def _join_non_empty(values: list[Any], separator: str = " ") -> str:
    clean = [_clean(value) for value in values if _has_value(value)]
    return separator.join(clean)


def _city_state_zip(city: Any, state: Any, zip_code: Any) -> str:
    if not (_has_value(city) or _has_value(zip_code)):
        return ""
    city_part = _clean(city)
    state_part = _clean(state) or "TX"
    zip_part = _clean(zip_code)
    prefix = f"{city_part}, " if city_part else ""
    return f"{prefix}{state_part} {zip_part}".strip()


def _full_address(address: Any, city_state_zip: Any) -> str:
    if not _has_value(address):
        return ""
    address_part = _clean(address)
    if _has_value(city_state_zip):
        return f"{address_part}, {_clean(city_state_zip)}"
    return address_part


def _all_have_values(data: dict[str, Any], *fields: str) -> bool:
    return all(_has_value(data.get(field)) for field in fields)


def _any_have_values(data: dict[str, Any], *fields: str) -> bool:
    return any(_has_value(data.get(field)) for field in fields)


def _set_if_blank(data: dict[str, Any], key: str, value: Any) -> None:
    if _is_blank(data.get(key)):
        data[key] = value


def _direct_name_has_value(value: Any) -> bool:
    return len([part for part in _clean(value).split() if part]) >= 2


def _direct_city_state_zip_has_value(value: Any) -> bool:
    return bool(
        re.search(
            r"^[A-Za-z][A-Za-z .'-]{2,},\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?$",
            _clean(value),
        )
    )


def _direct_full_address_has_value(value: Any) -> bool:
    text = _clean(value)
    match = re.search(
        r"^(.+?),\s*[A-Za-z][A-Za-z .'-]{2,},\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?$",
        text,
    )
    if not match:
        return False
    street_part = match.group(1)
    return bool(re.search(r"[A-Za-z]", street_part) and re.search(r"\d", street_part))


def _city_state_zip_has_value(
    data: dict[str, Any],
    composite_field: str,
    city_field: str,
    state_field: str,
    zip_field: str,
) -> bool:
    if _direct_city_state_zip_has_value(data.get(composite_field)):
        return True
    if _any_have_values(data, city_field, state_field, zip_field):
        return _all_have_values(data, city_field, state_field, zip_field)
    return False


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


def _coerce_bool(value: Any) -> bool | None:
    """Interpret a stored New/Used flag. Returns None when unset/ambiguous."""
    if isinstance(value, bool):
        return value
    text = _clean(value).lower()
    if not text:
        return None
    if text in {"true", "yes", "y", "on", "1", "new"}:
        return True
    if text in {"false", "no", "n", "off", "0", "used", "pre-owned", "preowned"}:
        return False
    return None


def _normalize_new_used(data: dict[str, Any]) -> None:
    """Resolve is_new / is_used / new_used_text from whatever signal is present.

    Priority: an explicit ``condition`` string ("New"/"Used"), then is_new, then
    is_used. Exactly one of is_new/is_used is set so the mutually-exclusive
    checkbox pair never renders both-blank. Mirrors the frontend toDocumentData
    derivation so non-frontend callers behave identically.
    """
    condition = _clean(data.get("condition")).lower()
    is_new = _coerce_bool(data.get("is_new"))
    is_used = _coerce_bool(data.get("is_used"))

    resolved_new: bool
    if condition in {"new", "used", "pre-owned", "preowned"}:
        resolved_new = condition == "new"
    elif is_new is not None:
        resolved_new = is_new
    elif is_used is not None:
        resolved_new = not is_used
    else:
        # No signal at all: leave the flags untouched so the engine's own
        # default (is_new=True) applies without us masking missing data.
        return

    data["is_new"] = resolved_new
    data["is_used"] = not resolved_new
    if _is_blank(data.get("new_used_text")):
        data["new_used_text"] = "New" if resolved_new else "Used"


def enrich_document_data(data: dict[str, Any]) -> dict[str, Any]:
    """Return data with safe seller and computed aliases filled.

    This does not invent customer/home identifiers. It only fills dealership
    seller fields and deterministic financing aliases from fields already
    present in the form.
    """
    enriched = dict(data or {})

    section_count = normalize_section_count(
        enriched.get("no_of_sections") or enriched.get("sections")
    )
    if section_count:
        enriched["no_of_sections"] = section_count

    # New/Used condition is the single source of truth for the New/Used checkbox
    # (contract + Statement of Ownership + R020) and the "New/Used" text field.
    # The frontend derives is_used from the is_new toggle; mirror it here so the
    # deal-based path and direct API callers fill the checkbox consistently
    # instead of leaving both New and Used boxes blank (Mark Willcott review).
    _normalize_new_used(enriched)

    _set_if_blank(enriched, "seller_name", BUSINESS_NAME)
    _set_if_blank(enriched, "seller_rbi", BUSINESS_RBI)
    _set_if_blank(enriched, "seller_phone", BUSINESS_PHONE)
    _set_if_blank(enriched, "seller_address", BUSINESS_ADDRESS)
    _set_if_blank(enriched, "seller_city", BUSINESS_CITY)
    _set_if_blank(enriched, "seller_state", BUSINESS_STATE)
    _set_if_blank(enriched, "seller_zip", BUSINESS_ZIP)
    _set_if_blank(enriched, "seller_city_state_zip", BUSINESS_CITY_STATE_ZIP)

    if _clean(enriched.get("installer_type")).lower() != "other":
        _set_if_blank(enriched, "installer_name", BUSINESS_NAME)
        _set_if_blank(enriched, "installer_phone", BUSINESS_PHONE)
        _set_if_blank(enriched, "installer_address", BUSINESS_ADDRESS)
        _set_if_blank(enriched, "installer_city", BUSINESS_CITY)
        _set_if_blank(enriched, "installer_state", BUSINESS_STATE)
        _set_if_blank(enriched, "installer_zip", BUSINESS_ZIP)

    if _is_blank(enriched.get("installer_city_state_zip")) and _all_have_values(
        enriched, "installer_city", "installer_state", "installer_zip"
    ):
        installer_city_state_zip = _city_state_zip(
            enriched.get("installer_city"),
            enriched.get("installer_state"),
            enriched.get("installer_zip"),
        )
        if installer_city_state_zip:
            enriched["installer_city_state_zip"] = installer_city_state_zip

    if _is_blank(enriched.get("installer_address_city_state_zip")) and _has_value(
        enriched.get("installer_address")
    ):
        installer_full_address = _full_address(
            enriched.get("installer_address"),
            enriched.get("installer_city_state_zip"),
        )
        if installer_full_address:
            enriched["installer_address_city_state_zip"] = installer_full_address

    if _is_blank(enriched.get("installer_name_address")) and _has_value(
        enriched.get("installer_name")
    ):
        installer_name_address = _join_non_empty(
            [
                enriched.get("installer_name"),
                enriched.get("installer_address_city_state_zip"),
            ],
            ", ",
        )
        if installer_name_address:
            enriched["installer_name_address"] = installer_name_address

    if _is_blank(enriched.get("installer_contact_name")) and _has_value(
        enriched.get("installer_name")
    ):
        enriched["installer_contact_name"] = _clean(enriched.get("installer_name"))

    if _is_blank(enriched.get("installer_contact_phone")) and _has_value(
        enriched.get("installer_phone")
    ):
        enriched["installer_contact_phone"] = _clean(enriched.get("installer_phone"))

    if _is_blank(enriched.get("buyer_name")) and _all_have_values(
        enriched, "buyer_first_name", "buyer_last_name"
    ):
        buyer_name = _join_non_empty(
            [enriched.get("buyer_first_name"), enriched.get("buyer_last_name")]
        )
        if buyer_name:
            enriched["buyer_name"] = buyer_name

    # Co-buyer name mirrors the buyer composition. Without this the co-buyer line
    # renders blank on every co-buyer document (Statement of Ownership co-owner,
    # Consumer Disclosure, Credit App) whenever the data arrives as first/last
    # parts — e.g. the deal-based path and any caller that did not pre-compose
    # the name on the client. See the highlighted-blanks report (Mark Willcott).
    if _is_blank(enriched.get("co_buyer_name")) and _all_have_values(
        enriched, "co_buyer_first_name", "co_buyer_last_name"
    ):
        co_buyer_name = _join_non_empty(
            [enriched.get("co_buyer_first_name"), enriched.get("co_buyer_last_name")]
        )
        if co_buyer_name:
            enriched["co_buyer_name"] = co_buyer_name

    # Loan number doubles as the Portfolio/Lender reference that the
    # Compliance Agreement and two-party contract map via their Portfolio[0]
    # widget. Backfill portfolio from loan_number so the deal-based path renders
    # it consistently with the frontend toDocumentData derivation (item A2/A8).
    if _is_blank(enriched.get("portfolio")) and _has_value(enriched.get("loan_number")):
        enriched["portfolio"] = _clean(enriched.get("loan_number"))

    # Compliance Agreement borrower line. The agreement names the borrower(s)
    # separately from the buyer; default to the composed buyer (+ co-buyer)
    # names so the agreement is not blank on the deal-based path (item A8).
    if _is_blank(enriched.get("compliance_borrowers")):
        compliance_borrowers = _join_non_empty(
            [enriched.get("buyer_name"), enriched.get("co_buyer_name")],
            " & ",
        )
        if compliance_borrowers:
            enriched["compliance_borrowers"] = compliance_borrowers

    # Combined purchaser names for forms whose single purchaser-name widget sits
    # on a plural "(purchaser(s) name(s))" / "Buyer(s)" line and has NO separate
    # co-buyer widget (arbitration agreements, A/C acknowledgement, TMHA limited
    # warranty PURCHASER line). Without this the co-buyer line renders blank on
    # those forms (Mark Willcott review, B3/B4). Mirrors compliance_borrowers and
    # degrades to the buyer name alone when there is no co-buyer.
    if _is_blank(enriched.get("buyers_combined")):
        buyers_combined = _join_non_empty(
            [enriched.get("buyer_name"), enriched.get("co_buyer_name")],
            " & ",
        )
        if buyers_combined:
            enriched["buyers_combined"] = buyers_combined

    # Mailing address composites (used by the credit application and several
    # state disclosures). Mirror the buyer_city_state_zip / buyer_full_address
    # composition so the mailing block is not blank when only the parts are sent.
    if _is_blank(enriched.get("mailing_city_state_zip")) and _all_have_values(
        enriched, "mailing_city", "mailing_state", "mailing_zip"
    ):
        mailing_city_state_zip = _join_non_empty(
            [
                enriched.get("mailing_city"),
                _join_non_empty([enriched.get("mailing_state"), enriched.get("mailing_zip")]),
            ],
            ", ",
        )
        if mailing_city_state_zip:
            enriched["mailing_city_state_zip"] = mailing_city_state_zip

    if (
        _is_blank(enriched.get("mailing_full_address"))
        and _has_value(enriched.get("mailing_address"))
        and _city_state_zip_has_value(
            enriched, "mailing_city_state_zip", "mailing_city", "mailing_state", "mailing_zip"
        )
    ):
        mailing_full_address = _join_non_empty(
            [enriched.get("mailing_address"), enriched.get("mailing_city_state_zip")],
            ", ",
        )
        if mailing_full_address:
            enriched["mailing_full_address"] = mailing_full_address

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

    if (
        _is_blank(enriched.get("buyer_full_address"))
        and _has_value(enriched.get("buyer_address"))
        and _city_state_zip_has_value(
            enriched, "buyer_city_state_zip", "buyer_city", "buyer_state", "buyer_zip"
        )
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

    if _is_blank(enriched.get("serial_label_combined")) and (
        _has_value(enriched.get("serial_number_1")) or _has_value(enriched.get("label_number_1"))
    ):
        serial_label_parts = [
            f"S/N: {_clean(enriched['serial_number_1'])}"
            if _has_value(enriched.get("serial_number_1"))
            else None,
            f"S/N2: {_clean(enriched['serial_number_2'])}"
            if _has_value(enriched.get("serial_number_2"))
            else None,
            f"HUD: {_clean(enriched['label_number_1'])}"
            if _has_value(enriched.get("label_number_1"))
            else None,
            f"HUD2: {_clean(enriched['label_number_2'])}"
            if _has_value(enriched.get("label_number_2"))
            else None,
        ]
        serial_label_combined = _join_non_empty(serial_label_parts, " | ")
        if serial_label_combined:
            enriched["serial_label_combined"] = serial_label_combined

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

    if (
        _is_blank(enriched.get("manufacturer_full_address"))
        and _has_value(enriched.get("manufacturer_address"))
        and _city_state_zip_has_value(
            enriched,
            "manufacturer_city_state_zip",
            "manufacturer_city",
            "manufacturer_state",
            "manufacturer_zip",
        )
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
        loan_amount = _round_money(sales_price - down_payment)
        for key in ("unpaid_balance", "total_unpaid_balance", "max_financed"):
            enriched[key] = _money(loan_amount)

    apr = _decimal(enriched.get("apr"))
    if apr is not None and _is_blank(enriched.get("interest_rate")):
        enriched["interest_rate"] = str(apr.normalize())

    monthly_payment = _decimal(enriched.get("monthly_payment"))
    loan_term = _int(enriched.get("loan_term")) or 240
    if monthly_payment is None and loan_amount is not None:
        monthly_payment = _compute_monthly_payment(loan_amount, loan_term, apr or Decimal("7.5"))

    if monthly_payment is not None:
        enriched["total_monthly_payment"] = _money(monthly_payment)
        if _is_blank(enriched.get("monthly_payment")):
            enriched["monthly_payment"] = _money(monthly_payment)

    if loan_amount is not None and monthly_payment is not None:
        total_payments = _round_money(monthly_payment * Decimal(loan_term))
        total_paid = _round_money(total_payments + down_payment)
        finance_charge = _round_money(total_payments - loan_amount)
        enriched["total_payments"] = _money(total_payments)
        enriched["total_paid"] = _money(total_paid)
        enriched["total_sale_price"] = _money(total_paid)
        enriched["finance_charge"] = _money(finance_charge)
        if _is_blank(enriched.get("payment_breakdown")):
            enriched["payment_breakdown"] = (
                f"{loan_term} monthly payments of ${_money(monthly_payment)}"
            )

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
        if _direct_full_address_has_value(data.get("buyer_full_address")):
            return True
        if _any_have_values(data, "buyer_address", "buyer_city", "buyer_state", "buyer_zip"):
            return _has_value(data.get("buyer_address")) and _required_field_has_value(
                data, "buyer_city_state_zip"
            )
        return False
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
        if _direct_full_address_has_value(data.get("manufacturer_full_address")):
            return True
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
        return False

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
