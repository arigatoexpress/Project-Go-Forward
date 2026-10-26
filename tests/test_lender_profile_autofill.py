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


def _prosperity_profiles(*, enabled: bool) -> dict:
    profiles = config_loader.get_lender_profiles()
    prosperity = {**profiles["prosperity"], "autofill_enabled": enabled}
    return {**profiles, "prosperity": prosperity}


def test_prosperity_autofill_stays_off_until_mark_confirms():
    """The lot address is stored, and it does not print while the flag is false."""
    profile = config_loader.get_lender_profiles()["prosperity"]
    assert profile["autofill_enabled"] is False
    assert profile["creditor_address"] == "10685 FM 1960 East"
    out = enrich_document_data(
        {"creditor_name": "Prosperity Acquisitions, Inc. dba Texas Home Outlet"}
    )
    assert "creditor_address" not in out
    assert "creditor_city_state_zip" not in out
    assert "creditor_phone" not in out


def test_prosperity_trade_name_stays_blank_while_autofill_is_off():
    out = enrich_document_data({"creditor_name": "Texas Home Outlet"})
    assert "creditor_address" not in out


def test_prosperity_staff_address_is_kept_while_autofill_is_off():
    out = enrich_document_data(
        {
            "creditor_name": "Prosperity Acquisitions, Inc. dba Texas Home Outlet",
            "creditor_address": "Branch Office Rd",
        }
    )
    assert out["creditor_address"] == "Branch Office Rd"
    assert "creditor_city_state_zip" not in out
    assert "creditor_phone" not in out


def test_prosperity_autofill_fills_blank_block_when_enabled(monkeypatch):
    enabled = _prosperity_profiles(enabled=True)
    monkeypatch.setattr(config_loader, "get_lender_profiles", lambda: enabled)
    out = enrich_document_data(
        {"creditor_name": "Prosperity Acquisitions, Inc. dba Texas Home Outlet"}
    )
    assert out["creditor_address"] == "10685 FM 1960 East"
    assert out["creditor_city_state_zip"] == "Huffman, TX 77336"
    assert out["creditor_phone"] == "281-324-3020"


def test_prosperity_enabled_profile_matches_the_trade_name(monkeypatch):
    enabled = _prosperity_profiles(enabled=True)
    monkeypatch.setattr(config_loader, "get_lender_profiles", lambda: enabled)
    out = enrich_document_data({"creditor_name": "Texas Home Outlet"})
    assert out["creditor_address"] == "10685 FM 1960 East"


def test_prosperity_staff_address_wins_when_autofill_is_enabled(monkeypatch):
    enabled = _prosperity_profiles(enabled=True)
    monkeypatch.setattr(config_loader, "get_lender_profiles", lambda: enabled)
    out = enrich_document_data(
        {
            "creditor_name": "Prosperity Acquisitions, Inc. dba Texas Home Outlet",
            "creditor_address": "Branch Office Rd",
        }
    )
    assert out["creditor_address"] == "Branch Office Rd"
    assert out["creditor_city_state_zip"] == "Huffman, TX 77336"


def test_21st_mortgage_address_stays_blank_in_config():
    profile = config_loader.get_lender_profiles()["21st_mortgage"]
    out = enrich_document_data({"creditor_name": "21st Mortgage"})
    assert profile["creditor_address"] == ""
    assert profile["creditor_city_state_zip"] == ""
    assert profile["creditor_phone"] == ""
    assert "creditor_address" not in out


def test_blank_config_values_leave_fields_blank(monkeypatch):
    blank = {"21st_mortgage": {**STAND_IN["21st_mortgage"], "creditor_address": ""}}
    monkeypatch.setattr(config_loader, "get_lender_profiles", lambda: blank)
    out = enrich_document_data({"creditor_name": "21st Mortgage"})
    assert "creditor_address" not in out
    assert out["creditor_city_state_zip"] == "Anytown, TX 75000"
