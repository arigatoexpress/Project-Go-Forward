"""Regression tests for Mark Willcott's 2026-11-12 contract notes.

Synthetic deal only: a used, two-section home with a co-buyer. Each test pins one
thing Mark circled on the generated packet:

* both buyers on the TDHCA 1023 and 1124,
* both section IDs on every form with a single Serial / HUD slot,
* purchase price, down payment, balance due and the wheels box on the deposit
  agreement,
* per-section size (W x L) on every form that has a size cell,
* used limited warranty (multi-section box, manufacturer, install address, names
  that fit),
* tax notice dollar signs and percent,
* the optional 1023 handling / homestead lines.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest
from pypdf import PdfReader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("THO_DISABLE_GCS_UPLOADS", "1")

import tools.document_tools as dt  # noqa: E402
from tools import document_quality  # noqa: E402
from tools.document_engine_v2 import generate_document  # noqa: E402
from tools.document_quality import enrich_document_data, missing_section_sizes  # noqa: E402

P1 = "topmostSubform[0].Page1[0]."
P2 = "topmostSubform[0].Page2[0]."

DEAL = {
    "buyer_first_name": "Casey",
    "buyer_last_name": "Rivera",
    "buyer_address": "100 Meadow Lane",
    "buyer_city": "Austin",
    "buyer_county": "Harris",
    "buyer_state": "TX",
    "buyer_zip": "77001",
    "buyer_phone": "713-555-0101",
    "co_buyer_first_name": "Morgan",
    "co_buyer_last_name": "Diaz Chavez",
    "is_new": False,
    "manufacturer": "Acme Manufacturing",
    "model": "Marvelous 4",
    "year": "2014",
    "serial_number_1": "MVL100001A",
    "serial_number_2": "MVL100001B",
    "label_number_1": "LBL0100001",
    "label_number_2": "LBL0100002",
    "no_of_sections": "2",
    "sq_ft": "1568",
    "wind_zone": "2",
    "date_of_manufacture": "2014-03-15",
    "manufacturer_address": "1 Factory Rd",
    "manufacturer_city": "Waco",
    "manufacturer_state": "TX",
    "manufacturer_zip": "76701",
    "sales_price": "61500",
    "down_payment": "6150",
    "loan_term": "240",
    "apr": "9.5",
    "payment_start_date": "2026-12-01",
    "date_of_sale": "2026-11-12",
    "tax_rate": "2.5",
    "creditor_name": "Lone Star Lender",
    "creditor_address": "1 Bank St",
    "creditor_city_state_zip": "Dallas, TX 75001",
    "creditor_phone": "214-555-0100",
    "section_1_width": "14",
    "section_1_length": "56",
    "section_2_width": "14",
    "section_2_length": "56",
    "ownership_handling": "expedite",
    "homestead_legal_description": "Lot 4 Block 2 Pine Acres",
    "appraisal_account_number": "R000123",
}

BOTH_NAMES = "Casey Rivera & Morgan Diaz Chavez"
HAS_PDFTOTEXT = shutil.which("pdftotext") is not None


@pytest.fixture(scope="module")
def out_dir(tmp_path_factory):
    path = tmp_path_factory.mktemp("mark_form_fixes")
    previous = dt.OUTPUT_DIR
    dt.OUTPUT_DIR = str(path)
    yield str(path)
    dt.OUTPUT_DIR = previous


def _generate(out_dir: str, template: str, **overrides) -> str:
    data = {**DEAL, **overrides}
    result = generate_document(template, data, output_filename=f"t_{len(overrides)}_{template}")
    assert result["success"], result.get("message")
    return result["file_path"]


def _fields(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for name, field in (PdfReader(path).get_fields() or {}).items():
        values[name] = str(field.get("/V") or "")
    return values


def _widget(path: str, qualified: str) -> str:
    return _fields(path).get(qualified, "")


def _text(path: str) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", path, "-"], check=True, capture_output=True, text=True
    ).stdout


# ── 1. Co-buyer on 1023 and 1124 ────────────────────────────────────────────


def test_1023_purchaser_line_has_both_buyers(out_dir):
    path = _generate(out_dir, "TDHCA_1023-Statement-Ownership.pdf")
    assert _widget(path, P1 + "Contract_Name[0]") == BOTH_NAMES


def test_1124_consumer_names_have_both_buyers(out_dir):
    path = _generate(out_dir, "TDHCA_1124_Installation_Warranty.pdf")
    assert _widget(path, P1 + "Consumer_Name[0]") == BOTH_NAMES
    assert _widget(path, P1 + "Name_of_Consumer[0]") == BOTH_NAMES


def test_single_buyer_still_prints_one_name(out_dir):
    data = {k: v for k, v in DEAL.items() if not k.startswith("co_buyer")}
    result = generate_document(
        "TDHCA_1124_Installation_Warranty.pdf", data, output_filename="t_single_1124.pdf"
    )
    assert result["success"]
    assert _widget(result["file_path"], P1 + "Consumer_Name[0]") == "Casey Rivera"


# ── 2. Both IDs on single-slot forms ────────────────────────────────────────

SINGLE_SLOT = [
    ("Internal_PropertyLocation.pdf", P1 + "Serial[0]", P1 + "HUD[0]"),
    ("Internal_AssignmentBySeller.pdf", P1 + "Serial[0]", P1 + "HUD[0]"),
    ("Internal_Consumer_Home_Acceptance_Acknowledgement.pdf", P1 + "Serial[0]", None),
    ("State_ImportantHealth.pdf", P1 + "Serial[0]", P1 + "HUD[0]"),
    (
        "TDHCA_1062_Install_Checklist.pdf",
        P1 + "Serial__s[0]",
        P1 + "HUD_Label_or_Texas_Seal__s[0]",
    ),
    ("TDHCA_1074_Vacate_If_No_Financing.pdf", P1 + "Serial[0]", P1 + "HUD[0]"),
    ("TDHCA_Disclosure.pdf", P1 + "Serial[0]", P1 + "HUD[0]"),
    ("TMHA_ConsumerInsulation.pdf", P1 + "Serial[0]", None),
    ("TDHCA_1055_Broker_Disclosure.pdf", P1 + "Serial_number[0]", P1 + "Label_or_seal_number[0]"),
    ("TDHCA_1023-Statement-Ownership.pdf", P2 + "Serial[0]", P2 + "HUD[0]"),
    ("TMHA_LimitedWarrantyUsed.pdf", None, P2 + "HUD[0]"),
]


@pytest.mark.parametrize("template,serial_widget,hud_widget", SINGLE_SLOT)
def test_single_slot_forms_carry_both_ids(out_dir, template, serial_widget, hud_widget):
    path = _generate(out_dir, template)
    if serial_widget:
        value = _widget(path, serial_widget)
        assert "MVL100001A" in value and "MVL100001B" in value, (template, value)
    if hud_widget:
        value = _widget(path, hud_widget)
        assert "LBL0100001" in value and "LBL0100002" in value, (template, value)


def test_ids_with_one_section_are_not_duplicated():
    out = enrich_document_data({"serial_number_1": "AAA1", "label_number_1": "L1"})
    assert out["serial_numbers_all"] == "AAA1"
    assert out["label_numbers_all"] == "L1"


def test_aoa_and_homestead_id_lines_carry_both_ids(out_dir):
    aoa = _generate(out_dir, "Internal_AoA.pdf")
    value = _widget(aoa, "topmostSubform[0].Page2[0].Manufacturer_Model_HUD[0]")
    assert "LBL0100001" in value and "LBL0100002" in value
    homestead = _generate(out_dir, "Internal_Homestead.pdf")
    value = _widget(homestead, P2 + "Manufacturer_Model_Serial[0]")
    assert "LBL0100001" in value and "LBL0100002" in value  # HUD labels per Mark


def test_one_id_client_composite_is_rebuilt_with_both_ids():
    """The Document Center used to send manufacturer_model_hud with ID 1 only."""
    out = enrich_document_data(
        {
            **DEAL,
            "manufacturer_model": "Acme Manufacturing Marvelous 4",
            "manufacturer_model_hud": "Acme Manufacturing Marvelous 4 / LBL0100001",
            "manufacturer_model_serial": "Acme Manufacturing Marvelous 4 / MVL100001A",
        }
    )
    assert out["manufacturer_model_hud"].endswith("LBL0100001, LBL0100002")
    assert out["manufacturer_model_serial"].endswith("MVL100001A, MVL100001B")


def test_explicit_composite_from_staff_is_kept():
    out = enrich_document_data(
        {**DEAL, "manufacturer_model": "A B", "manufacturer_model_hud": "typed by staff"}
    )
    assert out["manufacturer_model_hud"] == "typed by staff"


# ── 3. Deposit agreement ────────────────────────────────────────────────────


def test_deposit_agreement_prints_amounts_and_sizes(out_dir):
    path = _generate(out_dir, "TMHA-SalesContractDepositAgreement.pdf")
    fields = _fields(path)
    assert fields[P1 + "Total_Size_Sec1_L[0]"] == "14"  # left of the printed "x" = width
    assert fields[P1 + "Total_Size_Sec1_W[0]"] == "56"
    assert fields[P1 + "Total_Size_Sec2_L[0]"] == "14"
    assert fields[P1 + "Total_Size_Sec2_W[0]"] == "56"
    if HAS_PDFTOTEXT:
        text = _text(path)
        assert "61,500.00" in text  # purchase price
        assert "6,150.00" in text  # total down payment
        assert "55,350.00" in text  # balance due


def _record_checkmarks(monkeypatch) -> list[tuple[float, float]]:
    drawn: list[tuple[float, float]] = []
    original = dt._draw_checkmark

    def spy(pdf_canvas, x, y, size, line_width=None):
        drawn.append((round(x, 1), round(y, 1)))
        return original(pdf_canvas, x, y, size, line_width=line_width)

    monkeypatch.setattr(dt, "_draw_checkmark", spy)
    return drawn


DEPOSIT_WHEELS_YES = (417.8, 169.5)
DEPOSIT_WHEELS_NO = (467.0, 169.5)


def test_deposit_wheels_default_to_no_on_used_home(out_dir, monkeypatch):
    drawn = _record_checkmarks(monkeypatch)
    _generate(out_dir, "TMHA-SalesContractDepositAgreement.pdf")
    assert DEPOSIT_WHEELS_NO in drawn
    assert DEPOSIT_WHEELS_YES not in drawn


def test_deposit_wheels_not_checked_by_default_on_new_home(out_dir, monkeypatch):
    drawn = _record_checkmarks(monkeypatch)
    _generate(out_dir, "TMHA-SalesContractDepositAgreement.pdf", is_new=True, is_used=False)
    assert DEPOSIT_WHEELS_NO not in drawn
    assert DEPOSIT_WHEELS_YES not in drawn


def test_deposit_wheels_staff_yes_beats_used_default(out_dir, monkeypatch):
    drawn = _record_checkmarks(monkeypatch)
    _generate(out_dir, "TMHA-SalesContractDepositAgreement.pdf", wheels_included_yes=True)
    assert DEPOSIT_WHEELS_YES in drawn
    assert DEPOSIT_WHEELS_NO not in drawn


def test_wheels_default_for_already_sited_home():
    out = enrich_document_data({"is_new": True, "already_sited": True})
    assert out["wheels_included_no"] is True
    assert out["wheels_included_yes"] is False


def test_wheels_untouched_when_new_and_not_sited():
    out = enrich_document_data({"is_new": True})
    assert "wheels_included_no" not in out


# ── 4. Used limited warranty ────────────────────────────────────────────────


def test_used_warranty_multi_section_box_follows_section_count(out_dir):
    two = _generate(out_dir, "TMHA_LimitedWarrantyUsed.pdf")
    assert _widget(two, P2 + "Warranty_Double_Yes[0]") not in ("", "/Off", "Off")
    assert _widget(two, P2 + "Warranty_Single_Yes[0]") in ("", "/Off", "Off")
    one = _generate(out_dir, "TMHA_LimitedWarrantyUsed.pdf", no_of_sections="1")
    assert _widget(one, P2 + "Warranty_Single_Yes[0]") not in ("", "/Off", "Off")
    assert _widget(one, P2 + "Warranty_Double_Yes[0]") in ("", "/Off", "Off")


@pytest.mark.skipif(not HAS_PDFTOTEXT, reason="pdftotext not installed")
def test_used_warranty_prints_manufacturer_install_address_and_second_id(out_dir):
    text = _text(_generate(out_dir, "TMHA_LimitedWarrantyUsed.pdf"))
    assert "Acme Manufacturing" in text
    assert "100 Meadow Lane, Austin, TX 77001" in text
    assert "LBL0100002" in text


def test_used_warranty_homeowner_names_are_shrunk_to_fit(out_dir):
    path = _generate(out_dir, "TMHA_LimitedWarrantyUsed.pdf")
    reader = PdfReader(path)
    sizes = {}
    for annot in reader.pages[1]["/Annots"]:
        widget = annot.get_object()
        sizes[str(widget.get("/T"))] = str(widget.get("/DA"))
    # 10pt Helvetica for this name is wider than the 168pt box; it must shrink.
    assert " Tf" in sizes["Contract_Name[0]"]
    size = float(sizes["Contract_Name[0]"].split()[1])
    assert size < 10
    # ...but a field that already fits keeps its size.
    assert float(sizes["Model[0]"].split()[1]) == 10


def test_autoshrink_never_goes_below_minimum(out_dir):
    long_name = "Alexandria Montgomery-Featherstonehaugh " * 3
    path = _generate(
        out_dir,
        "TMHA_LimitedWarrantyUsed.pdf",
        buyer_first_name=long_name.strip(),
    )
    reader = PdfReader(path)
    for annot in reader.pages[1]["/Annots"]:
        widget = annot.get_object()
        if str(widget.get("/T")) == "Contract_Name[0]":
            assert float(str(widget["/DA"]).split()[1]) >= dt._AUTOSHRINK_MIN_SIZE


@pytest.mark.skipif(not HAS_PDFTOTEXT, reason="pdftotext not installed")
def test_health_notice_retailer_name_not_truncated(out_dir):
    path = _generate(out_dir, "State_ImportantHealth.pdf")
    fields = _fields(path)
    assert fields[P1 + "Seller_Name[0]"].endswith("Texas Home Outlet")
    reader = PdfReader(path)
    for annot in reader.pages[0]["/Annots"]:
        widget = annot.get_object()
        if str(widget.get("/T")) == "Seller_Name[0]":
            width = float(widget["/Rect"][2]) - float(widget["/Rect"][0])
            from reportlab.pdfbase.pdfmetrics import stringWidth

            size = float(str(widget["/DA"]).split()[1])
            assert stringWidth(fields[P1 + "Seller_Name[0]"], "Helvetica", size) <= width


# ── 5. Per-section size ─────────────────────────────────────────────────────


def test_1023_prints_both_section_sizes(out_dir):
    fields = _fields(_generate(out_dir, "TDHCA_1023-Statement-Ownership.pdf"))
    assert fields[P1 + "Total_Size_Sec1_W[0]"] == "14"
    assert fields[P1 + "Total_Size_Sec1_L[0]"] == "56"
    assert fields[P1 + "Total_Size_Sec2_W[0]"] == "14"
    assert fields[P1 + "Total_Size_Sec2_L[0]"] == "56"


@pytest.mark.skipif(not HAS_PDFTOTEXT, reason="pdftotext not installed")
@pytest.mark.parametrize(
    "template", ["TDHCA_1054_Habitability_Warranty.pdf", "TDHCA_WarrantyUsedHome.pdf"]
)
def test_used_warranty_size_cells_are_filled(out_dir, template):
    text = _text(_generate(out_dir, template, section_2_width="15", section_2_length="60"))
    assert "14" in text and "56" in text
    assert "15" in text and "60" in text


def test_one_line_home_size_forms(out_dir):
    for template in (
        "TDHCA_1074_Vacate_If_No_Financing.pdf",
        "TDHCA_Disclosure.pdf",
        "Internal_Proof_of_Insurance.pdf",
    ):
        value = _widget(_generate(out_dir, template), P1 + "Home_Size[0]")
        assert value == "14 x 56, 14 x 56", (template, value)


def test_form_t_width_x_length(out_dir):
    fields = _fields(_generate(out_dir, "TDHCA_1026_Form_T.pdf"))
    assert fields[P1 + "WidthXLength1[0]"] == "14 x 56"
    assert fields[P1 + "WidthXLength2[0]"] == "14 x 56"


def test_multi_section_size_is_never_invented_from_overall_size():
    out = enrich_document_data({"no_of_sections": "2", "width": "28", "length": "56"})
    assert not out.get("section_1_width") and not out.get("section_2_width")
    assert not out.get("home_size_text")


def test_missing_section_sizes_warning():
    assert missing_section_sizes({"no_of_sections": "2"}) == [
        "Section 1 size (width x length)",
        "Section 2 size (width x length)",
    ]
    assert missing_section_sizes(
        {"no_of_sections": "2", "section_1_width": "14", "section_1_length": "56"}
    ) == ["Section 2 size (width x length)"]
    assert missing_section_sizes(DEAL) == []
    # A single-section home takes its size from the overall width and length.
    assert missing_section_sizes({"no_of_sections": "1", "width": "16", "length": "76"}) == []


def test_missing_sizes_do_not_block_generation(out_dir):
    data = {k: v for k, v in DEAL.items() if not k.startswith("section_")}
    result = generate_document(
        "TMHA-SalesContractDepositAgreement.pdf", data, output_filename="t_nosize.pdf"
    )
    assert result["success"]


# ── 6. Optional deal fields ─────────────────────────────────────────────────


def test_1023_handling_expedite_draws_second_box(out_dir, monkeypatch):
    drawn = _record_checkmarks(monkeypatch)
    path = _generate(out_dir, "TDHCA_1023-Statement-Ownership.pdf")
    assert (249.9, 562.6) in drawn
    assert _widget(path, P1 + "Regular_Yes[0]") in ("", "/Off", "Off")


def test_1023_handling_regular_checks_first_box_only(out_dir, monkeypatch):
    drawn = _record_checkmarks(monkeypatch)
    path = _generate(out_dir, "TDHCA_1023-Statement-Ownership.pdf", ownership_handling="regular")
    assert (249.9, 562.6) not in drawn
    assert _widget(path, P1 + "Regular_Yes[0]") not in ("", "/Off", "Off")


def test_1023_handling_unset_checks_nothing(out_dir, monkeypatch):
    drawn = _record_checkmarks(monkeypatch)
    path = _generate(out_dir, "TDHCA_1023-Statement-Ownership.pdf", ownership_handling="")
    assert (249.9, 562.6) not in drawn
    assert _widget(path, P1 + "Regular_Yes[0]") in ("", "/Off", "Off")


def test_homestead_legal_description_and_account(out_dir):
    fields = _fields(_generate(out_dir, "Internal_Homestead.pdf"))
    assert fields[P2 + "Legal_Description_if_known[0]"] == "Lot 4 Block 2 Pine Acres"
    assert fields[P2 + "Appraisal_District_Account_Number_if_known[0]"] == "R000123"


# ── 7. Tax notice ───────────────────────────────────────────────────────────


def test_tax_notice_dollar_signs_and_percent(out_dir):
    fields = _fields(_generate(out_dir, "Internal_ImportantNoticeTax.pdf"))
    assert fields[P1 + "Tax_Escrow_Pct[0]"] == "2.5%"
    assert fields[P1 + "Escrow_Value[0]"] == "$61,500.00"
    assert fields[P1 + "Tax_Escrow_Yearly[0]"] == "$1,537.50"
    assert fields[P1 + "Tax_Escrow_Pmt[0]"] == "$128.13"
    assert fields[P1 + "Calc_Pmt[0]"].startswith("$")
    assert fields[P1 + "Total_Payment[0]"].startswith("$")
    assert fields[P1 + "Install_County[0]"] == "Harris"  # text fields untouched


def test_tax_notice_formatting_is_idempotent():
    prepared = {P1 + "Escrow_Value[0]": "$61,500.00", P1 + "Tax_Escrow_Pct[0]": "2.5%"}
    dt._format_tax_notice_values(prepared)
    assert prepared[P1 + "Escrow_Value[0]"] == "$61,500.00"
    assert prepared[P1 + "Tax_Escrow_Pct[0]"] == "2.5%"


# ── Guardrails left as-is in this deploy ────────────────────────────────────


def test_flat_late_fee_stays_off():
    assert document_quality.FLAT_LATE_FEE_25 is False


def test_tila_total_of_payments_math_unchanged():
    assert document_quality.TOTAL_OF_PAYMENTS_INCLUDES_ESCROW is False
