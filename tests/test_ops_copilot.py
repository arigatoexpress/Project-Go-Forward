"""Tests for the Ops Copilot — the in-app, admin/employee-only assistant.

Layers tested independently:
  * the how-to search (pure, deterministic),
  * the live business snapshot (managers/Firestore mocked, fault-isolation),
  * run_copilot orchestration (the model seam mocked),
  * the /api/admin/copilot endpoint (admin-gated, read-only, never 500s).

Async functions are driven with asyncio.run so no pytest-asyncio plugin is needed.
"""

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import ops_copilot  # noqa: E402


# --------------------------------------------------------------------------- #
# Fakes for the data layer
# --------------------------------------------------------------------------- #
class _FakeLead:
    def __init__(self, status):
        self.status = status


class _FakeLeadManager:
    def __init__(self, leads):
        self._leads = leads

    async def list_leads(self, limit=10000):
        return self._leads


class _FakeApptManager:
    def __init__(self, appts):
        self._appts = appts

    async def list_appointments(self, limit=10000):
        return self._appts


class _FakeDoc:
    def __init__(self, data):
        self._data = data

    def to_dict(self):
        return self._data


class _FakeCollection:
    def __init__(self, docs):
        self._docs = docs

    def stream(self, timeout=None):
        return iter(self._docs)


class _FakeDB:
    def __init__(self, collections):
        self._collections = collections

    def collection(self, name):
        return _FakeCollection(self._collections.get(name, []))


def _wire_fake_data(monkeypatch, *, leads=None, appts=None, collections=None):
    monkeypatch.setattr(
        ops_copilot, "_lead_manager", lambda pid: _FakeLeadManager(leads or [])
    )
    monkeypatch.setattr(
        ops_copilot, "_appointment_manager", lambda pid: _FakeApptManager(appts or [])
    )
    monkeypatch.setattr(ops_copilot, "_get_db", lambda: _FakeDB(collections or {}))
    # Keep the snapshot hermetic — don't read the real legacy snapshot file.
    monkeypatch.setattr(ops_copilot, "_load_inventory_snapshot", lambda: None)


# --------------------------------------------------------------------------- #
# How-to search
# --------------------------------------------------------------------------- #
def test_help_search_surfaces_login_topic_first():
    hits = ops_copilot.search_platform_help("how do I log in to the back office?")
    assert hits
    assert hits[0]["title"] == "Logging into the back office"


def test_help_search_matches_photo_upload():
    hits = ops_copilot.search_platform_help("where do I upload pictures of a home")
    assert any("photo" in h["title"].lower() for h in hits)


def test_help_search_empty_query_returns_nothing():
    assert ops_copilot.search_platform_help("") == []
    assert ops_copilot.search_platform_help("a") == []  # too short to tokenize


def test_help_search_is_bounded():
    hits = ops_copilot.search_platform_help("lead appointment photo analytics login help")
    assert len(hits) <= 3


# --------------------------------------------------------------------------- #
# Live business snapshot
# --------------------------------------------------------------------------- #
def test_snapshot_aggregates_counts(monkeypatch):
    _wire_fake_data(
        monkeypatch,
        leads=[_FakeLead("new"), _FakeLead("new"), _FakeLead("contacted")],
        appts=[_FakeLead("scheduled")],
        collections={
            "inventory": [_FakeDoc({"status": "available"}), _FakeDoc({"status": "sold"})],
            "deals": [_FakeDoc({"status": "open"})],
            "service_requests": [_FakeDoc({"status": "pending"})],
            "feedback": [_FakeDoc({"rating": 5}), _FakeDoc({"rating": 4})],
        },
    )
    snap = asyncio.run(ops_copilot.get_business_snapshot())

    assert snap["leads"]["total"] == 3
    assert snap["leads"]["by_status"] == {"new": 2, "contacted": 1}
    assert snap["appointments"]["total"] == 1
    assert snap["inventory"]["by_status"] == {"available": 1, "sold": 1}
    assert snap["deals"]["by_status"] == {"open": 1}
    assert snap["installations"]["by_status"] == {"pending": 1}
    assert snap["feedback"]["total"] == 2


def test_snapshot_fault_isolation(monkeypatch):
    """A failing collection degrades to an error stub; others still report."""

    def _boom(pid):
        raise RuntimeError("firestore down")

    monkeypatch.setattr(ops_copilot, "_lead_manager", _boom)
    monkeypatch.setattr(
        ops_copilot, "_appointment_manager", lambda pid: _FakeApptManager([])
    )
    monkeypatch.setattr(
        ops_copilot,
        "_get_db",
        lambda: _FakeDB({"inventory": [_FakeDoc({"status": "available"})]}),
    )

    snap = asyncio.run(ops_copilot.get_business_snapshot())
    assert snap["leads"] == {"error": "unavailable"}
    assert snap["inventory"]["by_status"] == {"available": 1}
    assert snap["appointments"]["total"] == 0


