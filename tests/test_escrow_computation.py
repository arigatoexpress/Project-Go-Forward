"""
Tests for insurance + tax escrow derivation in the LEGACY document engine path.

Spec (Mark Willcott, 2026-06-24) — values populate the EXISTING field_map keys
that wire to the real "Important Notice - Property Tax" form
(Internal_ImportantNoticeTax.pdf, the SB 521 personal-property-tax escrow
notice):
    insurance_premium_monthly = annual_insurance / 12
    escrow_value              = taxable_value (default = sales_price)
    tax_escrow_pct            = the entered tax rate (percent)
    tax_escrow_yearly         = (tax_rate / 100) * escrow_value   (annual tax)
    tax_escrow_payment        = tax_escrow_yearly / 12            (monthly)

These pin the math + edge cases for tools.document_engine._compute_fields (the
legacy v1 engine). The canonical derivation on the LIVE path
(tools.document_quality.enrich_document_data) and the end-to-end PDF fill are
covered by tests/test_escrow_field_mapping.py.
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


def test_insurance_premium_monthly_is_annual_over_12():
    data = _compute({"annual_insurance": 1200})
    # 1200 / 12 = 100.00
    assert data["insurance_premium_monthly"] == "100.00"


def test_insurance_premium_monthly_rounds_to_two_decimals():
    data = _compute({"annual_insurance": 1000})
    # 1000 / 12 = 83.333... -> 83.33
    assert data["insurance_premium_monthly"] == "83.33"


def test_insurance_premium_monthly_accepts_currency_string():
    data = _compute({"annual_insurance": "$1,440.00"})
    # 1440 / 12 = 120.00
    assert data["insurance_premium_monthly"] == "120.00"


def test_insurance_premium_monthly_absent_when_no_input():
    data = _compute({"sales_price": 50000})
    assert "insurance_premium_monthly" not in data


def test_insurance_premium_monthly_zero_premium_is_zero():
    data = _compute({"annual_insurance": 0})
    assert data["insurance_premium_monthly"] == "0.00"


# ─────────────────────────── Tax escrow ───────────────────────────


def test_tax_escrow_uses_explicit_taxable_value():
    data = _compute({"tax_rate": 2.5, "taxable_value": 100000})
    assert data["escrow_value"] == "100,000.00"
    assert data["tax_escrow_pct"] == "2.5"
    # annual = 2.5% * 100000 = 2500 ; monthly = 2500 / 12 = 208.333... -> 208.33
    assert data["tax_escrow_yearly"] == "2,500.00"
    assert data["tax_escrow_payment"] == "208.33"


def test_tax_escrow_defaults_taxable_value_to_sales_price():
    data = _compute({"tax_rate": 1.8, "sales_price": 120000})
    assert data["escrow_value"] == "120,000.00"
    # annual = 1.8% * 120000 = 2160 ; monthly = 2160 / 12 = 180.00
    assert data["tax_escrow_yearly"] == "2,160.00"
    assert data["tax_escrow_payment"] == "180.00"


def test_tax_escrow_explicit_value_overrides_sales_price():
    data = _compute({"tax_rate": 2.0, "taxable_value": 80000, "sales_price": 200000})
    assert data["escrow_value"] == "80,000.00"
    # annual = 2.0% * 80000 = 1600 ; monthly = 1600 / 12 = 133.333... -> 133.33
    assert data["tax_escrow_yearly"] == "1,600.00"
    assert data["tax_escrow_payment"] == "133.33"


def test_tax_escrow_pct_drops_trailing_zero():
    data = _compute({"tax_rate": 2.0, "sales_price": 100000})
    # 2.0 -> "2" (the percent the staff typed, no trailing .0)
    assert data["tax_escrow_pct"] == "2"


def test_tax_escrow_accepts_currency_and_percent_strings():
    data = _compute({"tax_rate": "2.0", "taxable_value": "$90,000"})
    # annual = 2% * 90000 = 1800 ; monthly = 150.00
    assert data["tax_escrow_yearly"] == "1,800.00"
    assert data["tax_escrow_payment"] == "150.00"


def test_tax_escrow_zero_rate_is_zero():
    data = _compute({"tax_rate": 0, "sales_price": 100000})
    assert data["tax_escrow_yearly"] == "0.00"
    assert data["tax_escrow_payment"] == "0.00"


def test_tax_escrow_absent_when_no_rate():
    data = _compute({"sales_price": 100000})
    assert "tax_escrow_yearly" not in data
    assert "tax_escrow_payment" not in data
    assert "escrow_value" not in data


def test_tax_escrow_absent_when_rate_but_no_value():
    # tax_rate given but no taxable_value and no sales_price -> cannot compute
    data = _compute({"tax_rate": 2.0})
    assert "tax_escrow_yearly" not in data
    assert "tax_escrow_payment" not in data
    assert "escrow_value" not in data


# ─────────────────────────── Non-interference ───────────────────────────


def test_does_not_clobber_existing_unpaid_balance_logic():
    data = _compute({"sales_price": 50000, "down_payment": 10000})
    # existing derivation still works
    assert data["unpaid_balance"] == str(40000.0)


def test_existing_escrow_values_not_overwritten():
    # If a caller already supplied a computed value, respect it (setdefault).
    data = _compute(
        {"annual_insurance": 1200, "insurance_premium_monthly": "999.99"}
    )
    assert data["insurance_premium_monthly"] == "999.99"


def test_retired_invented_keys_are_not_emitted():
    """The first-pass invented keys must NOT be produced any more.

    insurance_escrow_monthly / tax_escrow_monthly mapped to NO PDF widget; the
    derivation now uses the real field_map keys instead.
    """
    data = _compute({"annual_insurance": 1200, "tax_rate": 2.5, "sales_price": 100000})
    assert "insurance_escrow_monthly" not in data
    assert "tax_escrow_monthly" not in data


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
    assert computed["insurance_premium_monthly"] == "100.00"  # 1200/12
    assert computed["escrow_value"] == "120,000.00"
    assert computed["tax_escrow_yearly"] == "2,160.00"  # 1.8% * 120000
    assert computed["tax_escrow_payment"] == "180.00"  # / 12


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
