"""Regression tests for THO WebAuthn passkey routes."""

from __future__ import annotations

import importlib
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from threading import Barrier, Lock
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from google.api_core.exceptions import AlreadyExists

pytest.importorskip("webauthn")

from auth import routes as passkey_routes  # noqa: E402
from auth.session import PASSKEY_COOKIE_NAME, SESSION_COOKIE_NAME, SessionManager  # noqa: E402
from auth.store import (  # noqa: E402
    CredentialAlreadyExists,
    CredentialRecord,
    FirestoreCredentialStore,
    InMemoryCredentialStore,
    MinimumCredentialError,
)

CSRF_COOKIE_NAME = "tho_csrf_token"


class _CredentialSnapshot:
    def __init__(self, record):
        self._record = record
        self.exists = record is not None
        self.id = record.credential_id_b64 if record else "missing"

    def to_dict(self):
        if self._record is None:
            return {}
        return {
            "credential_id": self._record.credential_id_b64,
            "public_key": "cHVibGlj",
            "sign_count": self._record.sign_count,
            "user_id": self._record.user_id,
            "aaguid": self._record.aaguid,
            "created_at": self._record.created_at,
            "last_used_at": self._record.last_used_at,
        }


class _CredentialDocument:
    def __init__(self, store, credential_id_b64):
        self.store = store
        self.credential_id_b64 = credential_id_b64

    def snapshot(self):
        record = next(
            (
                row
                for row in self.store.rows.values()
                if row.credential_id_b64 == self.credential_id_b64
            ),
            None,
        )
        return _CredentialSnapshot(record)

    def get(self, transaction=None, timeout=None):
        if transaction is not None:
            return transaction.get(self, timeout=timeout)
        del timeout
        return self.snapshot()

    def create(self, data, timeout=None):
        del timeout
        if self.snapshot().exists:
            raise AlreadyExists("credential already exists")
        record = FirestoreCredentialStore._to_record(self.credential_id_b64, data)
        self.store.rows[record.credential_id] = record


class _CredentialQuery:
    def __init__(self, store, owner):
        self.store = store
        self.owner = owner


class _CredentialCollection:
    def __init__(self, store):
        self.store = store

    def document(self, credential_id_b64):
        return _CredentialDocument(self.store, credential_id_b64)

    def where(self, *, filter):
        return _CredentialQuery(self.store, filter.value)


class _CredentialTransaction:
    def __init__(self, store):
        self.store = store
        self.start_rows = deepcopy(store.rows)
        self.pending_deletes = []
        self.pending_updates = []

    def get(self, value, timeout=None):
        del timeout
        if isinstance(value, _CredentialDocument):
            record = next(
                (
                    row
                    for row in self.start_rows.values()
                    if row.credential_id_b64 == value.credential_id_b64
                ),
                None,
            )
            return _CredentialSnapshot(record)
        return [
            _CredentialSnapshot(record)
            for record in self.start_rows.values()
            if record.user_id == value.owner
        ]

    def delete(self, document):
        snapshot = self.get(document)
        if snapshot.exists:
            self.pending_deletes.append(snapshot._record.credential_id)

    def update(self, document, data):
        snapshot = self.get(document)
        if snapshot.exists:
            self.pending_updates.append((snapshot._record.credential_id, dict(data)))

    def commit(self):
        for credential_id, data in self.pending_updates:
            record = self.store.rows[credential_id]
            record.sign_count = data["sign_count"]
            record.last_used_at = data["last_used_at"]
        for credential_id in self.pending_deletes:
            self.store.rows.pop(credential_id, None)


class _FakeCredentialFirestore:
    def __init__(self, rows):
        self.rows = dict(rows)

    def collection(self, _name):
        return _CredentialCollection(self)

    def transaction(self, max_attempts):
        assert max_attempts == 5
        return _CredentialTransaction(self)


def _csrf_headers(client, token="passkey-route-csrf"):
    client.cookies.set(CSRF_COOKIE_NAME, token)
    return {"X-CSRF-Token": token}


