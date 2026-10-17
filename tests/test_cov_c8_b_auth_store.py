"""Targeted coverage for auth/store.py gaps (swarm agent C8-B).

Self-contained fakes: the Firestore emulator is unavailable, so
``google.cloud.firestore`` / ``google.cloud.firestore_v1.base_query`` are
replaced with in-memory fakes via ``sys.modules`` (mirroring the precedent in
tests/test_passkey_routes.py). Each test builds fresh store instances so the
threading.Lock state never leaks between tests.
"""

from __future__ import annotations

import sys
import types
from copy import deepcopy
from datetime import UTC, datetime

import pytest
from google.api_core.exceptions import AlreadyExists

from auth import store as auth_store
from auth.store import (
    CredentialAlreadyExists,
    CredentialRecord,
    CredentialStoreUnavailable,
    CredentialUsageCAS,
    FirestoreCredentialStore,
    InMemoryCredentialStore,
    MinimumCredentialError,
    _b64url_decode,
    _b64url_encode,
    _memory_fallback_allowed,
    default_store,
)

USED_AT = datetime(2026, 8, 13, 4, 30, tzinfo=UTC)


def _record(credential_id=b"cred-1", user_id="owner@example.com", sign_count=0):
    return CredentialRecord(
        credential_id=credential_id,
        public_key=b"public-key",
        sign_count=sign_count,
        user_id=user_id,
        aaguid="aaguid",
    )


# ---------------------------------------------------------------------------
# In-memory Firestore stand-ins (documents keyed by urlsafe-b64 credential id)
# ---------------------------------------------------------------------------


class _Snap:
    def __init__(self, record):
        self._record = record
        self.exists = record is not None
        self.id = record.credential_id_b64 if record else "missing"

    def to_dict(self):
        if self._record is None:
            return {}
        r = self._record
        return {
            "credential_id": r.credential_id_b64,
            "public_key": _b64url_encode(r.public_key),
            "sign_count": r.sign_count,
            "user_id": r.user_id,
            "aaguid": r.aaguid,
            "created_at": r.created_at,
            "last_used_at": r.last_used_at,
        }


class _DocRef:
    def __init__(self, client, doc_id):
        self.client = client
        self.id = doc_id

    def _record(self):
        return self.client.rows.get(_b64url_decode(self.id))

    def get(self, transaction=None, timeout=None):
        del timeout
        if transaction is not None:
            return transaction.get(self)
        return _Snap(self._record())

    def create(self, data, timeout=None):
        del timeout
        if self._record() is not None:
            raise AlreadyExists("credential already exists")
        record = FirestoreCredentialStore._to_record(self.id, data)
        self.client.rows[record.credential_id] = record

    def delete(self, timeout=None):
        del timeout
        self.client.rows.pop(_b64url_decode(self.id), None)


class _Query:
    def __init__(self, client, user_id):
        self.client = client
        self.user_id = user_id

    def stream(self, timeout=None):
        del timeout
        return [_Snap(r) for r in self.client.rows.values() if r.user_id == self.user_id]


class _Collection:
    def __init__(self, client):
        self.client = client

    def document(self, doc_id):
        return _DocRef(self.client, doc_id)

    def where(self, *args, **kwargs):
        if "filter" in kwargs:
            value = kwargs["filter"].value
        else:
            value = args[2]  # where("user_id", "==", user_id)
        return _Query(self.client, value)

    def stream(self, timeout=None):
        del timeout
        return [_Snap(r) for r in self.client.rows.values()]


class _Transaction:
    def __init__(self, client):
        self.client = client
        self.start_rows = deepcopy(client.rows)
        self.pending_updates = []
        self.pending_deletes = []

    def get(self, value, timeout=None):
        del timeout
        if isinstance(value, _DocRef):
            return _Snap(self.start_rows.get(_b64url_decode(value.id)))
        return [
            _Snap(record) for record in self.start_rows.values() if record.user_id == value.user_id
        ]

    def update(self, doc_ref, data):
        self.pending_updates.append((_b64url_decode(doc_ref.id), dict(data)))

    def delete(self, doc_ref):
        self.pending_deletes.append(_b64url_decode(doc_ref.id))

    def commit(self):
        for credential_id, data in self.pending_updates:
            record = self.client.rows[credential_id]
            record.sign_count = data["sign_count"]
            record.last_used_at = data["last_used_at"]
        for credential_id in self.pending_deletes:
            self.client.rows.pop(credential_id, None)


