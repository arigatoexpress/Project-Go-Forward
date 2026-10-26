"""Mark Willcott's 2026-10-26 TMHA sales-contract changes.

Fake buyer only (Jordan Brooks). Three items stay open and are not decided here:

* whether "or 5% of the payment, whichever is less" stays when $20 becomes $25
* whether escrow belongs in Total of Payments (flag stays False)
* whether 10685 FM 1960 East, Huffman, TX 77336 is the Prosperity address
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from pypdf import PdfReader

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools.document_tools as document_tools  # noqa: E402
from database.models import (  # noqa: E402
    Deal,
    DealPayloadError,
    parse_deal_create,
    parse_deal_update,
)
from tools.document_engine import _compute_fields  # noqa: E402
from tools.document_engine_v2 import FinancialModel  # noqa: E402
from tools.document_quality import (  # noqa: E402
    TOTAL_OF_PAYMENTS_INCLUDES_ESCROW,
    enrich_document_data,
    monthly_escrow_amount,
)

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "tho_documents" / "TMHA_SalesContract.pdf"

INSURANCE_YES = "topmostSubform[0].Page3[0].Insurance_Yes[0]"
INSURANCE_INCLUDED = "topmostSubform[0].Page3[0].Insurance_Included_Yes[0]"
TAX_BOX = "topmostSubform[0].Page4[0].Tax_Escrow_Included_Yes[0]"
INSURANCE_PREMIUM = "topmostSubform[0].Page3[0].Insurance_Premium[0]"
CREDITOR_ADDRESS = "topmostSubform[0].Page4[0].Creditor_Address[0]"
CREDITOR_CITY = "topmostSubform[0].Page4[0].Creditor_City_State_Zip[0]"
PHONE_FIELDS = tuple(
    f"topmostSubform[0].Page4[0].Creditor_Phone_{index}[0]" for index in range(1, 11)
)

# Template checkbox measured from "56.88 536.52 -10.98 -10.98 re".
FURNISH_BOX = (45.90, 525.54, 56.88, 536.52)
# Printed square on "The first year's premium is included in the Amount Financed."
# No AcroForm widget covers it. Insurance_Included_Yes is the line above.
AMOUNT_FINANCED_BOX = (103.50, 197.46, 108.78, 202.74)

MARK_NUMBERS = {
    "sales_price": "87000",
    "down_payment": "10000",
    "apr": "12.84",
    "loan_term": "276",
    "tax_rate": "2.5",
    "insurance_annual_premium": "2400",
}


def _sample_module():
    path = ROOT / "scripts" / "render_mark_contract_sample.py"
    spec = importlib.util.spec_from_file_location("render_mark_contract_sample", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _short_field(name: str) -> str:
    return name.split(".")[-1]


def _money(value: Decimal) -> str:
    return f"{value:,.2f}"


def _field_values(reader: PdfReader) -> dict[str, str | None]:
    return {
        name: (None if field.get("/V") is None else str(field.get("/V")))
        for name, field in (reader.get_fields() or {}).items()
    }


def _box_states(reader: PdfReader) -> dict[str, str | None]:
    states: dict[str, str | None] = {}
    for page in reader.pages:
        for annot in page.get("/Annots") or []:
            widget = annot.get_object()
            if widget.get("/Subtype") != "/Widget":
                continue
            name = widget.get("/T")
            if name is None and widget.get("/Parent") is not None:
                name = widget["/Parent"].get_object().get("/T")
            if name is not None:
                appearance = widget.get("/AS")
                states[str(name)] = None if appearance is None else str(appearance)
    return states


def _page_text(path: Path, page: int) -> str:
    return subprocess.check_output(
        ["pdftotext", "-layout", "-f", str(page), "-l", str(page), str(path), "-"],
        text=True,
    )


def _financial(enriched: dict) -> FinancialModel:
    return FinancialModel(
        sales_price=enriched["sales_price"],
        down_payment=enriched["down_payment"],
        apr=enriched["apr"],
        loan_term=enriched["loan_term"],
        monthly_escrow=str(monthly_escrow_amount(enriched)),
    )


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    directory = tmp_path_factory.mktemp("mark-1026")
    previous = document_tools.OUTPUT_DIR
    document_tools.OUTPUT_DIR = str(directory)
    os.environ["THO_DISABLE_GCS_UPLOADS"] = "1"
    try:
        paths = _sample_module().write_review_pdfs(directory)
    finally:
        document_tools.OUTPUT_DIR = previous
    by_name = {path.name: path for path in paths}
    return {
        "customer_path": by_name["mark-1026-customer-paid.pdf"],
        "financed_path": by_name["mark-1026-financed-prosperity.pdf"],
        "customer": PdfReader(str(by_name["mark-1026-customer-paid.pdf"])),
        "financed": PdfReader(str(by_name["mark-1026-financed-prosperity.pdf"])),
    }


def test_template_file_still_prints_twenty_dollars():
    data = PdfReader(str(TEMPLATE)).pages[1].get_contents().get_data()
    assert data.count(b"(20.00)") == 1
    assert b"(25.00)" not in data


def test_total_of_payments_excludes_escrow_until_mark_answers():
    """Needs Mark's own yes/no. Celeste said escrow is not in Total of Payments."""
    assert TOTAL_OF_PAYMENTS_INCLUDES_ESCROW is False
    enriched = enrich_document_data(dict(MARK_NUMBERS))
    assert enriched["total_monthly_payment"] == "1,251.26"
    assert enriched["monthly_payment"] == "870.01"
    assert enriched["tax_escrow_payment"] == "181.25"
    assert enriched["insurance_premium_monthly"] == "200.00"
    assert enriched["total_payments"] == "240,122.76"
    assert enriched["finance_charge"] == "163,122.76"
    payer_times_term = Decimal("1251.26") * Decimal("276")
    assert enriched["total_payments"] != _money(payer_times_term)