def _fixture_dependencies(client, routes):
    overrides = client.app.dependency_overrides
    return overrides[routes.get_session_manager](), overrides[routes.get_credential_store]()


def test_memory_store_create_only_rejects_collision_without_mutating_owner_record():
    owner = CredentialRecord(
        credential_id=b"shared-id",
        public_key=b"owner-public-key",
        sign_count=7,
        user_id="owner@example.com",
        aaguid="owner-aaguid",
    )
    store = InMemoryCredentialStore([owner])
    before = asdict(store.get(owner.credential_id))
    attacker = CredentialRecord(
        credential_id=owner.credential_id,
        public_key=b"attacker-public-key",
        sign_count=0,
        user_id="staff@texashomeoutlet.com",
        aaguid="attacker-aaguid",
    )

    with pytest.raises(CredentialAlreadyExists):
        store.add(attacker)

    assert asdict(store.get(owner.credential_id)) == before


def test_memory_store_concurrent_same_id_registration_has_exactly_one_winner():
    store = InMemoryCredentialStore()
    records = [
        CredentialRecord(
            credential_id=b"shared-id",
            public_key=f"public-{index}".encode(),
            sign_count=0,
            user_id=f"user-{index}@example.com",
        )
        for index in range(2)
    ]

    def create(record):
        try:
            store.add(record)
            return "created"
        except CredentialAlreadyExists:
            return "duplicate"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(create, records))

    assert outcomes.count("created") == 1
    assert outcomes.count("duplicate") == 1
    assert store.count() == 1


def test_firestore_store_create_only_maps_collision_without_mutating_owner_record():
    owner = CredentialRecord(
        credential_id=b"shared-id",
        public_key=b"owner-public-key",
        sign_count=7,
        user_id="owner@example.com",
        aaguid="owner-aaguid",
    )
    fake = _FakeCredentialFirestore({owner.credential_id: owner})
    store = FirestoreCredentialStore(client=fake)
    before = asdict(fake.rows[owner.credential_id])

    with pytest.raises(CredentialAlreadyExists):
        store.add(
            CredentialRecord(
                credential_id=owner.credential_id,
                public_key=b"attacker-public-key",
                sign_count=0,
                user_id="staff@texashomeoutlet.com",
                aaguid="attacker-aaguid",
            )
        )

    assert asdict(fake.rows[owner.credential_id]) == before


def test_memory_usage_cas_rejects_delayed_lower_counter_and_defines_counterless_zero():
    used_at = datetime(2026, 8, 13, 4, 30, tzinfo=UTC)
    credential = CredentialRecord(
        credential_id=b"countered",
        public_key=b"public-key",
        sign_count=5,
        user_id="owner@example.com",
    )
    store = InMemoryCredentialStore([credential])
    delayed_login = store.prepare_usage_cas(credential, new_sign_count=6)
    step_up = store.prepare_usage_cas(credential, new_sign_count=7)

    assert store.compare_and_set_usage(step_up, used_at=used_at) is True
    assert store.compare_and_set_usage(delayed_login, used_at=used_at) is False
    assert store.get(credential.credential_id).sign_count == 7

    counterless = CredentialRecord(
        credential_id=b"counterless",
        public_key=b"public-key",
        sign_count=0,
        user_id="owner@example.com",
    )
    store.add(counterless)
    first = store.prepare_usage_cas(counterless, new_sign_count=0)
    assert store.compare_and_set_usage(first, used_at=used_at) is True
    refreshed = store.get(counterless.credential_id)
    second = store.prepare_usage_cas(refreshed, new_sign_count=0)
    assert (
        store.compare_and_set_usage(
            second,
            used_at=used_at + timedelta(seconds=1),
        )
        is True
    )
    assert store.compare_and_set_usage(first, used_at=used_at) is False


