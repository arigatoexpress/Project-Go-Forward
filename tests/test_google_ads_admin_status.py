import ast
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from google_ads_admin.status import CONTRACT_PATH, build_deployment_readiness
from scripts.google_ads_launch_draft import contract_sha256
from scripts.google_ads_paused_worker import (
    DeploymentState,
    DraftReviewControlPlane,
    InMemoryAuthorityLedger,
    StaticContractSource,
)

ROOT = Path(__file__).resolve().parents[1]
READINESS = "/api/admin/google-ads/deployment-readiness"
DRAFT = "/api/admin/google-ads/draft"
VALIDATE = "/api/admin/google-ads/server-validation"
CONTRACT = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


class RecordingLedger(InMemoryAuthorityLedger):
    """Offline route seam that exposes only sanitized authority events."""

    def __init__(self):
        super().__init__(clock=lambda: datetime(2026, 8, 12, 12, 0, tzinfo=UTC))
        self.events = []
        self.outbox_state = None
        self.event_limits = []

    def create_or_get(self, candidate):
        record, created = super().create_or_get(candidate)
        if created:
            self.events.append(
                {
                    "event_id": "00000000000000000001-internal-draft-created",
                    "deployment_id": record.deployment_id,
                    "contract_hash": record.contract_hash,
                    "event_type": "INTERNAL_DRAFT_CREATED",
                    "record_version": 1,
                    "from_state": None,
                    "to_state": "INTERNAL_DRAFT",
                    "error_code": None,
                    "occurred_at": record.updated_at,
                }
            )
        return record, created

    def transition(self, deployment_id, **kwargs):
        before = self.get(deployment_id)
        record = super().transition(deployment_id, **kwargs)
        if record.version != before.version:
            self.events.append(
                {
                    "event_id": f"{record.version:020d}-server-validated",
                    "deployment_id": record.deployment_id,
                    "contract_hash": record.contract_hash,
                    "event_type": "SERVER_VALIDATED",
                    "record_version": record.version,
                    "from_state": before.state.value,
                    "to_state": record.state.value,
                    "error_code": None,
                    "occurred_at": record.updated_at,
                }
            )
        return record

    def list_events(self, _deployment_id, *, limit=20):
        self.event_limits.append(limit)
        return self.events[-limit:]

    def get_paused_create_outbox(self, _deployment_id):
        if self.outbox_state is None:
            raise AssertionError("pre-approval projection must not read an outbox")
        return SimpleNamespace(state=self.outbox_state)


@pytest.fixture
def ledger():
    return RecordingLedger()


@pytest.fixture
def admin_client(monkeypatch, ledger):
    import google_ads_admin.routes as routes
    import main

    monkeypatch.setattr(main, "_verify_admin_token", lambda _token: True)
    monkeypatch.setattr(routes, "get_authority_ledger", lambda: ledger)
    return TestClient(main.app, raise_server_exceptions=False)


def _headers():
    return {"X-Admin-Token": "test-token"}


def _request(record, **overrides):
    return {
        "deployment_id": record.deployment_id,
        "expected_version": record.version,
        "idempotency_key": "offline-validation-12345678",
        **overrides,
    }


def test_projection_is_allowlisted_durable_state_and_sanitized_events(ledger):
    record = DraftReviewControlPlane(ledger, StaticContractSource(CONTRACT)).ensure_internal_draft()
    digest = contract_sha256(CONTRACT)

    status = build_deployment_readiness(record, ledger.list_events(record.deployment_id))

    assert status["deployment_id"] == f"tho-search-high-intent-huffman-v1--{digest}"
    assert status["contract_hash"] == f"sha256:{digest}"
    assert status["state"] == "INTERNAL_DRAFT"
    assert status["state_source"] == "FIRESTORE_AUTHORITY_LEDGER"
    assert status["version"] == 1
    assert status["actions"] == {"server_validation": True}
    assert status["events"]["count"] == 1
    assert status["events"]["items"][0]["event_type"] == "INTERNAL_DRAFT_CREATED"
    serialized = json.dumps(status)
    for forbidden in ("customer_id", "developer_token", "provider_reference", "worker_claim"):
        assert forbidden not in serialized


