"""
Tests for insurance + tax escrow derivation in the document engine.

Spec (Mark Willcott, 2026-06-24):
- Insurance: staff enters ANNUAL insurance premium.
      monthly INS escrow = annual_insurance / 12
- Tax: staff enters a TAX RATE (percent) and a taxable value
  (default = sales_price if not given).
      annual_tax = (tax_rate / 100) * taxable_value
      monthly tax escrow = annual_tax / 12

These tests pin the math + edge cases. The computation lives in
tools.document_engine._compute_fields, reusing the existing _to_float helper.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.document_engine import _compute_fields  # noqa: E402


def _compute(data: dict) -> dict:
    """Run the in-place derivation and return the mutated dict."""
    _compute_fields(data)
    return data


# ─────────────────────────── Insurance escrow ───────────────────────────


def test_insurance_escrow_monthly_is_annual_over_12():
    data = _compute({"annual_insurance": 1200})
    # 1200 / 12 = 100.00
    assert data["insurance_escrow_monthly"] == "100.00"


def test_insurance_escrow_rounds_to_two_decimals():
    data = _compute({"annual_insurance": 1000})
    # 1000 / 12 = 83.333... -> 83.33
    assert data["insurance_escrow_monthly"] == "83.33"


def test_insurance_escrow_accepts_currency_string():
    data = _compute({"annual_insurance": "$1,440.00"})
    # 1440 / 12 = 120.00
    assert data["insurance_escrow_monthly"] == "120.00"


def test_insurance_escrow_absent_when_no_input():
    data = _compute({"sales_price": 50000})
    assert "insurance_escrow_monthly" not in data


def test_insurance_escrow_zero_premium_is_zero():
    data = _compute({"annual_insurance": 0})
    assert data["insurance_escrow_monthly"] == "0.00"


# ─────────────────────────── Tax escrow ───────────────────────────


def test_tax_escrow_uses_explicit_taxable_value():
    data = _compute({"tax_rate": 2.5, "taxable_value": 100000})
    # annual = 2.5% * 100000 = 2500 ; monthly = 2500 / 12 = 208.333... -> 208.33
    assert data["tax_escrow_monthly"] == "208.33"


def test_tax_escrow_defaults_taxable_value_to_sales_price():
    data = _compute({"tax_rate": 1.8, "sales_price": 120000})
    # annual = 1.8% * 120000 = 2160 ; monthly = 2160 / 12 = 180.00
    assert data["tax_escrow_monthly"] == "180.00"


def test_tax_escrow_explicit_value_overrides_sales_price():
    data = _compute({"tax_rate": 2.0, "taxable_value": 80000, "sales_price": 200000})
    # annual = 2.0% * 80000 = 1600 ; monthly = 1600 / 12 = 133.333... -> 133.33
    assert data["tax_escrow_monthly"] == "133.33"


def test_tax_escrow_accepts_currency_and_percent_strings():
    data = _compute({"tax_rate": "2.0", "taxable_value": "$90,000"})
    # annual = 2% * 90000 = 1800 ; monthly = 150.00
    assert data["tax_escrow_monthly"] == "150.00"


def test_tax_escrow_zero_rate_is_zero():
    data = _compute({"tax_rate": 0, "sales_price": 100000})
    assert data["tax_escrow_monthly"] == "0.00"


def test_tax_escrow_absent_when_no_rate():
    data = _compute({"sales_price": 100000})
    assert "tax_escrow_monthly" not in data


def test_tax_escrow_absent_when_rate_but_no_value():
    # tax_rate given but no taxable_value and no sales_price -> cannot compute
    data = _compute({"tax_rate": 2.0})
    assert "tax_escrow_monthly" not in data


# ─────────────────────────── Non-interference ───────────────────────────


def test_does_not_clobber_existing_unpaid_balance_logic():
    data = _compute({"sales_price": 50000, "down_payment": 10000})
    # existing derivation still works
    assert data["unpaid_balance"] == str(40000.0)


def test_existing_escrow_values_not_overwritten():
    # If a caller already supplied a computed value, respect it (setdefault).
    data = _compute(
        {"annual_insurance": 1200, "insurance_escrow_monthly": "999.99"}
    )
    assert data["insurance_escrow_monthly"] == "999.99"


# ─────────────────────── Deal model end-to-end ───────────────────────


def test_deal_model_escrow_fields_flow_through():
    """Deal validates the new fields and they reach the engine derivation."""
    from database.models import Deal

    deal = Deal(
        sales_price=120000,
        annual_insurance=1200,
        tax_rate=1.8,
        # taxable_value omitted -> should default to sales_price (120000)
    )
    doc_data = deal.to_document_data()
    assert _to_float_present(doc_data, "annual_insurance", 1200)
    assert _to_float_present(doc_data, "tax_rate", 1.8)

    computed = _compute(dict(doc_data))
    assert computed["insurance_escrow_monthly"] == "100.00"  # 1200/12
    assert computed["tax_escrow_monthly"] == "180.00"  # 1.8% * 120000 / 12


def test_deal_model_rejects_negative_escrow_inputs():
    import pytest
    from pydantic import ValidationError

    from database.models import Deal

    with pytest.raises(ValidationError):
        Deal(annual_insurance=-1)
    with pytest.raises(ValidationError):
        Deal(tax_rate=-0.5)
    with pytest.raises(ValidationError):
        Deal(taxable_value=-100)


def _to_float_present(data: dict, key: str, expected: float) -> bool:
    return key in data and float(data[key]) == expected
