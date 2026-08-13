"""Golden boundaries for owner-approved, PAUSED-only Google Ads outbox work."""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from auth.google_ads_step_up import (
    StepUpCaps,
    StepUpContext,
    StepUpNonce,
    build_evidence_envelope,
    context_digest,
    email_hash,
    hash_value,
    issue_proof_reference,
    verify_proof_reference,
)
from auth.session import PASSKEY_COOKIE_NAME, SessionManager
from database.models import GoogleAdsPausedCreateOutboxRecord
from google_ads_admin import approval_routes
from google_ads_admin.approval import PausedCreateApprovalRuntime
from google_ads_admin.dispatcher import DispatchError, FixedCloudRunJobDispatcher
from google_ads_admin.status import load_checked_in_contract
from scripts.google_ads_access_evidence import (
    AccessCheckKey,
    AccessEvidenceStatus,
    build_access_evidence,
)
from scripts.google_ads_launch_draft import contract_sha256
from scripts.google_ads_paused_dispatcher_job import main as dispatcher_main
from scripts.google_ads_paused_dispatcher_job import run_dispatcher_job
from scripts.google_ads_paused_worker import InvalidStateTransition

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
CSRF = "owner-approval-csrf"


def _identity():
    contract = load_checked_in_contract()
    digest = contract_sha256(contract)
    return contract, f"{contract['deployment']['key']}--{digest}", f"sha256:{digest}"


def _evidence():
    _contract, deployment_id, _contract_hash = _identity()
    return build_access_evidence(
        deployment_id=deployment_id,
        check_key=AccessCheckKey.GOOGLE_ADS_ACCOUNT_ACCESS_AND_USD_GREEN,
        status=AccessEvidenceStatus.PASSED,
        observed_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        source_revision="a" * 40,
        now=NOW,
    )


def _envelope():
    contract, deployment_id, contract_hash = _identity()
    evidence = _evidence()
    caps = StepUpCaps(
        average_daily_usd=contract["campaign"]["budget"]["average_daily_usd"],
        max_single_day_charge_usd=contract["campaign"]["budget"]["max_single_day_charge_usd"],
        monthly_charge_limit_usd=contract["campaign"]["budget"]["monthly_charge_limit_usd"],
        max_cpc_usd=contract["campaign"]["bidding"]["max_cpc_usd"],
    )
    context = StepUpContext(
        deployment_id=deployment_id,
        contract_hash=contract_hash,
        caps=caps,
        evidence_digest=evidence.evidence_digest,
    )
    nonce = StepUpNonce(
        nonce_hash=hash_value("approval-nonce"),
        context_digest=context_digest(context),
        owner_email_hash=email_hash("aristotlespec@gmail.com"),
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )
    return build_evidence_envelope(
        nonce=nonce,
        context=context,
        credential_id_hash=hash_value(b"owner-key"),
        verified_at=NOW,
    )


def test_signed_proof_reference_is_purpose_bound_sanitized_and_tamper_evident():
    manager = SessionManager(secret_key="approval-reference-secret")
    envelope = _envelope()

    reference = issue_proof_reference(manager, envelope)
    verified = verify_proof_reference(manager, reference)

    assert verified.proof_id == envelope.evidence_id
    assert verified.nonce_hash == envelope.nonce_hash
    assert verified.deployment_id == envelope.deployment_id
    assert verified.access_evidence_id == envelope.evidence_digest
    assert verified.purpose == "PAUSED_CREATE"
    assert "aristotlespec@gmail.com" not in reference
    assert "owner-key" not in reference
    assert verify_proof_reference(manager, f"{reference}x") is None