def test_projection_reports_terminal_paused_outbox_without_activation_or_spend(ledger):
    control = DraftReviewControlPlane(ledger, StaticContractSource(CONTRACT))
    draft = control.ensure_internal_draft()
    validated = control.server_validate(draft.deployment_id, expected_version=1)
    approved_at = datetime(2026, 8, 12, 12, 1, tzinfo=UTC)
    approved = replace(
        validated,
        state=DeploymentState.PAUSED_CREATE_APPROVED,
        version=3,
        updated_at=approved_at,
    )
    approved_event = {
        "event_id": "00000000000000000003-paused-create-approved",
        "deployment_id": approved.deployment_id,
        "contract_hash": approved.contract_hash,
        "event_type": "PAUSED_CREATE_APPROVED",
        "record_version": 3,
        "from_state": "SERVER_VALIDATED",
        "to_state": "PAUSED_CREATE_APPROVED",
        "worker_claim_hash": None,
        "error_code": None,
        "occurred_at": approved_at,
    }
    events = [*ledger.list_events(approved.deployment_id), approved_event]

    with pytest.raises(ValueError, match="outbox"):
        build_deployment_readiness(approved, events)
    status = build_deployment_readiness(approved, events, outbox_state="PENDING")

    assert status["state"] == "PAUSED_CREATE_APPROVED"
    assert status["paused_create"] == {
        "outbox_state": "PENDING",
        "activation_authorized": False,
        "spend_enabled": False,
    }

    created_at = datetime(2026, 8, 12, 12, 2, tzinfo=UTC)
    created = replace(
        approved,
        state=DeploymentState.PAUSED_CREATED,
        version=4,
        updated_at=created_at,
    )
    completed_event = {
        "event_id": "00000000000000000004-paused-create-completed",
        "deployment_id": created.deployment_id,
        "contract_hash": created.contract_hash,
        "event_type": "PAUSED_CREATE_COMPLETED",
        "record_version": 4,
        "from_state": "PAUSED_CREATE_APPROVED",
        "to_state": "PAUSED_CREATED",
        "worker_claim_hash": f"sha256:{'c' * 64}",
        "error_code": None,
        "occurred_at": created_at,
    }
    created_status = build_deployment_readiness(
        created,
        [*events, completed_event],
        outbox_state="DISPATCHED",
    )
    assert created_status["state"] == "PAUSED_CREATED"
    assert created_status["paused_create"]["outbox_state"] == "DISPATCHED"
    with pytest.raises(ValueError, match="dispatched"):
        build_deployment_readiness(created, [*events, completed_event], outbox_state="PENDING")


def test_projection_rejects_non_review_or_semantically_invalid_events(ledger):
    record = DraftReviewControlPlane(ledger, StaticContractSource(CONTRACT)).ensure_internal_draft()
    base = ledger.list_events(record.deployment_id)[0]

    for unsafe in (
        {**base, "event_type": "PAUSED_CREATE_APPROVED", "to_state": "PAUSED_CREATE_APPROVED"},
        {**base, "record_version": 2},
        {**base, "from_state": "SERVER_VALIDATED"},
        {**base, "event_id": "00000000000000000999-internal-draft-created"},
    ):
        with pytest.raises(ValueError, match="authority event"):
            build_deployment_readiness(record, [unsafe])


def test_get_requires_auth_and_is_read_only_when_draft_is_absent(admin_client, ledger):
    assert admin_client.get(READINESS).status_code == 401

    first = admin_client.get(READINESS, headers=_headers())
    second = admin_client.get(READINESS, headers=_headers())

    assert first.status_code == second.status_code == 404
    assert ledger._records == {}
    assert ledger.events == []


