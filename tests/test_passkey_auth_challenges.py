"""Single-use WebAuthn authentication challenges."""

from __future__ import annotations

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


class _Snapshot:
    def __init__(self, data):
        self.exists = data is not None
        self._data = data

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
        return _Snapshot(self._rows.get(self.doc_id))


class _Collection:
    def __init__(self, rows):
        self._rows = rows

    def document(self, doc_id):
        return _Reference(self._rows, doc_id)


class _Client:
    def __init__(self):
        self.rows: dict[str, dict] = {}

    def collection(self, _name):
        return _Collection(self.rows)

    def transaction(self, max_attempts):
        assert max_attempts == 5
        return _Transaction(self.rows)


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
