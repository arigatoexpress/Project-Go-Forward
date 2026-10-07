"""21st Mortgage creditor/lender block auto-fill from config.yaml ``lenders``.

No verified 21st Mortgage address exists in the repo, so config ships blank
values for the owner to fill in. These tests pin the wiring with a stand-in
profile and confirm blank config never invents an address.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config_loader  # noqa: E402
from tools.document_quality import enrich_document_data  # noqa: E402

STAND_IN = {
    "21st_mortgage": {
        "match_names": ["21st Mortgage"],
        "creditor_address": "100 Lender Way",
        "creditor_city_state_zip": "Anytown, TX 75000",
        "creditor_phone": "(800) 000-0000",
    }
}


@pytest.fixture
def stand_in_profile(monkeypatch):
    monkeypatch.setattr(config_loader, "get_lender_profiles", lambda: STAND_IN)


def test_config_declares_21st_mortgage_address_slots():
    profile = config_loader.get_lender_profiles()["21st_mortgage"]
    assert "21st Mortgage" in profile["match_names"]
    for key in ("creditor_address", "creditor_city_state_zip", "creditor_phone"):
        assert key in profile


@pytest.mark.parametrize(
    "creditor", ["21st Mortgage", "21st Mortgage Corporation", "21ST MORTGAGE CORP."]
)
def test_21st_mortgage_fills_blank_creditor_block(stand_in_profile, creditor):
    out = enrich_document_data({"creditor_name": creditor})
    assert out["creditor_address"] == "100 Lender Way"
    assert out["creditor_city_state_zip"] == "Anytown, TX 75000"
    assert out["creditor_phone"] == "(800) 000-0000"


def test_compliance_lender_also_matches(stand_in_profile):
    out = enrich_document_data({"compliance_lender": "21st Mortgage"})
    assert out["creditor_address"] == "100 Lender Way"


def test_staff_entered_creditor_values_win(stand_in_profile):
    out = enrich_document_data(
        {"creditor_name": "21st Mortgage", "creditor_address": "Branch Office Rd"}
    )
    assert out["creditor_address"] == "Branch Office Rd"
    assert out["creditor_city_state_zip"] == "Anytown, TX 75000"


def test_other_lenders_are_untouched(stand_in_profile):
    out = enrich_document_data({"creditor_name": "Vanderbilt Mortgage"})
    assert "creditor_address" not in out


def test_blank_config_values_leave_fields_blank(monkeypatch):
    blank = {"21st_mortgage": {**STAND_IN["21st_mortgage"], "creditor_address": ""}}
    monkeypatch.setattr(config_loader, "get_lender_profiles", lambda: blank)
    out = enrich_document_data({"creditor_name": "21st Mortgage"})
    assert "creditor_address" not in out
    assert out["creditor_city_state_zip"] == "Anytown, TX 75000"
