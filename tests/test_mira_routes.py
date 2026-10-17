"""Tests for the Mira partner-monitoring bridge endpoints.

Focus: notion-bridge workstream (Delivery Tracker & CS surveys).
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import mira_routes as mr  # noqa: E402


@dataclass
class FakeDocSnapshot:
    id: str
    _data: dict

    def to_dict(self) -> dict:
        return dict(self._data)


class FakeQuery:
    def __init__(self, docs, order_by=None, direction="ASCENDING", limit_n=None):
        self._docs = docs
        self._order_by = order_by
        self._direction = direction
        self._limit = limit_n

    def order_by(self, field, direction="ASCENDING"):
        return FakeQuery(self._docs, field, direction, self._limit)

    def limit(self, n):
        return FakeQuery(self._docs, self._order_by, self._direction, n)

    def stream(self, timeout: float | None = None):
        docs = list(self._docs)
        if self._order_by:
            reverse = self._direction == "DESCENDING"
            docs.sort(key=lambda d: d._data.get(self._order_by) or "", reverse=reverse)
        if self._limit is not None:
            docs = docs[: self._limit]
        return iter(docs)


class FakeCollection:
    def __init__(self, docs):
        self._docs = docs

    def stream(self, timeout: float | None = None):
        return iter(self._docs)

    def order_by(self, field, direction="ASCENDING"):
        return FakeQuery(self._docs).order_by(field, direction)

    def limit(self, n):
        return FakeQuery(self._docs).limit(n)


class FakeFirestoreDB:
    def __init__(self, collections):
        self._collections = collections

    def collection(self, name):
        return FakeCollection(self._collections.get(name, []))


class FakeTHODatabase:
    def __init__(self, collections):
        self.db = FakeFirestoreDB(collections)


@pytest.fixture
def patch_db(monkeypatch):
    collections = {"service_requests": [], "feedback": []}
    monkeypatch.setattr(mr, "get_database", lambda: FakeTHODatabase(collections))
    return collections


def test_parse_timestamp_handles_iso_string():
    dt = mr._parse_timestamp("2026-06-14T12:00:00Z")
    assert dt is not None
    assert dt.tzinfo is not None


def test_parse_timestamp_handles_datetime_object():
    dt = mr._parse_timestamp(datetime(2026, 6, 14, 12, 0, 0))
    assert dt is not None
    assert dt.tzinfo is UTC


def test_parse_timestamp_returns_none_for_garbage():
    assert mr._parse_timestamp("not-a-date") is None
    assert mr._parse_timestamp(None) is None


@pytest.mark.asyncio
async def test_installations_summary_counts_by_status(patch_db):
    patch_db["service_requests"] = [
        FakeDocSnapshot("sr-1", {"status": "open"}),
        FakeDocSnapshot("sr-2", {"status": "open"}),
        FakeDocSnapshot("sr-3", {"status": "resolved"}),
    ]
    result = await mr.mira_installations_summary(None)
    assert result["status"] == "healthy"
    assert result["total"] == 3
    assert result["by_status"] == {"open": 2, "resolved": 1}


@pytest.mark.asyncio
async def test_installations_recent_filters_by_hours_and_excludes_pii(patch_db):
    now = datetime.now(UTC)
    old = now - timedelta(hours=48)
    patch_db["service_requests"] = [
        FakeDocSnapshot(
            "sr-new",
            {
                "status": "in_progress",
                "issue_type": "plumbing",
                "is_warranty_claim": True,
                "warranty_status": "covered",
                "assigned_contractor": "Acme Installers",
                "deal_id": "deal-123",
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "customer_id": "cust-456",
                "buyer_phone": "555-0000",
            },
        ),
        FakeDocSnapshot(
            "sr-old", {"status": "open", "issue_type": "electrical", "created_at": old.isoformat()}
        ),
    ]
    result = await mr.mira_installations_recent(None, hours=24, limit=10)
    assert result["status"] == "healthy"
    assert result["count"] == 1
    item = result["items"][0]
    assert item["id"] == "sr-new"
    assert item["issue_type"] == "plumbing"
    assert item["deal_id"] == "deal-123"
    assert "customer_id" not in item
    assert "buyer_phone" not in item


@pytest.mark.asyncio
async def test_installations_recent_handles_firestore_datetime_objects(patch_db):
    now = datetime.now(UTC)
    patch_db["service_requests"] = [
        FakeDocSnapshot("sr-dt", {"status": "scheduled", "issue_type": "hvac", "created_at": now}),
    ]
    result = await mr.mira_installations_recent(None, hours=24, limit=10)
    assert result["count"] == 1
    assert result["items"][0]["id"] == "sr-dt"


@pytest.mark.asyncio
async def test_feedback_summary_counts_ratings_and_sentiments(patch_db):
    patch_db["feedback"] = [
        FakeDocSnapshot("fb-1", {"rating": 5, "sentiment": "positive"}),
        FakeDocSnapshot("fb-2", {"rating": 4, "sentiment": "positive"}),
        FakeDocSnapshot("fb-3", {"sentiment": "negative"}),
    ]
    result = await mr.mira_feedback_summary(None)
    assert result["status"] == "healthy"
    assert result["total"] == 3


@pytest.mark.asyncio
async def test_feedback_recent_filters_by_hours_and_excludes_comments(patch_db):
    now = datetime.now(UTC)
    old = now - timedelta(hours=48)
    patch_db["feedback"] = [
        FakeDocSnapshot(
            "fb-new",
            {
                "rating": 5,
                "sentiment": "positive",
                "source": "post-install-survey",
                "deal_id": "deal-789",
                "created_at": now.isoformat(),
                "comment": "The installer called me at 555-0000",
            },
        ),
        FakeDocSnapshot(
            "fb-old",
            {
                "rating": 2,
                "sentiment": "negative",
                "source": "email",
                "created_at": old.isoformat(),
            },
        ),
    ]
    result = await mr.mira_feedback_recent(None, hours=24, limit=10)
    assert result["status"] == "healthy"
    assert result["count"] == 1
    item = result["items"][0]
    assert item["id"] == "fb-new"
    assert item["deal_id"] == "deal-789"
    assert "comment" not in item


# --------------------------------------------------------------------------- #
# Source selection: Notion (when configured) vs Firestore (default)
# --------------------------------------------------------------------------- #
@pytest.fixture
def force_firestore(monkeypatch):
    """Pin both Notion sources OFF so the Firestore path is exercised."""
    monkeypatch.setattr(mr.notion_client, "is_installations_configured", lambda: False)
    monkeypatch.setattr(mr.notion_client, "is_feedback_configured", lambda: False)


@pytest.mark.asyncio
async def test_installations_summary_reports_firestore_source(patch_db, force_firestore):
    patch_db["service_requests"] = [FakeDocSnapshot("sr-1", {"status": "open"})]
    result = await mr.mira_installations_summary(None)
    assert result["source"] == "firestore"
    assert result["by_status"] == {"open": 1}


@pytest.mark.asyncio
async def test_installations_recent_reports_firestore_source(patch_db, force_firestore):
    now = datetime.now(UTC)
    patch_db["service_requests"] = [
        FakeDocSnapshot(
            "sr-1", {"status": "open", "issue_type": "hvac", "created_at": now.isoformat()}
        ),
    ]
    result = await mr.mira_installations_recent(None, hours=24, limit=10)
    assert result["source"] == "firestore"
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_feedback_summary_reports_firestore_source(patch_db, force_firestore):
    patch_db["feedback"] = [FakeDocSnapshot("fb-1", {"rating": 5, "sentiment": "positive"})]
    result = await mr.mira_feedback_summary(None)
    assert result["source"] == "firestore"
    assert result["total"] == 1


@pytest.mark.asyncio
async def test_feedback_recent_reports_firestore_source(patch_db, force_firestore):
    now = datetime.now(UTC)
    patch_db["feedback"] = [
        FakeDocSnapshot(
            "fb-1",
            {
                "rating": 4,
                "sentiment": "positive",
                "source": "email",
                "created_at": now.isoformat(),
            },
        ),
    ]
    result = await mr.mira_feedback_recent(None, hours=24, limit=10)
    assert result["source"] == "firestore"
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_installations_summary_uses_notion_when_configured(monkeypatch):
    monkeypatch.setattr(mr.notion_client, "is_installations_configured", lambda: True)
    monkeypatch.setattr(
        mr.notion_client,
        "fetch_installations",
        lambda limit=50: [{"status": "open"}, {"status": "open"}, {"status": "resolved"}],
    )
    # If Firestore were touched it would error loudly; make sure it isn't.
    monkeypatch.setattr(
        mr, "get_database", lambda: (_ for _ in ()).throw(AssertionError("firestore touched"))
    )
    result = await mr.mira_installations_summary(None)
    assert result["status"] == "healthy"
    assert result["source"] == "notion"
    assert result["total"] == 3
    assert result["by_status"] == {"open": 2, "resolved": 1}


@pytest.mark.asyncio
async def test_installations_recent_uses_notion_when_configured(monkeypatch):
    now = datetime.now(UTC)
    old = now - timedelta(hours=48)
    monkeypatch.setattr(mr.notion_client, "is_installations_configured", lambda: True)
    monkeypatch.setattr(
        mr.notion_client,
        "fetch_installations",
        lambda limit=50: [
            {
                "id": "n-1",
                "status": "in_progress",
                "issue_type": "plumbing",
                "is_warranty_claim": True,
                "warranty_status": "covered",
                "assigned_contractor": "Acme",
                "created_at": now.isoformat(),
                "deal_id": "deal-1",
            },
            {"id": "n-old", "status": "open", "issue_type": "hvac", "created_at": old.isoformat()},
        ],
    )
    monkeypatch.setattr(
        mr, "get_database", lambda: (_ for _ in ()).throw(AssertionError("firestore touched"))
    )
    result = await mr.mira_installations_recent(None, hours=24, limit=10)
    assert result["source"] == "notion"
    assert result["count"] == 1
    item = result["items"][0]
    assert item["id"] == "n-1"
    assert item["issue_type"] == "plumbing"
    assert item["deal_id"] == "deal-1"


@pytest.mark.asyncio
async def test_feedback_summary_uses_notion_when_configured(monkeypatch):
    monkeypatch.setattr(mr.notion_client, "is_feedback_configured", lambda: True)
    monkeypatch.setattr(
        mr.notion_client,
        "fetch_feedback",
        lambda limit=50: [
            {"rating": 5, "sentiment": "positive"},
            {"rating": 4, "sentiment": "positive"},
            {"rating": None, "sentiment": "negative"},
        ],
    )
    monkeypatch.setattr(
        mr, "get_database", lambda: (_ for _ in ()).throw(AssertionError("firestore touched"))
    )
    result = await mr.mira_feedback_summary(None)
    assert result["source"] == "notion"
    assert result["total"] == 3
    # rating=None row is not counted toward rating_counts
    assert result["rating_counts"] == {5: 1, 4: 1}
    assert result["sentiment_counts"] == {"positive": 2, "negative": 1}


@pytest.mark.asyncio
async def test_feedback_recent_uses_notion_when_configured(monkeypatch):
    now = datetime.now(UTC)
    monkeypatch.setattr(mr.notion_client, "is_feedback_configured", lambda: True)
    monkeypatch.setattr(
        mr.notion_client,
        "fetch_feedback",
        lambda limit=50: [
            {
                "id": "nf-1",
                "rating": 5,
                "sentiment": "positive",
                "source": "post-install-survey",
                "created_at": now.isoformat(),
                "deal_id": "deal-9",
            },
        ],
    )
    monkeypatch.setattr(
        mr, "get_database", lambda: (_ for _ in ()).throw(AssertionError("firestore touched"))
    )
    result = await mr.mira_feedback_recent(None, hours=24, limit=10)
    assert result["source"] == "notion"
    assert result["count"] == 1
    assert result["items"][0]["id"] == "nf-1"
    assert result["items"][0]["deal_id"] == "deal-9"


# --------------------------------------------------------------------------- #
# Helpers, uptime/version, health/system/metrics, errors/chat stubs
# --------------------------------------------------------------------------- #


@pytest.fixture
def mira_refs(monkeypatch):
    """Reset (and restore after the test) the module-level refs set_mira_refs
    mutates, so uptime/metrics tests cannot leak state into other tests."""
    monkeypatch.setattr(mr, "_mira_app_start_time", None)
    monkeypatch.setattr(mr, "_mira_metrics_store", None)
    return mr


class TestHelpers:
    def test_format_duration_units(self):
        assert mr._format_duration(45.5) == "45.5s"
        assert mr._format_duration(300) == "5.0m"
        assert mr._format_duration(7200) == "2.0h"
        assert mr._format_duration(172800) == "2.0d"

    def test_app_version_env_precedence(self, monkeypatch):
        monkeypatch.delenv("APP_VERSION", raising=False)
        monkeypatch.delenv("K_REVISION", raising=False)
        assert mr._get_app_version() == "local"
        monkeypatch.setenv("K_REVISION", "rev-9")
        assert mr._get_app_version() == "rev-9"
        monkeypatch.setenv("APP_VERSION", "1.2.3")
        assert mr._get_app_version() == "1.2.3"

    def test_mira_error_never_leaks_internal_strings(self):
        result = mr._mira_error(RuntimeError("secret db password"))
        assert result["status"] == "error"
        assert result["error"] == "internal_error"
        assert "secret" not in str(result)

    def test_count_collection_by_status_aggregates(self, monkeypatch):
        docs = [
            FakeDocSnapshot("a", {"status": "open"}),
            FakeDocSnapshot("b", {"status": "open"}),
            FakeDocSnapshot("c", {}),
        ]
        monkeypatch.setattr(mr, "get_database", lambda: FakeTHODatabase({"service_requests": docs}))
        assert mr._count_collection_by_status("service_requests") == {"open": 2, "UNKNOWN": 1}

    def test_count_collection_by_status_failure_is_zeroed(self, monkeypatch):
        class BoomDB:
            db = None

            def collection(self, _name):
                raise RuntimeError("down")

        monkeypatch.setattr(mr, "get_database", lambda: BoomDB())
        assert mr._count_collection_by_status("inventory") == {"UNKNOWN": 0}


class TestHealthSystemMetrics:
    @pytest.mark.asyncio
    async def test_health_reports_uptime(self, mira_refs):
        mira_refs.set_mira_refs(time.monotonic() - 45, None)
        result = await mira_refs.mira_health(None)
        assert result["status"] == "healthy"
        assert 40 < result["uptime_seconds"] < 50
        assert result["uptime_formatted"].endswith("s")

    @pytest.mark.asyncio
    async def test_health_without_start_time(self, mira_refs):
        result = await mira_refs.mira_health(None)
        assert result["uptime_seconds"] is None
        assert result["uptime_formatted"] is None

    @pytest.mark.asyncio
    async def test_system_reports_env_status(self, mira_refs, monkeypatch):
        mira_refs.set_mira_refs(time.monotonic(), None)
        monkeypatch.setenv("K_SERVICE", "tho-backend")
        monkeypatch.setenv("RESEND_API_KEY", "key-123")
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        monkeypatch.setattr(mr, "get_deployment_config", lambda: {"project_id": "tho-ai-agent"})

        result = await mira_refs.mira_system(None)

        assert result["platform"] == "cloud_run"
        assert result["project_id"] == "tho-ai-agent"
        assert result["env_status"]["RESEND_API_KEY"] is True
        assert result["env_status"]["SENTRY_DSN"] is False

    @pytest.mark.asyncio
    async def test_metrics_returns_store_data(self, mira_refs):
        class Store:
            def get_metrics(self):
                return {"p95_ms": 42}

        mira_refs.set_mira_refs(None, Store())
        result = await mira_refs.mira_metrics(None)
        assert result["metrics"] == {"p95_ms": 42}

    @pytest.mark.asyncio
    async def test_metrics_failure_returns_empty(self, mira_refs):
        class BoomStore:
            def get_metrics(self):
                raise RuntimeError("store down")

        mira_refs.set_mira_refs(None, BoomStore())
        result = await mira_refs.mira_metrics(None)
        assert result["status"] == "error"
        assert result["metrics"] == {}

    @pytest.mark.asyncio
    async def test_errors_and_chat_summary_clamp_hours(self):
        errors = await mr.mira_errors(None, hours=9999)
        assert errors["hours"] == 168
        chat = await mr.mira_chat_summary(None, hours=0)
        assert chat["hours"] == 1
        assert chat["collection"] == "chat_sessions"


# --------------------------------------------------------------------------- #
# Leads endpoints (LeadManager mocked) incl. triage webhook dispatch
# --------------------------------------------------------------------------- #


class FakeLead:
    def __init__(self, lead_id, status="new", source="chat", created_at=None, **fields):
        self.lead_id = lead_id
        self.status = status
        self.source = source
        self.created_at = created_at
        self.priority = fields.get("priority")
        self.assigned_to = fields.get("assigned_to")
        self.triage_reason = fields.get("triage_reason")
        self.triage_notes = fields.get("triage_notes")
        self.last_triage_at = fields.get("last_triage_at")


class FakeLeadManager:
    """Records constructor args and call args; replays scripted leads."""

    instances = []

    _class_leads = []
    _class_by_id = {}
    _class_triage_result = None

    def __init__(self, project_id=None):
        self.project_id = project_id
        self.calls = []
        # The endpoint constructs its own instance; data lives at class level.
        self._leads = FakeLeadManager._class_leads
        self._by_id = FakeLeadManager._class_by_id
        self._triage_result = FakeLeadManager._class_triage_result
        FakeLeadManager.instances.append(self)

    async def list_leads(self, limit=100):
        self.calls.append(("list_leads", {"limit": limit}))
        return list(self._leads)

    async def list_leads_needing_triage(self, status="new", min_age_hours=None, limit=100):
        self.calls.append(
            (
                "list_leads_needing_triage",
                {"status": status, "min_age_hours": min_age_hours, "limit": limit},
            )
        )
        return list(self._leads)

    async def get_lead(self, lead_id):
        self.calls.append(("get_lead", lead_id))
        return self._by_id.get(lead_id)

    async def triage_lead(self, lead_id, update):
        self.calls.append(("triage_lead", {"lead_id": lead_id, "update": update}))
        return self._triage_result


@pytest.fixture
def lead_manager(monkeypatch):
    FakeLeadManager.instances = []
    FakeLeadManager._class_leads = []
    FakeLeadManager._class_by_id = {}
    FakeLeadManager._class_triage_result = None
    monkeypatch.setattr(mr, "LeadManager", FakeLeadManager)
    return FakeLeadManager


@pytest.mark.asyncio
async def test_leads_summary_counts_by_status(lead_manager):
    FakeLeadManager._class_leads = [
        FakeLead("L1", status="new"),
        FakeLead("L2", status="contacted"),
        FakeLead("L3", status="new"),
    ]
    result = await mr.mira_leads_summary(None)
    assert result["total"] == 3
    assert result["by_status"] == {"new": 2, "contacted": 1}
    manager = FakeLeadManager.instances[-1]
    assert manager.project_id is not None
    assert manager.calls[0][0] == "list_leads"


@pytest.mark.asyncio
async def test_leads_recent_filters_sorts_and_limits(lead_manager):
    now = datetime.now(UTC)
    FakeLeadManager._class_leads = [
        FakeLead("L-old", created_at=(now - timedelta(hours=72)).isoformat()),
        FakeLead("L-2", created_at=(now - timedelta(hours=2)).isoformat()),
        FakeLead("L-1", created_at=(now - timedelta(hours=1)).isoformat()),
        FakeLead("L-no-date", created_at=None),
    ]
    result = await mr.mira_leads_recent(None, hours=24, limit=1)
    assert result["count"] == 2
    assert len(result["leads"]) == 1  # limit applied
    assert result["leads"][0]["lead_id"] == "L-1"  # newest first
    assert set(result["leads"][0].keys()) == {"lead_id", "status", "source", "created_at"}


@pytest.mark.asyncio
async def test_leads_triage_passes_filters(lead_manager):
    FakeLeadManager._class_leads = [FakeLead("L1", priority="high", assigned_to="rep-1")]
    result = await mr.mira_leads_triage(None, status="new", min_age_hours=4, limit=10)
    assert result["filter"] == {"status": "new", "min_age_hours": 4}
    assert result["count"] == 1
    assert result["leads"][0]["priority"] == "high"
    _, kwargs = FakeLeadManager.instances[-1].calls[0]
    assert kwargs == {"status": "new", "min_age_hours": 4, "limit": 10}


@pytest.mark.asyncio
async def test_update_lead_triage_rejects_invalid_status(lead_manager):
    result = await mr.mira_update_lead_triage(None, "L1", {"status": "bogus"})
    assert result["status"] == "error"
    assert "Invalid status" in result["error"]
    assert FakeLeadManager.instances == []  # manager never constructed


@pytest.mark.asyncio
async def test_update_lead_triage_not_found(lead_manager):
    result = await mr.mira_update_lead_triage(None, "ghost", {"priority": "high"})
    assert result["status"] == "error"
    assert "not found" in result["error"]
    assert FakeLeadManager.instances[-1].calls == [("get_lead", "ghost")]


@pytest.mark.asyncio
async def test_update_lead_triage_dispatches_partner_event(
    lead_manager, monkeypatch, patch_db
):
    old_lead = FakeLead("L1", status="new")
    FakeLeadManager._class_by_id["L1"] = old_lead
    FakeLeadManager._class_triage_result = FakeLead(
        "L1",
        status="qualified",
        priority="high",
        assigned_to="rep-1",
        triage_reason="hot_lead",
        triage_notes="Called twice",
        last_triage_at="2026-10-13T00:00:00+00:00",
    )

    dispatched = []
    monkeypatch.setattr(
        mr, "dispatch_partner_event", lambda event, payload, db=None: dispatched.append((event, payload))
    )

    result = await mr.mira_update_lead_triage(
        None, "L1", {"status": "qualified", "priority": "high"}
    )

    assert result["status"] == "healthy"
    assert result["lead"]["status"] == "qualified"
    assert len(dispatched) == 1
    event, payload = dispatched[0]
    assert event == "lead.triage_updated"
    assert payload["old_status"] == "new"
    assert payload["new_status"] == "qualified"
    assert payload["assigned_to"] == "rep-1"
    assert FakeLeadManager.instances[-1].calls[-1][0] == "triage_lead"


# --------------------------------------------------------------------------- #
# Appointments, inventory, deals, customers summaries
# --------------------------------------------------------------------------- #


class FakeAppointment:
    def __init__(self, status):
        self.status = status


class FakeAppointmentManager:
    _class_appts = []

    def __init__(self, project_id=None):
        self.project_id = project_id
        self._appts = FakeAppointmentManager._class_appts

    async def list_appointments(self, limit=100):
        return list(self._appts)


@pytest.fixture
def appt_manager(monkeypatch):
    monkeypatch.setattr(mr, "AppointmentManager", FakeAppointmentManager)
    return FakeAppointmentManager


@pytest.mark.asyncio
async def test_appointments_summary_counts_by_status(appt_manager):
    FakeAppointmentManager._class_appts = [
        FakeAppointment("scheduled"),
        FakeAppointment("scheduled"),
        FakeAppointment("done"),
    ]
    result = await mr.mira_appointments_summary(None)
    assert result["total"] == 3
    assert result["by_status"] == {"scheduled": 2, "done": 1}


@pytest.mark.asyncio
async def test_inventory_summary_uses_status_counts(monkeypatch):
    docs = [
        FakeDocSnapshot("i1", {"status": "AVAILABLE"}),
        FakeDocSnapshot("i2", {"status": "SOLD"}),
    ]
    monkeypatch.setattr(mr, "get_database", lambda: FakeTHODatabase({"inventory": docs}))
    result = await mr.mira_inventory_summary(None)
    assert result["total"] == 2
    assert result["by_status"] == {"AVAILABLE": 1, "SOLD": 1}


@pytest.mark.asyncio
async def test_deals_summary_uses_status_counts(monkeypatch):
    docs = [FakeDocSnapshot("d1", {"status": "approved"})]
    monkeypatch.setattr(mr, "get_database", lambda: FakeTHODatabase({"deals": docs}))
    result = await mr.mira_deals_summary(None)
    assert result["by_status"] == {"approved": 1}


@pytest.mark.asyncio
async def test_customers_summary_uses_count(monkeypatch):
    class CountingDB:
        db = None

        def count_customers(self):
            return 42

    monkeypatch.setattr(mr, "get_database", lambda: CountingDB())
    result = await mr.mira_customers_summary(None)
    assert result["total"] == 42


# --------------------------------------------------------------------------- #
# Firestore collection listing
# --------------------------------------------------------------------------- #


class FakeCollectionRef:
    def __init__(self, col_id, docs=(), raise_on_count=False):
        self.id = col_id
        self._docs = list(docs)
        self.raise_on_count = raise_on_count

    def limit(self, _n):
        return self

    def stream(self, timeout=None):
        assert timeout is not None
        if self.raise_on_count:
            raise RuntimeError("count failed")
        return iter(self._docs)


class CollectionsDB:
    def __init__(self, refs):
        self.db = self
        self._refs = refs

    def collections(self, timeout=None):
        assert timeout is not None
        return list(self._refs)


@pytest.mark.asyncio
async def test_firestore_collections_lists_sorted_counts(monkeypatch):
    refs = [
        FakeCollectionRef("inventory", [FakeDocSnapshot("a", {}), FakeDocSnapshot("b", {})]),
        FakeCollectionRef("leads", [FakeDocSnapshot("c", {})]),
    ]
    monkeypatch.setattr(mr, "get_database", lambda: CollectionsDB(refs))
    result = await mr.mira_firestore_collections(None)
    assert [c["collection"] for c in result["collections"]] == ["inventory", "leads"]
    assert result["collections"][0]["count"] == 2


@pytest.mark.asyncio
async def test_firestore_collections_count_failure_yields_null(monkeypatch):
    refs = [FakeCollectionRef("broken", raise_on_count=True)]
    monkeypatch.setattr(mr, "get_database", lambda: CollectionsDB(refs))
    result = await mr.mira_firestore_collections(None)
    assert result["collections"] == [{"collection": "broken", "count": None}]


@pytest.mark.asyncio
async def test_count_backed_endpoints_degrade_to_zero_on_internal_error(monkeypatch):
    """inventory/deals summaries count via _count_collection_by_status, which
    already swallows the exception: the endpoint stays healthy and reports the
    UNKNOWN bucket as zero rather than 500ing the partner poll."""
    def boom():
        raise RuntimeError("db credentials secret-stuff")

    monkeypatch.setattr(mr, "get_database", boom)
    for endpoint in (mr.mira_inventory_summary, mr.mira_deals_summary):
        result = await endpoint(None)
        assert result["status"] == "healthy"
        assert result["by_status"] == {"UNKNOWN": 0}
        assert result["total"] == 0
        assert "credentials" not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    [mr.mira_customers_summary, mr.mira_firestore_collections],
)
async def test_firestore_backed_endpoints_fail_closed_on_internal_error(endpoint, monkeypatch):
    """Endpoints without an internal swallow must return the generic
    internal_error code — never the raw exception — when Firestore blows up."""
    def boom():
        raise RuntimeError("db credentials secret-stuff")

    monkeypatch.setattr(mr, "get_database", boom)
    result = await endpoint(None)
    assert result["status"] == "error"
    assert result["error"] == "internal_error"
    assert "credentials" not in str(result)


@pytest.mark.asyncio
async def test_manager_backed_endpoints_fail_closed_on_internal_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("manager init secret-stuff")

    monkeypatch.setattr(mr, "LeadManager", boom)
    monkeypatch.setattr(mr, "AppointmentManager", boom)
    for endpoint in (mr.mira_leads_summary, mr.mira_appointments_summary):
        result = await endpoint(None)
        assert result["status"] == "error"
        assert result["error"] == "internal_error"
        assert "secret" not in str(result)
