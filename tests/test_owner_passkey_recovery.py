"""Owner passkey recovery: email or backup PIN can enroll a new key.

Historical owner credentials stay untouched. Recovery closes only after a
credential registered with an rp_id for an owner email completes a passkey login.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import struct
import time
from base64 import urlsafe_b64encode
from dataclasses import asdict
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytest.importorskip("webauthn")

from auth import routes as passkey_routes  # noqa: E402
from auth.session import PASSKEY_COOKIE_NAME, SessionManager  # noqa: E402
from auth.store import CredentialRecord, InMemoryCredentialStore  # noqa: E402

CSRF_COOKIE_NAME = "tho_csrf_token"
OWNER = "aribspector@gmail.com"
STAFF = "mark@texashomeoutlet.com"
HISTORICAL_PUBLIC_KEY = b"pubkey-marker-do-not-log"
HISTORICAL_IDS = (b"hist-owner-key-aaa", b"hist-owner-key-bbb", b"hist-owner-key-ccc")


def _csrf_headers(client, token="recovery-csrf"):
    client.cookies.set(CSRF_COOKIE_NAME, token)
    return {"X-CSRF-Token": token}


def _email_admin_token(email, *, secret, pin_hash, expires_in=3600):
    key = hmac.new(
        secret.encode(),
        f"tho-pin-session-v2:{pin_hash}".encode(),
        hashlib.sha256,
    ).digest()
    email_bytes = email.encode("utf-8")
    payload = (
        bytes([2])
        + struct.pack(">Q", int(time.time()) + expires_in)
        + struct.pack(">H", len(email_bytes))
        + email_bytes
    )
    sig = hmac.new(key, payload, hashlib.sha256).digest()[:16]
    return urlsafe_b64encode(payload + sig).decode().rstrip("=")


def _pin_admin_token(*, secret, pin_hash, expires_in=3600):
    key = hmac.new(
        secret.encode(),
        f"tho-pin-session-v2:{pin_hash}".encode(),
        hashlib.sha256,
    ).digest()
    payload = struct.pack(">Q", int(time.time()) + expires_in)
    sig = hmac.new(key, payload, hashlib.sha256).digest()[:16]
    return urlsafe_b64encode(payload + sig).decode().rstrip("=")


def _staff_env(monkeypatch):
    secret = "email-session-secret"  # pragma: allowlist secret
    pin_hash = "email-pin-hash"  # pragma: allowlist secret
    monkeypatch.setenv("ADMIN_SESSION_SECRET", secret)
    monkeypatch.setenv("ADMIN_PIN_HASH", pin_hash)
    return secret, pin_hash


def _historical_owner_keys():
    used = datetime(2024, 5, 1, tzinfo=UTC)
    return [
        CredentialRecord(
            credential_id=credential_id,
            public_key=HISTORICAL_PUBLIC_KEY,
            sign_count=3,
            user_id="admin",
            aaguid="50726f74-6f6e-5061-7373-50726f746f6e",
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            last_used_at=used,
            label="Proton Pass",
        )
        for credential_id in HISTORICAL_IDS
    ]


@pytest.fixture
def recovery_client(monkeypatch):
    passkey_routes.reset_owner_passkey_recovery_limits()
    store = InMemoryCredentialStore(_historical_owner_keys())
    store.add(
        CredentialRecord(
            credential_id=b"staff-key",
            public_key=b"staff-public",
            sign_count=1,
            user_id=STAFF,
        )
    )
    manager = SessionManager(secret_key="test-passkey-secret")
    app = FastAPI()
    app.dependency_overrides[passkey_routes.get_credential_store] = lambda: store
    app.dependency_overrides[passkey_routes.get_session_manager] = lambda: manager
    app.include_router(passkey_routes.router)
    client = TestClient(app)
    return client, store, manager


def _snapshot(store):
    return {
        record.credential_id: asdict(store.get(record.credential_id)) for record in store.list_all()
    }


def _sign_email(client, monkeypatch, email):
    secret, pin_hash = _staff_env(monkeypatch)
    client.cookies.set(
        "tho_admin_token", _email_admin_token(email, secret=secret, pin_hash=pin_hash)
    )


def _sign_pin(client, monkeypatch):
    secret, pin_hash = _staff_env(monkeypatch)
    client.cookies.set("tho_admin_token", _pin_admin_token(secret=secret, pin_hash=pin_hash))


def _staff_audit_lines(text):
    lines = []
    for line in text.splitlines():
        if '"service":"staff-admin"' not in line:
            continue
        lines.append(json.loads(line))
    return lines


def test_email_owner_can_start_registration_without_touching_historical_keys(
    recovery_client, monkeypatch, capsys
):
    client, store, _manager = recovery_client
    before = _snapshot(store)
    _sign_email(client, monkeypatch, OWNER)

    response = client.post(
        "/api/admin/passkey/register/begin",
        json={
            "email": OWNER,
            "code": "zz-code-marker-918273",
            "pin": "zz-pin-marker-9f3a",
            "public_key": "zz-pubkey-marker",
            "credential_id": "zz-cred-id-marker",
        },
        headers=_csrf_headers(client),
    )

    assert response.status_code == 200, response.text
    assert response.json()["user"]["name"] == OWNER
    assert _snapshot(store) == before
    captured = capsys.readouterr().out
    assert "zz-code-marker-918273" not in captured
    assert "zz-pin-marker-9f3a" not in captured
    assert "zz-pubkey-marker" not in captured
    assert "zz-cred-id-marker" not in captured
    assert HISTORICAL_PUBLIC_KEY.decode() not in captured
    for record in store.list_all():
        assert record.credential_id_b64 not in captured
    events = _staff_audit_lines(captured)
    assert any(
        row["event"] == "passkey.register.begin" and row["enrollment"] == "recovery"
        for row in events
    )
    assert all(row["lane"] == "pgf" and row["service"] == "staff-admin" for row in events)


def test_pin_owner_can_start_registration_and_whoami_offers_recovery(recovery_client, monkeypatch):
    client, store, _manager = recovery_client
    before = _snapshot(store)
    _sign_pin(client, monkeypatch)

    whoami = client.get("/api/admin/passkey/whoami")
    assert whoami.status_code == 200, whoami.text
    profile = whoami.json()
    assert profile["owner"] is False
    assert profile["auth_method"] == "pin"
    assert profile["owner_passkey_recovery"] is True
    assert OWNER in profile["owner_emails"]

    response = client.post(
        "/api/admin/passkey/register/begin",
        json={"email": OWNER},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 200, response.text
    assert _snapshot(store) == before


def test_unauthenticated_and_staff_cannot_register_an_owner_key(recovery_client, monkeypatch):
    client, store, manager = recovery_client
    before = _snapshot(store)

    anonymous = client.post(
        "/api/admin/passkey/register/begin",
        json={"email": OWNER},
        headers=_csrf_headers(client),
    )
    assert anonymous.status_code == 401
    assert _snapshot(store) == before

    _sign_email(client, monkeypatch, STAFF)
    staff = client.post(
        "/api/admin/passkey/register/begin",
        json={"email": OWNER},
        headers=_csrf_headers(client),
    )
    assert staff.status_code == 403
    assert _snapshot(store) == before

    client.cookies.set(
        PASSKEY_COOKIE_NAME,
        manager.issue_session("admin", email=STAFF, auth_method="passkey"),
    )
    staff_passkey = client.post(
        "/api/admin/passkey/register/begin",
        json={"email": OWNER},
        headers=_csrf_headers(client),
    )
    assert staff_passkey.status_code == 403
    assert _snapshot(store) == before


def test_owner_recovery_still_requires_cookie_csrf(recovery_client, monkeypatch):
    client, store, _manager = recovery_client
    before = _snapshot(store)
    _sign_email(client, monkeypatch, OWNER)

    missing = client.post("/api/admin/passkey/register/begin", json={"email": OWNER})
    bearer = client.post(
        "/api/admin/passkey/register/begin",
        json={"email": OWNER},
        headers={"Authorization": "Bearer not-a-passkey"},
    )

    assert missing.status_code == 403
    assert "CSRF" in missing.json()["detail"]
    assert bearer.status_code == 403
    assert "CSRF" in bearer.json()["detail"]
    assert _snapshot(store) == before


def test_email_recovery_can_complete_without_deleting_historical_keys(recovery_client, monkeypatch):
    client, store, manager = recovery_client
    before = _snapshot(store)
    _sign_email(client, monkeypatch, OWNER)

    def refuse_delete(*_args, **_kwargs):
        raise AssertionError("recovery must not delete credentials")

    monkeypatch.setattr(store, "delete", refuse_delete)
    monkeypatch.setattr(store, "delete_preserving_user_minimum", refuse_delete)
    challenge = manager.new_challenge_bytes()
    client.cookies.set(
        "tho_passkey_register",
        manager.wrap_challenge(challenge, flow="register", email=OWNER),
    )
    monkeypatch.setattr(passkey_routes, "parse_registration_credential_json", lambda value: value)
    monkeypatch.setattr(
        passkey_routes,
        "verify_registration_response",
        lambda **kwargs: SimpleNamespace(
            credential_id=b"new-owner-key",
            credential_public_key=b"new-owner-public",
            sign_count=0,
            aaguid=None,
        ),
    )

    response = client.post(
        "/api/admin/passkey/register/complete",
        json={"id": "bmV3LW93bmVyLWtleQ", "public_key": "zz-pubkey-marker"},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 200, response.text
    created = store.get(b"new-owner-key")
    assert created is not None
    assert created.user_id == OWNER
    assert created.rp_id == "texashomeoutlet.com"
    assert created.last_used_at is None
    for credential_id, row in before.items():
        assert asdict(store.get(credential_id)) == row


def test_recovery_closes_after_the_new_owner_key_completes_login(recovery_client, monkeypatch):
    client, store, manager = recovery_client
    owner_key = CredentialRecord(
        credential_id=b"current-owner-key",
        public_key=b"current-owner-public",
        sign_count=0,
        user_id=OWNER,
        rp_id="texashomeoutlet.com",
    )
    store.add(owner_key)
    historical = _snapshot(store)
    historical.pop(owner_key.credential_id)
    _sign_email(client, monkeypatch, OWNER)
    open_registration = client.post(
        "/api/admin/passkey/register/begin",
        json={"email": OWNER},
        headers=_csrf_headers(client),
    )
    assert open_registration.status_code == 200, open_registration.text

    challenge = manager.new_challenge_bytes()
    client.cookies.set("tho_passkey_login", manager.wrap_challenge(challenge, flow="login"))
    monkeypatch.setattr(passkey_routes, "parse_authentication_credential_json", lambda value: value)
    monkeypatch.setattr(
        passkey_routes,
        "verify_authentication_response",
        lambda **kwargs: SimpleNamespace(
            credential_id=owner_key.credential_id,
            new_sign_count=1,
        ),
    )
    signed_in = client.post(
        "/api/admin/passkey/login/complete",
        json={"id": owner_key.credential_id_b64, "public_key": "zz-pubkey-marker"},
    )
    assert signed_in.status_code == 200, signed_in.text
    used = store.get(owner_key.credential_id)
    assert used is not None
    assert used.last_used_at is not None
    assert used.rp_id == "texashomeoutlet.com"
    assert used.public_key == b"current-owner-public"
    for credential_id, row in historical.items():
        assert asdict(store.get(credential_id)) == row

    client.cookies.clear()
    _sign_email(client, monkeypatch, OWNER)
    closed = client.post(
        "/api/admin/passkey/register/begin",
        json={"email": OWNER},
        headers=_csrf_headers(client),
    )
    assert closed.status_code == 403
    assert closed.json()["detail"] == (
        "Owner passkeys can only be added from the same owner's passkey session."
    )

    client.cookies.set(
        PASSKEY_COOKIE_NAME,
        manager.issue_session("admin", email=OWNER, auth_method="passkey"),
    )
    again = client.post(
        "/api/admin/passkey/register/begin",
        json={"email": OWNER},
        headers=_csrf_headers(client),
    )
    assert again.status_code == 200, again.text
    for credential_id, row in historical.items():
        assert asdict(store.get(credential_id)) == row


def test_owner_recovery_begin_is_rate_limited_without_blocking_passkey_session(
    recovery_client, monkeypatch
):
    client, store, manager = recovery_client
    passkey_routes.reset_owner_passkey_recovery_limits()
    client.cookies.set(
        PASSKEY_COOKIE_NAME,
        manager.issue_session("admin", email=OWNER, auth_method="passkey"),
    )
    for _ in range(6):
        allowed = client.post(
            "/api/admin/passkey/register/begin",
            json={"email": OWNER},
            headers=_csrf_headers(client),
        )
        assert allowed.status_code == 200, allowed.text

    client.cookies.clear()
    _sign_email(client, monkeypatch, OWNER)
    for _ in range(5):
        opened = client.post(
            "/api/admin/passkey/register/begin",
            json={"email": OWNER},
            headers=_csrf_headers(client),
        )
        assert opened.status_code == 200, opened.text
    limited = client.post(
        "/api/admin/passkey/register/begin",
        json={"email": OWNER},
        headers=_csrf_headers(client),
    )
    assert limited.status_code == 429
    assert store.get(HISTORICAL_IDS[0]) is not None
    assert store.get(b"staff-key") is not None
