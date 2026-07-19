"""Tests for audit_log.py — admin-mutation audit trail.

Covers:
  * log_admin_action writes a Firestore document with the expected schema
  * log_admin_action sanitizes PII keys out of `details`
  * log_admin_action swallows write failures (never raises)
  * /api/admin/audit-log returns entries with optional filtering
  * /api/admin/audit-log requires admin auth
  * /api/admin/audit-log rejects bogus action/target_type values

Run: python -m pytest tests/test_audit_log.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_api_v1 import create_client  # noqa: E402  (test helper)

# ─── Firestore stand-in ────────────────────────────────────────────────────


class FakeAuditCollection:
    """Mimics the subset of firestore.CollectionReference we use."""

    def __init__(self):
        self._docs: list[dict] = []
        self._counter = 0

    # log_admin_action calls .add(entry); query_audit_log uses where + stream
    def add(self, entry: dict, timeout=None):
        self._counter += 1
        doc_id = f"audit-{self._counter}"
        # Snapshot to avoid leaking caller mutations into the store.
        self._docs.append({"_id": doc_id, **dict(entry)})
        return SimpleNamespace(id=doc_id)

    def where(self, field: str, op: str, value):
        return FakeAuditQuery(self._docs).where(field, op, value)

    def limit(self, n: int):
        return FakeAuditQuery(self._docs).limit(n)

    def stream(self, timeout=None):
        for doc in list(self._docs):
            yield _Snap(doc["_id"], {k: v for k, v in doc.items() if k != "_id"})


class FakeAuditQuery:
    def __init__(self, docs, filters=None, limit_value=None):
        self._docs = docs
        self._filters = list(filters or [])
        self._limit = limit_value

    def where(self, field, op, value):
        return FakeAuditQuery(self._docs, self._filters + [(field, op, value)], self._limit)

    def limit(self, n: int):
        return FakeAuditQuery(self._docs, self._filters, n)

    def stream(self, timeout=None):
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
    """Top-level Firestore client stand-in for the audit_log module."""

    def __init__(self):
        self.collections: dict[str, FakeAuditCollection] = {}

    def collection(self, name: str) -> FakeAuditCollection:
        return self.collections.setdefault(name, FakeAuditCollection())


# ─── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def fake_audit_db(monkeypatch):
    """Swap audit_log's Firestore client for an in-memory fake."""
    import audit_log

    fake = FakeFirestore()
    monkeypatch.setattr(audit_log, "_firestore_client", fake, raising=False)

    # The audit_log module's _get_db caches the client; force it to return our
    # fake even on the first call.
    monkeypatch.setattr(audit_log, "_get_db", lambda: fake)
    yield fake


# ─── Unit tests: log_admin_action ──────────────────────────────────────────


def test_log_admin_action_writes_expected_schema(fake_audit_db):
    from audit_log import log_admin_action

    fake_request = SimpleNamespace(
        headers={"x-forwarded-for": "203.0.113.7, 10.0.0.1", "user-agent": "Mozilla/5.0"},
        client=SimpleNamespace(host="10.0.0.1"),
    )

    log_admin_action(
        actor="admin:abc123",
        action="deal.create",
        target_type="deal",
        target_id="deal-42",
        details={"fields": ["status", "model"]},
        request=fake_request,
    )

    docs = fake_audit_db.collections["audit_log"]._docs
    assert len(docs) == 1
    entry = docs[0]
    # Required keys present
    for key in (
        "timestamp",
        "actor",
        "action",
        "target_type",
        "target_id",
        "ip",
        "user_agent",
        "details",
    ):
        assert key in entry, f"missing key {key}"
    assert entry["actor"] == "admin:abc123"
    assert entry["action"] == "deal.create"
    assert entry["target_type"] == "deal"
    assert entry["target_id"] == "deal-42"
    # X-Forwarded-For first hop wins
    assert entry["ip"] == "203.0.113.7"
    assert entry["user_agent"].startswith("Mozilla/5.0")
    assert entry["details"] == {"fields": ["status", "model"]}


