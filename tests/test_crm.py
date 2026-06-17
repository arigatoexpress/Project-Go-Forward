"""Tests for tools.crm_tools.save_lead — lead capture validation + contract.

Replaces a legacy smoke *script* that called ``save_lead`` at module top level,
which (a) provided zero pytest coverage and (b) appended a fake lead to
``data/leads.json`` every time the suite was merely collected. These tests pin
the validation rules and success contract without writing to disk.
"""

import pytest

from tools import crm_tools


@pytest.fixture(autouse=True)
def _no_disk_writes(monkeypatch):
    """Keep save_lead's success path from appending to the repo's data/leads.json.

    save_lead guards its file write behind ``os.access(..., os.W_OK)``; forcing
    that False exercises the full structure-and-return logic while skipping the
    side effect, so collecting/running tests never pollutes local lead data.
    """
    monkeypatch.setattr(crm_tools.os, "access", lambda *a, **k: False)


def test_save_lead_valid_returns_success():
    result = crm_tools.save_lead(
        user_name="John Doe",
        phone_number="555-123-4567",
        interest_notes="Looking for a 3 bedroom double wide.",
    )
    assert result["success"] is True
    # Confirmation echoes the customer's name and number back to the agent.
    assert "John Doe" in result["message"]
    assert "555-123-4567" in result["message"]


def test_save_lead_accepts_formatted_phone():
    result = crm_tools.save_lead(
        user_name="Jane Smith",
        phone_number="(281) 555-0100",
        interest_notes="Financing question",
    )
    assert result["success"] is True


def test_save_lead_rejects_missing_name():
    result = crm_tools.save_lead(
        user_name="", phone_number="555-123-4567", interest_notes="test"
    )
    assert result["success"] is False
    assert "name" in result["message"].lower()


def test_save_lead_rejects_missing_phone():
    result = crm_tools.save_lead(
        user_name="No Phone", phone_number="", interest_notes="test"
    )
    assert result["success"] is False


def test_save_lead_rejects_short_phone():
    result = crm_tools.save_lead(
        user_name="Short Phone", phone_number="123", interest_notes="test"
    )
    assert result["success"] is False
    assert "invalid" in result["message"].lower() or "10" in result["message"]