class _FakeClient:
    """Minimal stand-in for google.cloud.firestore.Client."""

    def __init__(self, rows=None):
        self.rows = dict(rows or {})

    def collection(self, name):
        assert name == "tho_admin_credentials"
        return _Collection(self)

    def transaction(self, max_attempts):
        assert max_attempts == 5
        return _Transaction(self)


def _install_firestore_module(monkeypatch):
    """Patch sys.modules + the google.cloud attribute with a fake module."""

    module = types.ModuleType("google.cloud.firestore")

    def transactional(operation):
        def execute(transaction):
            result = operation(transaction)
            transaction.commit()
            return result

        return execute

    module.transactional = transactional
    monkeypatch.setitem(sys.modules, "google.cloud.firestore", module)
    monkeypatch.setattr("google.cloud.firestore", module, raising=False)
    return module


def _install_field_filter_module(monkeypatch):
    base_query = types.ModuleType("google.cloud.firestore_v1.base_query")

    class FieldFilter:
        def __init__(self, field_path, op_string, value):
            self.field_path = field_path
            self.op_string = op_string
            self.value = value

    base_query.FieldFilter = FieldFilter
    monkeypatch.setitem(sys.modules, "google.cloud.firestore_v1.base_query", base_query)


# ---------------------------------------------------------------------------
# CredentialUsageCAS.__post_init__ (L67-77)
# ---------------------------------------------------------------------------


def _valid_usage(**overrides):
    credential_id = overrides.pop("credential_id", b"cred-1")
    base = dict(
        credential_id=credential_id,
        credential_id_b64=_b64url_encode(credential_id),
        expected_user_id="owner@example.com",
        expected_sign_count=1,
        expected_last_used_at=None,
        new_sign_count=2,
    )
    base.update(overrides)
    return CredentialUsageCAS(**base)


def test_cas_post_init_raises_on_credential_identity_mismatch():
    with pytest.raises(ValueError, match="credential identity mismatch"):
        _valid_usage(credential_id_b64="not-the-encoded-id")
    with pytest.raises(ValueError, match="credential identity mismatch"):
        _valid_usage(credential_id=b"")


def test_cas_post_init_raises_on_unnormalized_owner_and_stale_counter():
    with pytest.raises(ValueError, match="credential owner must be normalized"):
        _valid_usage(expected_user_id="Owner@Example.com")
    with pytest.raises(ValueError, match="credential sign count must advance"):
        _valid_usage(expected_sign_count=-1, new_sign_count=0)
    with pytest.raises(ValueError, match="credential sign count must advance"):
        _valid_usage(expected_sign_count=5, new_sign_count=4)
    with pytest.raises(ValueError, match="credential sign count must advance"):
        _valid_usage(expected_sign_count=5, new_sign_count=5)


def test_cas_post_init_accepts_counterless_repeated_zero():
    usage = _valid_usage(expected_sign_count=0, new_sign_count=0)
    assert usage.new_sign_count == 0


# ---------------------------------------------------------------------------
# InMemoryCredentialStore locked ops + delete_preserving_user_minimum
# ---------------------------------------------------------------------------


def test_memory_store_locked_list_get_count_delete_and_usage_helpers():
    owner = _record()
    other = _record(credential_id=b"cred-2", user_id="staff@example.com")
    store = InMemoryCredentialStore([owner, other])

    assert store.count() == 2
    assert {r.credential_id for r in store.list_all()} == {b"cred-1", b"cred-2"}
    assert [r.credential_id for r in store.list_for_user("owner@example.com")] == [b"cred-1"]
    assert store.get(b"missing") is None
    assert store.get(b"cred-1") is owner

    usage = store.prepare_usage_cas(owner, new_sign_count=1)
    assert store.usage_update(usage, used_at=USED_AT) == {
        "sign_count": 1,
        "last_used_at": USED_AT,
    }
    with pytest.raises(TypeError, match="no Firestore reference"):
        store.usage_reference(usage)
    assert store.usage_matches_snapshot(usage, object()) is False

    assert store.delete(b"missing") is False
    assert store.delete(b"cred-2") is True
    assert store.count() == 1