def test_firestore_usage_cas_rejects_delayed_lower_counter(monkeypatch):
    credential = CredentialRecord(
        credential_id=b"countered",
        public_key=b"public-key",
        sign_count=5,
        user_id="owner@example.com",
    )
    fake = _FakeCredentialFirestore({credential.credential_id: credential})

    def transactional(operation):
        def execute(transaction):
            result = operation(transaction)
            transaction.commit()
            return result

        return execute

    monkeypatch.setattr("google.cloud.firestore.transactional", transactional)
    store = FirestoreCredentialStore(client=fake)
    snapshot = store.get(credential.credential_id)
    delayed_login = store.prepare_usage_cas(snapshot, new_sign_count=6)
    step_up = store.prepare_usage_cas(snapshot, new_sign_count=7)
    used_at = datetime(2026, 8, 13, 4, 30, tzinfo=UTC)

    assert store.compare_and_set_usage(step_up, used_at=used_at) is True
    assert store.compare_and_set_usage(delayed_login, used_at=used_at) is False
    assert store.get(credential.credential_id).sign_count == 7


@pytest.mark.parametrize("backend", ("memory", "firestore"))
def test_concurrent_usage_writes_are_cas_monotonic(backend, monkeypatch):
    credential = CredentialRecord(
        credential_id=b"countered",
        public_key=b"public-key",
        sign_count=5,
        user_id="owner@example.com",
    )
    if backend == "memory":
        store = InMemoryCredentialStore([credential])
    else:
        fake = _FakeCredentialFirestore({credential.credential_id: credential})
        barrier = Barrier(2)
        commit_lock = Lock()

        def transactional(operation):
            def execute(transaction):
                result = operation(transaction)
                barrier.wait(timeout=5)
                with commit_lock:
                    if transaction.start_rows != fake.rows:
                        retry = _CredentialTransaction(fake)
                        result = operation(retry)
                        retry.commit()
                    else:
                        transaction.commit()
                return result

            return execute

        monkeypatch.setattr("google.cloud.firestore.transactional", transactional)
        store = FirestoreCredentialStore(client=fake)
    snapshot = store.get(credential.credential_id)
    usages = [store.prepare_usage_cas(snapshot, new_sign_count=new_count) for new_count in (6, 7)]

    def update(index):
        return store.compare_and_set_usage(
            usages[index],
            used_at=datetime(2026, 8, 13, 4, 30 + index, tzinfo=UTC),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(update, range(2)))

    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 1
    assert store.get(credential.credential_id).sign_count in {6, 7}


@pytest.fixture
def passkey_client():
    routes = importlib.reload(passkey_routes)
    store = InMemoryCredentialStore(
        [
            CredentialRecord(
                credential_id=b"credential-1",
                public_key=b"public-key",
                sign_count=0,
                user_id="admin",
            ),
            CredentialRecord(
                credential_id=b"credential-2",
                public_key=b"public-key-2",
                sign_count=0,
                user_id="mark@texashomeoutlet.com",
            ),
        ]
    )
    manager = SessionManager(secret_key="test-passkey-secret")
    app = FastAPI()
    app.dependency_overrides[routes.get_credential_store] = lambda: store
    app.dependency_overrides[routes.get_session_manager] = lambda: manager
    app.include_router(routes.router)
    return TestClient(app), routes


def test_passkey_session_cookie_matches_admin_verifier():
    assert SESSION_COOKIE_NAME == PASSKEY_COOKIE_NAME == "tho_passkey_session"


def test_login_begin_returns_browser_json(passkey_client):
    client, _routes = passkey_client

    response = client.post("/api/admin/passkey/login/begin")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rpId"] == "sapphirealpha.xyz"
    assert isinstance(body["challenge"], str)
    assert body.get("allowCredentials", []) == []
    assert "tho_passkey_login=" in response.headers["set-cookie"]


def test_login_begin_can_use_strict_allow_list_when_configured(passkey_client, monkeypatch):
    client, routes = passkey_client
    monkeypatch.setattr(routes, "_discoverable_login_enabled", lambda: False)

    response = client.post("/api/admin/passkey/login/begin")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["allowCredentials"][0]["id"] == "Y3JlZGVudGlhbC0y"
    assert body["allowCredentials"][0]["type"] == "public-key"