def test_valid_passkey_get_without_csrf_never_bootstraps_draft(monkeypatch, ledger):
    import google_ads_admin.routes as routes
    import main

    monkeypatch.setattr(routes, "get_authority_ledger", lambda: ledger)
    client = TestClient(main.app, raise_server_exceptions=False)
    session = main._get_passkey_session_manager().issue_session(
        "admin", email="mark@texashomeoutlet.com", auth_method="passkey"
    )
    client.cookies.set(main.PASSKEY_COOKIE_NAME, session)

    response = client.get(READINESS)

    assert response.status_code == 404
    assert ledger._records == {}
    assert ledger.events == []


def test_csrf_protected_bootstrap_post_is_idempotent_and_get_then_projects(admin_client, ledger):
    first = admin_client.post(DRAFT, headers=_headers(), json={})
    replay = admin_client.post(DRAFT, headers=_headers(), json={})
    read = admin_client.get(READINESS, headers=_headers())

    assert first.status_code == replay.status_code == read.status_code == 200
    assert first.json() == replay.json() == read.json()
    assert read.json()["version"] == 1
    assert len(ledger.events) == 1


def test_idempotent_bootstrap_projects_existing_approved_outbox(admin_client, ledger):
    draft = admin_client.post(DRAFT, headers=_headers(), json={}).json()
    admin_client.post(
        VALIDATE,
        headers=_headers(),
        json={
            "deployment_id": draft["deployment_id"],
            "expected_version": 1,
            "idempotency_key": "offline-validation-12345678",
        },
    )
    validated = ledger.get(draft["deployment_id"])
    approved = replace(
        validated,
        state=DeploymentState.PAUSED_CREATE_APPROVED,
        version=3,
    )
    ledger._records[approved.deployment_id] = approved
    ledger.events.append(
        {
            "event_id": "00000000000000000003-paused-create-approved",
            "deployment_id": approved.deployment_id,
            "contract_hash": approved.contract_hash,
            "event_type": "PAUSED_CREATE_APPROVED",
            "record_version": 3,
            "from_state": "SERVER_VALIDATED",
            "to_state": "PAUSED_CREATE_APPROVED",
            "error_code": None,
            "occurred_at": approved.updated_at,
        }
    )
    ledger.outbox_state = "PENDING"

    response = admin_client.post(DRAFT, headers=_headers(), json={})

    assert response.status_code == 200
    assert response.json()["state"] == "PAUSED_CREATE_APPROVED"
    assert response.json()["paused_create"]["outbox_state"] == "PENDING"
    assert ledger.event_limits[-1] == 3


def test_terminal_projection_fetches_and_validates_more_than_default_twenty_events(
    admin_client, ledger
):
    draft = admin_client.post(DRAFT, headers=_headers(), json={}).json()
    admin_client.post(
        VALIDATE,
        headers=_headers(),
        json={
            "deployment_id": draft["deployment_id"],
            "expected_version": 1,
            "idempotency_key": "offline-validation-12345678",
        },
    )
    record = ledger.get(draft["deployment_id"])
    approved_at = datetime(2026, 8, 12, 12, 2, tzinfo=UTC)
    ledger.events.append(
        {
            "event_id": "00000000000000000003-paused-create-approved",
            "deployment_id": record.deployment_id,
            "contract_hash": record.contract_hash,
            "event_type": "PAUSED_CREATE_APPROVED",
            "record_version": 3,
            "from_state": "SERVER_VALIDATED",
            "to_state": "PAUSED_CREATE_APPROVED",
            "error_code": None,
            "occurred_at": approved_at,
        }
    )
    worker_hash = f"sha256:{'c' * 64}"
    for version in range(4, 104):
        claimed = version % 2 == 0
        event_type = "PAUSED_CREATE_CLAIMED" if claimed else "PAUSED_CREATE_CLAIM_RELEASED"
        ledger.events.append(
            {
                "event_id": f"{version:020d}-{event_type.lower().replace('_', '-')}",
                "deployment_id": record.deployment_id,
                "contract_hash": record.contract_hash,
                "event_type": event_type,
                "record_version": version,
                "from_state": "PAUSED_CREATE_APPROVED",
                "to_state": "PAUSED_CREATE_APPROVED",
                "worker_claim_hash": worker_hash,
                "error_code": None if claimed else "provider_validation_failed",
                "occurred_at": approved_at,
            }
        )
    ledger._records[record.deployment_id] = replace(
        record,
        state=DeploymentState.PAUSED_CREATE_APPROVED,
        version=103,
        error_code="provider_validation_failed",
        updated_at=approved_at,
    )
    ledger.outbox_state = "PENDING"

    response = admin_client.get(READINESS, headers=_headers())

    assert response.status_code == 200
    assert response.json()["events"]["count"] == 103
    assert response.json()["events"]["first_version"] == 4
    assert len(response.json()["events"]["items"]) == 100
    assert ledger.event_limits[-1] == 100


