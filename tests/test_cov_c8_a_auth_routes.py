"""Coverage tests for auth/routes.py helpers and error branches (campaign C8-A).

Extends the patterns from tests/test_passkey_routes.py: same fixture shape,
same CSRF helper, same monkeypatching style for the webauthn helpers.
"""

from __future__ import annotations

import hashlib
import hmac
import struct
import time
from base64 import urlsafe_b64encode
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

pytest.importorskip("webauthn")

from auth import routes as passkey_routes  # noqa: E402
from auth.session import PASSKEY_COOKIE_NAME, SessionManager  # noqa: E402
from auth.store import (  # noqa: E402
    CredentialRecord,
    CredentialStoreUnavailable,
    InMemoryCredentialStore,
)

CSRF_COOKIE_NAME = "tho_csrf_token"


class _BrokenListStore(InMemoryCredentialStore):
    def list_all(self):
        raise RuntimeError("directory exploded")


class _BrokenGetStore(InMemoryCredentialStore):
    def get(self, credential_id):
        raise RuntimeError("read exploded")


class _NoopDeleteStore(InMemoryCredentialStore):
    def delete(self, credential_id):
        return False


def _csrf_headers(client, token="cov-c8-a-csrf"):
    client.cookies.set(CSRF_COOKIE_NAME, token)
    return {"X-CSRF-Token": token}


def _deps(client, routes):
    overrides = client.app.dependency_overrides
    return overrides[routes.get_session_manager](), overrides[routes.get_credential_store]()


def _owner_cookie(client, routes, email="aribspector@gmail.com"):
    manager, _store = _deps(client, routes)
    client.cookies.set(
        PASSKEY_COOKIE_NAME,
        manager.issue_session("admin", email=email, auth_method="passkey"),
    )


def _pin_token(secret="cov-admin-secret", pin_hash="cov-pin-hash", expires_in=3600):
    key = hmac.new(
        secret.encode(),
        f"tho-pin-session-v2:{pin_hash}".encode(),
        hashlib.sha256,
    ).digest()
    payload = struct.pack(">Q", int(time.time()) + expires_in)
    sig = hmac.new(key, payload, hashlib.sha256).digest()[:16]
    return urlsafe_b64encode(payload + sig).decode().rstrip("=")


@pytest.fixture
def cov_client():
    routes = passkey_routes
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
    manager = SessionManager(secret_key="cov-c8-a-secret")
    app = FastAPI()
    app.dependency_overrides[routes.get_credential_store] = lambda: store
    app.dependency_overrides[routes.get_session_manager] = lambda: manager
    app.include_router(routes.router)
    return TestClient(app), routes


# ---------------------------------------------------------------------------
# Pure helpers (direct calls)
# ---------------------------------------------------------------------------


def test_normalize_origin_strips_case_and_trailing_slash():
    assert passkey_routes._normalize_origin(" HTTPS://Example.COM/ ") == "https://example.com"
    assert passkey_routes._normalize_origin(None) == ""


def test_origin_rp_ids_merges_env_override_pairs(monkeypatch):
    monkeypatch.setenv(
        "WEBAUTHN_ORIGIN_RP_IDS",
        "https://NEW.example.com=New.Example.com,badpair, = ,https://other.example=x",
    )

    pairs = passkey_routes._origin_rp_ids()

    assert pairs["https://new.example.com"] == "new.example.com"
    assert pairs["https://other.example"] == "x"
    assert pairs["https://www.texashomeoutlet.com"] == "texashomeoutlet.com"


def _fake_request(headers=None, cookies=None, scheme="https"):
    return SimpleNamespace(
        headers=dict(headers or {}),
        cookies=dict(cookies or {}),
        url=SimpleNamespace(scheme=scheme),
    )


def test_request_origin_prefers_header_then_falls_back_to_canonical_origin():
    assert (
        passkey_routes._request_origin(_fake_request({"origin": " HTTPS://A.EXAMPLE.COM/ "}))
        == "https://a.example.com"
    )
    forwarded = _fake_request(
        {"x-forwarded-host": "b.example.com", "x-forwarded-proto": "https"},
        scheme="http",
    )
    assert passkey_routes._request_origin(forwarded) == "https://b.example.com"
    host_only = _fake_request({"host": "c.example.com"}, scheme="http")
    assert passkey_routes._request_origin(host_only) == "http://c.example.com"
    # No origin/host headers at all: canonical THO origin fallback.
    assert passkey_routes._request_origin(_fake_request()) == passkey_routes.THO_ORIGIN


