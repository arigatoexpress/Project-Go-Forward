"""Tests for the /api/v1/obsidian partner integration surface.

Run: python -m pytest tests/test_obsidian_routes.py -v
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _fake_require_partner_api_key(request: Request):
    """Minimal stand-in for main.require_partner_api_key."""
    auth = request.headers.get("Authorization", "")
    token = ""
    if auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing API key")
    expected = (os.environ.get("THO_API_KEY_OBSIDIAN") or "").strip()
    if not expected or token != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")


def _make_app(monkeypatch, api_key: str = "obsidian-secret"):
    monkeypatch.setenv("THO_API_KEY_OBSIDIAN", api_key)

    from obsidian_routes import public_router, router, set_obsidian_refs

    app = FastAPI()
    set_obsidian_refs(0.0, type("Metrics", (), {"get_metrics": lambda self: {"requests": 7}})())
    app.include_router(public_router)
    app.include_router(router, dependencies=[Depends(_fake_require_partner_api_key)])
    return app


@dataclass
class FakeLead:
    lead_id: str
    user_id: str = "u1"
    session_id: str = "s1"
    status: str = "new"
    source: str = "chat"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class FakeDocSnapshot:
    def __init__(self, doc_id: str, data: dict):
        self.id = doc_id
        self._data = data

    def to_dict(self) -> dict:
        return dict(self._data)


class FakeFirestoreDB:
    def __init__(self, collections: dict[str, dict[str, dict]]):
        self._collections = collections

    def collection(self, name: str):
        return FakeCollection(self._collections.get(name, {}))

    def collections(self, timeout=None):
        return [FakeCollection(v, id=k) for k, v in self._collections.items()]


class FakeCollection:
    def __init__(self, store: dict, id: str | None = None):
        self.id = id
        self._store = store

    def stream(self, timeout=None):
        for doc_id, data in self._store.items():
            yield FakeDocSnapshot(doc_id, data)

    def limit(self, n: int):
        return self

    def order_by(self, *args, **kwargs):
        return self


class FakeTHODatabase:
    def __init__(self):
        self.collections = {
            "inventory": {
                "i1": {"status": "AVAILABLE"},
                "i2": {"status": "SOLD"},
                "i3": {"status": "AVAILABLE"},
            },
            "deals": {
                "d1": {"status": "pending"},
                "d2": {"status": "funded"},
            },
            "feedback": {
                "f1": {"rating": 5, "sentiment": "positive"},
                "f2": {"rating": 4, "sentiment": "positive"},
            },
            "activities": {},
        }
        self.db = FakeFirestoreDB(self.collections)

    def count_customers(self):
        return {"total": 3, "by_status": {"LEAD": 2, "CUSTOMER": 1}}


class FakeLeadManager:
    def __init__(self, project_id: str | None = None):
        self.project_id = project_id

    async def list_leads(self, limit: int = 100):
        now = datetime.now(UTC)
        return [
            FakeLead(lead_id="l1", status="new", created_at=(now - timedelta(hours=1)).isoformat()),
            FakeLead(lead_id="l2", status="contacted", created_at=(now - timedelta(hours=25)).isoformat()),
            FakeLead(lead_id="l3", status="new", created_at=now.isoformat()),
        ]


class FakeAppointmentManager:
    def __init__(self, project_id: str | None = None):
        self.project_id = project_id

    async def list_appointments(self, limit: int = 100):
        return [
            type("A", (), {"appointment_id": "a1", "status": "confirmed"})(),
            type("A", (), {"appointment_id": "a2", "status": "completed"})(),
            type("A", (), {"appointment_id": "a3", "status": "confirmed"})(),
        ]


@pytest.fixture
def client(monkeypatch):
    app = _make_app(monkeypatch)

    import obsidian_routes

    fake_db = FakeTHODatabase()
    monkeypatch.setattr(obsidian_routes, "get_database", lambda: fake_db)
    monkeypatch.setattr(obsidian_routes, "LeadManager", FakeLeadManager)
    monkeypatch.setattr(obsidian_routes, "AppointmentManager", FakeAppointmentManager)

    return TestClient(app), fake_db


def _auth_headers(key: str = "obsidian-secret"):
    return {"Authorization": f"Bearer {key}"}


class TestPublicHealth:
    def test_health_no_auth(self, client):
        tc, _ = client
        resp = tc.get("/api/v1/obsidian/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data
        assert "version" in data


class TestProtectedRoutes:
    def test_system_requires_auth(self, client):
        tc, _ = client
        resp = tc.get("/api/v1/obsidian/system")
        assert resp.status_code == 401

    def test_system_with_auth(self, client):
        tc, _ = client
        resp = tc.get("/api/v1/obsidian/system", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert "env_status" in data
        assert data["project_id"]

    def test_metrics_with_auth(self, client):
        tc, _ = client
        resp = tc.get("/api/v1/obsidian/metrics", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["metrics"]["requests"] == 7

    def test_leads_summary(self, client):
        tc, _ = client
        resp = tc.get("/api/v1/obsidian/leads/summary", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert data["by_status"]["new"] == 2
        assert data["by_status"]["contacted"] == 1

    def test_leads_recent_default_window(self, client):
        tc, _ = client
        resp = tc.get("/api/v1/obsidian/leads/recent", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["hours"] == 24
        assert data["count"] == 2
        assert {lead["lead_id"] for lead in data["leads"]} == {"l1", "l3"}

    def test_appointments_summary(self, client):
        tc, _ = client
        resp = tc.get("/api/v1/obsidian/appointments/summary", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert data["by_status"]["confirmed"] == 2
        assert data["by_status"]["completed"] == 1

    def test_inventory_summary(self, client):
        tc, _ = client
        resp = tc.get("/api/v1/obsidian/inventory/summary", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert data["by_status"]["AVAILABLE"] == 2
        assert data["by_status"]["SOLD"] == 1

    def test_deals_summary(self, client):
        tc, _ = client
        resp = tc.get("/api/v1/obsidian/deals/summary", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["by_status"]["pending"] == 1
        assert data["by_status"]["funded"] == 1

    def test_customers_summary(self, client):
        tc, _ = client
        resp = tc.get("/api/v1/obsidian/customers/summary", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == {"total": 3, "by_status": {"LEAD": 2, "CUSTOMER": 1}}

    def test_feedback_summary(self, client):
        tc, _ = client
        resp = tc.get("/api/v1/obsidian/feedback/summary", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 4
        assert data["rating_counts"]["5"] == 1
        assert data["sentiment_counts"]["positive"] == 2

    def test_firestore_collections(self, client):
        tc, _ = client
        resp = tc.get("/api/v1/obsidian/firestore/collections", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        names = {c["collection"] for c in data["collections"]}
        assert names >= {"inventory", "deals", "feedback", "activities"}


class TestNotifyWebhook:
    def test_notify_no_obsidian_url_returns_ok_false(self, client, monkeypatch):
        tc, _ = client
        for name in list(os.environ):
            if name.startswith("PARTNER_WEBHOOK_URL_"):
                monkeypatch.delenv(name, raising=False)
        resp = tc.post(
            "/api/v1/obsidian/notify",
            headers=_auth_headers(),
            json={"message": "hello vault", "level": "info", "source": "test"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["delivered_to"] == []

    def test_notify_delivers_to_obsidian_only(self, client, monkeypatch):
        tc, _ = client
        monkeypatch.setenv("PARTNER_WEBHOOK_URL_OBSIDIAN", "https://obsidian.example.com/hook")
        monkeypatch.setenv("PARTNER_WEBHOOK_URL_ETAI", "https://etai.example.com/hook")
        monkeypatch.setenv("PARTNER_WEBHOOK_SIGNING_KEY", "signing-secret")

        calls = []

        def _fake_post(url, data=None, headers=None, timeout=None):
            calls.append({"url": url, "body": data, "headers": dict(headers or {}), "timeout": timeout})

            class Resp:
                status_code = 200
                text = ""

            return Resp()

        import tools.partner_webhooks as pw

        monkeypatch.setattr(pw.requests, "post", _fake_post)

        resp = tc.post(
            "/api/v1/obsidian/notify",
            headers=_auth_headers(),
            json={"message": "deal funded", "level": "warn", "source": "tho", "action_url": "https://tho.example.com/d1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["delivered_to"] == ["obsidian"]

        assert len(calls) == 1
        assert calls[0]["url"] == "https://obsidian.example.com/hook"
        assert calls[0]["headers"]["X-THO-Partner"] == "obsidian"
        assert calls[0]["headers"]["X-THO-Event"] == "obsidian.notify"
        assert "X-THO-Signature" in calls[0]["headers"]

    def test_notify_invalid_level_rejected(self, client):
        tc, _ = client
        resp = tc.post(
            "/api/v1/obsidian/notify",
            headers=_auth_headers(),
            json={"message": "x", "level": "unknown"},
        )
        assert resp.status_code == 422