def test_log_admin_action_strips_pii_from_details(fake_audit_db):
    """`details` must NEVER carry raw PII even if the call site passes it."""
    from audit_log import log_admin_action

    log_admin_action(
        actor="admin",
        action="customer.update",
        target_type="customer",
        target_id="cust-7",
        details={
            "fields": ["status"],
            # All of these MUST be dropped server-side.
            "ssn": "123-45-6789",
            "ssn_hash": "abcdef",
            "email": "buyer@example.com",
            "phone": "555-867-5309",
            "full_name": "Jane Doe",
            "buyer_first_name": "Jane",
            "buyer_last_name": "Doe",
            "buyer_email": "jane@example.com",
            "address": "123 Main St",
            "password": "hunter2",  # pragma: allowlist secret
            "token": "secret-bearer",  # pragma: allowlist secret
        },
    )

    docs = fake_audit_db.collections["audit_log"]._docs
    assert len(docs) == 1
    persisted = docs[0]["details"]
    # Only the non-PII key survives.
    assert persisted == {"fields": ["status"]}
    # Belt-and-suspenders: ensure no PII string anywhere in the document.
    flat = repr(docs[0])
    for pii_value in (
        "123-45-6789",
        "buyer@example.com",
        "jane@example.com",
        "555-867-5309",
        "Jane Doe",
        "123 Main St",
        "hunter2",
        "secret-bearer",
    ):
        assert pii_value not in flat, f"PII leaked: {pii_value!r}"


def test_log_admin_action_swallows_db_failure(monkeypatch):
    """Audit failures must never raise — admin actions must complete."""
    import audit_log

    class ExplodingCollection:
        def add(self, _entry, timeout=None):
            raise RuntimeError("Firestore is down")

    class ExplodingDB:
        def collection(self, _name):
            return ExplodingCollection()

    monkeypatch.setattr(audit_log, "_get_db", lambda: ExplodingDB())

    # Must not raise.
    audit_log.log_admin_action(
        actor="admin",
        action="deal.update",
        target_type="deal",
        target_id="deal-1",
    )


def test_log_admin_action_handles_no_request(fake_audit_db):
    """Calling without a Request must still write a complete record."""
    from audit_log import log_admin_action

    log_admin_action(
        actor="admin",
        action="admin.logout",
        target_type="session",
        target_id="session-1",
    )

    docs = fake_audit_db.collections["audit_log"]._docs
    assert len(docs) == 1
    assert docs[0]["ip"] == ""
    assert docs[0]["user_agent"] == ""


def test_log_admin_action_warns_on_unknown_action(fake_audit_db, caplog):
    """Drift detector — unknown actions still write but emit a warning."""
    import logging

    from audit_log import log_admin_action

    with caplog.at_level(logging.WARNING, logger="audit_log"):
        log_admin_action(
            actor="admin",
            action="deal.exfiltrate",  # not in ALLOWED_ACTIONS
            target_type="deal",
            target_id="x",
        )
    assert any("unknown action" in rec.message.lower() for rec in caplog.records)


# ─── Unit tests: query_audit_log ───────────────────────────────────────────


def test_query_audit_log_filters_and_orders(fake_audit_db):
    from audit_log import log_admin_action, query_audit_log

    log_admin_action(actor="admin:a", action="deal.create", target_type="deal", target_id="d1")
    log_admin_action(actor="admin:b", action="deal.update", target_type="deal", target_id="d2")
    log_admin_action(actor="admin:a", action="deal.update", target_type="deal", target_id="d1")

    by_actor = query_audit_log(actor="admin:a")
    assert all(e["actor"] == "admin:a" for e in by_actor)
    assert len(by_actor) == 2

    by_action = query_audit_log(action="deal.create")
    assert all(e["action"] == "deal.create" for e in by_action)

    by_target = query_audit_log(target_id="d1")
    assert all(e["target_id"] == "d1" for e in by_target)

    # Reverse-chronological — last write should be first (timestamps strictly
    # increasing in our fixture because each call calls datetime.now()).
    all_entries = query_audit_log()
    timestamps = [e["timestamp"] for e in all_entries]
    assert timestamps == sorted(timestamps, reverse=True)