def test_webauthn_context_maps_known_origin_and_defaults_unknown():
    known = _fake_request({"origin": "https://tho.sapphire.xyz"})
    assert passkey_routes._webauthn_context(known) == ("https://tho.sapphire.xyz", "sapphire.xyz")
    unknown = _fake_request({"origin": "https://evil.example.com"})
    assert passkey_routes._webauthn_context(unknown) == (
        "https://www.texashomeoutlet.com",
        "texashomeoutlet.com",
    )


def test_google_ads_owner_emails_rejects_inconsistent_configuration(monkeypatch):
    monkeypatch.setenv("THO_PASSKEY_OWNER_EMAILS", "owner@example.com")
    monkeypatch.setenv("THO_GOOGLE_ADS_OWNER_EMAILS", "other@example.com")
    with pytest.raises(ValueError):
        passkey_routes.google_ads_owner_emails()

    monkeypatch.setenv("THO_GOOGLE_ADS_OWNER_EMAILS", "not-an-email")
    with pytest.raises(ValueError):
        passkey_routes.google_ads_owner_emails()

    monkeypatch.setenv("THO_GOOGLE_ADS_OWNER_EMAILS", "x" * 201 + "@example.com")
    with pytest.raises(ValueError):
        passkey_routes.google_ads_owner_emails()


def test_passkey_email_allowed_honors_directory_blocked_and_allowed(monkeypatch):
    monkeypatch.setattr(passkey_routes, "override_status", lambda email: "blocked")
    assert passkey_routes._passkey_email_allowed("mark@texashomeoutlet.com") is False
    # Owners stay allowed even when the directory says blocked.
    assert passkey_routes._passkey_email_allowed("aribspector@gmail.com") is True

    monkeypatch.setattr(passkey_routes, "override_status", lambda email: "allowed")
    assert passkey_routes._passkey_email_allowed("outsider@other.com") is True


def test_allowed_owner_and_staff_domain_aliases_return_copies():
    assert passkey_routes.allowed_owner_emails() == {
        "aribspector@gmail.com",
        "aristotlespec@gmail.com",
    }
    assert passkey_routes.allowed_staff_domains() == {"texashomeoutlet.com"}


def test_require_allowed_passkey_email_rejects_missing_and_invalid():
    with pytest.raises(HTTPException) as missing:
        passkey_routes._require_allowed_passkey_email("")
    assert missing.value.status_code == 400

    with pytest.raises(HTTPException) as invalid:
        passkey_routes._require_allowed_passkey_email("not-an-email")
    assert invalid.value.status_code == 400

    assert (
        passkey_routes._require_allowed_passkey_email(" Mark@TexasHomeOutlet.com ")
        == "mark@texashomeoutlet.com"
    )


def test_credential_records_surfaces_store_failure_as_503():
    store = _BrokenListStore()
    with pytest.raises(HTTPException) as exc_info:
        passkey_routes._credential_records(store)
    assert exc_info.value.status_code == 503


def test_current_user_and_require_passkey_user():
    manager = SessionManager(secret_key="cov-c8-a-secret")
    token = manager.issue_session("admin", email="mark@texashomeoutlet.com")

    assert passkey_routes._current_user(session=token, manager=manager)["user_id"] == "admin"
    assert passkey_routes._current_user(session="junk", manager=manager) is None
    assert passkey_routes._require_passkey_user(session=token, manager=manager)["email"] == (
        "mark@texashomeoutlet.com"
    )
    with pytest.raises(HTTPException) as exc_info:
        passkey_routes._require_passkey_user(session=None, manager=manager)
    assert exc_info.value.status_code == 401


def test_user_verification_prefers_preferred():
    from webauthn.helpers.structs import UserVerificationRequirement

    assert passkey_routes._user_verification() == UserVerificationRequirement.PREFERRED


