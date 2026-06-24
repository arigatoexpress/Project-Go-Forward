"""Escrow values must land on the real "Important Notice - Property Tax" form.

PART A defect (adversarial review, 2026-06-24): the first escrow pass derived
NET-NEW keys (``insurance_escrow_monthly`` / ``tax_escrow_monthly``) that no PDF
widget maps to, and computed the annual tax inline without ever storing it. The
EXISTING field_map already wires real keys to the real regulatory form
``Internal_ImportantNoticeTax.pdf`` ("Important Notice - Property Tax", the SB
521 personal-property-tax escrow notice, punch-list page 22):

    tax_escrow_pct            -> Tax_Escrow_Pct[0]
    escrow_value              -> Escrow_Value[0]
    tax_escrow_yearly         -> Tax_Escrow_Yearly[0]
    tax_escrow_payment        -> Tax_Escrow_Pmt[0]
    insurance_premium_monthly -> Insurance_Premium_Monthly[0]

The derivation must populate THOSE keys, on the live fill path
(``enrich_document_data`` — the v2 engine and deal-based path both run it), so
the figures actually render on the form.

Spec (Mark Willcott, 2026-06-24), unchanged math, mapped to the form's lines:
    insurance_premium_monthly = annual_insurance / 12
    escrow_value              = taxable_value (defaults to sales_price)
    tax_escrow_pct            = tax_rate (the percent staff entered)
    tax_escrow_yearly         = (tax_rate / 100) * escrow_value      (annual tax)
    tax_escrow_payment        = tax_escrow_yearly / 12               (monthly)

CI-safe: derivation tests are pure; the fill test reads AcroForm ``/V`` values
back out of the real template (no poppler / rasterization), mirroring
tests/test_pypdf_acroform_fill_regression.py.
"""

from __future__ import annotations

import os
import sys

from pypdf import PdfReader

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.field_map_loader import get_template_config  # noqa: E402
from tools.document_quality import enrich_document_data  # noqa: E402

TEMPLATE = "Internal_ImportantNoticeTax.pdf"


# ───────────────────── derivation onto the REAL keys ─────────────────────


def test_insurance_premium_monthly_is_annual_over_12():
    out = enrich_document_data({"annual_insurance": 1200})
    assert out["insurance_premium_monthly"] == "100.00"


def test_insurance_premium_monthly_rounds_two_decimals():
    out = enrich_document_data({"annual_insurance": 1000})
    assert out["insurance_premium_monthly"] == "83.33"


def test_insurance_premium_monthly_accepts_currency_string():
    out = enrich_document_data({"annual_insurance": "$1,440.00"})
    assert out["insurance_premium_monthly"] == "120.00"


def test_no_insurance_premium_monthly_when_no_annual():
    out = enrich_document_data({"sales_price": 50000})
    assert "insurance_premium_monthly" not in out or not out.get("insurance_premium_monthly")


def test_escrow_value_defaults_to_explicit_taxable_value():
    out = enrich_document_data({"tax_rate": 2.5, "taxable_value": 100000})
    assert out["escrow_value"] == "100,000.00"


def test_escrow_value_defaults_to_sales_price():
    out = enrich_document_data({"tax_rate": 1.8, "sales_price": 120000})
    assert out["escrow_value"] == "120,000.00"


def test_tax_escrow_pct_is_the_entered_rate():
    out = enrich_document_data({"tax_rate": 2.5, "taxable_value": 100000})
    # The percent the staff typed renders on the Tax_Escrow_Pct line.
    assert out["tax_escrow_pct"] in ("2.5", "2.50")


def test_tax_escrow_yearly_is_annual_tax():
    out = enrich_document_data({"tax_rate": 2.5, "taxable_value": 100000})
    # 2.5% * 100000 = 2500.00 annual
    assert out["tax_escrow_yearly"] == "2,500.00"


def test_tax_escrow_payment_is_yearly_over_12():
    out = enrich_document_data({"tax_rate": 2.5, "taxable_value": 100000})
    # 2500 / 12 = 208.333... -> 208.33
    assert out["tax_escrow_payment"] == "208.33"


def test_tax_escrow_explicit_value_overrides_sales_price():
    out = enrich_document_data({"tax_rate": 2.0, "taxable_value": 80000, "sales_price": 200000})
    assert out["escrow_value"] == "80,000.00"
    assert out["tax_escrow_yearly"] == "1,600.00"
    assert out["tax_escrow_payment"] == "133.33"