def test_nondefault_canonical_owner_can_complete_normal_passkey_login(passkey_client, monkeypatch):
    client, routes = passkey_client
    manager, store = _fixture_dependencies(client, routes)
    owner = "new-owner@example.com"
    credential = CredentialRecord(
        credential_id=b"new-owner-credential",
        public_key=b"new-owner-public-key",
        sign_count=0,
        user_id=owner,
    )
    store.add(credential)
    monkeypatch.setenv("THO_PASSKEY_OWNER_EMAILS", owner)
    monkeypatch.setenv("THO_GOOGLE_ADS_OWNER_EMAILS", owner)
    challenge = manager.new_challenge_bytes()
    client.cookies.set("tho_passkey_login", manager.wrap_challenge(challenge, flow="login"))
    monkeypatch.setattr(routes, "parse_authentication_credential_json", lambda value: value)
    monkeypatch.setattr(
        routes,
        "verify_authentication_response",
        lambda **kwargs: SimpleNamespace(
            credential_id=credential.credential_id,
            new_sign_count=1,
        ),
    )

    response = client.post(
        "/api/admin/passkey/login/complete",
        json={"id": credential.credential_id_b64},
    )

    assert response.status_code == 200, response.text
    assert response.json()["email"] == owner
    assert routes.google_ads_owner_emails() == {owner}