def test_memory_store_add_raises_on_duplicate_id():
    owner = _record()
    store = InMemoryCredentialStore([owner])
    with pytest.raises(CredentialAlreadyExists, match="credential_id_already_exists"):
        store.add(_record())
    assert store.count() == 1


def test_memory_delete_preserving_user_minimum_all_branches():
    owner_first = _record()
    owner_second = _record(credential_id=b"cred-2")
    other_user = _record(credential_id=b"cred-3", user_id="staff@example.com")
    store = InMemoryCredentialStore([owner_first, owner_second, other_user])

    # Unknown credential id -> False.
    assert store.delete_preserving_user_minimum(b"missing", user_id="owner@example.com") is False
    # Credential owned by a different user -> False.
    assert store.delete_preserving_user_minimum(b"cred-3", user_id="owner@example.com") is False
    # Deleting the owner's final credential -> MinimumCredentialError.
    store.delete_preserving_user_minimum(b"cred-2", user_id="OWNER@example.com")
    with pytest.raises(MinimumCredentialError, match="owner_recovery_credential_required"):
        store.delete_preserving_user_minimum(b"cred-1", user_id="owner@example.com")
    # minimum_remaining=0 permits deleting the final credential.
    assert (
        store.delete_preserving_user_minimum(b"cred-1", user_id="owner@example.com", minimum_remaining=0)
        is True
    )
    assert store.count() == 1


# ---------------------------------------------------------------------------
# FirestoreCredentialStore construction + record mapping
# ---------------------------------------------------------------------------


def test_firestore_lazy_init_imports_client_from_google_cloud(monkeypatch):
    module = _install_firestore_module(monkeypatch)
    created = []

    class _ClientShim(_FakeClient):
        def __init__(self, project=None):
            created.append(project)
            super().__init__()

    module.Client = _ClientShim

    FirestoreCredentialStore(project="tho-test")
    assert created == ["tho-test"]
    without_project = FirestoreCredentialStore()
    assert created == ["tho-test", None]
    assert isinstance(without_project.client, _ClientShim)
    assert without_project.client is without_project.client  # property is stable


def test_firestore_explicit_client_skips_lazy_import():
    client = _FakeClient()
    store = FirestoreCredentialStore(client=client)
    assert store.client is client


def test_firestore_to_record_document_id_and_bytes_fallbacks():
    doc_id = _b64url_encode(b"from-doc-id")
    # credential_id absent -> decoded from the document id.
    record = FirestoreCredentialStore._to_record(
        doc_id,
        {
            "public_key": b"raw-bytes-key",
            "sign_count": "3",
        },
    )
    assert record.credential_id == b"from-doc-id"
    assert record.public_key == b"raw-bytes-key"
    assert record.sign_count == 3
    assert record.user_id == "admin"  # default when absent
    assert record.aaguid == ""
    assert record.created_at is not None  # default when absent/falsy
    # credential_id supplied as raw bytes.
    record = FirestoreCredentialStore._to_record(
        "unused",
        {
            "credential_id": b"raw-cid",
            "public_key": _b64url_encode(b"encoded-key"),
            "user_id": "owner@example.com",
            "created_at": USED_AT,
            "last_used_at": USED_AT,
        },
    )
    assert record.credential_id == b"raw-cid"
    assert record.public_key == b"encoded-key"
    assert record.last_used_at == USED_AT


def test_firestore_add_success_and_conflict(monkeypatch):
    _install_firestore_module(monkeypatch)
    store = FirestoreCredentialStore(client=_FakeClient())
    record = _record()
    store.add(record)
    assert store.count() == 1
    with pytest.raises(CredentialAlreadyExists, match="credential_id_already_exists"):
        store.add(_record())
    assert store.count() == 1


# ---------------------------------------------------------------------------
# FirestoreCredentialStore reads
# ---------------------------------------------------------------------------