def test_query_audit_log_clamps_limit(fake_audit_db):
    from audit_log import log_admin_action, query_audit_log

    for i in range(20):
        log_admin_action(
            actor="admin",
            action="deal.update",
            target_type="deal",
            target_id=f"d{i}",
        )

    # limit > 500 is clamped to 500
    rows = query_audit_log(limit=10_000)
    assert len(rows) == 20  # only 20 written
    # limit < 1 is clamped up to 1
    rows = query_audit_log(limit=0)
    assert len(rows) == 1


# ─── Endpoint tests: /api/admin/audit-log ──────────────────────────────────


def _swap_main_audit_db(main):
    """Replace the audit_log module's Firestore client with a fresh fake.

    main.py's `query_audit_log` and `log_admin_action` are imported from the
    module, but they call `_get_db()` lazily, so swapping the module-level
    cache is enough — no need to patch main itself.
    """
    fake = FakeFirestore()
    main.audit_log = main.audit_log if hasattr(main, "audit_log") else None
    import audit_log

    audit_log._get_db = lambda: fake  # type: ignore[attr-defined]
    return fake


def test_audit_log_endpoint_requires_admin(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch)
    _swap_main_audit_db(main)
    response = client.get("/api/admin/audit-log")
    assert response.status_code == 401


def test_audit_log_endpoint_returns_entries(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch)
    fake = _swap_main_audit_db(main)

    # Seed 3 entries via the in-process logger
    main.log_admin_action(
        actor="admin:x",
        action="deal.create",
        target_type="deal",
        target_id="deal-100",
        details={"fields": ["status"]},
    )
    main.log_admin_action(
        actor="admin:y",
        action="deal.update",
        target_type="deal",
        target_id="deal-100",
        details={"fields": ["status"]},
    )
    main.log_admin_action(
        actor="admin:x",
        action="customer.update",
        target_type="customer",
        target_id="cust-5",
        details={"fields": ["phone"]},
    )

    token = main._create_admin_token()
    headers = {"X-Admin-Token": token}

    # No filter → all 3
    r = client.get("/api/admin/audit-log", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["count"] == 3
    assert len(body["entries"]) == 3

    # Filter by actor
    r = client.get("/api/admin/audit-log", headers=headers, params={"actor": "admin:x"})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert all(e["actor"] == "admin:x" for e in body["entries"])

    # Filter by action
    r = client.get("/api/admin/audit-log", headers=headers, params={"action": "deal.create"})
    body = r.json()
    assert body["count"] == 1
    assert body["entries"][0]["action"] == "deal.create"

    # Filter by target_id
    r = client.get("/api/admin/audit-log", headers=headers, params={"target_id": "deal-100"})
    body = r.json()
    assert body["count"] == 2
    assert {e["target_id"] for e in body["entries"]} == {"deal-100"}

    # since (ISO timestamp lower bound) — using a future timestamp filters all out
    r = client.get(
        "/api/admin/audit-log",
        headers=headers,
        params={"since": "2999-01-01T00:00:00+00:00"},
    )
    assert r.json()["count"] == 0

    # Quiet "fake unused" warning — fake is the live store query reads from.
    assert fake is not None


def test_audit_log_endpoint_rejects_invalid_action(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch)
    _swap_main_audit_db(main)
    token = main._create_admin_token()
    r = client.get(
        "/api/admin/audit-log",
        headers={"X-Admin-Token": token},
        params={"action": "deal.exfiltrate"},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["success"] is False
    assert "Invalid action" in body["error"]


def test_audit_log_endpoint_rejects_invalid_target_type(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch)
    _swap_main_audit_db(main)
    token = main._create_admin_token()
    r = client.get(
        "/api/admin/audit-log",
        headers={"X-Admin-Token": token},
        params={"target_type": "wallet"},
    )
    assert r.status_code == 400
    assert "Invalid target_type" in r.json()["error"]
