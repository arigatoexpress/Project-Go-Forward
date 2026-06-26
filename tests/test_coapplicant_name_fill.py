"""Live-path co-applicant-name + 1023 remap gate (Adriana contract audit, 2026-06-25).

Adriana reported the co-applicant (co-buyer) name "disappears" on a set of forms.
Root cause (ADRIANA-CONTRACT-AUDIT-2026-06-25): those forms have a single plural
"Buyer(s)" widget (``Contract_Name[0]``) mapped to the SINGULAR ``buyer_name``,
so the co-buyer never prints. The fix remaps those widgets to ``buyers_combined``
("Buyer & Co-Buyer"), which ``enrich_document_data`` composes and which degrades
to the buyer name alone for single-buyer deals.

This gate asserts on REAL OUTPUT through the SAME entrypoint the live server uses
(``main.engine_generate_document`` -> v2 engine -> ``enrich_document_data``), never
on an internal helper. It mirrors tests/test_live_doc_generation_reachability.py.

  (a) 2-buyer deal  -> BOTH names appear on Contract_Name[0].
  (b) 1-buyer deal  -> ONLY the buyer name (no trailing " &", no blank artifact).
  (c) TDHCA_1023     -> the 4 remapped widgets fill from a deal carrying the values.

CI-safe: reads AcroForm ``/V`` back (no poppler), GCS upload disabled, output to tmp.
"""

from __future__ import annotations

import os
import sys

from pypdf import PdfReader

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read_acroform_values(pdf_path: str) -> dict[str, str]:
    reader = PdfReader(pdf_path)
    out: dict[str, str] = {}
    for name, fld in (reader.get_fields() or {}).items():
        v = fld.get("/V")
        if v is not None:
            out[str(name)] = str(v)
    return out


def _generate(template: str, data: dict, tmp_path, monkeypatch) -> dict:
    import main
    import tools.document_tools as dt

    monkeypatch.setattr(dt, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(dt, "upload_to_gcs", lambda *a, **k: None)
    result = main.engine_generate_document(template, dict(data))
    assert result["success"], result.get("message")
    return result


# Forms whose plural "Buyer(s)" line is remapped to buyers_combined. Each tuple is
# (template, widget that should now carry BOTH names).
COMBINED_BUYER_FORMS = [
    ("Internal_Consumer_Home_Acceptance_Acknowledgement.pdf",
     "topmostSubform[0].Page1[0].Contract_Name[0]"),
    ("approval.pdf", "topmostSubform[0].Page1[0].Contract_Name[0]"),
    ("TDHCA_HUDDisclosure.pdf", "topmostSubform[0].Page2[0].Contract_Name[0]"),
]


def test_two_buyer_deal_renders_both_names(tmp_path, monkeypatch):
    """A 2-buyer deal must print BOTH names on the remapped Buyer(s) line."""
    from scripts.validate_document_fills import FULL_DEAL

    data = dict(FULL_DEAL)
    buyer = data["buyer_name"]
    co_buyer = data["co_buyer_name"]
    expected = f"{buyer} & {co_buyer}"

    for template, widget in COMBINED_BUYER_FORMS:
        result = _generate(template, data, tmp_path, monkeypatch)
        actual = _read_acroform_values(result["file_path"])
        assert actual.get(widget) == expected, (
            f"{template}: {widget} = {actual.get(widget)!r}, expected {expected!r}"
        )


def test_single_buyer_deal_has_no_trailing_ampersand(tmp_path, monkeypatch):
    """A 1-buyer deal must print ONLY the buyer name — no ' &', no blank co-buyer."""
    from scripts.validate_document_fills import FULL_DEAL

    data = dict(FULL_DEAL)
    # Strip every co-buyer signal so buyers_combined degrades to the buyer alone.
    for k in list(data):
        if k.startswith("co_buyer"):
            data.pop(k)
    buyer = data["buyer_name"]

    for template, widget in COMBINED_BUYER_FORMS:
        result = _generate(template, data, tmp_path, monkeypatch)
        actual = _read_acroform_values(result["file_path"])
        value = actual.get(widget)
        assert value == buyer, (
            f"{template}: {widget} = {value!r}, expected exactly {buyer!r}"
        )
        assert value is not None
        assert "&" not in value, f"{template}: trailing ampersand artifact in {value!r}"
        assert value.strip() == value, f"{template}: stray whitespace in {value!r}"


def test_1023_remapped_widgets_fill(tmp_path, monkeypatch):
    """The 4 remapped TDHCA_1023 widgets must fill from a deal carrying the values."""
    from scripts.validate_document_fills import FULL_DEAL

    data = dict(FULL_DEAL)
    data["date_of_sale"] = "2026-06-04"
    data["buyer_address"] = "456 Meadow Lane"
    data["portfolio"] = "21st Mortgage"
    data["has_lien"] = True

    result = _generate(
        "TDHCA_1023-Statement-Ownership.pdf", data, tmp_path, monkeypatch
    )
    actual = _read_acroform_values(result["file_path"])

    # date_of_sale -> Contract_Start_Date (page 1)
    assert actual.get("topmostSubform[0].Page1[0].Contract_Start_Date[0]") == "2026-06-04"
    # buyer_address -> Install_Address[1] (the second/duplicate address widget)
    assert actual.get("topmostSubform[0].Page1[0].Install_Address[1]") == "456 Meadow Lane"
    # portfolio -> Page2 Portfolio
    assert actual.get("topmostSubform[0].Page2[0].Portfolio[0]") == "21st Mortgage"
    # has_lien -> Page2 Lien_Yes checkbox (on-state resolved to the widget's /1)
    lien = actual.get("topmostSubform[0].Page2[0].Lien_Yes[0]")
    assert lien not in (None, "/Off", "Off"), f"Page2 Lien_Yes not checked: {lien!r}"