def test_cookie_secure_tracks_request_origin_scheme(monkeypatch):
    assert passkey_routes._cookie_secure(_fake_request({"origin": "https://a.com"})) is True
    # Fallback origin (no headers) is the canonical https storefront.
    assert passkey_routes._cookie_secure(_fake_request()) is True
    # Unknown origins fall back to the canonical https origin, so register an
    # explicit http origin to exercise the insecure-cookie branch.
    monkeypatch.setenv("WEBAUTHN_ORIGIN_RP_IDS", "http://local.test=local.test")
    assert passkey_routes._cookie_secure(_fake_request({"origin": "http://local.test"})) is False


def test_admin_token_from_request_reads_cookie_header_and_bearer():
    routes = passkey_routes
    assert routes._admin_token_from_request(_fake_request(cookies={"tho_admin_token": " c "})) == "c"
    assert routes._admin_token_from_request(_fake_request({"X-Admin-Token": " h "})) == "h"
    assert routes._admin_token_from_request(_fake_request({"Authorization": "Bearer  b "})) == "b"
    assert routes._admin_token_from_request(_fake_request({"Authorization": "Basic xyz"})) == ""
    assert routes._admin_token_from_request(_fake_request()) == ""


def test_pin_token_key_requires_secret_and_hash(monkeypatch):
    monkeypatch.delenv("ADMIN_SESSION_SECRET", raising=False)
    monkeypatch.delenv("ADMIN_PIN_HASH", raising=False)
    assert passkey_routes._pin_token_key("hash") is None

    monkeypatch.setenv("ADMIN_SESSION_SECRET", "s")
    assert passkey_routes._pin_token_key("") is None

    monkeypatch.setenv("ADMIN_PIN_HASH", "h")
    key = passkey_routes._pin_token_key("h")
    assert isinstance(key, bytes) and len(key) == 32


def test_verify_admin_pin_token_accepts_valid_and_rejects_tampered(monkeypatch):
    monkeypatch.setenv("ADMIN_SESSION_SECRET", "cov-admin-secret")
    monkeypatch.setenv("ADMIN_PIN_HASH", "cov-pin-hash")

    assert passkey_routes._verify_admin_pin_token("") is False
    assert passkey_routes._verify_admin_pin_token(_pin_token()) is True
    assert passkey_routes._verify_admin_pin_token(_pin_token(expires_in=-10)) is False
    # Wrong signing key -> signature mismatch.
    wrong_key_token = _pin_token(secret="other-secret")
    assert passkey_routes._verify_admin_pin_token(wrong_key_token) is False
    # Decodes but wrong length.
    short = urlsafe_b64encode(b"short").decode().rstrip("=")
    assert passkey_routes._verify_admin_pin_token(short) is False
    # Raises inside the decoder -> False.
    assert passkey_routes._verify_admin_pin_token("a") is False


def test_verify_admin_pin_token_fails_closed_without_configuration(monkeypatch):
    monkeypatch.delenv("ADMIN_SESSION_SECRET", raising=False)
    monkeypatch.delenv("ADMIN_PIN_HASH", raising=False)
    assert passkey_routes._verify_admin_pin_token(_pin_token()) is False

    monkeypatch.setenv("ADMIN_PIN_HASH", "cov-pin-hash")
    # Hash configured but session secret missing -> no signing key.
    assert passkey_routes._verify_admin_pin_token(_pin_token()) is False


def test_request_is_admin_accepts_passkey_session_or_pin_token(monkeypatch):
    manager = SessionManager(secret_key="cov-c8-a-secret")
    admin_token = manager.issue_session("admin", auth_method="passkey")
    staff_token = manager.issue_session("staff", email="mark@texashomeoutlet.com")

    assert (
        passkey_routes._request_is_admin(
            _fake_request(cookies={PASSKEY_COOKIE_NAME: admin_token}), manager
        )
        is True
    )
    assert (
        passkey_routes._request_is_admin(
            _fake_request(cookies={PASSKEY_COOKIE_NAME: staff_token}), manager
        )
        is False
    )

    monkeypatch.setenv("ADMIN_SESSION_SECRET", "cov-admin-secret")
    monkeypatch.setenv("ADMIN_PIN_HASH", "cov-pin-hash")
    bearer = _fake_request({"Authorization": f"Bearer {_pin_token()}"})
    assert passkey_routes._request_is_admin(bearer, manager) is True
    assert passkey_routes._request_is_admin(_fake_request(), manager) is False


