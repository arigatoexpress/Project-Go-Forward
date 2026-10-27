"""Single-use WebAuthn authentication challenges."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from auth.authentication_challenges import (
    FirestoreAuthenticationChallengeLedger,
    InMemoryAuthenticationChallengeLedger,
    authentication_challenge_id,
)
from auth.session import DEFAULT_CHALLENGE_TTL, SessionManager
from database.models import AdminPasskeyAuthChallenge
from database.rpc_timeout import FIRESTORE_RPC_TIMEOUT

_CHALLENGE_ID = "ab" * 32


def test_default_login_challenge_ttl_stays_five_minutes():
    assert DEFAULT_CHALLENGE_TTL == 300


def test_challenge_document_rejects_a_raw_or_short_id():
    with pytest.raises(ValidationError):
        AdminPasskeyAuthChallenge(challenge_id="not-a-hash", expires_at=10.0)


def test_challenge_document_round_trips_without_the_raw_challenge():
    document = AdminPasskeyAuthChallenge(challenge_id=_CHALLENGE_ID, expires_at=1_700_000_000.0)
    stored = document.model_dump()

    assert stored == {"challenge_id": _CHALLENGE_ID, "expires_at": 1_700_000_000.0}
    assert "challenge" not in stored
    assert AdminPasskeyAuthChallenge.model_validate(stored) == document


def test_memory_ledger_accepts_a_challenge_once_then_rejects_the_replay():
    ledger = InMemoryAuthenticationChallengeLedger()
    now = 1_000.0
    ledger.issue(_CHALLENGE_ID, expires_at=now + 300)

    assert ledger.consume(_CHALLENGE_ID, now=now + 1) is True
    assert ledger.consume(_CHALLENGE_ID, now=now + 1) is False


def test_memory_ledger_deletes_an_expired_challenge_so_it_cannot_be_revived():
    ledger = InMemoryAuthenticationChallengeLedger()
    ledger.issue(_CHALLENGE_ID, expires_at=100.0)

    assert ledger.consume(_CHALLENGE_ID, now=100.1) is False
    assert ledger.consume(_CHALLENGE_ID, now=50.0) is False
    assert _CHALLENGE_ID not in ledger._rows


def test_memory_consume_has_one_winner_under_contention():
    ledger = InMemoryAuthenticationChallengeLedger()
    ledger.issue(_CHALLENGE_ID, expires_at=time.time() + 60)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda _n: ledger.consume(_CHALLENGE_ID, now=time.time()), range(8))
        )

    assert results.count(True) == 1
    assert results.count(False) == 7


def test_session_consume_rejects_a_replayed_login_challenge():
    ledger = InMemoryAuthenticationChallengeLedger()
    manager = SessionManager(secret_key="challenge-secret", challenge_ledger=ledger)
    challenge = manager.new_challenge_bytes()
    handle = manager.wrap_challenge(challenge, flow="login")

    assert manager.consume_authentication_challenge(handle, flow="login") == challenge
    assert manager.consume_authentication_challenge(handle, flow="login") is None
    assert authentication_challenge_id(challenge) not in ledger._rows


def test_session_consume_invalidates_an_expired_cookie_and_blocks_revival():
    ledger = InMemoryAuthenticationChallengeLedger()
    manager = SessionManager(secret_key="challenge-secret", challenge_ledger=ledger)
    challenge = manager.new_challenge_bytes()
    handle = manager.wrap_challenge(challenge, flow="login")
    manager.challenge_ttl = -1

    assert manager.consume_authentication_challenge(handle, flow="login") is None
    manager.challenge_ttl = 300
    assert manager.consume_authentication_challenge(handle, flow="login") is None


def _filter_field(field) -> str:
    return field if isinstance(field, str) else str(field)


class _Snapshot:
    def __init__(self, data, doc_id=None):
        self.exists = data is not None
        self._data = data
        self.id = doc_id

    def to_dict(self):
        return dict(self._data) if self._data is not None else {}


class _Transaction:
    def __init__(self, rows):
        self._rows = rows
        self._deletes: list[str] = []

    def delete(self, ref):
        self._deletes.append(ref.doc_id)

    def commit(self):
        for doc_id in self._deletes:
            self._rows.pop(doc_id, None)


class _Reference:
    def __init__(self, rows, doc_id):
        self._rows = rows
        self.doc_id = doc_id

    def set(self, data, timeout=None):
        self._rows[self.doc_id] = dict(data)

    def get(self, transaction=None, timeout=None):
        return _Snapshot(self._rows.get(self.doc_id), self.doc_id)


class _Query:
    def __init__(self, client, filters, limit, order):
        self._client = client
        self._filters = filters
        self._limit = limit
        self._order = order

    def where(self, field=None, op=None, value=None, *, filter=None):
        return _Query(
            self._client,
            self._filters + [_filter_parts(field, op, value, filter)],
            self._limit,
            self._order,
        )

    def order_by(self, field, direction="ASCENDING"):
        return _Query(self._client, self._filters, self._limit, (_filter_field(field), direction))

    def limit(self, count):
        return _Query(self._client, self._filters, count, self._order)

    def stream(self, timeout=None):
        self._client.query_timeouts.append(timeout)
        self._client.query_limits.append(self._limit)
        self._client.query_filters.append(list(self._filters))
        matched = [
            (doc_id, data)
            for doc_id, data in self._client.rows.items()
            if _row_matches(data, self._filters)
        ]
        if self._order:
            field, direction = self._order
            reverse = str(direction).upper().endswith("DESCENDING")
            matched.sort(key=lambda item: item[1].get(field, 0), reverse=reverse)
        if self._limit is not None:
            matched = matched[: self._limit]
        for doc_id, data in matched:
            yield _Snapshot(data, doc_id)


def _filter_parts(field, op, value, filter_obj):
    if filter_obj is not None:
        field = filter_obj.field_path
        op = filter_obj.op_string
        value = filter_obj.value
    return _filter_field(field), op, value


def _row_matches(data, filters) -> bool:
    for field, op, value in filters:
        actual = data.get(field)
        if isinstance(actual, bool) or not isinstance(actual, int | float):
            return False
        if op == "<":
            if not actual < value:
                return False
            continue
        raise AssertionError(op)
    return True


class _Batch:
    def __init__(self, client):
        self._client = client
        self._deletes: list[str] = []

    def delete(self, ref):
        self._deletes.append(ref.doc_id)

    def commit(self, timeout=None):
        self._client.commit_timeouts.append(timeout)
        self._client.deleted_ids.extend(self._deletes)
        for doc_id in self._deletes:
            self._client.rows.pop(doc_id, None)


class _Collection:
    def __init__(self, client):
        self._client = client

    def document(self, doc_id):
        return _Reference(self._client.rows, doc_id)

    def where(self, field=None, op=None, value=None, *, filter=None):
        if self._client.fail_expired_cleanup:
            raise RuntimeError("cleanup query failed")
        return _Query(
            self._client,
            [_filter_parts(field, op, value, filter)],
            None,
            None,
        )


class _Client:
    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.query_timeouts: list = []
        self.query_limits: list = []
        self.query_filters: list = []
        self.commit_timeouts: list = []
        self.deleted_ids: list[str] = []
        self.fail_expired_cleanup = False

    def collection(self, _name):
        return _Collection(self)

    def transaction(self, max_attempts):
        assert max_attempts == 5
        return _Transaction(self.rows)

    def batch(self):
        return _Batch(self)


def test_firestore_consume_rejects_replay_and_expired_records(monkeypatch):
    pytest.importorskip("google.cloud.firestore")

    def transactional(operation):
        def execute(transaction):
            result = operation(transaction)
            transaction.commit()
            return result

        return execute

    monkeypatch.setattr("google.cloud.firestore.transactional", transactional)
    client = _Client()
    ledger = FirestoreAuthenticationChallengeLedger(client=client)
    ledger.issue(_CHALLENGE_ID, expires_at=1_000.0)

    assert ledger.consume(_CHALLENGE_ID, now=500.0) is True
    assert ledger.consume(_CHALLENGE_ID, now=500.0) is False
    assert client.rows == {}

    expired_id = "cd" * 32
    ledger.issue(expired_id, expires_at=100.0)
    assert ledger.consume(expired_id, now=101.0) is False
    assert expired_id not in client.rows
    assert ledger.consume(expired_id, now=50.0) is False


_NOW = 1_700_000_000.0


def _doc_id(n: int) -> str:
    return f"{n:064x}"


def _row(doc_id: str, expires_at) -> dict:
    return {"challenge_id": doc_id, "expires_at": expires_at}


def test_memory_issue_drops_expired_abandoned_challenges_and_keeps_live_ones(monkeypatch):
    ledger = InMemoryAuthenticationChallengeLedger()
    monkeypatch.setattr("auth.authentication_challenges.time.time", lambda: _NOW)
    expired_id = _doc_id(1)
    boundary_id = _doc_id(2)
    live_id = _doc_id(3)
    ledger._rows[expired_id] = _NOW - 1
    ledger._rows[boundary_id] = _NOW
    ledger._rows[live_id] = _NOW + 10

    fresh_id = _doc_id(4)
    ledger.issue(fresh_id, expires_at=_NOW + 300)

    assert expired_id not in ledger._rows
    assert ledger._rows[boundary_id] == _NOW
    assert ledger._rows[live_id] == _NOW + 10
    assert ledger._rows[fresh_id] == _NOW + 300


def test_firestore_issue_removes_an_expired_abandoned_challenge_and_keeps_a_live_one(
    monkeypatch,
):
    pytest.importorskip("google.cloud.firestore_v1")
    monkeypatch.setattr("auth.authentication_challenges.time.time", lambda: _NOW)
    client = _Client()
    expired_id = _doc_id(1)
    boundary_id = _doc_id(2)
    live_id = _doc_id(3)
    untyped_id = _doc_id(4)
    client.rows[live_id] = _row(live_id, _NOW + 10)
    client.rows[expired_id] = _row(expired_id, _NOW - 1)
    client.rows[boundary_id] = _row(boundary_id, _NOW)
    client.rows[untyped_id] = _row(untyped_id, "still-here")

    ledger = FirestoreAuthenticationChallengeLedger(client=client)
    fresh_id = _doc_id(5)
    ledger.issue(fresh_id, expires_at=_NOW + 300)

    assert expired_id not in client.rows
    assert client.deleted_ids == [expired_id]
    assert client.rows[boundary_id]["expires_at"] == _NOW
    assert client.rows[live_id]["expires_at"] == _NOW + 10
    assert client.rows[untyped_id]["expires_at"] == "still-here"
    assert client.rows[fresh_id] == _row(fresh_id, _NOW + 300)
    assert client.query_limits == [FirestoreAuthenticationChallengeLedger.EXPIRED_PURGE_LIMIT]
    assert client.query_filters == [[("expires_at", "<", _NOW)]]
    assert client.query_timeouts == [FIRESTORE_RPC_TIMEOUT]
    assert client.commit_timeouts == [FIRESTORE_RPC_TIMEOUT]


def test_firestore_expired_cleanup_stops_at_the_batch_limit(monkeypatch):
    pytest.importorskip("google.cloud.firestore_v1")
    monkeypatch.setattr("auth.authentication_challenges.time.time", lambda: _NOW)
    client = _Client()
    limit = FirestoreAuthenticationChallengeLedger.EXPIRED_PURGE_LIMIT
    live_id = _doc_id(1)
    client.rows[live_id] = _row(live_id, _NOW + 50)
    expired_ids = []
    for offset in range(limit + 2):
        doc_id = _doc_id(100 + offset)
        expired_ids.append(doc_id)
        # Smaller expires_at is older. Ascending order deletes those first.
        client.rows[doc_id] = _row(doc_id, _NOW - (limit + 2 - offset))

    ledger = FirestoreAuthenticationChallengeLedger(client=client)
    fresh_id = _doc_id(9)
    ledger.issue(fresh_id, expires_at=_NOW + 300)

    assert client.deleted_ids == expired_ids[:limit]
    assert len(client.deleted_ids) == limit
    assert client.query_limits == [limit]
    for doc_id in expired_ids[limit:]:
        assert doc_id in client.rows
    assert live_id in client.rows
    assert fresh_id in client.rows
    assert client.rows[live_id]["expires_at"] == _NOW + 50


def test_firestore_issue_still_records_a_challenge_when_expired_cleanup_fails(monkeypatch, caplog):
    pytest.importorskip("google.cloud.firestore_v1")
    monkeypatch.setattr("auth.authentication_challenges.time.time", lambda: _NOW)
    client = _Client()
    client.fail_expired_cleanup = True
    expired_id = _doc_id(1)
    client.rows[expired_id] = _row(expired_id, _NOW - 30)

    ledger = FirestoreAuthenticationChallengeLedger(client=client)
    fresh_id = _doc_id(2)
    with caplog.at_level(logging.WARNING, logger="auth.authentication_challenges"):
        ledger.issue(fresh_id, expires_at=_NOW + 300)

    assert expired_id in client.rows
    assert client.deleted_ids == []
    assert client.rows[fresh_id] == _row(fresh_id, _NOW + 300)
    assert "expired authentication challenge cleanup skipped" in caplog.text
    assert "cleanup query failed" in caplog.text
