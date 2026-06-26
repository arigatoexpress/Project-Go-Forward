"""Tests for user_activity_log.py — user-interaction trail.

Covers:
  * log_user_action writes a Firestore document with the expected schema
  * log_user_action sanitizes PII keys out of `details`
  * log_user_action swallows write failures (never raises)
  * query_user_activity filters and orders correctly
  * /api/admin/user-activity returns entries with optional filtering
  * /api/admin/user-activity requires admin auth
  * /api/admin/user-activity rejects bogus action values

Run: python -m pytest tests/test_user_activity_log.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_api_v1 import create_client  # noqa: E402

# ─── Firestore stand-in ───────────────────────────────────────────────────


class FakeCollection:
    def __init__(self):
        self._docs: list[dict] = []
        self._counter = 0

    def add(self, entry: dict):
        self._counter += 1
        doc_id = f"ua-{self._counter}"
        self._docs.append({"_id": doc_id, **dict(entry)})
        return SimpleNamespace(id=doc_id)

    def where(self, field: str, op: str, value):
        return FakeQuery(self._docs).where(field, op, value)

    def limit(self, n: int):
        return FakeQuery(self._docs).limit(n)

    def stream(self):
        for doc in list(self._docs):
            yield _Snap(doc["_id"], {k: v for k, v in doc.items() if k != "_id"})


class FakeQuery:
    def __init__(self, docs, filters=None, limit_value=None):
        self._docs = docs
        self._filters = list(filters or [])
        self._limit = limit_value

    def where(self, field, op, value):
        return FakeQuery(self._docs, self._filters + [(field, op, value)], self._limit)

    def limit(self, n: int):
        return FakeQuery(self._docs, self._filters, n)

    def stream(self):
        emitted = 0
        for doc in self._docs:
            ok = True
            for field, op, value in self._filters:
                actual = doc.get(field)
                if op == "==" and actual != value:
                    ok = False
                    break
                if op == ">=" and not (actual is not None and actual >= value):
                    ok = False
                    break
            if not ok:
                continue
            yield _Snap(doc["_id"], {k: v for k, v in doc.items() if k != "_id"})
            emitted += 1
            if self._limit is not None and emitted >= self._limit:
                return


class _Snap:
    def __init__(self, doc_id: str, data: dict):
        self.id = doc_id
        self._data = data

    def to_dict(self):
        return dict(self._data)


class FakeFirestore:
    def __init__(self):
        self.collections: dict[str, FakeCollection] = {}

    def collection(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection())


# ─── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def fake_db(monkeypatch):
    """Swap user_activity_log's Firestore client for an in-memory fake."""
    import tools.user_activity_log as ua

    fake = FakeFirestore()
    monkeypatch.setattr(ua, "_firestore_client", fake, raising=False)
    monkeypatch.setattr(ua, "_get_db", lambda: fake)
    yield fake


# ─── Unit tests: log_user_action ──────────────────────────────────────────


def test_log_user_action_writes_expected_schema(fake_db):
    from tools.user_activity_log import log_user_action

    fake_request = SimpleNamespace(
        headers={"x-forwarded-for": "203.0.113.7, 10.0.0.1", "user-agent": "Mozilla/5.0"},
        client=SimpleNamespace(host="10.0.0.1"),
    )

    log_user_action(
        action="contact.submit",
        session_id="sess-42",
        details={"has_email": True},
        request=fake_request,
    )

    docs = fake_db.collections["user_activity_log"]._docs
    assert len(docs) == 1
    entry = docs[0]
    for key in ("timestamp", "action", "session_id", "ip", "user_agent", "details"):
        assert key in entry, f"missing key {key}"
    assert entry["action"] == "contact.submit"
    assert entry["session_id"] == "sess-42"
    assert entry["ip"] == "203.0.113.7"
    assert entry["user_agent"].startswith("Mozilla/5.0")
    assert entry["details"] == {"has_email": True}


def test_log_user_action_strips_pii_from_details(fake_db):
    from tools.user_activity_log import log_user_action

    log_user_action(
        action="contact.submit",
        session_id="sess-1",
        details={
            "has_email": True,
            "email": "user@example.com",
            "phone": "555-123-4567",
            "full_name": "Jane Doe",
            "message_text": "Hello there",
        },
    )

    docs = fake_db.collections["user_activity_log"]._docs
    assert len(docs) == 1
    persisted = docs[0]["details"]
    assert persisted == {"has_email": True}
    flat = repr(docs[0])
    for pii in ("user@example.com", "555-123-4567", "Jane Doe", "Hello there"):
        assert pii not in flat, f"PII leaked: {pii!r}"


def test_log_user_action_swallows_db_failure(monkeypatch):
    import tools.user_activity_log as ua

    class ExplodingCollection:
        def add(self, _entry):
            raise RuntimeError("Firestore is down")

    class ExplodingDB:
        def collection(self, _name):
            return ExplodingCollection()

    monkeypatch.setattr(ua, "_get_db", lambda: ExplodingDB())

    ua.log_user_action(
        action="chat.message",
        session_id="sess-1",
    )


