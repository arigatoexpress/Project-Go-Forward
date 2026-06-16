"""Tests for the env-gated Notion source (tools/notion_client.py).

HTTP is fully mocked: we monkeypatch ``httpx.post`` (the only network call the
client makes) so nothing leaves the process. No live Notion credentials needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import notion_client as nc  # noqa: E402


# --------------------------------------------------------------------------- #
# Helpers / fixtures
# --------------------------------------------------------------------------- #
class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)


def _title(text):
    return {"type": "title", "title": [{"plain_text": text}]}


def _rich_text(text):
    return {"type": "rich_text", "rich_text": [{"plain_text": text}]}


def _select(name):
    return {"type": "select", "select": {"name": name}}


def _status(name):
    return {"type": "status", "status": {"name": name}}


def _number(n):
    return {"type": "number", "number": n}


def _checkbox(b):
    return {"type": "checkbox", "checkbox": b}


def _date(start):
    return {"type": "date", "date": {"start": start}}


def _installation_page():
    return {
        "id": "page-inst-1",
        "created_time": "2026-06-14T10:00:00.000Z",
        "properties": {
            "Status": _status("in_progress"),
            "Issue Type": _select("plumbing"),
            "Warranty Claim": _checkbox(True),
            "Warranty Status": _select("covered"),
            "Assigned Contractor": _rich_text("Acme Installers"),
            "Created At": _date("2026-06-14T12:00:00Z"),
            "Deal ID": _rich_text("deal-123"),
            # PII columns that must NOT be surfaced:
            "Customer Name": _title("Jane Buyer"),
            "Phone": {"type": "phone_number", "phone_number": "555-0000"},
            "Email": {"type": "email", "email": "jane@example.com"},
        },
    }


def _feedback_page():
    return {
        "id": "page-fb-1",
        "created_time": "2026-06-14T10:00:00.000Z",
        "properties": {
            "Rating": _number(5),
            "Sentiment": _select("positive"),
            "Source": _select("post-install-survey"),
            "Created At": _date("2026-06-14T12:00:00Z"),
            "Deal ID": _rich_text("deal-789"),
            # free-text comment / PII that must NOT be surfaced:
            "Comment": _rich_text("The installer called me at 555-0000"),
            "Customer Email": {"type": "email", "email": "jane@example.com"},
        },
    }


@pytest.fixture
def notion_env(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "secret_test")
    monkeypatch.setenv("NOTION_DELIVERY_TRACKER_DB_ID", "db-delivery")
    monkeypatch.setenv("NOTION_CS_SURVEY_DB_ID", "db-cs")


def _patch_post(monkeypatch, payload=None, raises=None):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        if raises is not None:
            raise raises
        return FakeResponse(payload)

    monkeypatch.setattr(nc.httpx, "post", fake_post)
    return captured


# --------------------------------------------------------------------------- #
# Configuration gating
# --------------------------------------------------------------------------- #
def test_unconfigured_by_default(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.delenv("NOTION_DELIVERY_TRACKER_DB_ID", raising=False)
    monkeypatch.delenv("NOTION_CS_SURVEY_DB_ID", raising=False)
    assert nc.is_installations_configured() is False
    assert nc.is_feedback_configured() is False


def test_configured_requires_both_token_and_db_id(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "secret_test")
    monkeypatch.delenv("NOTION_DELIVERY_TRACKER_DB_ID", raising=False)
    monkeypatch.delenv("NOTION_CS_SURVEY_DB_ID", raising=False)
    # token but no DB ids → not configured
    assert nc.is_installations_configured() is False
    assert nc.is_feedback_configured() is False
    monkeypatch.setenv("NOTION_DELIVERY_TRACKER_DB_ID", "db-delivery")
    assert nc.is_installations_configured() is True
    assert nc.is_feedback_configured() is False


def test_fetch_returns_empty_when_unconfigured(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.delenv("NOTION_DELIVERY_TRACKER_DB_ID", raising=False)
    monkeypatch.delenv("NOTION_CS_SURVEY_DB_ID", raising=False)
    # Network must not even be attempted; assert via a post that would fail.
    _patch_post(monkeypatch, raises=AssertionError("should not call httpx"))
    assert nc.fetch_installations() == []
    assert nc.fetch_feedback() == []


# --------------------------------------------------------------------------- #
# Property normalization
# --------------------------------------------------------------------------- #
def test_fetch_installations_normalizes_and_redacts(notion_env, monkeypatch):
    captured = _patch_post(monkeypatch, payload={"results": [_installation_page()]})
    rows = nc.fetch_installations(limit=25)
    assert len(rows) == 1
    row = rows[0]
    assert row == {
        "id": "page-inst-1",
        "status": "in_progress",
        "issue_type": "plumbing",
        "is_warranty_claim": True,
        "warranty_status": "covered",
        "assigned_contractor": "Acme Installers",
        "created_at": "2026-06-14T12:00:00+00:00",
        "deal_id": "deal-123",
    }
    # PII keys never appear
    for forbidden in ("customer_name", "phone", "email", "Customer Name"):
        assert forbidden not in row
    # request went to the right DB with the right headers
    assert captured["url"].endswith("/databases/db-delivery/query")
    assert captured["headers"]["Authorization"] == "Bearer secret_test"
    assert captured["headers"]["Notion-Version"] == "2022-06-28"
    assert captured["json"]["page_size"] == 25


def test_fetch_feedback_normalizes_and_redacts(notion_env, monkeypatch):
    captured = _patch_post(monkeypatch, payload={"results": [_feedback_page()]})
    rows = nc.fetch_feedback(limit=10)
    assert len(rows) == 1
    row = rows[0]
    assert row == {
        "id": "page-fb-1",
        "rating": 5,
        "sentiment": "positive",
        "source": "post-install-survey",
        "created_at": "2026-06-14T12:00:00+00:00",
        "deal_id": "deal-789",
    }
    assert "comment" not in row
    assert "customer_email" not in row
    assert captured["url"].endswith("/databases/db-cs/query")


def test_fetch_installations_applies_defaults_for_missing_props(notion_env, monkeypatch):
    page = {"id": "p2", "created_time": "2026-06-14T10:00:00Z", "properties": {}}
    _patch_post(monkeypatch, payload={"results": [page]})
    rows = nc.fetch_installations()
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "UNKNOWN"
    assert row["issue_type"] == "UNKNOWN"
    assert row["is_warranty_claim"] is False
    # created_at falls back to the page's created_time
    assert row["created_at"] == "2026-06-14T10:00:00+00:00"
    # no deal_id when absent
    assert "deal_id" not in row


def test_page_size_clamped_to_100(notion_env, monkeypatch):
    captured = _patch_post(monkeypatch, payload={"results": []})
    nc.fetch_installations(limit=5000)
    assert captured["json"]["page_size"] == 100


# --------------------------------------------------------------------------- #
# Graceful degradation
# --------------------------------------------------------------------------- #
def test_fetch_returns_empty_on_http_error(notion_env, monkeypatch):
    _patch_post(monkeypatch, raises=httpx.ConnectError("boom"))
    assert nc.fetch_installations() == []
    assert nc.fetch_feedback() == []


def test_fetch_returns_empty_on_bad_payload(notion_env, monkeypatch):
    # results missing / not a list → empty, no raise
    _patch_post(monkeypatch, payload={"object": "error", "message": "nope"})
    assert nc.fetch_installations() == []


def test_fetch_skips_non_dict_results(notion_env, monkeypatch):
    _patch_post(monkeypatch, payload={"results": ["garbage", None, _feedback_page()]})
    rows = nc.fetch_feedback()
    assert len(rows) == 1
    assert rows[0]["id"] == "page-fb-1"


# --------------------------------------------------------------------------- #
# Command Center reader (config-driven, GCP-native — increment 2)
# --------------------------------------------------------------------------- #
def _ops_page(status, *, prop_name="Status", with_pii=True):
    props = {prop_name: _status(status)}
    if with_pii:
        # PII columns that must NEVER reach a count-by-status result:
        props["Customer Name"] = _title("Jane Buyer")
        props["Phone Number"] = {"type": "phone_number", "phone_number": "555-0000"}
        props["Escrow Balance"] = _number(12345.67)
    return {"id": f"pg-{status}", "created_time": "2026-06-14T10:00:00.000Z", "properties": props}


def test_command_center_disabled_by_default(monkeypatch):
    monkeypatch.delenv("NOTION_COMMAND_CENTER", raising=False)
    assert nc.is_command_center_enabled() is False
    for val in ("on", "true", "1", "YES"):
        monkeypatch.setenv("NOTION_COMMAND_CENTER", val)
        assert nc.is_command_center_enabled() is True
    monkeypatch.setenv("NOTION_COMMAND_CENTER", "off")
    assert nc.is_command_center_enabled() is False


def test_db_id_resolves_config_then_env(monkeypatch):
    # Configured keys resolve from config.yaml notion.databases.* (real ids).
    assert nc._db_id("title").startswith("34e6688d")
    assert nc._db_id("delivery_tracker").startswith("34e6688d")
    # An unconfigured key (not in config.yaml) falls back to env.
    monkeypatch.setenv("NOTION_TEAM_TASKS_DB_ID", "db-team")
    assert nc._db_id("team_tasks") == "db-team"
    assert nc._db_id("unknown_key") == ""


def test_fetch_status_counts_counts_by_status_and_is_pii_free(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "secret_test")
    monkeypatch.setenv("NOTION_TITLE_DB_ID", "db-title")
    _patch_post(monkeypatch, payload={"results": [
        _ops_page("Title Issued"),
        _ops_page("Title Issued"),
        _ops_page("MCO Received from Factory"),
    ]})
    counts = nc.fetch_status_counts("title")
    assert counts == {"Title Issued": 2, "MCO Received from Factory": 1}
    # The PII columns on every page must not have leaked into the result.
    blob = repr(counts).lower()
    assert "jane" not in blob and "555-0000" not in blob and "12345" not in blob


def test_fetch_status_counts_tolerates_dirty_and_aliased_status_columns(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "secret_test")
    monkeypatch.setenv("NOTION_DELIVERY_TRACKER_DB_ID", "db-delivery")
    _patch_post(monkeypatch, payload={"results": [
        _ops_page("Delivered to Site", prop_name="Current Phase"),     # alias, not "Status"
        _ops_page("Delivered to Site", prop_name="Status "),            # trailing-space column
        _ops_page("Unknown one", prop_name="No Status Column At All"),  # -> UNKNOWN
    ]})
    counts = nc.fetch_status_counts("delivery_tracker")
    assert counts == {"Delivered to Site": 2, "UNKNOWN": 1}


def test_fetch_status_counts_empty_when_unconfigured(monkeypatch):
    # Use a key NOT in config.yaml so resolution depends only on env/token.
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.setenv("NOTION_TEAM_TASKS_DB_ID", "db-team")
    assert nc.fetch_status_counts("team_tasks") == {}  # no token
    monkeypatch.setenv("NOTION_TOKEN", "secret_test")
    monkeypatch.delenv("NOTION_TEAM_TASKS_DB_ID", raising=False)
    assert nc.fetch_status_counts("team_tasks") == {}  # no db id (not in config or env)


def test_query_database_paginates_across_has_more(monkeypatch):
    """fetch_status_counts must count across pages, not silently cap at 100."""
    monkeypatch.setenv("NOTION_TOKEN", "secret_test")
    pages = [
        {"results": [_ops_page("A"), _ops_page("A")], "has_more": True, "next_cursor": "cur2"},
        {"results": [_ops_page("B"), _ops_page("A")], "has_more": False, "next_cursor": None},
    ]
    state = {"i": 0, "cursors": []}

    def fake_post(url, headers=None, json=None, timeout=None):
        state["cursors"].append(json.get("start_cursor"))
        page = pages[state["i"]]
        state["i"] += 1
        return FakeResponse(page)

    monkeypatch.setattr(nc.httpx, "post", fake_post)
    counts = nc.fetch_status_counts("title")  # db id from config.yaml
    assert counts == {"A": 3, "B": 1}          # summed across both pages
    assert state["cursors"] == [None, "cur2"]   # page 2 used next_cursor
