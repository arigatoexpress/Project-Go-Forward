"""Regression tests for THO WebAuthn passkey routes."""

from __future__ import annotations

import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytest.importorskip("webauthn")

from auth import routes as passkey_routes  # noqa: E402
from auth.session import PASSKEY_COOKIE_NAME, SESSION_COOKIE_NAME, SessionManager  # noqa: E402
from auth.store import CredentialRecord, InMemoryCredentialStore  # noqa: E402


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
            )
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
    assert body["allowCredentials"][0]["id"] == "Y3JlZGVudGlhbC0x"
    assert body["allowCredentials"][0]["type"] == "public-key"
    assert "tho_passkey_login=" in response.headers["set-cookie"]


def test_register_begin_requires_existing_admin_session(passkey_client):
    client, _routes = passkey_client

    response = client.post("/api/admin/passkey/register/begin")

    assert response.status_code == 401


def test_register_begin_returns_browser_json_after_admin_auth(passkey_client, monkeypatch):
    client, routes = passkey_client
    monkeypatch.setattr(routes, "_request_is_admin", lambda request, manager: True)

    response = client.post("/api/admin/passkey/register/begin")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rp"]["id"] == "sapphirealpha.xyz"
    assert body["user"]["id"] == "dGhvLWFkbWlu"
    assert body["excludeCredentials"][0]["id"] == "Y3JlZGVudGlhbC0x"
    assert "tho_passkey_register=" in response.headers["set-cookie"]


def test_status_reports_store_persistence_contract(passkey_client):
    client, _routes = passkey_client

    response = client.get("/api/admin/passkey/status")

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["registered_keys"] == 1
    assert body["has_keys"] is True
    assert body["store_backend"] == "memory"
    assert body["persistent"] is False
    assert body["store_ready"] is True
