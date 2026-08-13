"""Golden security boundaries for owner-only Google Ads WebAuthn step-up."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytest.importorskip("webauthn")

from auth import google_ads_step_up_routes as routes  # noqa: E402
from auth.google_ads_step_up import InMemoryStepUpStore  # noqa: E402
from auth.session import PASSKEY_COOKIE_NAME, SessionManager  # noqa: E402
from auth.store import CredentialRecord, InMemoryCredentialStore  # noqa: E402
from google_ads_admin.status import load_checked_in_contract  # noqa: E402
from scripts.google_ads_access_evidence import (  # noqa: E402
    AccessCheckKey,
    AccessEvidenceStatus,
    build_access_evidence,
)
from scripts.google_ads_launch_draft import contract_sha256  # noqa: E402

NOW = datetime(2026, 8, 13, 3, 0, tzinfo=UTC)
CSRF = "owner-step-up-csrf"


def _request_context(**overrides):
    contract = load_checked_in_contract()
    digest = contract_sha256(contract)
    values = {
        "purpose": "PAUSED_CREATE",
        "deployment_id": f"{contract['deployment']['key']}--{digest}",
        "contract_hash": f"sha256:{digest}",
        "caps": {
            "average_daily_usd": 20,
            "max_single_day_charge_usd": 40,
            "monthly_charge_limit_usd": 608,
            "max_cpc_usd": 5,
        },
    }
    values.update(overrides)
    return values


def _access_evidence(
    *,
    status=AccessEvidenceStatus.PASSED,
    observed_at=NOW,
    source_revision="a" * 40,
):
    context = _request_context()
    return build_access_evidence(
        deployment_id=context["deployment_id"],
        check_key=AccessCheckKey.GOOGLE_ADS_ACCOUNT_ACCESS_GREEN,
        status=status,
        observed_at=observed_at,
        expires_at=observed_at + timedelta(minutes=5),
        source_revision=source_revision,
        now=observed_at,
    )


class _AccessEvidenceLedger:
    def __init__(self, evidence=None, *, error=None):
        self.evidence = evidence or _access_evidence()
        self.error = error
        self.calls = []

    def get_access_evidence(self, deployment_id, check_key):
        self.calls.append((deployment_id, check_key))
        if self.error:
            raise self.error
        return self.evidence


@pytest.fixture
def step_up_client(monkeypatch):
    monkeypatch.setenv(
        "THO_GOOGLE_ADS_OWNER_EMAILS",
        "aristotlespec@gmail.com,aribspector@gmail.com",
    )
    manager = SessionManager(secret_key="test-owner-step-up-secret")
    credential_store = InMemoryCredentialStore(
        [
            CredentialRecord(
                credential_id=b"owner-credential",
                public_key=b"owner-public-key",
                sign_count=0,
                user_id="aristotlespec@gmail.com",
            ),
            CredentialRecord(
                credential_id=b"staff-credential",
                public_key=b"staff-public-key",
                sign_count=0,
                user_id="mark@texashomeoutlet.com",
            ),
        ]
    )
    evidence_store = InMemoryStepUpStore(clock=lambda: NOW)
    access_ledger = _AccessEvidenceLedger()
    monkeypatch.setattr(routes, "_utc_now", lambda: NOW)
    app = FastAPI()
    app.dependency_overrides[routes.get_session_manager] = lambda: manager
    app.dependency_overrides[routes.get_credential_store] = lambda: credential_store
    app.dependency_overrides[routes.get_step_up_store] = lambda: evidence_store
    app.dependency_overrides[routes.get_access_evidence_ledger] = lambda: access_ledger
    app.include_router(routes.router)
    client = TestClient(app, base_url="https://tho.sapphirealpha.xyz")
    return client, manager, credential_store, evidence_store, access_ledger


def _authenticate(client, manager, email="aristotlespec@gmail.com", method="passkey"):
    client.cookies.set(
        PASSKEY_COOKIE_NAME,
        manager.issue_session("admin", email=email, auth_method=method),
    )
    client.cookies.set("tho_csrf_token", CSRF)


def _begin(client):
    return client.post(
        "/api/admin/passkey/google-ads-step-up/begin",
        headers={"X-CSRF-Token": CSRF},
        json=_request_context(),
    )


def _complete(client, context=None):
    return client.post(
        "/api/admin/passkey/google-ads-step-up/complete",
        headers={"X-CSRF-Token": CSRF},
        json={
            "context": context or _request_context(),
            "credential": {"id": "b3duZXItY3JlZGVudGlhbA", "response": {}},
        },
    )


def test_begin_rejects_staff_domain_pin_bearer_missing_owner_config_and_missing_csrf(
    step_up_client, monkeypatch
):
    client, manager, _credentials, evidence_store, _access = step_up_client

    _authenticate(client, manager, email="mark@texashomeoutlet.com")
    assert _begin(client).status_code == 403

    _authenticate(client, manager, method="pin")
    assert _begin(client).status_code == 403

    client.cookies.clear()
    assert (
        client.post(
            "/api/admin/passkey/google-ads-step-up/begin",
            headers={"Authorization": "Bearer shared-admin", "X-CSRF-Token": CSRF},
            json=_request_context(),
        ).status_code
        == 401
    )

    _authenticate(client, manager)
    assert (
        client.post(
            "/api/admin/passkey/google-ads-step-up/begin",
            json=_request_context(),
        ).status_code
        == 403
    )

    client.cookies.delete("tho_csrf_token")
    for bypass_header in (
        {"Authorization": "Bearer junk"},
        {"X-Admin-Token": "junk"},
    ):
        assert (
            client.post(
                "/api/admin/passkey/google-ads-step-up/begin",
                headers=bypass_header,
                json=_request_context(),
            ).status_code
            == 403
        )

    monkeypatch.delenv("THO_GOOGLE_ADS_OWNER_EMAILS")
    assert _begin(client).status_code == 503
    monkeypatch.setenv(
        "THO_GOOGLE_ADS_OWNER_EMAILS",
        "aristotlespec@gmail.com,aribspector@gmail.com,not-an-email",
    )
    monkeypatch.setenv(
        "THO_PASSKEY_OWNER_EMAILS",
        "aristotlespec@gmail.com,aribspector@gmail.com,not-an-email",
    )
    assert _begin(client).status_code == 503
    assert evidence_store._nonces == {}


def test_begin_requires_exact_checked_in_contract_and_creates_uv_required_short_nonce(
    step_up_client,
):
    client, manager, credentials, evidence_store, access = step_up_client
    _authenticate(client, manager)

    response = _begin(client)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["userVerification"] == "required"
    assert [item["id"] for item in body["allowCredentials"]] == ["b3duZXItY3JlZGVudGlhbA"]
    assert len(evidence_store._nonces) == 1
    stored = next(iter(evidence_store._nonces.values()))
    assert (stored.expires_at - stored.issued_at).total_seconds() == 300
    serialized = json.dumps(stored.model_dump(mode="json"))
    assert "aristotlespec@gmail.com" not in serialized
    assert "challenge" not in serialized
    assert "tho_google_ads_step_up=" in response.headers["set-cookie"]
    assert access.calls == [
        (
            _request_context()["deployment_id"],
            AccessCheckKey.GOOGLE_ADS_ACCOUNT_ACCESS_GREEN,
        )
    ]

    for unsafe in (
        {**_request_context(), "provider": "google"},
        {**_request_context(), "account_id": "123"},
        {**_request_context(), "approval": True},
        {**_request_context(), "evidence_digest": f"sha256:{'b' * 64}"},
        {**_request_context(), "contract_hash": f"sha256:{'c' * 64}"},
        {
            **_request_context(),
            "caps": {**_request_context()["caps"], "monthly_charge_limit_usd": 609},
        },
    ):
        rejected = client.post(
            "/api/admin/passkey/google-ads-step-up/begin",
            headers={"X-CSRF-Token": CSRF},
            json=unsafe,
        )
        assert rejected.status_code in {409, 422}
    assert len(evidence_store._nonces) == 1


def test_complete_requires_fresh_uv_assertion_and_records_only_sanitized_evidence(
    step_up_client, monkeypatch
):
    client, manager, credentials, evidence_store, access = step_up_client
    _authenticate(client, manager)
    assert _begin(client).status_code == 200
    verification_calls = []

    monkeypatch.setattr(
        routes.passkey_routes, "parse_authentication_credential_json", lambda raw: raw
    )

    def verify(**kwargs):
        verification_calls.append(kwargs)
        return SimpleNamespace(credential_id=b"owner-credential", new_sign_count=1)

    monkeypatch.setattr(routes.passkey_routes, "verify_authentication_response", verify)

    completed = _complete(client)

    assert completed.status_code == 200, completed.text
    assert verification_calls[0]["require_user_verification"] is True
    body = completed.json()
    assert body["verified"] is True
    assert body["approval_enabled"] is False
    assert body["action_available"] is False
    assert body["evidence"]["evidence_digest"] == access.evidence.evidence_digest
    serialized = json.dumps(body)
    for forbidden in (
        "aristotlespec@gmail.com",
        "owner-credential",
        "owner-public-key",
        "authenticatorData",
        "clientDataJSON",
        "signature",
        "account_id",
        "customer_id",
        "token",
    ):
        assert forbidden not in serialized
    assert len(evidence_store._evidence) == 1
    assert credentials.get(b"owner-credential").sign_count == 1

    replay = _complete(client)
    assert replay.status_code == 409
    assert len(evidence_store._evidence) == 1


def test_complete_rejects_changed_context_stale_nonce_and_uv_false_without_evidence(
    step_up_client, monkeypatch
):
    client, manager, _credentials, evidence_store, access = step_up_client
    _authenticate(client, manager)
    assert _begin(client).status_code == 200
    monkeypatch.setattr(
        routes.passkey_routes, "parse_authentication_credential_json", lambda raw: raw
    )
    monkeypatch.setattr(
        routes.passkey_routes,
        "verify_authentication_response",
        lambda **kwargs: SimpleNamespace(credential_id=b"owner-credential", new_sign_count=1),
    )

    changed = _request_context(caps={**_request_context()["caps"], "max_cpc_usd": 4})
    assert _complete(client, changed).status_code == 409
    assert evidence_store._evidence == {}

    access.evidence = _access_evidence(source_revision="b" * 40)
    assert _complete(client).status_code == 409
    assert evidence_store._evidence == {}
    access.evidence = _access_evidence()

    evidence_store._clock = lambda: NOW + timedelta(seconds=300)
    assert _complete(client).status_code == 409
    assert evidence_store._evidence == {}

    evidence_store._clock = lambda: NOW
    monkeypatch.setattr(
        routes.passkey_routes,
        "verify_authentication_response",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("user verification required")),
    )
    assert _complete(client).status_code == 400
    assert evidence_store._evidence == {}


def test_complete_rejects_raw_authority_fields_before_verification(step_up_client, monkeypatch):
    client, manager, _credentials, evidence_store, _access = step_up_client
    _authenticate(client, manager)
    assert _begin(client).status_code == 200
    verifier = SimpleNamespace(called=False)

    def verify(**kwargs):
        verifier.called = True
        raise AssertionError("must not verify an invalid request")

    monkeypatch.setattr(routes.passkey_routes, "verify_authentication_response", verify)
    for unsafe_field in ("provider", "account_id", "token", "approval", "create", "spend"):
        payload = {
            "context": _request_context(),
            "credential": {"id": "b3duZXItY3JlZGVudGlhbA", "response": {}},
            unsafe_field: True,
        }
        response = client.post(
            "/api/admin/passkey/google-ads-step-up/complete",
            headers={"X-CSRF-Token": CSRF},
            json=payload,
        )
        assert response.status_code == 422
    assert verifier.called is False
    assert evidence_store._evidence == {}


def test_begin_fails_closed_when_current_server_access_evidence_is_missing_failed_or_stale(
    step_up_client,
):
    client, manager, _credentials, evidence_store, access = step_up_client
    _authenticate(client, manager)

    access.error = RuntimeError("missing")
    assert _begin(client).status_code == 503
    access.error = None
    access.evidence = _access_evidence(status=AccessEvidenceStatus.FAILED)
    assert _begin(client).status_code == 503
    access.evidence = _access_evidence(observed_at=NOW - timedelta(minutes=6))
    assert _begin(client).status_code == 503
    assert evidence_store._nonces == {}


def test_nondefault_ads_owner_requires_one_consistent_canonical_passkey_allowlist(
    step_up_client, monkeypatch
):
    client, manager, credentials, evidence_store, _access = step_up_client
    owner = "new-owner@example.com"
    credentials.add(
        CredentialRecord(
            credential_id=b"new-owner-credential",
            public_key=b"new-owner-public-key",
            sign_count=0,
            user_id=owner,
        )
    )
    monkeypatch.setenv("THO_GOOGLE_ADS_OWNER_EMAILS", owner)
    _authenticate(client, manager, email=owner)

    assert _begin(client).status_code == 503
    monkeypatch.setenv("THO_PASSKEY_OWNER_EMAILS", owner)
    assert routes.passkey_routes._passkey_email_allowed(owner) is True
    assert _begin(client).status_code == 200
    assert len(evidence_store._nonces) == 1


def test_step_up_router_is_mounted_but_has_no_approval_provider_or_job_capability():
    root = Path(routes.__file__).resolve().parents[1]
    source = (root / "main.py").read_text(encoding="utf-8")
    assert "app.include_router(google_ads_step_up_router" in source

    route_source = Path(routes.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "DraftReviewControlPlane",
        "google.ads",
        "run.jobs",
        "Cloud Run",
        "approve_paused_create",
        "claim_paused_create",
        "create_campaign",
    ):
        assert forbidden not in route_source