def test_runtime_gate_is_false_without_every_explicit_current_revision_and_iam_gate(
    monkeypatch,
):
    names = (
        "THO_GOOGLE_ADS_PAUSED_CREATE_APPROVAL_ENABLED",
        "THO_GOOGLE_ADS_PAUSED_CREATE_CLOUD_READINESS_VERIFIED",
        "THO_GOOGLE_ADS_PAUSED_CREATE_IAM_VERIFIED",
        "THO_GOOGLE_ADS_PAUSED_CREATE_READINESS_REVISION",
        "THO_GOOGLE_ADS_PAUSED_CREATE_PROJECT",
        "THO_GOOGLE_ADS_PAUSED_CREATE_REGION",
        "THO_GOOGLE_ADS_PAUSED_CREATE_JOB",
        "APP_VERSION",
    )
    for name in names:
        monkeypatch.delenv(name, raising=False)
    assert PausedCreateApprovalRuntime.from_env().approval_available is False

    monkeypatch.setenv("THO_GOOGLE_ADS_PAUSED_CREATE_APPROVAL_ENABLED", "true")
    monkeypatch.setenv("THO_GOOGLE_ADS_PAUSED_CREATE_CLOUD_READINESS_VERIFIED", "true")
    monkeypatch.setenv("THO_GOOGLE_ADS_PAUSED_CREATE_IAM_VERIFIED", "true")
    monkeypatch.setenv("THO_GOOGLE_ADS_PAUSED_CREATE_READINESS_REVISION", "a" * 40)
    monkeypatch.setenv("APP_VERSION", "a" * 40)
    monkeypatch.setenv("THO_GOOGLE_ADS_PAUSED_CREATE_PROJECT", "tho-ai-agent")
    monkeypatch.setenv("THO_GOOGLE_ADS_PAUSED_CREATE_REGION", "us-central1")
    monkeypatch.setenv("THO_GOOGLE_ADS_PAUSED_CREATE_JOB", "google-growth-paused-create")
    assert PausedCreateApprovalRuntime.from_env().approval_available is True

    monkeypatch.setenv("THO_GOOGLE_ADS_PAUSED_CREATE_IAM_VERIFIED", "false")
    assert PausedCreateApprovalRuntime.from_env().approval_available is False
    monkeypatch.setenv("THO_GOOGLE_ADS_PAUSED_CREATE_IAM_VERIFIED", "true")
    monkeypatch.setenv("THO_GOOGLE_ADS_PAUSED_CREATE_READINESS_REVISION", "b" * 40)
    assert PausedCreateApprovalRuntime.from_env().approval_available is False


def test_outbox_schema_is_strict_sanitized_and_never_represents_activation_or_spend():
    _contract, deployment_id, contract_hash = _identity()
    envelope = _envelope()
    payload = {
        "schema_version": 1,
        "outbox_id": "paused-create",
        "deployment_id": deployment_id,
        "contract_hash": contract_hash,
        "approval_record_version": 3,
        "proof_id": envelope.evidence_id,
        "access_evidence_id": envelope.evidence_digest,
        "state": "PENDING",
        "attempt_count": 0,
        "dispatcher_claim_hash": None,
        "claim_expires_at": None,
        "error_code": None,
        "created_at": NOW,
        "updated_at": NOW,
        "dispatched_at": None,
    }
    record = GoogleAdsPausedCreateOutboxRecord.model_validate(payload)
    assert record.state == "PENDING"
    for forbidden in (
        "account_id",
        "customer_id",
        "provider_id",
        "raw_error",
        "activate",
        "publish",
        "spend",
        "caps",
    ):
        with pytest.raises(ValidationError):
            GoogleAdsPausedCreateOutboxRecord.model_validate({**payload, forbidden: True})


