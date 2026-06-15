"""Tests for the Mira partner-monitoring bridge endpoints.

Focus: notion-bridge workstream (Delivery Tracker & CS surveys).
"""
from __future__ import annotations

import sys
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

    def stream(self):
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

    def stream(self):
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
        FakeDocSnapshot("sr-old", {"status": "open", "issue_type": "electrical", "created_at": old.isoformat()}),
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
        FakeDocSnapshot("fb-old", {"rating": 2, "sentiment": "negative", "source": "email", "created_at": old.isoformat()}),
    ]
    result = await mr.mira_feedback_recent(None, hours=24, limit=10)
    assert result["status"] == "healthy"
    assert result["count"] == 1
    item = result["items"][0]
    assert item["id"] == "fb-new"
    assert item["deal_id"] == "deal-789"
    assert "comment" not in item
