"""Regression guard: checkboxes must render CHECKED, not silently blank.

Mark Willcott review — the New/Used election (and every other checkbox: wheels,
insurance, tax-escrow, marital, own/rent, warranty single/double) came out blank
on generated packets. Root cause: the fill layer wrote the literal string "On",
but every THO checkbox widget's on-state is "/1" (TMHA/TDHCA) or "/Yes"
(creditapp) — pypdf needs the EXACT state name with its leading slash, so "On"
silently rendered /Off (unchecked). One fill-layer fix covers all checkboxes.
"""

from __future__ import annotations

import os
import sys

from pypdf import PdfReader

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_document_fields_no_blanks import FULL_CUSTOMER  # noqa: E402

from tools.document_engine_v2 import generate_document  # noqa: E402

# ─── unit: the on-state resolver ────────────────────────────────────────────


def test_resolve_checkbox_values_maps_to_real_on_state():
    from tools.document_tools import _resolve_checkbox_values

    # Simple checkbox: one non-Off state "/1"; truthy "On" -> "/1".
    states = {"New_Yes": ["/1", "/Off"], "Used_Yes": ["/1", "/Off"]}
    out = _resolve_checkbox_values({"New_Yes": "On", "Used_Yes": "Off"}, states)
    assert out["New_Yes"] == "/1"
    assert out["Used_Yes"] == "/Off"

    # /Yes-style widget.
    yes_states = {"Box": ["/Yes", "/Off"]}
    assert _resolve_checkbox_values({"Box": True}, yes_states)["Box"] == "/Yes"
    assert _resolve_checkbox_values({"Box": "1"}, yes_states)["Box"] == "/Yes"

    # Radio group: value selects the matching option by name.
    radio = {"Marital": ["/Married", "/Unmarried", "/Off"]}
    assert _resolve_checkbox_values({"Marital": "Married"}, radio)["Marital"] == "/Married"
    assert _resolve_checkbox_values({"Marital": "/Unmarried"}, radio)["Marital"] == "/Unmarried"

    # Non-checkbox fields are left untouched.
    assert _resolve_checkbox_values({"Name": "On"}, {})["Name"] == "On"


# ─── end-to-end: the New box actually renders checked ───────────────────────


def _checkbox_value(pdf_path: str, field: str):
    return (PdfReader(pdf_path).get_fields() or {}).get(field, {}).get("/V")


def test_new_used_checkbox_renders_checked(tmp_path, monkeypatch):
    import tools.document_tools as dt

    monkeypatch.setattr(dt, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(dt, "upload_to_gcs", lambda *a, **k: None)

    result = generate_document("TMHA_SalesContract.pdf", dict(FULL_CUSTOMER))  # is_new=True
    assert result["success"], result.get("message")

    v = _checkbox_value(result["file_path"], "topmostSubform[0].Page1[0].New_Yes[0]")
    assert str(v) == "/1", f"New election did not render checked: /V={v!r}"


def test_used_checkbox_renders_checked_when_used(tmp_path, monkeypatch):
    import tools.document_tools as dt

    monkeypatch.setattr(dt, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(dt, "upload_to_gcs", lambda *a, **k: None)

    data = dict(FULL_CUSTOMER)
    data["is_new"] = False  # -> is_used True via enrich

    result = generate_document("TMHA_SalesContract.pdf", data)
    assert result["success"], result.get("message")

    used = _checkbox_value(result["file_path"], "topmostSubform[0].Page1[0].Used_Yes[0]")
    new = _checkbox_value(result["file_path"], "topmostSubform[0].Page1[0].New_Yes[0]")
    assert str(used) == "/1", f"Used election did not render checked: /V={used!r}"
    assert str(new) != "/1", f"New must NOT be checked on a used home: /V={new!r}"