@pytest.mark.parametrize(
    "unsafe",
    [
        {"provider": "google"},
        {"account_id": "123"},
        {"developer_token": "secret"},
        {"approve": True},
        {"create": True},
        {"activate": True},
        {"spend": True},
    ],
)
def test_bootstrap_rejects_every_nonempty_body_without_database_change(
    admin_client, ledger, unsafe
):
    response = admin_client.post(DRAFT, headers=_headers(), json=unsafe)

    assert response.status_code == 400
    assert ledger._records == {}
    assert ledger.events == []


def test_post_strictly_server_validates_once_and_replay_is_safe(admin_client, ledger):
    draft = admin_client.post(DRAFT, headers=_headers(), json={}).json()
    body = _request(type("Record", (), draft))

    first = admin_client.post(VALIDATE, headers=_headers(), json=body)
    replay = admin_client.post(VALIDATE, headers=_headers(), json=body)

    assert first.status_code == replay.status_code == 200
    assert first.json()["state"] == replay.json()["state"] == "SERVER_VALIDATED"
    assert first.json()["version"] == replay.json()["version"] == 2
    assert first.json()["actions"] == {"server_validation": False}
    assert [event["event_type"] for event in ledger.events] == [
        "INTERNAL_DRAFT_CREATED",
        "SERVER_VALIDATED",
    ]

    different_key = admin_client.post(
        VALIDATE,
        headers=_headers(),
        json={**body, "idempotency_key": "different-validation-12345678"},
    )
    assert different_key.status_code == 409


@pytest.mark.parametrize(
    "extra",
    [
        {"contract": CONTRACT},
        {"customer_id": "123"},
        {"provider": "google"},
        {"account_id": "123"},
        {"developer_token": "secret"},
    ],
)
def test_post_rejects_contract_provider_account_and_extra_inputs_without_change(
    admin_client, ledger, extra
):
    record = DraftReviewControlPlane(ledger, StaticContractSource(CONTRACT)).ensure_internal_draft()

    response = admin_client.post(VALIDATE, headers=_headers(), json={**_request(record), **extra})

    # The application-level validation handler intentionally normalizes 422 to 400.
    assert response.status_code == 400
    assert ledger.get(record.deployment_id).state is DeploymentState.INTERNAL_DRAFT
    assert len(ledger.events) == 1


def test_stale_version_or_wrong_deployment_conflicts_without_change(admin_client, ledger):
    record = DraftReviewControlPlane(ledger, StaticContractSource(CONTRACT)).ensure_internal_draft()

    stale = admin_client.post(
        VALIDATE, headers=_headers(), json=_request(record, expected_version=99)
    )
    wrong = admin_client.post(
        VALIDATE,
        headers=_headers(),
        json=_request(record, deployment_id=f"wrong--{'f' * 64}"),
    )

    assert stale.status_code == wrong.status_code == 409
    assert ledger.get(record.deployment_id).state is DeploymentState.INTERNAL_DRAFT
    assert len(ledger.events) == 1