def test_login_begin_uses_sapphire_xyz_cutover_context(passkey_client):
    client, _routes = passkey_client

    response = client.post(
        "/api/admin/passkey/login/begin",
        headers={"Origin": "https://tho.sapphire.xyz"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["rpId"] == "sapphire.xyz"


def test_login_begin_defaults_to_current_production_origin(passkey_client):
    client, routes = passkey_client

    assert routes.THO_ORIGIN == "https://tho.sapphirealpha.xyz"

    response = client.post("/api/admin/passkey/login/begin")

    assert response.status_code == 200, response.text
    assert response.json()["rpId"] == "sapphirealpha.xyz"


def test_login_begin_keeps_texashomeoutlet_cutover_context(passkey_client):
    client, _routes = passkey_client

    response = client.post(
        "/api/admin/passkey/login/begin",
        headers={"Origin": "https://texashomeoutlet.com"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["rpId"] == "texashomeoutlet.com"


def test_register_begin_requires_existing_admin_session(passkey_client):
    client, _routes = passkey_client

    response = client.post("/api/admin/passkey/register/begin")

    assert response.status_code == 401


def test_register_begin_returns_browser_json_after_admin_auth(passkey_client, monkeypatch):
    client, routes = passkey_client
    monkeypatch.setattr(routes, "_request_is_admin", lambda request, manager: True)

    response = client.post(
        "/api/admin/passkey/register/begin",
        json={"email": "Mark@TexasHomeOutlet.com"},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rp"]["id"] == "sapphirealpha.xyz"
    assert body["user"]["name"] == "mark@texashomeoutlet.com"
    assert body["user"]["displayName"] == "mark@texashomeoutlet.com"
    assert len(body["user"]["id"]) > 20
    assert {cred["id"] for cred in body["excludeCredentials"]} == {
        "Y3JlZGVudGlhbC0x",
        "Y3JlZGVudGlhbC0y",
    }
    assert "tho_passkey_register=" in response.headers["set-cookie"]


def test_register_begin_rejects_unapproved_email_after_admin_auth(passkey_client, monkeypatch):
    client, routes = passkey_client
    monkeypatch.setattr(routes, "_request_is_admin", lambda request, manager: True)

    response = client.post(
        "/api/admin/passkey/register/begin",
        json={"email": "vendor@example.com"},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 403
    assert "texashomeoutlet.com" in response.json()["detail"]


def test_register_begin_accepts_owner_email_only_from_same_owner_passkey_session(passkey_client):
    client, routes = passkey_client
    manager, _store = _fixture_dependencies(client, routes)
    client.cookies.set(
        PASSKEY_COOKIE_NAME,
        manager.issue_session(
            "admin",
            email="aribspector@gmail.com",
            auth_method="passkey",
        ),
    )

    response = client.post(
        "/api/admin/passkey/register/begin",
        json={"email": "aribspector@gmail.com"},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 200, response.text
    assert response.json()["user"]["name"] == "aribspector@gmail.com"


def test_register_begin_rejects_owner_email_from_shared_admin_or_staff_session(
    passkey_client, monkeypatch
):
    client, routes = passkey_client
    manager, _store = _fixture_dependencies(client, routes)
    monkeypatch.setattr(routes, "_request_is_admin", lambda request, manager: True)

    shared_admin = client.post(
        "/api/admin/passkey/register/begin",
        json={"email": "aribspector@gmail.com"},
        headers=_csrf_headers(client),
    )
    assert shared_admin.status_code == 403

    client.cookies.set(
        PASSKEY_COOKIE_NAME,
        manager.issue_session(
            "admin",
            email="mark@texashomeoutlet.com",
            auth_method="passkey",
        ),
    )
    staff = client.post(
        "/api/admin/passkey/register/begin",
        json={"email": "aribspector@gmail.com"},
        headers=_csrf_headers(client),
    )
    assert staff.status_code == 403


def test_register_begin_uses_sapphire_xyz_cutover_context(passkey_client, monkeypatch):
    client, routes = passkey_client
    monkeypatch.setattr(routes, "_request_is_admin", lambda request, manager: True)

    response = client.post(
        "/api/admin/passkey/register/begin",
        headers={"Origin": "https://tho.sapphire.xyz", **_csrf_headers(client)},
        json={"email": "mark@texashomeoutlet.com"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["rp"]["id"] == "sapphire.xyz"


def test_register_begin_rejects_authenticated_cookie_without_csrf(passkey_client, monkeypatch):
    client, routes = passkey_client
    monkeypatch.setattr(routes, "_request_is_admin", lambda request, manager: True)

    response = client.post(
        "/api/admin/passkey/register/begin",
        json={"email": "mark@texashomeoutlet.com"},
    )

    assert response.status_code == 403
    assert "CSRF" in response.json()["detail"]


def test_status_reports_store_persistence_contract(passkey_client):
    client, _routes = passkey_client

    response = client.get("/api/admin/passkey/status")

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["registered_keys"] == 1
    assert body["total_registered_keys"] == 2
    assert body["unauthorized_keys"] == 1
    assert body["has_keys"] is True
    assert body["store_backend"] == "memory"
    assert body["persistent"] is False
    assert body["store_ready"] is True
    assert body["discoverable_login"] is True
    assert body["allowed_domains"] == ["texashomeoutlet.com"]
    assert "sapphire.xyz" in body["rp_ids"]
    assert "sapphirealpha.xyz" in body["rp_ids"]


def test_credentials_management_requires_admin(passkey_client):
    client, _routes = passkey_client

    response = client.get("/api/admin/passkey/credentials")

    assert response.status_code == 401


def test_credentials_management_lists_and_deletes_deprecated_key(passkey_client, monkeypatch):
    client, routes = passkey_client
    monkeypatch.setattr(routes, "_request_is_admin", lambda request, manager: True)

    listed = client.get("/api/admin/passkey/credentials")

    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["registered_keys"] == 1
    assert body["credentials"][0]["credential_id"] == "Y3JlZGVudGlhbC0x"
    assert body["credentials"][0]["authorized"] is False
    assert body["credentials"][1]["credential_id"] == "Y3JlZGVudGlhbC0y"
    assert body["credentials"][1]["authorized"] is True

    deleted = client.delete(
        "/api/admin/passkey/credentials/Y3JlZGVudGlhbC0x",  # pragma: allowlist secret
        headers=_csrf_headers(client),
    )

    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["registered_keys"] == 1
    assert deleted.json()["total_registered_keys"] == 1
    assert deleted.json()["unauthorized_keys"] == 0
    remaining = client.get("/api/admin/passkey/credentials").json()["credentials"]
    assert [cred["credential_id"] for cred in remaining] == ["Y3JlZGVudGlhbC0y"]


def test_credentials_delete_rejects_authenticated_cookie_without_csrf(passkey_client, monkeypatch):
    client, routes = passkey_client
    monkeypatch.setattr(routes, "_request_is_admin", lambda request, manager: True)

    response = client.delete(
        "/api/admin/passkey/credentials/Y3JlZGVudGlhbC0x"  # pragma: allowlist secret
    )

    assert response.status_code == 403
    assert "CSRF" in response.json()["detail"]


def test_owner_credential_delete_requires_same_owner_passkey_and_keeps_one_recovery_key(
    passkey_client, monkeypatch
):
    client, routes = passkey_client
    manager, store = _fixture_dependencies(client, routes)
    owner = "owner@example.com"
    monkeypatch.setenv("THO_PASSKEY_OWNER_EMAILS", owner)
    monkeypatch.setenv("THO_GOOGLE_ADS_OWNER_EMAILS", owner)
    first = CredentialRecord(
        credential_id=b"owner-first",
        public_key=b"owner-first-public",
        sign_count=0,
        user_id=owner,
    )
    second = CredentialRecord(
        credential_id=b"owner-second",
        public_key=b"owner-second-public",
        sign_count=0,
        user_id=owner,
    )
    store.add(first)
    monkeypatch.setattr(routes, "_request_is_admin", lambda request, manager: True)

    shared_admin = client.delete(
        f"/api/admin/passkey/credentials/{first.credential_id_b64}",
        headers=_csrf_headers(client),
    )
    assert shared_admin.status_code == 403
    assert store.get(first.credential_id) is not None

    client.cookies.set(
        PASSKEY_COOKIE_NAME,
        manager.issue_session(
            "admin",
            email="mark@texashomeoutlet.com",
            auth_method="passkey",
        ),
    )
    staff = client.delete(
        f"/api/admin/passkey/credentials/{first.credential_id_b64}",
        headers=_csrf_headers(client),
    )
    assert staff.status_code == 403
    assert store.get(first.credential_id) is not None

    client.cookies.set(
        PASSKEY_COOKIE_NAME,
        manager.issue_session("admin", email=owner, auth_method="passkey"),
    )
    client.cookies.delete(CSRF_COOKIE_NAME)
    for bypass_header in (
        {"Authorization": "Bearer junk"},
        {"X-Admin-Token": "junk"},
    ):
        without_csrf = client.delete(
            f"/api/admin/passkey/credentials/{first.credential_id_b64}",
            headers=bypass_header,
        )
        assert without_csrf.status_code == 403
        assert store.get(first.credential_id) is not None

    last_key = client.delete(
        f"/api/admin/passkey/credentials/{first.credential_id_b64}",
        headers=_csrf_headers(client),
    )
    assert last_key.status_code == 409
    assert store.get(first.credential_id) is not None

    store.add(second)
    replaced = client.delete(
        f"/api/admin/passkey/credentials/{first.credential_id_b64}",
        headers=_csrf_headers(client),
    )
    assert replaced.status_code == 200
    assert store.get(first.credential_id) is None
    assert store.get(second.credential_id) is not None


def test_concurrent_owner_deletes_never_remove_the_final_recovery_key():
    owner = "owner@example.com"
    records = [
        CredentialRecord(
            credential_id=f"owner-{index}".encode(),
            public_key=f"public-{index}".encode(),
            sign_count=0,
            user_id=owner,
        )
        for index in range(2)
    ]
    store = InMemoryCredentialStore(records)

    def revoke(record):
        try:
            return store.delete_preserving_user_minimum(
                record.credential_id,
                user_id=owner,
                minimum_remaining=1,
            )
        except Exception as exc:
            return type(exc).__name__

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(revoke, records))

    assert outcomes.count(True) == 1
    assert outcomes.count("MinimumCredentialError") == 1
    assert len(store.list_for_user(owner)) == 1


def test_firestore_owner_delete_uses_transactional_minimum_policy(monkeypatch):
    owner = "owner@example.com"
    rows = {
        b"owner-first": CredentialRecord(
            credential_id=b"owner-first",
            public_key=b"public-first",
            sign_count=0,
            user_id=owner,
        ),
        b"owner-second": CredentialRecord(
            credential_id=b"owner-second",
            public_key=b"public-second",
            sign_count=0,
            user_id=owner,
        ),
    }
    fake = _FakeCredentialFirestore(rows)

    def transactional(operation):
        def execute(transaction):
            result = operation(transaction)
            transaction.commit()
            return result

        return execute

    monkeypatch.setattr("google.cloud.firestore.transactional", transactional)
    store = FirestoreCredentialStore(client=fake)

    assert (
        store.delete_preserving_user_minimum(b"owner-first", user_id=owner, minimum_remaining=1)
        is True
    )
    with pytest.raises(MinimumCredentialError):
        store.delete_preserving_user_minimum(b"owner-second", user_id=owner, minimum_remaining=1)
    assert b"owner-second" in fake.rows


def test_firestore_conflict_retry_never_deletes_both_owner_recovery_keys(monkeypatch):
    owner = "owner@example.com"
    records = [
        CredentialRecord(
            credential_id=f"owner-{index}".encode(),
            public_key=f"public-{index}".encode(),
            sign_count=0,
            user_id=owner,
        )
        for index in range(2)
    ]
    fake = _FakeCredentialFirestore({record.credential_id: record for record in records})
    barrier = Barrier(2)
    commit_lock = Lock()

    def transactional(operation):
        def execute(transaction):
            result = operation(transaction)
            barrier.wait(timeout=5)
            with commit_lock:
                if transaction.start_rows != fake.rows:
                    retry = _CredentialTransaction(fake)
                    result = operation(retry)
                    retry.commit()
                else:
                    transaction.commit()
            return result

        return execute

    monkeypatch.setattr("google.cloud.firestore.transactional", transactional)
    store = FirestoreCredentialStore(client=fake)

    def revoke(record):
        try:
            return store.delete_preserving_user_minimum(
                record.credential_id,
                user_id=owner,
                minimum_remaining=1,
            )
        except MinimumCredentialError:
            return "minimum"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(revoke, records))

    assert outcomes.count(True) == 1
    assert outcomes.count("minimum") == 1
    assert len(fake.rows) == 1


def test_successful_passkey_login_issues_csrf_cookie_and_response_token(
    passkey_client, monkeypatch
):
    client, routes = passkey_client
    manager, _store = _fixture_dependencies(client, routes)
    challenge = manager.new_challenge_bytes()
    client.cookies.set("tho_passkey_login", manager.wrap_challenge(challenge, flow="login"))
    monkeypatch.setattr(routes, "parse_authentication_credential_json", lambda value: value)
    monkeypatch.setattr(
        routes,
        "verify_authentication_response",
        lambda **kwargs: SimpleNamespace(credential_id=b"credential-2", new_sign_count=1),
    )

    response = client.post(
        "/api/admin/passkey/login/complete",
        json={"id": "Y3JlZGVudGlhbC0y"},
    )

    assert response.status_code == 200, response.text
    csrf_token = response.json()["csrf_token"]
    assert csrf_token
    assert response.cookies[CSRF_COOKIE_NAME] == csrf_token
    assert PASSKEY_COOKIE_NAME in response.cookies


def test_delayed_login_counter_cannot_regress_newer_step_up_counter(passkey_client, monkeypatch):
    client, routes = passkey_client
    manager, store = _fixture_dependencies(client, routes)
    credential = store.get(b"credential-2")
    credential.sign_count = 5
    challenge = manager.new_challenge_bytes()
    client.cookies.set("tho_passkey_login", manager.wrap_challenge(challenge, flow="login"))
    monkeypatch.setattr(routes, "parse_authentication_credential_json", lambda value: value)

    def verify(**kwargs):
        step_up = store.prepare_usage_cas(credential, new_sign_count=7)
        assert (
            store.compare_and_set_usage(
                step_up,
                used_at=datetime(2026, 8, 13, 4, 30, tzinfo=UTC),
            )
            is True
        )
        return SimpleNamespace(credential_id=credential.credential_id, new_sign_count=6)

    monkeypatch.setattr(routes, "verify_authentication_response", verify)

    response = client.post(
        "/api/admin/passkey/login/complete",
        json={"id": credential.credential_id_b64},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Passkey counter changed. Refresh and retry."
    assert PASSKEY_COOKIE_NAME not in response.cookies
    assert store.get(credential.credential_id).sign_count == 7


def test_successful_passkey_registration_issues_csrf_cookie_and_response_token(
    passkey_client, monkeypatch
):
    client, routes = passkey_client
    manager, _store = _fixture_dependencies(client, routes)
    challenge = manager.new_challenge_bytes()
    client.cookies.set(
        "tho_passkey_register",
        manager.wrap_challenge(
            challenge,
            flow="register",
            email="mark@texashomeoutlet.com",
        ),
    )
    monkeypatch.setattr(routes, "_request_is_admin", lambda request, manager: True)
    monkeypatch.setattr(routes, "parse_registration_credential_json", lambda value: value)
    monkeypatch.setattr(
        routes,
        "verify_registration_response",
        lambda **kwargs: SimpleNamespace(
            credential_id=b"new-credential",
            credential_public_key=b"new-public-key",
            sign_count=0,
            aaguid=None,
        ),
    )

    response = client.post(
        "/api/admin/passkey/register/complete",
        json={"id": "bmV3LWNyZWRlbnRpYWw"},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 200, response.text
    csrf_token = response.json()["csrf_token"]
    assert csrf_token
    assert response.cookies[CSRF_COOKIE_NAME] == csrf_token
    assert PASSKEY_COOKIE_NAME in response.cookies


def test_staff_registration_collision_cannot_overwrite_listed_owner_credential(
    passkey_client, monkeypatch
):
    client, routes = passkey_client
    manager, store = _fixture_dependencies(client, routes)
    owner = CredentialRecord(
        credential_id=b"listed-owner-id",
        public_key=b"owner-public-key",
        sign_count=9,
        user_id="aribspector@gmail.com",
        aaguid="owner-aaguid",
    )
    store.add(owner)
    before = asdict(store.get(owner.credential_id))
    challenge = manager.new_challenge_bytes()
    client.cookies.set(
        "tho_passkey_register",
        manager.wrap_challenge(
            challenge,
            flow="register",
            email="mark@texashomeoutlet.com",
        ),
    )
    monkeypatch.setattr(routes, "_request_is_admin", lambda request, manager: True)
    monkeypatch.setattr(routes, "parse_registration_credential_json", lambda value: value)
    monkeypatch.setattr(
        routes,
        "verify_registration_response",
        lambda **kwargs: SimpleNamespace(
            credential_id=owner.credential_id,
            credential_public_key=b"attacker-public-key",
            sign_count=0,
            aaguid="attacker-aaguid",
        ),
    )

    response = client.post(
        "/api/admin/passkey/register/complete",
        json={"id": owner.credential_id_b64},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Passkey credential is already registered."
    assert PASSKEY_COOKIE_NAME not in response.cookies
    assert asdict(store.get(owner.credential_id)) == before


def test_register_complete_rejects_owner_challenge_from_shared_admin_before_verification(
    passkey_client, monkeypatch
):
    client, routes = passkey_client
    manager, _store = _fixture_dependencies(client, routes)
    challenge = manager.new_challenge_bytes()
    client.cookies.set(
        "tho_passkey_register",
        manager.wrap_challenge(
            challenge,
            flow="register",
            email="aribspector@gmail.com",
        ),
    )
    monkeypatch.setattr(routes, "_request_is_admin", lambda request, manager: True)
    verifier = SimpleNamespace(called=False)

    def verify(**kwargs):
        verifier.called = True
        raise AssertionError("owner registration must reject shared admin first")

    monkeypatch.setattr(routes, "verify_registration_response", verify)

    response = client.post(
        "/api/admin/passkey/register/complete",
        json={"id": "bmV3LWNyZWRlbnRpYWw"},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 403
    assert verifier.called is False