def test_customer_paid_leaves_page1_cash_price_unchanged():
    enriched = enrich_document_data(
        {**MARK_NUMBERS, "insurance_first_year_paid_by": "customer"}
    )
    assert enriched["sales_price"] == "87000"
    assert enriched.get("sales_price_includes_financed_insurance") is not True
    assert enriched["unpaid_balance"] == "77,000.00"
    assert enriched["insurance_premium"] == "2,400.00"
    assert enriched["insurance_included"] is True
    assert enriched["insurance_furnish_term"] == "12"
    assert enriched["insurance_furnish_premium"] == "2,400.00"
    assert enriched["insurance_furnish_yes"] is True
    assert enriched["tax_escrow_payment"] == "181.25"


def test_financed_premium_is_added_to_cash_price_once():
    payload = {**MARK_NUMBERS, "insurance_first_year_paid_by": "financed"}
    once = enrich_document_data(dict(payload))
    twice = enrich_document_data(dict(once))
    assert once["sales_price"] == "89,400.00"
    assert once["home_sales_price"] == "87,000.00"
    assert once["unpaid_balance"] == "79,400.00"
    assert once["total_unpaid_balance"] == "79,400.00"
    assert once["sales_price_includes_financed_insurance"] is True
    assert twice["sales_price"] == "89,400.00"
    assert twice["home_sales_price"] == "87,000.00"
    assert once["insurance_premium"] == ""
    assert once["insurance_included"] is False
    assert once["tax_escrow_payment"] == "181.25"
    assert once["insurance_premium_monthly"] == "200.00"
    # The year's premium is not a separate "paid to others" line.
    assert "total_paid_to_others" not in once


def test_new_annual_field_wins_over_legacy_annual_insurance():
    enriched = enrich_document_data(
        {
            "sales_price": "87000",
            "down_payment": "10000",
            "insurance_annual_premium": "2400",
            "annual_insurance": "1200",
            "insurance_first_year_paid_by": "financed",
        }
    )
    assert enriched["sales_price"] == "89,400.00"
    assert enriched["insurance_premium_monthly"] == "200.00"
    assert enriched["annual_insurance"] == "2,400.00"