def test_require_admin_request_raises_401_for_strangers():
    manager = SessionManager(secret_key="cov-c8-a-secret")
    with pytest.raises(HTTPException) as exc_info:
        passkey_routes._require_admin_request(_fake_request(), manager)
    assert exc_info.value.status_code == 401


def test_discoverable_login_disabled_by_env(monkeypatch):
    for raw in ("0", "false", "no", "off"):
        monkeypatch.setenv("THO_PASSKEY_DISCOVERABLE_LOGIN", raw)
        assert passkey_routes._discoverable_login_enabled() is False
    monkeypatch.setenv("THO_PASSKEY_DISCOVERABLE_LOGIN", "yes")
    assert passkey_routes._discoverable_login_enabled() is True


def test_decode_credential_id_validation_errors():
    with pytest.raises(HTTPException) as empty:
        passkey_routes._decode_credential_id("")
    assert empty.value.status_code == 400

    with pytest.raises(HTTPException) as invalid:
        passkey_routes._decode_credential_id("a")
    assert invalid.value.status_code == 400

    assert passkey_routes._decode_credential_id("Y3JlZGVudGlhbC0y") == b"credential-2"


def test_serialize_credential_handles_missing_and_present_timestamps():
    with_dt = CredentialRecord(
        credential_id=b"credential-2",
        public_key=b"public-key-2",
        sign_count=3,
        user_id="mark@texashomeoutlet.com",
        aaguid="aaguid-value",
    )
    row = passkey_routes._serialize_credential(with_dt)
    assert row["credential_id"] == "Y3JlZGVudGlhbC0y"
    assert row["authorized"] is True
    assert row["aaguid"] == "aaguid-value"
    assert row["created_at"] == with_dt.created_at.isoformat()
    assert row["sign_count"] == 3

    plain = SimpleNamespace(
        credential_id_b64="abc",
        user_id="nobody",
        aaguid=None,
        created_at="not-a-datetime",
        last_used_at=None,
        sign_count=0,
    )
    row = passkey_routes._serialize_credential(plain)
    assert row["created_at"] is None
    assert row["last_used_at"] is None
    assert row["aaguid"] == ""


def test_store_backend_and_persistent_helpers():
    store = InMemoryCredentialStore()
    assert passkey_routes._store_backend(store) == "memory"
    assert passkey_routes._store_persistent(store) is False

    custom = SimpleNamespace(backend_name="firestore", persistent=True)
    assert passkey_routes._store_backend(custom) == "firestore"
    assert passkey_routes._store_persistent(custom) is True


def test_get_session_manager_lazily_initializes_singleton(monkeypatch):
    monkeypatch.setattr(passkey_routes, "_session_manager", None)
    manager = passkey_routes.get_session_manager()
    assert isinstance(manager, SessionManager)
    assert passkey_routes.get_session_manager() is manager


def test_get_credential_store_surfaces_unavailability(monkeypatch):
    def unavailable(project=None):
        raise CredentialStoreUnavailable("down")

    monkeypatch.setattr(passkey_routes, "default_store", unavailable)
    with pytest.raises(HTTPException) as exc_info:
        passkey_routes.get_credential_store()
    assert exc_info.value.status_code == 503

    memory = InMemoryCredentialStore()
    monkeypatch.setattr(passkey_routes, "default_store", lambda project=None: memory)
    assert passkey_routes.get_credential_store() is memory


# ---------------------------------------------------------------------------
# Route error branches (TestClient against the passkey router app)
# ---------------------------------------------------------------------------


def test_register_begin_reports_unavailable_without_webauthn(cov_client, monkeypatch):
    client, routes = cov_client
    monkeypatch.setattr(routes, "_HAS_WEBAUTHN", False)

    response = client.post("/api/admin/passkey/register/begin")

    assert response.status_code == 503


def test_register_complete_reports_unavailable_without_webauthn(cov_client, monkeypatch):
    client, routes = cov_client
    monkeypatch.setattr(routes, "_HAS_WEBAUTHN", False)

    response = client.post("/api/admin/passkey/register/complete", json={"id": "x"})

    assert response.status_code == 503


