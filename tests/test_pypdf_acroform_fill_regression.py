"""Regression guard: pypdf >= 6.13 AcroForm-fill must populate the sales contract.

pypdf 6.13 rewrote appearance-stream generation to glyph-render form-field text.
Its TrueType code path (``Font._get_typographic_maps`` else-branch, which pypdf
itself annotates "not covered nor tested") executes ``bytes((glyph_id,))`` on a
*string* glyph id and raises::

    TypeError: 'str' object cannot be interpreted as an integer

That blanked 19 of the 64 THO AcroForm templates — including the legally
operative ``TMHA_SalesContract.pdf``, whose fields use the embedded ``/ArialMT``
font — because ``fill_pdf_form`` swallows the fill error and falls back to a
plain summary PDF with no field values. The bug persists through pypdf 6.14.x.

The fix routes field appearances through the standard-14 ``/Helv`` font (already
declared in every template's AcroForm ``/DR`` and its document-level default
``/DA``), which pypdf can render. These tests pin two things so the regression
can never silently return:

  1. ``fill_pdf_form`` on the real ``TMHA_SalesContract.pdf`` writes the supplied
     values into the AcroForm and they read back correctly (the exact failure
     mode was blank ``/V`` values).
  2. The end-to-end ``generate_document`` path produces a contract that actually
     carries buyer-identifying values — not the empty summary fallback.

CI-safe: asserts on AcroForm ``/V`` values + extracted page text only (no
poppler / rasterization).
"""

from __future__ import annotations

import os
import sys

from pypdf import PdfReader

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools.document_tools as dt  # noqa: E402
from tools.document_engine_v2 import generate_document  # noqa: E402

TEMPLATE = "TMHA_SalesContract.pdf"
TEMPLATE_PATH = os.path.join(dt.DOCUMENTS_DIR, TEMPLATE)

# Distinctive sample values, one per buyer-identifying field, so a read-back can
# unambiguously confirm each landed (and didn't collide with template boilerplate).
SAMPLE_FIELD_VALUES: dict[str, str] = {
    "buyer_name": "Wendell Q. Testbuyer",
    "buyer_address": "742 Evergreen Terrace",
    "buyer_city": "Springfield",
    "buyer_county": "Greene",
    "buyer_state": "TX",
    "buyer_zip": "75001",
    "seller_name": "Texas Home Outlet",
    "manufacturer": "TRU Homes",
    "model": "The Marvel",
    "serial_number_1": "ZREG6131A",
    "label_number_1": "TEXREG6132",
    "sales_price": "94500",
}


def _read_acroform_values(pdf_path: str) -> dict[str, str]:
    """Return every AcroForm field's ``/V`` value, as strings."""
    reader = PdfReader(pdf_path)
    out: dict[str, str] = {}
    for name, fld in (reader.get_fields() or {}).items():
        v = fld.get("/V")
        if v is not None:
            out[str(name)] = str(v)
    return out


def _rendered_haystack(pdf_path: str) -> str:
    """AcroForm ``/V`` values + extracted page text — the CI-safe "what renders"."""
    reader = PdfReader(pdf_path)
    parts: list[str] = []
    for fld in (reader.get_fields() or {}).values():
        v = fld.get("/V")
        if v is not None:
            parts.append(str(v))
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            pass
    return "\n".join(parts)