class _ApprovalLedger:
    def __init__(self):
        self.calls = []
        self.error = None

    def approve_paused_create_with_proof(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        if kwargs["expected_version"] != 2:
            self.calls.pop()
            raise InvalidStateTransition("stale deployment version")
        return {
            "deployment_id": kwargs["deployment_id"],
            "contract_hash": kwargs["contract_hash"],
            "state": "PAUSED_CREATE_APPROVED",
            "version": 3,
            "outbox_state": "PENDING",
            "replayed": len(self.calls) > 1,
        }


def _approval_env(monkeypatch):
    values = {
        "THO_GOOGLE_ADS_PAUSED_CREATE_APPROVAL_ENABLED": "true",
        "THO_GOOGLE_ADS_PAUSED_CREATE_CLOUD_READINESS_VERIFIED": "true",
        "THO_GOOGLE_ADS_PAUSED_CREATE_IAM_VERIFIED": "true",
        "THO_GOOGLE_ADS_PAUSED_CREATE_READINESS_REVISION": "a" * 40,
        "APP_VERSION": "a" * 40,
        "THO_GOOGLE_ADS_PAUSED_CREATE_PROJECT": "tho-ai-agent",
        "THO_GOOGLE_ADS_PAUSED_CREATE_REGION": "us-central1",
        "THO_GOOGLE_ADS_PAUSED_CREATE_JOB": "google-growth-paused-create",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def approval_client(monkeypatch):
    _approval_env(monkeypatch)
    monkeypatch.setenv(
        "THO_GOOGLE_ADS_OWNER_EMAILS",
        "aristotlespec@gmail.com,aribspector@gmail.com",
    )
    monkeypatch.setenv(
        "THO_PASSKEY_OWNER_EMAILS",
        "aristotlespec@gmail.com,aribspector@gmail.com",
    )
    manager = SessionManager(secret_key="approval-reference-secret")
    ledger = _ApprovalLedger()
    app = FastAPI()
    app.dependency_overrides[approval_routes.get_session_manager] = lambda: manager
    app.dependency_overrides[approval_routes.get_approval_ledger] = lambda: ledger
    app.include_router(approval_routes.router)
    client = TestClient(app, base_url="https://www.texashomeoutlet.com")
    return client, manager, ledger


def _owner(client, manager, *, email="aristotlespec@gmail.com", method="passkey"):
    client.cookies.set(
        PASSKEY_COOKIE_NAME,
        manager.issue_session("admin", email=email, auth_method=method),
    )
    client.cookies.set("tho_csrf_token", CSRF)


def _approval_body(manager, **overrides):
    envelope = _envelope()
    body = {
        "deployment_id": envelope.deployment_id,
        "expected_version": 2,
        "proof_reference": issue_proof_reference(manager, envelope),
        "proof_id": envelope.evidence_id,
        "access_evidence_id": envelope.evidence_digest,
    }
    body.update(overrides)
    return body


def test_approval_route_accepts_only_exact_owner_uv_reference_and_strict_ids(approval_client):
    client, manager, ledger = approval_client
    _owner(client, manager)
    body = _approval_body(manager)

    response = client.post(
        "/api/admin/google-ads/paused-create-approval",
        headers={"X-CSRF-Token": CSRF},
        json=body,
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "PAUSED_CREATE_APPROVED"
    assert response.json()["outbox_state"] == "PENDING"
    assert response.json()["spend_enabled"] is False
    assert ledger.calls[0]["expected_version"] == 2
    assert ledger.calls[0]["proof"].proof_id == body["proof_id"]

    for forbidden in ("caps", "contract", "account_id", "provider", "create", "activate", "spend"):
        rejected = client.post(
            "/api/admin/google-ads/paused-create-approval",
            headers={"X-CSRF-Token": CSRF},
            json={**body, forbidden: True},
        )
        assert rejected.status_code == 422
    assert len(ledger.calls) == 1


def test_approval_route_rejects_pin_bearer_staff_stale_or_mismatched_proof(approval_client):
    client, manager, ledger = approval_client
    body = _approval_body(manager)

    _owner(client, manager, method="pin")
    assert (
        client.post(
            "/api/admin/google-ads/paused-create-approval",
            headers={"X-CSRF-Token": CSRF},
            json=body,
        ).status_code
        == 403
    )
    _owner(client, manager, email="aribspector@gmail.com")
    assert (
        client.post(
            "/api/admin/google-ads/paused-create-approval",
            headers={"X-CSRF-Token": CSRF},
            json=body,
        ).status_code
        == 409
    )
    _owner(client, manager, email="mark@texashomeoutlet.com")
    assert (
        client.post(
            "/api/admin/google-ads/paused-create-approval",
            headers={"X-CSRF-Token": CSRF},
            json=body,
        ).status_code
        == 403
    )
    client.cookies.clear()
    assert (
        client.post(
            "/api/admin/google-ads/paused-create-approval",
            headers={"Authorization": "Bearer shared", "X-CSRF-Token": CSRF},
            json=body,
        ).status_code
        == 401
    )

    _owner(client, manager)
    for mismatch in (
        {"expected_version": 99},
        {"proof_id": f"sha256:{'f' * 64}"},
        {"access_evidence_id": f"sha256:{'e' * 64}"},
        {"deployment_id": f"other--{'d' * 64}"},
        {"proof_reference": body["proof_reference"] + "x"},
    ):
        rejected = client.post(
            "/api/admin/google-ads/paused-create-approval",
            headers={"X-CSRF-Token": CSRF},
            json={**body, **mismatch},
        )
        assert rejected.status_code in {409, 422}
    assert ledger.calls == []


def test_approval_fails_503_before_ledger_when_feature_cloud_or_iam_config_missing(
    approval_client, monkeypatch
):
    client, manager, ledger = approval_client
    _owner(client, manager)
    body = _approval_body(manager)

    for name in (
        "THO_GOOGLE_ADS_PAUSED_CREATE_APPROVAL_ENABLED",
        "THO_GOOGLE_ADS_PAUSED_CREATE_CLOUD_READINESS_VERIFIED",
        "THO_GOOGLE_ADS_PAUSED_CREATE_IAM_VERIFIED",
        "THO_GOOGLE_ADS_PAUSED_CREATE_JOB",
    ):
        monkeypatch.delenv(name, raising=False)
        response = client.post(
            "/api/admin/google-ads/paused-create-approval",
            headers={"X-CSRF-Token": CSRF},
            json=body,
        )
        assert response.status_code == 503
        _approval_env(monkeypatch)
    assert ledger.calls == []


def test_fixed_dispatcher_uses_official_v2_run_endpoint_and_empty_body_only():
    calls = []

    class Transport:
        def post(self, url, *, json, timeout):
            calls.append((url, json, timeout))
            return type("Response", (), {"ok": True})()

    dispatcher = FixedCloudRunJobDispatcher(
        project="tho-ai-agent",
        region="us-central1",
        job="google-growth-paused-create",
        transport=Transport(),
    )
    dispatcher.invoke()

    assert calls == [
        (
            "https://run.googleapis.com/v2/projects/tho-ai-agent/locations/us-central1/jobs/google-growth-paused-create:run",
            {},
            10,
        )
    ]


def test_fixed_dispatcher_distinguishes_definite_rejection_from_unknown_acceptance():
    class Rejected:
        def post(self, *_args, **_kwargs):
            return type("Response", (), {"ok": False, "status_code": 403})()

    class TimedOut:
        def post(self, *_args, **_kwargs):
            raise TimeoutError("raw transport detail")

    class RequestTimedOut:
        def post(self, *_args, **_kwargs):
            return type("Response", (), {"ok": False, "status_code": 408})()

    for transport, acceptance_unknown in (
        (Rejected(), False),
        (RequestTimedOut(), True),
        (TimedOut(), True),
    ):
        dispatcher = FixedCloudRunJobDispatcher(
            project="tho-ai-agent",
            region="us-central1",
            job="google-growth-paused-create",
            transport=transport,
        )
        with pytest.raises(DispatchError) as raised:
            dispatcher.invoke()
        assert raised.value.acceptance_unknown is acceptance_unknown
        assert "raw transport detail" not in str(raised.value)


def test_dispatcher_failure_leaves_outbox_pending_and_success_settles_once():
    contract, deployment_id, _contract_hash = _identity()

    class Ledger:
        def __init__(self):
            self.state = "PENDING"
            self.claimed = None

        def claim_paused_create_outbox(self, target, claimant):
            assert target == deployment_id
            if self.state != "PENDING":
                return False
            self.state = "DISPATCHING"
            self.claimed = claimant
            return True

        def release_paused_create_outbox(self, target, claimant):
            assert (target, claimant) == (deployment_id, self.claimed)
            self.state = "PENDING"

        def get_paused_create_outbox(self, target):
            assert target == deployment_id
            return type("Outbox", (), {"state": self.state})()

        def reconcile_terminal_paused_create_outbox(self, target):
            assert target == deployment_id
            return self.state

    class Failing:
        def invoke(self):
            raise DispatchError(acceptance_unknown=False)

    ledger = Ledger()
    failed = run_dispatcher_job(
        ledger=ledger,
        dispatcher=Failing(),
        contract=contract,
        claimant_factory=lambda: "dispatcher-one",
    )
    assert failed["outbox_state"] == "PENDING"
    assert failed["error_code"] == "job_invocation_failed"
    assert "raw provider-looking detail" not in json.dumps(failed)

    invoked = []
    completed = run_dispatcher_job(
        ledger=ledger,
        dispatcher=type("Success", (), {"invoke": lambda self: invoked.append(True)})(),
        contract=contract,
        claimant_factory=lambda: "dispatcher-two",
    )
    assert completed["outbox_state"] == "DISPATCHING"
    assert completed["dispatch_accepted"] is True
    assert completed["dispatch_succeeded"] is False
    assert invoked == [True]
    ledger.state = "DISPATCHED"  # worker-only terminal reconciliation
    replay = run_dispatcher_job(
        ledger=ledger,
        dispatcher=type("Never", (), {"invoke": lambda self: invoked.append(False)})(),
        contract=contract,
        claimant_factory=lambda: "dispatcher-three",
    )
    assert replay["dispatch_attempted"] is False
    assert replay["dispatch_succeeded"] is True
    assert invoked == [True]


def test_dispatcher_retry_heals_terminal_authority_after_post_invocation_crash():
    contract, deployment_id, _contract_hash = _identity()

    class Ledger:
        def claim_paused_create_outbox(self, target, _claimant):
            assert target == deployment_id
            return False

        def reconcile_terminal_paused_create_outbox(self, target):
            assert target == deployment_id
            return "DISPATCHED"

        def get_paused_create_outbox(self, _target):
            raise AssertionError("healed terminal state must be returned directly")

    result = run_dispatcher_job(
        ledger=Ledger(),
        dispatcher=type(
            "Never",
            (),
            {"invoke": lambda _self: (_ for _ in ()).throw(AssertionError("no redispatch"))},
        )(),
        contract=contract,
        claimant_factory=lambda: "retry-after-crash",
    )

    assert result["outbox_state"] == "DISPATCHED"
    assert result["dispatch_attempted"] is False
    assert result["dispatch_succeeded"] is True


def test_dispatcher_error_after_worker_completion_settles_terminal_without_release():
    contract, deployment_id, _contract_hash = _identity()

    class Ledger:
        def claim_paused_create_outbox(self, target, _claimant):
            assert target == deployment_id
            return True

        def reconcile_terminal_paused_create_outbox(self, target):
            assert target == deployment_id
            return "DISPATCHED"

        def release_paused_create_outbox(self, _target, _claimant):
            raise AssertionError("terminal outbox must never be released")

    result = run_dispatcher_job(
        ledger=Ledger(),
        dispatcher=type(
            "AcceptedThenErrored",
            (),
            {"invoke": lambda _self: (_ for _ in ()).throw(DispatchError(acceptance_unknown=True))},
        )(),
        contract=contract,
        claimant_factory=lambda: "dispatcher-ambiguous",
    )

    assert result["outbox_state"] == "DISPATCHED"
    assert result["dispatch_attempted"] is True
    assert result["dispatch_succeeded"] is True
    assert "error_code" not in result


def test_dispatcher_ambiguous_acceptance_stays_leased_until_worker_or_expiry():
    contract, deployment_id, _contract_hash = _identity()

    class Ledger:
        def claim_paused_create_outbox(self, target, _claimant):
            assert target == deployment_id
            return True

        def reconcile_terminal_paused_create_outbox(self, target):
            assert target == deployment_id
            return "DISPATCHING"

        def release_paused_create_outbox(self, _target, _claimant):
            raise AssertionError("ambiguous acceptance must retain the lease")

    result = run_dispatcher_job(
        ledger=Ledger(),
        dispatcher=type(
            "Ambiguous",
            (),
            {"invoke": lambda _self: (_ for _ in ()).throw(DispatchError(acceptance_unknown=True))},
        )(),
        contract=contract,
        claimant_factory=lambda: "dispatcher-ambiguous",
    )

    assert result["outbox_state"] == "DISPATCHING"
    assert result["dispatch_succeeded"] is False
    assert result["error_code"] == "job_invocation_acceptance_unknown"


def test_dispatcher_cli_rejects_every_argument_before_production(monkeypatch, capsys):
    import scripts.google_ads_paused_dispatcher_job as job

    monkeypatch.setattr(
        job,
        "_run_production_job",
        lambda: (_ for _ in ()).throw(AssertionError("must stay inert")),
    )
    assert dispatcher_main(["--deployment", "unsafe"]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "configured": False,
        "dispatch_attempted": False,
        "dispatch_succeeded": False,
        "error_code": "runtime_overrides_rejected",
        "schema_version": 1,
        "spend_enabled": False,
    }


def test_storefront_routes_never_import_dispatcher_provider_sdk_or_run_job():
    route_path = ROOT / "google_ads_admin" / "approval_routes.py"
    source = route_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        for alias in (
            node.names
            if isinstance(node, ast.Import)
            else [type("Alias", (), {"name": node.module})]
            if isinstance(node, ast.ImportFrom) and node.module
            else []
        )
    }
    assert "google_ads_admin.dispatcher" not in imports
    assert not any(name.startswith("scripts.google_ads_paused_provider") for name in imports)
    for forbidden in ("run.jobs.run", ":run", "GoogleAdsV25PausedProvider", "create_paused"):
        assert forbidden not in source


def test_dispatcher_entrypoint_is_fixed_zero_arg_and_not_mounted_on_storefront():
    source = (ROOT / "scripts" / "google_ads_paused_dispatcher_job.py").read_text(encoding="utf-8")
    assert "def main(argv" in source
    assert "if argv:" in source
    assert "app.include_router" not in source
    assert "google_ads_paused_dispatcher_job" not in (ROOT / "main.py").read_text(encoding="utf-8")


def test_runbook_and_example_config_keep_approval_dispatch_and_spend_separate():
    runbook = (ROOT / "docs" / "runbooks" / "google-growth-activation.md").read_text()
    example = (ROOT / ".env.example").read_text()
    assert "admin approval request also never invokes a job" in runbook
    assert "a later,\nseparate Ari gate" in runbook
    assert "No activation/publish/spend state or control exists" in runbook
    assert "THO_GOOGLE_ADS_PAUSED_CREATE_APPROVAL_ENABLED=false" in example
    assert "THO_GOOGLE_ADS_PAUSED_CREATE_DISPATCH_ENABLED=false" in example