# --------------------------------------------------------------------------- #
# Inventory freshness (surfaces the legacy-snapshot age to staff)
# --------------------------------------------------------------------------- #
def test_inventory_freshness_flags_stale_snapshot(monkeypatch):
    from datetime import UTC, datetime, timedelta

    old = (datetime.now(UTC) - timedelta(days=400)).isoformat()
    monkeypatch.setattr(
        ops_copilot,
        "_load_inventory_snapshot",
        lambda: {"source": "legacy_site_snapshot", "retrieved_at": old, "homes": [1, 2, 3]},
    )
    fresh = ops_copilot.get_inventory_freshness()
    assert fresh["available"] is True
    assert fresh["stale"] is True
    assert fresh["age_days"] >= 14
    assert fresh["total_homes"] == 3
    assert fresh["source"] == "legacy_site_snapshot"


def test_inventory_freshness_not_stale_when_recent(monkeypatch):
    from datetime import UTC, datetime, timedelta

    recent = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    monkeypatch.setattr(
        ops_copilot,
        "_load_inventory_snapshot",
        lambda: {"source": "legacy_site_snapshot", "retrieved_at": recent, "homes": [1]},
    )
    fresh = ops_copilot.get_inventory_freshness()
    assert fresh["stale"] is False and fresh["age_days"] == 2


def test_inventory_freshness_unavailable_without_snapshot(monkeypatch):
    monkeypatch.setattr(ops_copilot, "_load_inventory_snapshot", lambda: None)
    assert ops_copilot.get_inventory_freshness() == {"available": False}


def test_inventory_freshness_tolerates_bad_timestamp(monkeypatch):
    monkeypatch.setattr(
        ops_copilot,
        "_load_inventory_snapshot",
        lambda: {"source": "x", "retrieved_at": "not-a-date", "homes": []},
    )
    fresh = ops_copilot.get_inventory_freshness()
    assert fresh["available"] is True
    assert fresh["age_days"] is None and fresh["stale"] is None


def test_snapshot_includes_inventory_freshness(monkeypatch):
    import asyncio

    _wire_fake_data(monkeypatch, leads=[_FakeLead("new")])
    # _wire_fake_data sets _load_inventory_snapshot -> None, so freshness is unavailable.
    snap = asyncio.run(ops_copilot.get_business_snapshot())
    assert "inventory_freshness" in snap
    assert snap["inventory_freshness"] == {"available": False}


# --------------------------------------------------------------------------- #
# run_copilot orchestration (model seam mocked)
# --------------------------------------------------------------------------- #
def test_run_copilot_empty_message_greets_without_model(monkeypatch):
    called = {"model": False}

    async def _should_not_run(*a, **k):
        called["model"] = True
        return "x"

    monkeypatch.setattr(ops_copilot, "_call_model", _should_not_run)
    out = asyncio.run(ops_copilot.run_copilot("   "))
    assert out["error"] is False
    assert "Texas Home Outlet" in out["reply"]
    assert called["model"] is False  # empty input short-circuits before the model


def test_run_copilot_passes_through_model_reply(monkeypatch):
    _wire_fake_data(monkeypatch, leads=[_FakeLead("new")])

    captured = {}

    async def _fake_model(system_instruction, contents):
        captured["system"] = system_instruction
        captured["contents"] = contents
        return "You have 1 new lead."

    monkeypatch.setattr(ops_copilot, "_call_model", _fake_model)
    out = asyncio.run(ops_copilot.run_copilot("how many new leads?"))

    assert out == {
        "reply": "You have 1 new lead.",
        "error": False,
        "help_topics": out["help_topics"],
    }
    # The live snapshot is grounded into the system instruction.
    assert "LIVE DATA SNAPSHOT" in captured["system"]
    assert "READ-ONLY" in captured["system"]
    assert captured["contents"][-1]["parts"][0]["text"] == "how many new leads?"


def test_run_copilot_includes_history(monkeypatch):
    _wire_fake_data(monkeypatch)

    async def _fake_model(system_instruction, contents):
        return "ok"

    monkeypatch.setattr(ops_copilot, "_call_model", _fake_model)
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    out = asyncio.run(ops_copilot.run_copilot("next", history=history))
    assert out["error"] is False


def test_run_copilot_model_failure_is_graceful(monkeypatch):
    _wire_fake_data(monkeypatch)

    async def _boom(system_instruction, contents):
        raise RuntimeError("vertex unreachable")

    monkeypatch.setattr(ops_copilot, "_call_model", _boom)
    out = asyncio.run(ops_copilot.run_copilot("hello"))
    assert out["error"] is True
    assert "trouble" in out["reply"].lower()