def test_invalid_checked_in_contract_makes_no_database_change(admin_client, ledger, monkeypatch):
    import google_ads_admin.routes as routes

    monkeypatch.setattr(
        routes,
        "load_checked_in_contract",
        lambda: (_ for _ in ()).throw(ValueError("raw invalid contract detail")),
    )
    response = admin_client.post(
        VALIDATE,
        headers=_headers(),
        json={
            "deployment_id": f"draft--{'a' * 64}",
            "expected_version": 1,
            "idempotency_key": "offline-validation-12345678",
        },
    )

    assert response.status_code == 503
    assert "raw invalid contract detail" not in response.text
    assert ledger._records == {}
    assert ledger.events == []


def test_post_sanitizes_storage_failures(admin_client, ledger):
    record = DraftReviewControlPlane(ledger, StaticContractSource(CONTRACT)).ensure_internal_draft()
    ledger.fail_next_write()
    response = admin_client.post(VALIDATE, headers=_headers(), json=_request(record))

    assert response.status_code == 503
    assert response.json()["message"] == "Paid Search offline server validation is unavailable."
    assert "ledger_write" not in response.text


def test_post_is_admin_and_csrf_protected_for_cookie_sessions(monkeypatch, ledger):
    import google_ads_admin.routes as routes
    import main

    monkeypatch.setattr(routes, "get_authority_ledger", lambda: ledger)
    monkeypatch.setattr(main, "_verify_admin_token", lambda _token: True)
    monkeypatch.setattr(main, "_admin_token_from_request", lambda _request: "cookie-token")
    client = TestClient(main.app, raise_server_exceptions=False)
    client.cookies.set("tho_admin_token", "cookie-token")
    draft = DraftReviewControlPlane(ledger, StaticContractSource(CONTRACT)).ensure_internal_draft()

    bootstrap_denied = client.post(DRAFT)
    denied = client.post(VALIDATE, json=_request(draft))
    client.cookies.set("tho_csrf_token", "csrf-value")
    allowed = client.post(VALIDATE, headers={"X-CSRF-Token": "csrf-value"}, json=_request(draft))

    assert bootstrap_denied.status_code == denied.status_code == 403
    assert allowed.status_code == 200


@pytest.mark.parametrize(
    ("state", "version", "duplicate_events"),
    [
        (DeploymentState.INTERNAL_DRAFT, 999, False),
        (DeploymentState.SERVER_VALIDATED, 1, False),
        (DeploymentState.INTERNAL_DRAFT, 1, True),
    ],
)
def test_projection_rejects_impossible_state_version_event_combinations(
    ledger, state, version, duplicate_events
):
    record = DraftReviewControlPlane(ledger, StaticContractSource(CONTRACT)).ensure_internal_draft()
    record = replace(record, state=state, version=version)
    evidence = ledger.list_events(record.deployment_id)
    if duplicate_events:
        evidence *= 2

    with pytest.raises(ValueError, match="durable|authority event"):
        build_deployment_readiness(record, evidence)


def test_route_has_no_provider_job_or_outbound_network_imports():
    forbidden = ("google.ads", "httpx", "requests", "subprocess", "urllib")
    source = (ROOT / "google_ads_admin/routes.py").read_text(encoding="utf-8")
    assert "WorkerInvoker" not in source
    assert "PausedCreate" not in source
    tree = ast.parse(source)
    imports = [
        alias.name
        for node in ast.walk(tree)
        for alias in (
            node.names
            if isinstance(node, ast.Import)
            else [type("Alias", (), {"name": node.module})]
            if isinstance(node, ast.ImportFrom) and node.module
            else []
        )
    ]
    assert not any(
        name == blocked or name.startswith(f"{blocked}.")
        for name in imports
        for blocked in forbidden
    )