def test_no_tax_escrow_when_no_rate():
    out = enrich_document_data({"sales_price": 100000})
    assert not out.get("tax_escrow_yearly")
    assert not out.get("tax_escrow_payment")


def test_no_tax_escrow_when_rate_but_no_value():
    out = enrich_document_data({"tax_rate": 2.0})
    assert not out.get("escrow_value")
    assert not out.get("tax_escrow_yearly")
    assert not out.get("tax_escrow_payment")


def test_explicit_escrow_values_not_overwritten():
    # A caller-supplied value wins (the form may carry a manual override).
    out = enrich_document_data(
        {"annual_insurance": 1200, "insurance_premium_monthly": "999.99"}
    )
    assert out["insurance_premium_monthly"] == "999.99"


# ─────────── the keys actually map to this real regulatory form ───────────


def test_existing_field_map_wires_escrow_keys_to_real_widgets():
    """Guard the wiring: these human keys must map to the real PDF widgets.

    If a future edit renames a key or drops a mapping, the escrow figures would
    silently stop rendering on the SB 521 notice. Pin the contract here.
    """
    cfg = get_template_config(TEMPLATE)
    field_map = cfg.get("field_map", {})
    # human_key -> widget
    human_to_widget = {}
    for widget, human in field_map.items():
        human_to_widget.setdefault(human, widget)

    expected = {
        "tax_escrow_pct": "Tax_Escrow_Pct[0]",
        "escrow_value": "Escrow_Value[0]",
        "tax_escrow_yearly": "Tax_Escrow_Yearly[0]",
        "tax_escrow_payment": "Tax_Escrow_Pmt[0]",
        "insurance_premium_monthly": "Insurance_Premium_Monthly[0]",
    }
    for human, widget_suffix in expected.items():
        widget = human_to_widget.get(human)
        assert widget is not None, f"{human} no longer mapped on {TEMPLATE}"
        assert widget.endswith(widget_suffix), f"{human} -> {widget}, expected ...{widget_suffix}"


# ───────────── end-to-end: values render on the real template ─────────────


def _read_acroform_values(pdf_path: str) -> dict[str, str]:
    reader = PdfReader(pdf_path)
    out: dict[str, str] = {}
    for name, fld in (reader.get_fields() or {}).items():
        v = fld.get("/V")
        if v is not None:
            out[str(name)] = str(v)
    return out


def test_escrow_values_render_on_important_notice_tax_form(tmp_path, monkeypatch):
    """The computed escrow figures must land in the real AcroForm fields.

    Drives the same v2 engine path callers use, then reads the AcroForm ``/V``
    values back out of the generated PDF. On the original defect these widgets
    were blank because the derivation used keys no widget maps to.
    """
    import tools.document_tools as dt
    from scripts.validate_document_fills import FULL_DEAL
    from tools.document_engine_v2 import generate_document

    monkeypatch.setattr(dt, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(dt, "upload_to_gcs", lambda *a, **k: None)

    # Start from a complete, valid deal so the v2 payload validates, then overlay
    # the escrow inputs Mark's form needs.
    data = dict(FULL_DEAL)
    data["annual_insurance"] = "1200"
    data["tax_rate"] = "2.5"
    data["taxable_value"] = "100000"

    result = generate_document(TEMPLATE, dict(data))
    assert result["success"], result.get("message")

    actual = _read_acroform_values(result["file_path"])
    assert actual, "no AcroForm values written (summary fallback fired)"

    # escrow_value = 100000, tax_escrow_pct = 2.5, tax_escrow_yearly = 2500,
    # tax_escrow_payment = 208.33, insurance_premium_monthly = 100.00
    def widget(name: str) -> str:
        return f"topmostSubform[0].Page1[0].{name}"

    assert actual.get(widget("Escrow_Value[0]")) == "100,000.00"
    assert actual.get(widget("Tax_Escrow_Yearly[0]")) == "2,500.00"
    assert actual.get(widget("Tax_Escrow_Pmt[0]")) == "208.33"
    assert actual.get(widget("Insurance_Premium_Monthly[0]")) == "100.00"
    assert actual.get(widget("Tax_Escrow_Pct[0]")) in ("2.5", "2.50")
