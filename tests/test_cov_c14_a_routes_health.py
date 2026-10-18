"""Coverage campaign c14-a: error branches in obsidian/mira request paths and
tools/health_check internals.

Targets the paired ``except Exception`` tails and helper branches measured as
missing in the full-suite baseline:

- obsidian_routes.py: 47, 53, 55, 57, 67-68, 116-117, 128-129, 146-147,
  158-159, 186-187, 210-211, 224-225, 227-228, 251-252
  (167-168 and 176-177 are unreachable: ``_count_collection_by_status``
  swallows every exception before the endpoint's own ``except`` can fire.)
- mira_routes.py: 99, 106, 230-231, 266-267, 295, 327-328, 388-389,
  455-456, 529-530, 590-591
  (360-361 and 470-471 are unreachable for the same reason.)
- tools/health_check.py: 41-57, 82, 100-117
  (129-130 is the ``__main__`` entry guard, dead under import.)
"""

from __future__ import annotations

import sys
from datetime import UTC
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import mira_routes as mr  # noqa: E402
import obsidian_routes as obr  # noqa: E402
from tools import health_check  # noqa: E402


class _BoomCtor:
    """Constructor that raises, standing in for a manager class."""

    def __init__(self, *args, **kwargs):
        raise RuntimeError("secret-manager-credential")


class _BoomStore:
    def get_metrics(self):
        raise RuntimeError("store down")


class _BoomCustomerDB:
    def count_customers(self):
        raise RuntimeError("secret-db-credential")


# --------------------------------------------------------------------------- #
# obsidian_routes helpers
# --------------------------------------------------------------------------- #


def test_obsidian_uptime_none_when_start_time_unset(monkeypatch):
    monkeypatch.setattr(obr, "_obsidian_app_start_time", None)
    assert obr._get_uptime_seconds() is None


def test_obsidian_format_duration_units():
    assert obr._format_duration(45.0) == "45.0s"
    assert obr._format_duration(300.0) == "5.0m"
    assert obr._format_duration(7200.0) == "2.0h"
    assert obr._format_duration(200000.0) == "2.3d"


def test_obsidian_count_collection_error_swallowed(monkeypatch):
    def boom():
        raise RuntimeError("secret-db-credential")

    monkeypatch.setattr(obr, "get_database", boom)
    assert obr._count_collection_by_status("inventory") == {"UNKNOWN": 0}


# --------------------------------------------------------------------------- #
# obsidian_routes endpoint error tails
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_obsidian_metrics_store_error_returns_error_payload(monkeypatch):
    monkeypatch.setattr(obr, "_obsidian_metrics_store", _BoomStore())
    result = await obr.obsidian_metrics(None)
    assert result["status"] == "error"
    assert "store down" in result["error"]
    assert result["metrics"] == {}


@pytest.mark.asyncio
async def test_obsidian_leads_summary_error(monkeypatch):
    monkeypatch.setattr(obr, "LeadManager", _BoomCtor)
    result = await obr.obsidian_leads_summary(None)
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_obsidian_leads_recent_error(monkeypatch):
    monkeypatch.setattr(obr, "LeadManager", _BoomCtor)
    result = await obr.obsidian_leads_recent(None)
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_obsidian_appointments_summary_error(monkeypatch):
    monkeypatch.setattr(obr, "AppointmentManager", _BoomCtor)
    result = await obr.obsidian_appointments_summary(None)
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_obsidian_customers_summary_error(monkeypatch):
    monkeypatch.setattr(obr, "get_database", lambda: _BoomCustomerDB())
    result = await obr.obsidian_customers_summary(None)
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_obsidian_feedback_summary_error(monkeypatch):
    def boom():
        raise RuntimeError("secret-db-credential")

    monkeypatch.setattr(obr, "get_database", boom)
    result = await obr.obsidian_feedback_summary(None)
    assert result["status"] == "error"


class _RaisingCollectionRef:
    """Collection whose per-doc count stream blows up."""

    id = "broken"

    def limit(self, _n):
        return self

    def stream(self, timeout=None):
        raise RuntimeError("count failed")


class _CollectionsClient:
    def __init__(self, refs):
        self._refs = refs

    def collections(self, timeout=None):
        return list(self._refs)


class _DbWrap:
    def __init__(self, client):
        self.db = client


@pytest.mark.asyncio
async def test_obsidian_firestore_collections_per_collection_error(monkeypatch):
    monkeypatch.setattr(
        obr, "get_database", lambda: _DbWrap(_CollectionsClient([_RaisingCollectionRef()]))
    )
    result = await obr.obsidian_firestore_collections(None)
    assert result["collections"] == [{"collection": "broken", "count": None}]