def test_firestore_get_missing_document_returns_none():
    store = FirestoreCredentialStore(client=_FakeClient())
    assert store.get(b"missing") is None


def test_firestore_list_all_and_count_round_trip():
    rows = [_record(), _record(credential_id=b"cred-2", user_id="staff@example.com")]
    store = FirestoreCredentialStore(client=_FakeClient({r.credential_id: r for r in rows}))
    assert store.count() == 2
    listed = store.list_all()
    assert {r.credential_id for r in listed} == {b"cred-1", b"cred-2"}
    assert all(isinstance(r, CredentialRecord) for r in listed)


def test_firestore_list_for_user_filters_with_where_query():
    rows = [_record(), _record(credential_id=b"cred-2", user_id="staff@example.com")]
    store = FirestoreCredentialStore(client=_FakeClient({r.credential_id: r for r in rows}))
    matches = store.list_for_user("owner@example.com")
    assert [r.credential_id for r in matches] == [b"cred-1"]


# ---------------------------------------------------------------------------
# FirestoreCredentialStore CAS usage update
# ---------------------------------------------------------------------------


def test_firestore_compare_and_set_usage_happy_path_and_stale_rejection(monkeypatch):
    _install_firestore_module(monkeypatch)
    credential = _record(sign_count=5)
    store = FirestoreCredentialStore(client=_FakeClient({credential.credential_id: credential}))
    snapshot = store.get(credential.credential_id)
    step_up = store.prepare_usage_cas(snapshot, new_sign_count=7)
    delayed = store.prepare_usage_cas(snapshot, new_sign_count=6)

    assert store.compare_and_set_usage(step_up, used_at=USED_AT) is True
    assert store.get(credential.credential_id).sign_count == 7
    assert store.compare_and_set_usage(delayed, used_at=USED_AT) is False
    assert store.get(credential.credential_id).sign_count == 7


def test_firestore_usage_matches_snapshot_branches():
    store = FirestoreCredentialStore(client=_FakeClient())
    credential = _record(sign_count=5)
    usage = InMemoryCredentialStore.prepare_usage_cas(credential, new_sign_count=6)

    # Snapshot without ``exists`` -> False.
    assert store.usage_matches_snapshot(usage, object()) is False

    # Snapshot whose payload cannot map to a record -> False.
    class _BrokenSnap:
        exists = True
        id = "broken"

        def to_dict(self):
            raise ValueError("corrupt")

    assert store.usage_matches_snapshot(usage, _BrokenSnap()) is False

    # Matching and mismatched snapshots.
    assert store.usage_matches_snapshot(usage, _Snap(credential)) is True
    stale = _record(sign_count=4)
    assert store.usage_matches_snapshot(usage, _Snap(stale)) is False


# ---------------------------------------------------------------------------
# FirestoreCredentialStore deletes
# ---------------------------------------------------------------------------


def test_firestore_delete_missing_and_existing():
    rows = {b"cred-1": _record()}
    store = FirestoreCredentialStore(client=_FakeClient(rows))
    assert store.delete(b"missing") is False
    assert store.delete(b"cred-1") is True
    assert store.count() == 0
    assert store.delete(b"cred-1") is False


def test_firestore_delete_preserving_user_minimum_all_branches(monkeypatch):
    _install_firestore_module(monkeypatch)
    _install_field_filter_module(monkeypatch)
    owner_first = _record()
    owner_second = _record(credential_id=b"cred-2")
    other_user = _record(credential_id=b"cred-3", user_id="staff@example.com")
    store = FirestoreCredentialStore(
        client=_FakeClient(
            {
                owner_first.credential_id: owner_first,
                owner_second.credential_id: owner_second,
                other_user.credential_id: other_user,
            }
        )
    )

    # Unknown document -> False inside the transaction.
    assert store.delete_preserving_user_minimum(b"missing", user_id="owner@example.com") is False
    # Document owned by a different user -> False.
    assert (
        store.delete_preserving_user_minimum(other_user.credential_id, user_id="owner@example.com")
        is False
    )
    # Removing the owner's last credential -> MinimumCredentialError.
    store.delete_preserving_user_minimum(owner_second.credential_id, user_id="OWNER@example.com")
    with pytest.raises(MinimumCredentialError, match="owner_recovery_credential_required"):
        store.delete_preserving_user_minimum(
            owner_first.credential_id, user_id="owner@example.com"
        )
    assert store.count() == 2  # nothing was deleted by the failed transaction
    # minimum_remaining=0 permits deleting the final credential.
    assert (
        store.delete_preserving_user_minimum(
            owner_first.credential_id, user_id="owner@example.com", minimum_remaining=0
        )
        is True
    )
    assert store.count() == 1