def test_register_complete_rejects_expired_challenge(cov_client, monkeypatch):
    client, routes = cov_client
    monkeypatch.setattr(routes, "_request_is_admin", lambda request, manager: True)

    response = client.post(
        "/api/admin/passkey/register/complete",
        json={"id": "bmV3LWNyZWRlbnRpYWw"},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Challenge expired or invalid"


def test_register_complete_wraps_verification_failure(cov_client, monkeypatch):
    client, routes = cov_client
    manager, _store = _deps(client, routes)
    challenge = manager.new_challenge_bytes()
    client.cookies.set(
        "tho_passkey_register",
        manager.wrap_challenge(challenge, flow="register", email="mark@texashomeoutlet.com"),
    )
    monkeypatch.setattr(routes, "_request_is_admin", lambda request, manager: True)
    monkeypatch.setattr(routes, "parse_registration_credential_json", lambda value: value)

    def boom(**kwargs):
        raise ValueError("bad attestation")

    monkeypatch.setattr(routes, "verify_registration_response", boom)

    response = client.post(
        "/api/admin/passkey/register/complete",
        json={"id": "bmV3LWNyZWRlbnRpYWw"},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Registration verification failed"


def test_register_complete_surfaces_store_persist_failure(cov_client, monkeypatch):
    client, routes = cov_client
    manager, store = _deps(client, routes)
    challenge = manager.new_challenge_bytes()
    client.cookies.set(
        "tho_passkey_register",
        manager.wrap_challenge(challenge, flow="register", email="mark@texashomeoutlet.com"),
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

    def explode(record):
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "add", explode)

    response = client.post(
        "/api/admin/passkey/register/complete",
        json={"id": "bmV3LWNyZWRlbnRpYWw"},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Passkey credential store unavailable"


def test_register_complete_maps_duplicate_credential_to_409(cov_client, monkeypatch):
    client, routes = cov_client
    manager, store = _deps(client, routes)
    existing = store.get(b"credential-2")
    challenge = manager.new_challenge_bytes()
    client.cookies.set(
        "tho_passkey_register",
        manager.wrap_challenge(challenge, flow="register", email="mark@texashomeoutlet.com"),
    )
    monkeypatch.setattr(routes, "_request_is_admin", lambda request, manager: True)
    monkeypatch.setattr(routes, "parse_registration_credential_json", lambda value: value)
    monkeypatch.setattr(
        routes,
        "verify_registration_response",
        lambda **kwargs: SimpleNamespace(
            credential_id=existing.credential_id,
            credential_public_key=b"new-public-key",
            sign_count=0,
            aaguid=None,
        ),
    )

    response = client.post(
        "/api/admin/passkey/register/complete",
        json={"id": existing.credential_id_b64},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 409
    assert PASSKEY_COOKIE_NAME not in response.cookies


def test_login_begin_reports_unavailable_without_webauthn(cov_client, monkeypatch):
    client, routes = cov_client
    monkeypatch.setattr(routes, "_HAS_WEBAUTHN", False)

    response = client.post("/api/admin/passkey/login/begin")

    assert response.status_code == 503


def test_login_complete_reports_unavailable_without_webauthn(cov_client, monkeypatch):
    client, routes = cov_client
    monkeypatch.setattr(routes, "_HAS_WEBAUTHN", False)

    response = client.post("/api/admin/passkey/login/complete", json={"id": "x"})

    assert response.status_code == 503


def test_login_complete_rejects_expired_challenge(cov_client):
    client, _routes = cov_client

    response = client.post("/api/admin/passkey/login/complete", json={"id": "eW91bm93bg"})

    assert response.status_code == 400
    assert response.json()["detail"] == "Challenge expired or invalid"


def test_login_complete_surfaces_store_read_failure(cov_client, monkeypatch):
    client, routes = cov_client
    manager, store = _deps(client, routes)
    broken = _BrokenGetStore(list(store.list_all()))
    client.app.dependency_overrides[routes.get_credential_store] = lambda: broken
    challenge = manager.new_challenge_bytes()
    client.cookies.set("tho_passkey_login", manager.wrap_challenge(challenge, flow="login"))

    response = client.post(
        "/api/admin/passkey/login/complete",
        json={"id": "Y3JlZGVudGlhbC0y"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Passkey credential store unavailable"


def test_login_complete_rejects_unknown_credential(cov_client, monkeypatch):
    client, routes = cov_client
    manager, _store = _deps(client, routes)
    challenge = manager.new_challenge_bytes()
    client.cookies.set("tho_passkey_login", manager.wrap_challenge(challenge, flow="login"))

    response = client.post("/api/admin/passkey/login/complete", json={"id": "dW5rbm93bg"})

    assert response.status_code == 400
    assert response.json()["detail"] == "Unknown credential"


def test_login_complete_rejects_credential_for_unapproved_email(cov_client, monkeypatch):
    client, routes = cov_client
    manager, _store = _deps(client, routes)
    challenge = manager.new_challenge_bytes()
    client.cookies.set("tho_passkey_login", manager.wrap_challenge(challenge, flow="login"))

    response = client.post("/api/admin/passkey/login/complete", json={"id": "Y3JlZGVudGlhbC0x"})

    assert response.status_code == 403
    assert "approved THO admin email" in response.json()["detail"]


def test_login_complete_wraps_verify_failure_and_identity_mismatch(cov_client, monkeypatch):
    client, routes = cov_client
    manager, _store = _deps(client, routes)
    challenge = manager.new_challenge_bytes()
    client.cookies.set("tho_passkey_login", manager.wrap_challenge(challenge, flow="login"))
    monkeypatch.setattr(routes, "parse_authentication_credential_json", lambda value: value)

    def boom(**kwargs):
        raise ValueError("bad signature")

    monkeypatch.setattr(routes, "verify_authentication_response", boom)
    failed = client.post(
        "/api/admin/passkey/login/complete",
        json={"id": "Y3JlZGVudGlhbC0y"},
    )
    assert failed.status_code == 400
    assert failed.json()["detail"] == "Authentication verification failed"

    monkeypatch.setattr(
        routes,
        "verify_authentication_response",
        lambda **kwargs: SimpleNamespace(credential_id=b"someone-else", new_sign_count=1),
    )
    mismatched = client.post(
        "/api/admin/passkey/login/complete",
        json={"id": "Y3JlZGVudGlhbC0y"},
    )
    assert mismatched.status_code == 400


def test_login_complete_surfaces_usage_update_failure(cov_client, monkeypatch):
    client, routes = cov_client
    manager, store = _deps(client, routes)
    challenge = manager.new_challenge_bytes()
    client.cookies.set("tho_passkey_login", manager.wrap_challenge(challenge, flow="login"))
    monkeypatch.setattr(routes, "parse_authentication_credential_json", lambda value: value)
    monkeypatch.setattr(
        routes,
        "verify_authentication_response",
        lambda **kwargs: SimpleNamespace(credential_id=b"credential-2", new_sign_count=1),
    )

    def explode(usage, used_at):
        raise RuntimeError("transaction aborted")

    monkeypatch.setattr(store, "compare_and_set_usage", explode)

    response = client.post(
        "/api/admin/passkey/login/complete",
        json={"id": "Y3JlZGVudGlhbC0y"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Passkey credential store unavailable"


def test_login_complete_reports_lost_counter_race_as_409(cov_client, monkeypatch):
    client, routes = cov_client
    manager, store = _deps(client, routes)
    challenge = manager.new_challenge_bytes()
    client.cookies.set("tho_passkey_login", manager.wrap_challenge(challenge, flow="login"))
    monkeypatch.setattr(routes, "parse_authentication_credential_json", lambda value: value)
    monkeypatch.setattr(
        routes,
        "verify_authentication_response",
        lambda **kwargs: SimpleNamespace(credential_id=b"credential-2", new_sign_count=1),
    )
    monkeypatch.setattr(store, "compare_and_set_usage", lambda usage, used_at: False)

    response = client.post(
        "/api/admin/passkey/login/complete",
        json={"id": "Y3JlZGVudGlhbC0y"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Passkey counter changed. Refresh and retry."


def test_list_credentials_surfaces_store_failure(cov_client, monkeypatch):
    client, routes = cov_client
    _manager, store = _deps(client, routes)
    broken = _BrokenListStore(list(store.list_all()))
    client.app.dependency_overrides[routes.get_credential_store] = lambda: broken
    _owner_cookie(client, routes)
    monkeypatch.setattr(routes, "_request_is_admin", lambda request, manager: True)

    response = client.get("/api/admin/passkey/credentials", headers=_csrf_headers(client))

    assert response.status_code == 503
    assert response.json()["detail"] == "Passkey credential store unavailable"


def test_delete_credential_returns_404_for_missing_target(cov_client, monkeypatch):
    client, routes = cov_client
    _owner_cookie(client, routes)
    monkeypatch.setattr(routes, "_request_is_admin", lambda request, manager: True)

    response = client.delete(
        "/api/admin/passkey/credentials/dW5rbm93bg",
        headers=_csrf_headers(client),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Passkey credential not found"


def test_delete_credential_surfaces_store_failure(cov_client, monkeypatch):
    client, routes = cov_client
    _manager, store = _deps(client, routes)
    broken = _BrokenListStore(list(store.list_all()))
    client.app.dependency_overrides[routes.get_credential_store] = lambda: broken
    _owner_cookie(client, routes)
    monkeypatch.setattr(routes, "_request_is_admin", lambda request, manager: True)

    response = client.delete(
        "/api/admin/passkey/credentials/Y3JlZGVudGlhbC0y",
        headers=_csrf_headers(client),
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Passkey credential store unavailable"


def test_delete_credential_reports_missing_when_delete_is_noop(cov_client, monkeypatch):
    client, routes = cov_client
    _manager, store = _deps(client, routes)
    noop = _NoopDeleteStore(list(store.list_all()))
    client.app.dependency_overrides[routes.get_credential_store] = lambda: noop
    _owner_cookie(client, routes)
    monkeypatch.setattr(routes, "_request_is_admin", lambda request, manager: True)

    response = client.delete(
        "/api/admin/passkey/credentials/Y3JlZGVudGlhbC0y",
        headers=_csrf_headers(client),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Passkey credential not found"


def test_delete_credential_rejects_invalid_credential_id(cov_client, monkeypatch):
    client, routes = cov_client
    _owner_cookie(client, routes)
    monkeypatch.setattr(routes, "_request_is_admin", lambda request, manager: True)

    response = client.delete(
        "/api/admin/passkey/credentials/a",
        headers=_csrf_headers(client),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid credential id"


def test_status_marks_store_unready_when_listing_fails(cov_client, monkeypatch):
    client, routes = cov_client
    _manager, store = _deps(client, routes)
    broken = _BrokenListStore(list(store.list_all()))
    client.app.dependency_overrides[routes.get_credential_store] = lambda: broken

    response = client.get("/api/admin/passkey/status")

    assert response.status_code == 200
    body = response.json()
    assert body["store_ready"] is False
    assert body["registered_keys"] == 0
    assert body["total_registered_keys"] == 0
    assert body["has_keys"] is False


def test_whoami_returns_authenticated_payload_for_passkey_session(cov_client):
    client, _routes = cov_client
    manager, _store = _deps(client, cov_client[1])
    client.cookies.set(
        PASSKEY_COOKIE_NAME,
        manager.issue_session(
            "admin",
            email="mark@texashomeoutlet.com",
            auth_method="passkey",
        ),
    )

    response = client.get("/api/admin/passkey/whoami")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "user_id": "admin",
        "email": "mark@texashomeoutlet.com",
        "authed": True,
        "owner": False,
        "auth_method": "passkey",
    }
    assert "owner_emails" not in body


def test_whoami_rejects_missing_session(cov_client):
    client, _routes = cov_client

    response = client.get("/api/admin/passkey/whoami")

    assert response.status_code == 401


def test_passkey_logout_clears_session_cookies(cov_client):
    client, _routes = cov_client

    response = client.post("/api/admin/passkey/logout")

    assert response.status_code == 200
    assert response.json() == {"success": True}
    set_cookies = response.headers.get_list("set-cookie") if hasattr(
        response.headers, "get_list"
    ) else [response.headers["set-cookie"]]
    assert any(PASSKEY_COOKIE_NAME in cookie for cookie in set_cookies)
    assert any("tho_admin_session" in cookie for cookie in set_cookies)


def test_shared_pin_cannot_list_passkey_recovery(cov_client, monkeypatch):
    client, _routes = cov_client
    monkeypatch.setenv("ADMIN_SESSION_SECRET", "cov-admin-secret")
    monkeypatch.setenv("ADMIN_PIN_HASH", "cov-pin-hash")

    response = client.get(
        "/api/admin/passkey/credentials",
        headers={"Authorization": f"Bearer {_pin_token()}"},
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "Passkey recovery is limited to the owner."