def test_log_user_action_handles_no_request(fake_db):
    from tools.user_activity_log import log_user_action

    log_user_action(
        action="feedback.submit",
        session_id="sess-1",
    )

    docs = fake_db.collections["user_activity_log"]._docs
    assert len(docs) == 1
    assert docs[0]["ip"] == ""
    assert docs[0]["user_agent"] == ""


def test_log_user_action_warns_on_unknown_action(fake_db, caplog):
    import logging

    from tools.user_activity_log import log_user_action

    with caplog.at_level(logging.WARNING, logger="user_activity_log"):
        log_user_action(
            action="user.exfiltrate",
            session_id="sess-1",
        )
    assert any("unknown action" in rec.message.lower() for rec in caplog.records)


# ─── Unit tests: query_user_activity ──────────────────────────────────────


def test_query_user_activity_filters_and_orders(fake_db):
    from tools.user_activity_log import log_user_action, query_user_activity

    log_user_action(action="contact.submit", session_id="sess-a")
    log_user_action(action="appointment.book", session_id="sess-b")
    log_user_action(action="contact.submit", session_id="sess-a")

    by_action = query_user_activity(action="contact.submit")
    assert all(e["action"] == "contact.submit" for e in by_action)
    assert len(by_action) == 2

    by_session = query_user_activity(session_id="sess-a")
    assert all(e["session_id"] == "sess-a" for e in by_session)
    assert len(by_session) == 2

    all_entries = query_user_activity()
    timestamps = [e["timestamp"] for e in all_entries]
    assert timestamps == sorted(timestamps, reverse=True)


def test_query_user_activity_clamps_limit(fake_db):
    from tools.user_activity_log import log_user_action, query_user_activity

    for i in range(20):
        log_user_action(action="contact.submit", session_id=f"sess-{i}")

    rows = query_user_activity(limit=10_000)
    assert len(rows) == 20
    rows = query_user_activity(limit=0)
    assert len(rows) == 1


# ─── Endpoint tests: /api/admin/user-activity ─────────────────────────────


def _swap_main_db(main):
    """Replace the user_activity_log module's Firestore client with a fresh fake."""
    fake = FakeFirestore()
    import tools.user_activity_log as ua

    ua._get_db = lambda: fake  # type: ignore[attr-defined]
    return fake


def test_user_activity_endpoint_requires_admin(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch)
    _swap_main_db(main)
    response = client.get("/api/admin/user-activity")
    assert response.status_code == 401


def test_user_activity_endpoint_returns_entries(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch)
    fake = _swap_main_db(main)

    from tools.user_activity_log import log_user_action

    log_user_action(
        action="contact.submit",
        session_id="sess-100",
        details={"has_email": True},
    )
    log_user_action(
        action="appointment.book",
        session_id="sess-200",
        details={"date": "2026-07-01"},
    )
    log_user_action(
        action="feedback.submit",
        session_id="sess-300",
        details={"page": "/contact"},
    )

    token = main._create_admin_token()
    headers = {"X-Admin-Token": token}

    r = client.get("/api/admin/user-activity", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["count"] == 3
    assert len(body["entries"]) == 3

    # Filter by action
    r = client.get(
        "/api/admin/user-activity",
        headers=headers,
        params={"action": "contact.submit"},
    )
    body = r.json()
    assert body["count"] == 1
    assert body["entries"][0]["action"] == "contact.submit"

    # Filter by session_id
    r = client.get(
        "/api/admin/user-activity",
        headers=headers,
        params={"session_id": "sess-200"},
    )
    body = r.json()
    assert body["count"] == 1
    assert body["entries"][0]["session_id"] == "sess-200"

    # since filter — future timestamp filters all out
    r = client.get(
        "/api/admin/user-activity",
        headers=headers,
        params={"since": "2999-01-01T00:00:00+00:00"},
    )
    assert r.json()["count"] == 0

    assert fake is not None


def test_user_activity_endpoint_rejects_invalid_action(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch)
    _swap_main_db(main)
    token = main._create_admin_token()
    r = client.get(
        "/api/admin/user-activity",
        headers={"X-Admin-Token": token},
        params={"action": "user.exfiltrate"},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["success"] is False
    assert "Invalid action" in body["error"]


# ─── End-to-end: log_user_action via real endpoint ────────────────────────


def test_contact_form_logs_user_activity(monkeypatch):
    """Submitting the contact form should create a user_activity_log entry."""
    client, main, _db, _logger = create_client(monkeypatch)
    fake = _swap_main_db(main)

    client.post(
        "/api/contact",
        json={
            "name": "Alice Smith",
            "phone": "555-123-4567",
            "email": "alice@example.com",
            "message": "Interested in a home",
        },
    )
    # The contact form may return 200 or 422 depending on lead validation.
    # We only care that the log was written.
    docs = fake.collections["user_activity_log"]._docs
    assert len(docs) >= 1
    assert any(d["action"] == "contact.submit" for d in docs)
    contact_entry = [d for d in docs if d["action"] == "contact.submit"][0]
    assert contact_entry["details"]["has_email"] is True
    assert contact_entry["details"]["has_message"] is True
    # No PII in the persisted details
    flat = repr(contact_entry)
    assert "Alice Smith" not in flat
    assert "alice@example.com" not in flat
    assert "555-123-4567" not in flat