# ---------------------------------------------------------------------------
# _memory_fallback_allowed + default_store env-driven selection
# ---------------------------------------------------------------------------


@pytest.fixture
def clear_default_store_cache():
    default_store.cache_clear()
    yield
    default_store.cache_clear()


def test_memory_fallback_allowed_env_and_k_service(monkeypatch):
    monkeypatch.delenv("THO_PASSKEY_ALLOW_MEMORY_STORE", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    assert _memory_fallback_allowed() is True  # no K_SERVICE -> local dev allowed

    monkeypatch.setenv("K_SERVICE", "tho-api")
    assert _memory_fallback_allowed() is False  # Cloud Run never falls back

    for truthy in ("1", "true", "YES", "on"):
        monkeypatch.setenv("THO_PASSKEY_ALLOW_MEMORY_STORE", truthy)
        assert _memory_fallback_allowed() is True

    # Non-truthy values are not an explicit enable; without K_SERVICE the
    # local-dev default still applies.
    monkeypatch.setenv("THO_PASSKEY_ALLOW_MEMORY_STORE", "0")
    monkeypatch.delenv("K_SERVICE", raising=False)
    assert _memory_fallback_allowed() is True


def test_default_store_memory_backend_when_fallback_allowed(monkeypatch, clear_default_store_cache):
    monkeypatch.setenv("THO_PASSKEY_STORE", "memory")
    monkeypatch.delenv("K_SERVICE", raising=False)
    store = default_store()
    assert isinstance(store, InMemoryCredentialStore)
    assert store.backend_name == "memory"


def test_default_store_memory_backend_disabled_on_cloud_run(
    monkeypatch, clear_default_store_cache
):
    monkeypatch.setenv("THO_PASSKEY_STORE", "memory")
    monkeypatch.setenv("K_SERVICE", "tho-api")
    with pytest.raises(CredentialStoreUnavailable, match="memory passkey store is disabled"):
        default_store()


class _StubFirestoreStore:
    """Records construction and optionally raises, mimicking a failed connect."""

    def __init__(self, project=None):
        self.project = project


def test_default_store_firestore_backend_passes_project(
    monkeypatch, clear_default_store_cache
):
    monkeypatch.setenv("THO_PASSKEY_STORE", "firestore")
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setattr(auth_store, "FirestoreCredentialStore", _StubFirestoreStore)

    assert default_store(project="explicit-project").project == "explicit-project"
    default_store.cache_clear()
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "env-project")
    assert default_store().project == "env-project"


@pytest.mark.parametrize("backend", ("firestore", "bogus-value-treats-as-firestore"))
def test_default_store_invalid_or_unset_backend_falls_back_to_memory_when_allowed(
    monkeypatch, clear_default_store_cache, backend
):
    monkeypatch.setenv("THO_PASSKEY_STORE", backend)
    monkeypatch.delenv("K_SERVICE", raising=False)

    def _boom(project=None):
        raise OSError("emulator unreachable")

    monkeypatch.setattr(auth_store, "FirestoreCredentialStore", _boom)
    store = default_store()
    assert isinstance(store, InMemoryCredentialStore)


def test_default_store_unavailable_raises_when_fallback_disallowed(
    monkeypatch, clear_default_store_cache
):
    monkeypatch.delenv("THO_PASSKEY_STORE", raising=False)
    monkeypatch.setenv("K_SERVICE", "tho-api")

    def _boom(project=None):
        raise OSError("emulator unreachable")

    monkeypatch.setattr(auth_store, "FirestoreCredentialStore", _boom)
    with pytest.raises(CredentialStoreUnavailable, match="persistent passkey credential store"):
        default_store()