@pytest.mark.asyncio
async def test_obsidian_firestore_collections_outer_error(monkeypatch):
    class _ClientWithBrokenList:
        def collections(self, timeout=None):
            raise RuntimeError("secret-db-credential")

    monkeypatch.setattr(obr, "get_database", lambda: _DbWrap(_ClientWithBrokenList()))
    result = await obr.obsidian_firestore_collections(None)
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_obsidian_notify_dispatch_error_returns_ok_false(monkeypatch):
    import tools.partner_webhooks as pw

    def boom(*args, **kwargs):
        raise RuntimeError("webhook transport down")

    monkeypatch.setattr(pw, "dispatch_partner_event", boom)
    monkeypatch.setattr(obr, "get_database", lambda: object())

    payload = obr.ObsidianNotifyPayload(message="vault alert")
    result = await obr.obsidian_notify(payload, None)
    assert result["ok"] is False
    assert "webhook transport down" in result["error"]
    assert result["event"] == "obsidian.notify"


# --------------------------------------------------------------------------- #
# mira_routes helper gaps
# --------------------------------------------------------------------------- #


def test_parse_timestamp_naive_iso_string_assumed_utc():
    dt = mr._parse_timestamp("2026-06-14T12:00:00")
    assert dt is not None
    assert dt.tzinfo is UTC
    assert dt.hour == 12


def test_parse_timestamp_unsupported_type_returns_none():
    assert mr._parse_timestamp(20260614) is None
    assert mr._parse_timestamp(["2026-06-14"]) is None


# --------------------------------------------------------------------------- #
# mira_routes endpoint error tails
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_mira_leads_recent_error_fail_closed(monkeypatch):
    monkeypatch.setattr(mr, "LeadManager", _BoomCtor)
    result = await mr.mira_leads_recent(None)
    assert result["status"] == "error"
    assert result["error"] == "internal_error"
    assert "secret" not in str(result)


@pytest.mark.asyncio
async def test_mira_leads_triage_error_fail_closed(monkeypatch):
    monkeypatch.setattr(mr, "LeadManager", _BoomCtor)
    result = await mr.mira_leads_triage(None)
    assert result["status"] == "error"
    assert result["error"] == "internal_error"


class _Lead:
    def __init__(self, lead_id, status="new"):
        self.lead_id = lead_id
        self.status = status
        self.source = "chat"
        self.created_at = None
        self.priority = None
        self.assigned_to = None
        self.triage_reason = None
        self.triage_notes = None
        self.last_triage_at = None


class _NoneTriageManager:
    """get_lead finds the lead, but triage_lead reports it gone (race)."""

    def __init__(self, project_id=None):
        self.project_id = project_id

    async def get_lead(self, lead_id):
        return _Lead(lead_id)

    async def triage_lead(self, lead_id, update):
        return None


@pytest.mark.asyncio
async def test_mira_update_lead_triage_vanished_lead(monkeypatch):
    monkeypatch.setattr(mr, "LeadManager", _NoneTriageManager)
    result = await mr.mira_update_lead_triage(None, "L1", {"priority": "high"})
    assert result["status"] == "error"
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_mira_update_lead_triage_error_fail_closed(monkeypatch):
    monkeypatch.setattr(mr, "LeadManager", _BoomCtor)
    result = await mr.mira_update_lead_triage(None, "L1", {"priority": "high"})
    assert result["status"] == "error"
    assert result["error"] == "internal_error"


@pytest.mark.asyncio
async def test_mira_installations_summary_error_fail_closed(monkeypatch):
    def boom():
        raise RuntimeError("notion secret-token")

    monkeypatch.setattr(mr.notion_client, "is_installations_configured", boom)
    result = await mr.mira_installations_summary(None)
    assert result["status"] == "error"
    assert result["error"] == "internal_error"
    assert "secret" not in str(result)


@pytest.mark.asyncio
async def test_mira_installations_recent_error_fail_closed(monkeypatch):
    def boom():
        raise RuntimeError("notion secret-token")

    monkeypatch.setattr(mr.notion_client, "is_installations_configured", boom)
    result = await mr.mira_installations_recent(None)
    assert result["status"] == "error"
    assert result["error"] == "internal_error"


@pytest.mark.asyncio
async def test_mira_feedback_summary_error_fail_closed(monkeypatch):
    def boom():
        raise RuntimeError("notion secret-token")

    monkeypatch.setattr(mr.notion_client, "is_feedback_configured", boom)
    result = await mr.mira_feedback_summary(None)
    assert result["status"] == "error"
    assert result["error"] == "internal_error"