def test_fill_pdf_form_populates_sales_contract_acroform(tmp_path, monkeypatch):
    """The low-level fill that pypdf 6.13 broke must write values into the form.

    Maps human field keys to this template's real widget names via the field map,
    fills the real PDF, then reads the AcroForm ``/V`` values back out. On the
    regression every value was blank (the summary fallback fired); here every
    supplied value must be present.
    """
    from config.field_map_loader import get_template_config

    monkeypatch.setattr(dt, "upload_to_gcs", lambda *a, **k: None)

    cfg = get_template_config(TEMPLATE)
    # field_map maps widget-name -> human-key; invert to human-key -> widget-name.
    field_map = cfg.get("field_map", {})
    human_to_widget: dict[str, str] = {}
    for widget_name, human_key in field_map.items():
        human_to_widget.setdefault(human_key, widget_name)

    # Some high-value fields are deliberately routed through baked text overlays
    # rather than the AcroForm appearance path (they carry no /V by design); we
    # verify those via the end-to-end rendered-text test instead.
    suppressed = dt.PDF_ACROFORM_APPEARANCE_SUPPRESSED_FIELDS.get(TEMPLATE, set())

    # Build the widget-keyed dict fill_pdf_form expects, keeping only the
    # AcroForm-backed fields whose /V we can read straight back.
    widget_values: dict[str, str] = {}
    expected_widget_values: dict[str, str] = {}
    for human_key, value in SAMPLE_FIELD_VALUES.items():
        widget = human_to_widget.get(human_key)
        if widget:
            widget_values[widget] = value
            if widget not in suppressed:
                expected_widget_values[widget] = value

    assert expected_widget_values, "field map exposes no verifiable AcroForm fields"

    out_path = dt.fill_pdf_form(
        TEMPLATE_PATH, widget_values, str(tmp_path / "contract_lowlevel.pdf")
    )
    assert os.path.exists(out_path)

    actual = _read_acroform_values(out_path)
    # The fill must have produced a real AcroForm (summary fallback has none).
    assert actual, "no AcroForm values written — summary fallback fired (regression)"

    missing = {
        w: v for w, v in expected_widget_values.items() if actual.get(w) != v
    }
    assert not missing, f"sales-contract fields not populated: {missing}"


def test_generate_document_sales_contract_carries_buyer_values(tmp_path, monkeypatch):
    """End-to-end: a generated contract must render the buyer's details.

    Guards the client-visible symptom — a blank contract — through the full
    engine path callers actually use.
    """
    monkeypatch.setattr(dt, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(dt, "upload_to_gcs", lambda *a, **k: None)

    data = {
        "buyer_first_name": "Wendell",
        "buyer_last_name": "Testbuyer",
        "buyer_address": "742 Evergreen Terrace",
        "buyer_city": "Springfield",
        "buyer_county": "Greene",
        "buyer_state": "TX",
        "buyer_zip": "75001",
        "is_new": True,
        "manufacturer": "TRU Homes",
        "model": "The Marvel",
        "no_of_sections": "Double Section",
        "serial_number_1": "ZREG6131A",
        "label_number_1": "TEXREG6132",
        "sales_price": "94500",
    }

    result = generate_document(TEMPLATE, dict(data))
    assert result["success"], result.get("message")

    haystack = _rendered_haystack(result["file_path"])
    needles = [
        "742 Evergreen Terrace",
        "Springfield",
        "ZREG6131A",  # serial number
        "TEXREG6132",  # HUD label number
    ]
    missing = [n for n in needles if n not in haystack]
    assert not missing, f"generated contract did not render: {missing}"


def test_force_helvetica_field_appearance_swaps_only_font(tmp_path):
    """The /DA rewrite must change ONLY the font name, preserving size + colour.

    This is the mechanism that dodges pypdf's broken TrueType appearance path;
    if it ever altered the size or colour, rendered contracts would silently
    change typography on a regulatory document.
    """
    from pypdf import PdfWriter

    reader = PdfReader(TEMPLATE_PATH)
    writer = PdfWriter()
    writer.append(reader)

    # Capture original (font, size, colour-tail) for each text widget.
    before: dict[str, str] = {}
    for page in writer.pages:
        for annot in page.get("/Annots") or []:
            a = annot.get_object()
            da = a.get("/DA")
            if da is not None:
                before[id(a)] = str(da)

    dt._force_helvetica_field_appearance(writer)

    swapped = 0
    for page in writer.pages:
        for annot in page.get("/Annots") or []:
            a = annot.get_object()
            da = a.get("/DA")
            if da is None or id(a) not in before:
                continue
            old = before[id(a)]
            new = str(da)
            # Font name is now /Helv.
            assert "/Helv" in new
            # Everything after the "Tf" operator (size already consumed) — i.e.
            # the colour tail — is preserved verbatim.
            assert new.split("Tf", 1)[-1] == old.split("Tf", 1)[-1]
            # The numeric size before "Tf" is preserved.
            old_size = old.split("Tf", 1)[0].split()[-1]
            new_size = new.split("Tf", 1)[0].split()[-1]
            assert old_size == new_size, f"font size changed: {old} -> {new}"
            if "/Helv" not in old:
                swapped += 1

    assert swapped > 0, "no widget /DA was rewritten — fix is a no-op (regression)"