def test_legacy_annual_insurance_still_fills_escrow_and_you_have_paid():
    enriched = enrich_document_data(
        {"sales_price": "87000", "down_payment": "10000", "annual_insurance": "1200"}
    )
    assert enriched["insurance_annual_premium"] == "1,200.00"
    assert enriched["insurance_premium"] == "1,200.00"
    assert enriched["insurance_included"] is True
    assert enriched["sales_price"] == "87000"
    assert enriched["insurance_premium_monthly"] == "100.00"


def test_staff_entered_you_have_paid_amount_is_kept():
    enriched = enrich_document_data(
        {
            "annual_insurance": "2400",
            "insurance_premium": "100",
            "insurance_included": False,
        }
    )
    assert enriched["insurance_premium"] == "100"
    assert enriched["insurance_included"] is False


def test_monthly_only_deal_does_not_check_you_have_paid():
    monthly_only = {
        "sales_price": "87000",
        "down_payment": "10000",
        "apr": "12.84",
        "loan_term": "276",
        "tax_rate": "2.5",
        "insurance_premium_monthly": "200",
    }
    enriched = enrich_document_data(monthly_only)
    assert "insurance_included" not in enriched
    assert "insurance_premium" not in enriched
    assert enriched["total_monthly_payment"] == "1,251.26"


def test_legacy_engine_taxes_the_home_price_when_insurance_is_financed():
    data = {**MARK_NUMBERS, "insurance_first_year_paid_by": "financed"}
    _compute_fields(data)
    assert data["sales_price"] == "89,400.00"
    assert data["home_sales_price"] == "87,000.00"
    assert data["tax_escrow_payment"] == "181.25"
    assert float(data["unpaid_balance"]) == 79400.0


def test_old_deal_without_the_new_fields_still_loads():
    payload = {
        "buyer_first_name": "Jordan",
        "buyer_last_name": "Brooks",
        "sales_price": 87000,
        "down_payment": 10000,
        "annual_insurance": 2400,
        "insurance_premium": 50,
        "loan_term": "276",
        "apr": "12.84",
    }
    deal = parse_deal_create(payload)
    assert deal.insurance_annual_premium is None
    assert deal.insurance_first_year_paid_by == "customer"
    assert deal.annual_insurance == 2400
    assert deal.insurance_premium == 50
    loaded = Deal.model_validate(
        {"sales_price": 87000, "creditor_name": "21st Mortgage"}
    )
    assert loaded.insurance_first_year_paid_by == "customer"
    assert loaded.insurance_annual_premium is None
    blank = parse_deal_create({"insurance_first_year_paid_by": "  "})
    assert blank.insurance_first_year_paid_by == "customer"
    titled = parse_deal_create({"insurance_first_year_paid_by": "Financed"})
    assert titled.insurance_first_year_paid_by == "financed"
    with pytest.raises(DealPayloadError):
        parse_deal_create({"insurance_first_year_paid_by": "escrow"})
    update = parse_deal_update({"sales_price": 87000})
    assert "insurance_first_year_paid_by" not in update
    assert "insurance_annual_premium" not in update
    document = deal.to_document_data()
    enriched = enrich_document_data(document)
    # The stored "you have paid" figure is a different fact from the year's cost.
    assert enriched["insurance_premium"] == 50
    assert enriched.get("sales_price_includes_financed_insurance") is not True


def test_customer_paid_contract_prints_marks_sample(rendered):
    path = rendered["customer_path"]
    page1 = _page_text(path, 1)
    page2 = _page_text(path, 2)
    page3 = _page_text(path, 3)
    assert "87,000.00" in page1
    assert "77,000.00" in page1
    assert "89,400.00" not in page1
    assert "2,400.00" not in page1
    assert "1,251.26" in page2
    assert "276 monthly payments of $1,251.26" in page2
    assert "163,122.76" in page2
    assert "240,122.76" in page2
    assert "$25.00 or 5% of the payment, whichever is less." in page2
    assert "$20.00" not in page2
    assert re.search(
        r"You will furnish the required insurance for a term of\s+12\s+"
        r"months at an estimated premium of \$\s*2,400\.00",
        page3,
    )
    values = _field_values(rendered["customer"])
    states = _box_states(rendered["customer"])
    assert values[INSURANCE_PREMIUM] == "2,400.00"
    assert values[INSURANCE_YES] == "/1"
    assert states[_short_field(INSURANCE_YES)] == "/1"
    assert values[INSURANCE_INCLUDED] == "/1"
    assert states[_short_field(INSURANCE_INCLUDED)] == "/1"
    assert values[TAX_BOX] == "/1"
    assert states[_short_field(TAX_BOX)] == "/1"
    assert values[CREDITOR_ADDRESS] is None
    _assert_furnish_checkmark(rendered["customer"])


