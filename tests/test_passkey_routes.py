"""Regression tests for THO WebAuthn passkey routes."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytest.importorskip("webauthn")

from auth import routes as passkey_routes  # noqa: E402
from auth.session import PASSKEY_COOKIE_NAME, SESSION_COOKIE_NAME, SessionManager  # noqa: E402
from auth.store import CredentialRecord, InMemoryCredentialStore  # noqa: E402

CSRF_COOKIE_NAME = "tho_csrf_token"


def _csrf_headers(client, token="passkey-route-csrf"):
    client.cookies.set(CSRF_COOKIE_NAME, token)
    return {"X-CSRF-Token": token}


def _fixture_dependencies(client, routes):
    overrides = client.app.dependency_overrides
    return overrides[routes.get_session_manager](), overrides[routes.get_credential_store]()


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


def test_register_begin_accepts_owner_email_after_admin_auth(passkey_client, monkeypatch):
    client, routes = passkey_client
    monkeypatch.setattr(routes, "_request_is_admin", lambda request, manager: True)

    response = client.post(
        "/api/admin/passkey/register/begin",
        json={"email": "aribspector@gmail.com"},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 200, response.text
    assert response.json()["user"]["name"] == "aribspector@gmail.com"


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