# --------------------------------------------------------------------------- #
# Endpoint: admin-gated, read-only
# --------------------------------------------------------------------------- #
def test_copilot_endpoint_requires_admin(monkeypatch):
    from tests.test_api_v1 import create_client

    client, *_ = create_client(monkeypatch)
    res = client.post("/api/admin/copilot", json={"message": "hi"})
    assert res.status_code in (401, 403)


def test_copilot_endpoint_returns_reply_for_admin(monkeypatch):
    from tests.test_api_v1 import create_client

    client, main, *_ = create_client(monkeypatch)

    async def _stub(message, history=None):
        return {"reply": "Hello from the copilot.", "error": False, "help_topics": []}

    monkeypatch.setattr("tools.ops_copilot.run_copilot", _stub)

    # Log in to obtain the admin cookie + CSRF token, mirroring the CSRF tests.
    login = client.post("/api/admin/verify", json={"pin": "4832"})
    assert login.status_code == 200
    csrf = login.json()["csrf_token"]

    res = client.post(
        "/api/admin/copilot",
        json={"message": "hi", "history": []},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["reply"] == "Hello from the copilot."
    assert body["error"] is False


# --------------------------------------------------------------------------- #
# Notion Command Center source switch (increment 3) — the Mira replacement
# --------------------------------------------------------------------------- #
def _fake_notion_counts(monkeypatch, mapping):
    from tools import notion_client

    monkeypatch.setattr(
        notion_client, "fetch_status_counts", lambda db_key, **kw: mapping.get(db_key, {})
    )


def test_snapshot_sources_from_notion_when_command_center_on(monkeypatch):
    monkeypatch.setenv("NOTION_COMMAND_CENTER", "on")
    _fake_notion_counts(monkeypatch, {
        "delivery_tracker": {"Delivered to Site": 3, "Trim-Out": 2},
        "cs_survey": {"Contacted-Positive": 4, "Do Not Contact": 1},
        "service_warranty": {"Repair In Progress": 2},
        "title": {"Title Issued": 5, "MCO Received from Factory": 1},
        "collections": {"Current": 10, "Default": 1},
        "insurance": {"Policy Active": 7},
    })
    _wire_fake_data(monkeypatch, collections={
        "service_requests": [_FakeDoc({"status": "pending"})],   # Firestore — should be IGNORED
        "feedback": [_FakeDoc({"rating": 5})],
    })

    snap = asyncio.run(ops_copilot.get_business_snapshot())

    # Installations now come from the Notion Delivery Tracker, not Firestore.
    assert snap["installations"]["by_status"] == {"Delivered to Site": 3, "Trim-Out": 2}
    # Feedback total derived from the CS-survey counts.
    assert snap["feedback"] == {"total": 5, "by_status": {"Contacted-Positive": 4, "Do Not Contact": 1}}
    # Post-funding ops surface as count-by-status only.
    ops = snap["operations"]
    assert ops["title"]["by_status"] == {"Title Issued": 5, "MCO Received from Factory": 1}
    assert ops["collections"]["by_status"] == {"Current": 10, "Default": 1}
    assert ops["insurance"]["by_status"] == {"Policy Active": 7}
    # PII-free: the whole snapshot is statuses + counts, no identities/emails/phones.
    blob = repr(snap).lower()
    assert "@" not in blob and "555-" not in blob


def test_snapshot_firestore_only_when_command_center_off(monkeypatch):
    """Regression lock: flag off => Firestore feedback count, no 'operations' key."""
    monkeypatch.delenv("NOTION_COMMAND_CENTER", raising=False)
    _wire_fake_data(monkeypatch, collections={
        "service_requests": [_FakeDoc({"status": "pending"})],
        "feedback": [_FakeDoc({"rating": 5}), _FakeDoc({"rating": 4})],
    })
    snap = asyncio.run(ops_copilot.get_business_snapshot())
    assert snap["installations"]["by_status"] == {"pending": 1}
    assert snap["feedback"] == {"total": 2}
    assert "operations" not in snap


def test_snapshot_degrades_when_notion_reader_raises(monkeypatch):
    monkeypatch.setenv("NOTION_COMMAND_CENTER", "on")
    from tools import notion_client

    def _boom(db_key, **kw):
        raise RuntimeError("notion down")

    monkeypatch.setattr(notion_client, "fetch_status_counts", _boom)
    _wire_fake_data(monkeypatch, collections={
        "service_requests": [_FakeDoc({"status": "pending"})],
        "feedback": [_FakeDoc({"rating": 5})],
    })
    snap = asyncio.run(ops_copilot.get_business_snapshot())
    # Notion raised -> falls back to Firestore for installations + feedback, ops degrade to {}.
    assert snap["installations"]["by_status"] == {"pending": 1}
    assert snap["feedback"] == {"total": 1}
    assert snap["operations"]["title"]["by_status"] == {}


def test_help_search_matches_delivery_status():
    hits = ops_copilot.search_platform_help("where is my home in the delivery process")
    assert any("delivery" in h["title"].lower() for h in hits)
