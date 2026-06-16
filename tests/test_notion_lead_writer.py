"""Tests for the website-lead -> Notion Lead Pipeline writer.

This is the one piece of the integration that WRITES customer PII to a live
system, so the tests pin: it's off by default, it targets the exact live schema
(incl. the trailing-space "Email " column), it avoids the dirty duplicate phone
columns, it never writes SSN, and any failure is swallowed (fire-and-forget).
HTTP is fully mocked — nothing leaves the process.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import notion_lead_writer as nlw  # noqa: E402


class _FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)


def _capture_post(monkeypatch, *, raises=None, status=200):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        if raises is not None:
            raise raises
        return _FakeResponse(status)

    monkeypatch.setattr(nlw.httpx, "post", fake_post)
    return captured


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("NOTION_LEAD_SYNC", "on")
    monkeypatch.setenv("NOTION_TOKEN", "secret_test")
    # lead_pipeline db id comes from config.yaml notion.databases.lead_pipeline


# --- gating -------------------------------------------------------------------

def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("NOTION_LEAD_SYNC", raising=False)
    monkeypatch.setenv("NOTION_TOKEN", "secret_test")
    assert nlw.is_lead_sync_enabled() is False


def test_enabled_requires_flag_and_token(monkeypatch):
    monkeypatch.setenv("NOTION_LEAD_SYNC", "on")
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    assert nlw.is_lead_sync_enabled() is False  # flag on but no token
    monkeypatch.setenv("NOTION_TOKEN", "secret_test")
    assert nlw.is_lead_sync_enabled() is True   # config.yaml supplies the db id


def test_write_lead_is_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("NOTION_LEAD_SYNC", raising=False)
    captured = _capture_post(monkeypatch)
    assert nlw.write_lead("Jane Buyer", phone="555-111-2222") is False
    assert captured == {}  # no HTTP call at all


# --- schema-exact, PII-safe payload ------------------------------------------

def test_payload_targets_exact_live_columns():
    props = nlw.build_lead_properties(
        "Jane Buyer", email="jane@example.com", phone="555-111-2222",
        message="Interested in the Oak 28x56", source="contact_form",
    )
    assert props["Customer Name"]["title"][0]["text"]["content"] == "Jane Buyer"
    # "Email " has a trailing space in the live schema — must match exactly.
    assert "Email " in props and props["Email "] == {"email": "jane@example.com"}
    assert props["Phone Number"] == {"phone_number": "555-111-2222"}
    assert props["Pipeline Stage"] == {"select": {"name": "New Lead"}}
    notes = props["Notes"]["rich_text"][0]["text"]["content"]
    assert "Website lead" in notes and "source=contact_form" in notes
    assert "Interested in the Oak 28x56" in notes


def test_payload_avoids_dirty_and_sensitive_columns():
    props = nlw.build_lead_properties("Jane", email="j@x.com", phone="5551112222")
    # The two dirty duplicate phone columns and SSN must NEVER be written.
    assert "Phone number " not in props      # text duplicate
    assert "phone number " not in props      # date-typed duplicate
    assert "SSN / Tax ID" not in props
    assert "Email" not in props              # only the trailing-space "Email " exists


def test_omits_email_and_phone_when_absent():
    props = nlw.build_lead_properties("Walk-in", email=None, phone=None)
    assert "Email " not in props and "Phone Number" not in props
    assert props["Customer Name"]["title"][0]["text"]["content"] == "Walk-in"


# --- write + fire-and-forget --------------------------------------------------

def test_write_lead_posts_to_pages_and_returns_true(enabled, monkeypatch):
    captured = _capture_post(monkeypatch, status=200)
    assert nlw.write_lead("Jane", email="j@x.com", phone="5551112222") is True
    assert captured["url"].endswith("/pages")
    assert captured["headers"]["Authorization"] == "Bearer secret_test"
    # parent db id resolved from config.yaml (real Lead Pipeline id)
    assert captured["json"]["parent"]["database_id"].startswith("34e6688d")


def test_write_lead_swallows_errors_and_returns_false(enabled, monkeypatch):
    _capture_post(monkeypatch, raises=httpx.ConnectError("boom"))
    assert nlw.write_lead("Jane", phone="5551112222") is False  # never raises
