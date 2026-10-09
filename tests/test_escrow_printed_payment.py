"""Printed-value regression for the escrow-inclusive monthly payment.

PR #360 (2026-10-01) added tax + insurance escrow to the monthly payment, but the
v2 engine still printed principal-and-interest (870.01) on the TMHA retail
installment contract (page 2) and on the property tax notice, and the notice's
"Estimated Personal Property Tax" line (Tax_Escrow_Pmt[1]) printed blank.

These tests render the real PDFs through the live entrypoint
(``main.engine_generate_document``) with Mark's sample deal numbers only and
assert the values actually printed:

  price 87,000; down 10,000; rate 12.84%; 276 payments;
  2.5% tax rate (181.25/mo); insurance 200/mo
  P&I 870.01 + 181.25 + 200.00 = 1,251.26

Contract page 2 is drawn as a text overlay (its AcroForm values are suppressed),
so it is read from the page text; the tax notice is read from AcroForm /V.
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal

from pypdf import PdfReader

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import document_quality  # noqa: E402
from tools.document_engine_v2 import FinancialModel  # noqa: E402
from tools.document_quality import enrich_document_data  # noqa: E402

# Fictional buyer/home identity; financial figures are Mark's sample deal.
DEAL: dict[str, object] = {
    "buyer_first_name": "Jordan",
    "buyer_last_name": "Brooks",
    "buyer_address": "456 Meadow Lane",
    "buyer_city": "Austin",
    "buyer_county": "Travis",
    "buyer_state": "TX",
    "buyer_zip": "78701",
    "is_new": True,
    "manufacturer": "TRU Homes",
    "model": "The Marvel",
    "year": "2026",
    "serial_number_1": "TRU0987654A",
    "serial_number_2": "TRU0987654B",
    "label_number_1": "TEX0482913",
    "label_number_2": "TEX0482914",
    "no_of_sections": "Double Section",
    "date_of_manufacture": "2026-01-15",
    "manufacturer_address": "500 Factory Road",
    "manufacturer_city": "Fort Worth",
    "manufacturer_state": "TX",
    "manufacturer_zip": "76101",
    "payment_start_date": "2026-11-01",
    "creditor_name": "21st Mortgage",
    "sales_price": "87000",
    "down_payment": "10000",
    "apr": "12.84",
    "loan_term": "276",
    "tax_rate": "2.5",
    "insurance_premium_monthly": "200",
}
NO_ESCROW_DEAL = {
    k: v for k, v in DEAL.items() if k not in {"tax_rate", "insurance_premium_monthly"}
}

PRINCIPAL_AND_INTEREST = "870.01"
TAX_MONTHLY = "181.25"
TOTAL_WITH_ESCROW = "1,251.26"

CONTRACT = "TMHA_SalesContract.pdf"
TAX_NOTICE = "Internal_ImportantNoticeTax.pdf"
NOTICE = "topmostSubform[0].Page1[0]."


def _render(template: str, data: dict, tmp_path, monkeypatch) -> PdfReader:
    import main
    import tools.document_tools as dt

    monkeypatch.setattr(dt, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(dt, "upload_to_gcs", lambda *a, **k: None)
    result = main.engine_generate_document(template, dict(data))
    assert result["success"], result.get("message")
    return PdfReader(result["file_path"])


def _field_values(reader: PdfReader) -> dict[str, str | None]:
    return {
        name: (None if fld.get("/V") is None else str(fld.get("/V")))
        for name, fld in (reader.get_fields() or {}).items()
    }


def test_contract_page2_prints_escrow_inclusive_payment(tmp_path, monkeypatch):
    page2 = _render(CONTRACT, DEAL, tmp_path, monkeypatch).pages[1].extract_text()

    assert TOTAL_WITH_ESCROW in page2
    assert f"276 monthly payments of ${TOTAL_WITH_ESCROW}" in page2
    assert PRINCIPAL_AND_INTEREST not in page2
    # Escrow is not interest: finance charge and (TILA) Total of Payments stay P&I.
    assert "163,122.76" in page2
    assert "240,122.76" in page2


def test_contract_page2_keeps_principal_and_interest_without_escrow(tmp_path, monkeypatch):
    page2 = _render(CONTRACT, NO_ESCROW_DEAL, tmp_path, monkeypatch).pages[1].extract_text()

    assert f"276 monthly payments of ${PRINCIPAL_AND_INTEREST}" in page2
    assert PRINCIPAL_AND_INTEREST in page2
    assert TOTAL_WITH_ESCROW not in page2


def test_tax_notice_prints_escrow_total_and_tax_line(tmp_path, monkeypatch):
    values = _field_values(_render(TAX_NOTICE, DEAL, tmp_path, monkeypatch))

    assert values[NOTICE + "Calc_Pmt[0]"] == PRINCIPAL_AND_INTEREST
    assert values[NOTICE + "Tax_Escrow_Pmt[0]"] == TAX_MONTHLY
    assert values[NOTICE + "Tax_Escrow_Pmt[1]"] == TAX_MONTHLY
    assert values[NOTICE + "Insurance_Premium_Monthly[0]"] == "200.00"
    assert values[NOTICE + "Total_Payment[0]"] == TOTAL_WITH_ESCROW


def test_tax_notice_estimated_property_tax_line_is_mapped():
    from config.field_map_loader import get_template_config

    field_map = get_template_config(TAX_NOTICE)["field_map"]
    assert field_map.get(NOTICE + "Tax_Escrow_Pmt[1]") == "tax_escrow_payment"


def test_enrichment_matches_the_printed_total():
    enriched = enrich_document_data(dict(DEAL))
    assert enriched["monthly_payment"] == PRINCIPAL_AND_INTEREST
    assert enriched["tax_escrow_payment"] == TAX_MONTHLY
    assert enriched["total_monthly_payment"] == TOTAL_WITH_ESCROW
    assert enriched["payment_breakdown"] == f"276 monthly payments of ${TOTAL_WITH_ESCROW}"
    assert enriched["total_payments"] == "240,122.76"
    assert enriched["finance_charge"] == "163,122.76"


def _financial(escrow: str) -> FinancialModel:
    return FinancialModel(
        sales_price="87000", down_payment="10000", apr="12.84", loan_term=276, monthly_escrow=escrow
    )


def test_total_of_payments_flag_defaults_to_tila_reading():
    assert document_quality.TOTAL_OF_PAYMENTS_INCLUDES_ESCROW is False
    fin = _financial("381.25")
    assert fin.monthly_payment == Decimal(PRINCIPAL_AND_INTEREST)
    assert fin.total_monthly_payment == Decimal("1251.26")
    assert fin.total_of_payments == Decimal("240122.76")
    assert fin.total_paid == Decimal("250122.76")
    assert fin.finance_charge == Decimal("163122.76")


def test_total_of_payments_flag_flips_both_engines(monkeypatch):
    monkeypatch.setattr(document_quality, "TOTAL_OF_PAYMENTS_INCLUDES_ESCROW", True)

    fin = _financial("381.25")
    assert fin.total_of_payments == Decimal("345347.76")
    assert fin.total_paid == Decimal("355347.76")
    assert fin.finance_charge == Decimal("163122.76")

    enriched = enrich_document_data(dict(DEAL))
    assert enriched["total_payments"] == "345,347.76"
    assert enriched["total_paid"] == "355,347.76"
    assert enriched["finance_charge"] == "163,122.76"


INSURANCE_BOX = "Insurance_Included_Yes[0]"
TAX_BOX = "Tax_Escrow_Included_Yes[0]"


def _box_states(reader: PdfReader) -> dict[str, str | None]:
    """Appearance state of every checkbox widget, keyed by its short field name."""
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
                as_state = widget.get("/AS")
                states[str(name)] = None if as_state is None else str(as_state)
    return states


def test_contract_page3_checks_insurance_escrow_box_when_insurance_escrowed(tmp_path, monkeypatch):
    states = _box_states(_render(CONTRACT, DEAL, tmp_path, monkeypatch))
    assert states[INSURANCE_BOX] not in (None, "/Off")
    assert states[TAX_BOX] not in (None, "/Off")


def test_contract_page3_leaves_insurance_escrow_box_off_without_insurance(tmp_path, monkeypatch):
    deal = {k: v for k, v in DEAL.items() if k != "insurance_premium_monthly"}
    states = _box_states(_render(CONTRACT, deal, tmp_path, monkeypatch))
    assert states[INSURANCE_BOX] in (None, "/Off")
    assert states[TAX_BOX] not in (None, "/Off")


def test_enrichment_sets_insurance_included_only_for_positive_insurance():
    assert enrich_document_data(dict(DEAL))["insurance_included"] is True
    assert "insurance_included" not in enrich_document_data(dict(NO_ESCROW_DEAL))
    zero = enrich_document_data({**DEAL, "insurance_premium_monthly": "0"})
    assert "insurance_included" not in zero


def test_enrichment_keeps_staff_entered_insurance_included():
    kept = enrich_document_data({**DEAL, "insurance_included": False})
    assert kept["insurance_included"] is False