def test_financed_contract_prints_cash_price_and_blank_prepaid_line(rendered):
    enriched = enrich_document_data(
        {
            **MARK_NUMBERS,
            "insurance_first_year_paid_by": "financed",
            "creditor_name": "Prosperity Acquisitions, Inc. dba Texas Home Outlet",
        }
    )
    fin = _financial(enriched)
    path = rendered["financed_path"]
    page1 = _page_text(path, 1)
    page2 = _page_text(path, 2)
    assert "89,400.00" in page1
    assert "79,400.00" in page1
    assert "87,000.00" not in page1
    assert "2,400.00" not in page1
    assert _money(fin.total_monthly_payment) in page2
    assert _money(fin.total_of_payments) in page2
    assert _money(fin.finance_charge) in page2
    escrow_total = (fin.total_monthly_payment * Decimal(fin.loan_term)).quantize(Decimal("0.01"))
    assert _money(escrow_total) not in page2
    assert fin.total_of_payments == fin.monthly_payment * Decimal(fin.loan_term)
    assert "$25.00 or 5% of the payment, whichever is less." in page2
    values = _field_values(rendered["financed"])
    states = _box_states(rendered["financed"])
    assert values[INSURANCE_PREMIUM] is None
    assert values[INSURANCE_INCLUDED] == "/Off"
    assert states[_short_field(INSURANCE_INCLUDED)] == "/Off"
    assert values[INSURANCE_YES] == "/1"
    assert states[_short_field(INSURANCE_YES)] == "/1"
    assert values[TAX_BOX] == "/1"
    assert states[_short_field(TAX_BOX)] == "/1"
    # Autofill is off until Mark confirms the lot address, so the block stays blank.
    assert values[CREDITOR_ADDRESS] is None
    assert values[CREDITOR_CITY] is None
    assert "".join(values[field] or "" for field in PHONE_FIELDS) == ""
    _assert_furnish_checkmark(rendered["financed"])
    # "The first year's premium is included in the Amount Financed." has a
    # printed square and no AcroForm field. Leave it unmarked for Mark.
    _assert_no_mark_in_box(rendered["financed"], AMOUNT_FINANCED_BOX)


def _assert_no_mark_in_box(reader: PdfReader, box: tuple[float, float, float, float]) -> None:
    contents = reader.pages[2].get_contents().get_data()
    left, bottom, right, top = box
    moves = re.findall(rb"([\d.]+) ([\d.]+) m\n([\d.]+) ([\d.]+) l", contents)
    for x1, y1, x2, y2 in moves:
        points = ((float(x1), float(y1)), (float(x2), float(y2)))
        if all(left <= x <= right and bottom <= y <= top for x, y in points):
            raise AssertionError(f"unexpected mark inside {box}")


def _assert_furnish_checkmark(reader: PdfReader):
    """The IX.B box has no widget. The drawn check must land inside that box."""
    contents = reader.pages[2].get_contents().get_data()
    left, bottom, right, top = FURNISH_BOX
    moves = re.findall(rb"([\d.]+) ([\d.]+) m\n([\d.]+) ([\d.]+) l", contents)
    inside = []
    for x1, y1, x2, y2 in moves:
        points = ((float(x1), float(y1)), (float(x2), float(y2)))
        if all(left <= x <= right and bottom <= y <= top for x, y in points):
            inside.append(points)
    assert len(inside) >= 2