@pytest.mark.asyncio
async def test_mira_feedback_recent_error_fail_closed(monkeypatch):
    def boom():
        raise RuntimeError("notion secret-token")

    monkeypatch.setattr(mr.notion_client, "is_feedback_configured", boom)
    result = await mr.mira_feedback_recent(None)
    assert result["status"] == "error"
    assert result["error"] == "internal_error"


# --------------------------------------------------------------------------- #
# tools/health_check
# --------------------------------------------------------------------------- #


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


def test_check_endpoint_get_ok(monkeypatch):
    monkeypatch.setattr(
        health_check.requests, "get", lambda url, timeout=None: _Resp(200)
    )
    result = health_check.check_endpoint("GET", "/health")
    assert result == {"ok": True, "status": 200, "error": None}


def test_check_endpoint_status_mismatch_reports_error(monkeypatch):
    monkeypatch.setattr(
        health_check.requests, "get", lambda url, timeout=None: _Resp(500)
    )
    result = health_check.check_endpoint("GET", "/health", expected_status=200)
    assert result["ok"] is False
    assert result["status"] == 500
    assert result["error"] == "Expected 200, got 500"


def test_check_endpoint_post(monkeypatch):
    seen = {}

    def fake_post(url, timeout=None):
        seen["url"] = url
        return _Resp(403)

    monkeypatch.setattr(health_check.requests, "post", fake_post)
    result = health_check.check_endpoint("POST", "/api/leads", expected_status=403)
    assert result["ok"] is True
    assert seen["url"] == f"{health_check.BASE_URL}/api/leads"


def test_check_endpoint_unknown_method():
    result = health_check.check_endpoint("DELETE", "/health")
    assert result["ok"] is False
    assert "Unknown method" in result["error"]


def test_check_endpoint_request_exception(monkeypatch):
    def boom(url, timeout=None):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(health_check.requests, "get", boom)
    result = health_check.check_endpoint("GET", "/health")
    assert result == {"ok": False, "status": None, "error": "connection refused"}


# -- run_health_check body (Firestore block + failing-check error line) ------ #


class _HCDoc:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    def to_dict(self):
        return dict(self._data)


class _HCCollection:
    def __init__(self, docs):
        self._docs = docs

    def stream(self, timeout=None):
        return iter(self._docs)

    def limit(self, _n):
        return self


def _install_firestore_fake(monkeypatch, docs):
    """Patch google.cloud.firestore.Client so run_health_check stays hermetic."""
    import google.cloud.firestore as firestore_module

    class FakeClient:
        def __init__(self, project=None):
            self.project = project

        def collection(self, _name):
            return _HCCollection(docs)

    monkeypatch.setattr(firestore_module, "Client", FakeClient)


def test_run_health_check_prints_error_for_failing_check(monkeypatch, capsys):
    monkeypatch.setattr(
        health_check,
        "check_endpoint",
        lambda *a, **k: {"ok": False, "status": 500, "error": "Expected 200, got 500"},
    )
    _install_firestore_fake(monkeypatch, docs=[])

    assert health_check.run_health_check() is False
    out = capsys.readouterr().out
    assert "Error: Expected 200, got 500" in out
    assert "Results: 0/4 checks passed" in out


def test_run_health_check_firestore_count_and_clean_quality(monkeypatch, capsys):
    monkeypatch.setattr(
        health_check,
        "check_endpoint",
        lambda *a, **k: {"ok": True, "status": 200, "error": None},
    )
    docs = [
        _HCDoc("i1", {"model_name": "M1", "photos": ["p1"]}),
        _HCDoc("i2", {"model_name": "M2", "image_url": "https://img"}),
    ]
    _install_firestore_fake(monkeypatch, docs=docs)

    assert health_check.run_health_check() is True
    out = capsys.readouterr().out
    assert "Inventory documents: 2" in out
    assert "Data quality looks good" in out


def test_run_health_check_reports_data_quality_issues(monkeypatch, capsys):
    monkeypatch.setattr(
        health_check,
        "check_endpoint",
        lambda *a, **k: {"ok": True, "status": 200, "error": None},
    )
    docs = [
        _HCDoc("bad1", {}),  # missing model_name, no images
        _HCDoc("bad2", {"model_name": "M2"}),  # no images
        _HCDoc("good", {"model_name": "M3", "photos": ["p"]}),
    ]
    _install_firestore_fake(monkeypatch, docs=docs)

    assert health_check.run_health_check() is True
    out = capsys.readouterr().out
    assert "Data quality issues found" in out
    assert "bad1: Missing model_name" in out
    assert "bad1: No images" in out
    assert "bad2: No images" in out
