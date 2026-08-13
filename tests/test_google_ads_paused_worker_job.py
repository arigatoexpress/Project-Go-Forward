from __future__ import annotations

import inspect
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.google_ads_paused_worker import (
    DeploymentState,
    DraftReviewControlPlane,
    InMemoryAuthorityLedger,
    PausedCreateApproval,
    PausedCreateControlPlane,
    ProviderPausedDeployment,
    StaticContractSource,
    deployment_id,
)
from scripts.google_ads_paused_worker_job import main, run_paused_create_job

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "config" / "google_ads_launch_draft.json").read_text())
SOURCE_REVISION = "a" * 40


class _Provider:
    def __init__(self):
        self.calls = []

    def validate(self, request):
        self.calls.append(("validate", request))

    def find_by_contract_label(self, label):
        self.calls.append(("find", label))
        return ProviderPausedDeployment(
            contract_hash="sha256:" + deployment_id(CONTRACT).rsplit("--", 1)[1],
            campaign_resource_name="customers/1234567890/campaigns/987654321",
            status="PAUSED",
        )

    def create_paused(self, request):
        raise AssertionError("existing immutable readback must prevent create")

    @property
    def last_failure(self):
        return None


def _ledger(state=DeploymentState.PAUSED_CREATE_APPROVED):
    ledger = InMemoryAuthorityLedger()
    source = StaticContractSource(CONTRACT)
    draft = DraftReviewControlPlane(ledger, source).ensure_internal_draft()
    validated = DraftReviewControlPlane(ledger, source).server_validate(draft.deployment_id)
    if state is DeploymentState.SERVER_VALIDATED:
        return ledger
    approval = PausedCreateApproval.for_record(validated)
    PausedCreateControlPlane(
        ledger,
        SimpleNamespace(invoke=lambda _deployment_id: None),
    ).approve_paused_create(approval)
    ledger.get_paused_create_outbox = lambda _deployment_id: SimpleNamespace(
        state="DISPATCHING",
        dispatcher_claim_hash="sha256:" + "d" * 64,
    )
    ledger.reconcile_terminal_paused_create_outbox = lambda _deployment_id: "DISPATCHED"
    ledger.record_paused_create_worker_failure = lambda _deployment_id, _claim_hash: "PENDING"
    return ledger


def _environ():
    return {
        "GOOGLE_ADS_CUSTOMER_ID": "1234567890",
        "GOOGLE_ADS_DEVELOPER_TOKEN": "fake-managed-secret",
        "GOOGLE_ADS_LOGIN_CUSTOMER_ID": "9999999999",
        "APP_VERSION": SOURCE_REVISION,
    }


def test_fixed_job_consumes_only_existing_approval_and_server_owned_contract():
    provider = _Provider()
    captured = []

    result = run_paused_create_job(
        ledger=_ledger(),
        contract_loader=lambda: CONTRACT,
        provider_factory=lambda **kwargs: captured.append(set(kwargs)) or provider,
        environ=_environ(),
    )

    assert result.state is DeploymentState.PAUSED_CREATED
    assert result.reconciled is True
    assert [call[0] for call in provider.calls] == ["validate", "find"]
    assert captured == [
        {
            "customer_id",
            "developer_token",
            "login_customer_id",
            "contract",
        }
    ]


def test_unapproved_authority_is_inert_and_never_touches_provider():
    called = []
    result = run_paused_create_job(
        ledger=_ledger(DeploymentState.SERVER_VALIDATED),
        contract_loader=lambda: CONTRACT,
        provider_factory=lambda **_kwargs: called.append(True),
        environ=_environ(),
    )

    assert result.state is DeploymentState.SERVER_VALIDATED
    assert result.error_code == "worker_not_approved"
    assert called == []


def test_direct_worker_invocation_is_inert_while_outbox_is_pending():
    ledger = _ledger()
    ledger.get_paused_create_outbox = lambda _deployment_id: SimpleNamespace(
        state="PENDING",
        dispatcher_claim_hash=None,
    )
    called = []

    result = run_paused_create_job(
        ledger=ledger,
        contract_loader=lambda: CONTRACT,
        provider_factory=lambda **_kwargs: called.append(True),
        environ=_environ(),
    )

    assert result.state is DeploymentState.PAUSED_CREATE_APPROVED
    assert result.error_code == "worker_not_dispatched"
    assert called == []


def test_provider_failure_rearms_accepted_dispatch_without_marking_success():
    ledger = _ledger()
    settled = []
    ledger.record_paused_create_worker_failure = (
        lambda target, claim_hash: settled.append((target, claim_hash)) or "PENDING"
    )

    class FailingProvider(_Provider):
        def validate(self, request):
            self.calls.append(("validate", request))
            raise RuntimeError("raw provider failure")

    result = run_paused_create_job(
        ledger=ledger,
        contract_loader=lambda: CONTRACT,
        provider_factory=lambda **_kwargs: FailingProvider(),
        environ=_environ(),
    )

    assert result.state is DeploymentState.PAUSED_CREATE_APPROVED
    assert result.error_code == "provider_validation_failed"
    assert settled == [(deployment_id(CONTRACT), "sha256:" + "d" * 64)]


@pytest.mark.parametrize(
    "changed",
    [
        {},
        {"APP_VERSION": "latest"},
        {"GOOGLE_ADS_CUSTOMER_ID": "invalid"},
        {"GOOGLE_ADS_LOGIN_CUSTOMER_ID": "invalid"},
        {"GOOGLE_ADS_DEVELOPER_TOKEN": ""},
    ],
)
def test_invalid_fixed_runtime_configuration_fails_before_provider(changed):
    environment = _environ()
    if changed:
        environment.update(changed)
    else:
        environment.pop("GOOGLE_ADS_CUSTOMER_ID")
    called = []

    with pytest.raises(ValueError, match="invalid_configuration"):
        run_paused_create_job(
            ledger=_ledger(),
            contract_loader=lambda: CONTRACT,
            provider_factory=lambda **_kwargs: called.append(True),
            environ=environment,
        )

    assert called == []


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda authority: replace(authority, contract_label="tho-contract-000000000000"),
        lambda authority: replace(authority, version=0),
    ],
)
def test_job_rejects_corrupt_authority_identity_before_provider(corrupt):
    valid_ledger = _ledger()
    authority = valid_ledger.get(deployment_id(CONTRACT))
    called = []

    with pytest.raises(ValueError, match="invalid_configuration"):
        run_paused_create_job(
            ledger=SimpleNamespace(get=lambda _deployment_id: corrupt(authority)),
            contract_loader=lambda: CONTRACT,
            provider_factory=lambda **_kwargs: called.append(True),
            environ=_environ(),
        )

    assert called == []


def test_cli_rejects_every_argument_before_loading_production_dependencies(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(
        "scripts.google_ads_paused_worker_job._run_production_job",
        lambda: called.append(True),
    )

    assert main(["--deployment-id", deployment_id(CONTRACT)]) == 2
    output = json.loads(capsys.readouterr().out)

    assert called == []
    assert output == {
        "activation_authorized": False,
        "failure": "fixed_command_required",
        "paused_create_completed": False,
        "ready_to_spend": False,
        "spend_enabled": False,
    }


def test_cli_success_and_failure_outputs_are_strictly_sanitized(monkeypatch, capsys):
    successful = SimpleNamespace(
        deployment_id=deployment_id(CONTRACT),
        state=DeploymentState.PAUSED_CREATED,
        error_code=None,
        provider_reference_hash="sha256:" + "b" * 64,
        existing=False,
        reconciled=True,
    )
    monkeypatch.setattr(
        "scripts.google_ads_paused_worker_job._run_production_job",
        lambda: (successful, None),
    )
    assert main([]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "activation_authorized": False,
        "deployment_id": deployment_id(CONTRACT),
        "error_code": None,
        "existing": False,
        "paused_create_completed": True,
        "provider_failure": None,
        "provider_reference_hash": "sha256:" + "b" * 64,
        "ready_to_spend": False,
        "reconciled": True,
        "spend_enabled": False,
        "state": "PAUSED_CREATED",
    }

    monkeypatch.setattr(
        "scripts.google_ads_paused_worker_job._run_production_job",
        lambda: (_ for _ in ()).throw(
            RuntimeError("customer=1234567890 token=do-not-leak request-id=raw")
        ),
    )
    assert main([]) == 1
    serialized = capsys.readouterr().out
    assert json.loads(serialized) == {
        "activation_authorized": False,
        "failure": "paused_create_job_failed",
        "paused_create_completed": False,
        "ready_to_spend": False,
        "spend_enabled": False,
    }
    for forbidden in ("1234567890", "do-not-leak", "request-id"):
        assert forbidden not in serialized


def test_job_has_no_storefront_dispatch_approval_activation_or_feature_enable_surface():
    import scripts.google_ads_paused_worker_job as job

    source = inspect.getsource(job)
    assert list(inspect.signature(main).parameters) == ["argv"]
    for forbidden in (
        "fastapi",
        "APIRouter",
        "run.jobs.run",
        "runWithOverrides",
        "PausedCreateApproval",
        "PausedCreateControlPlane",
        "transition(",
        "GOOGLE_ADS_ONE_CLICK_ENABLED",
        '"ENABLED"',
        "activate",
        "spend_authorized",
    ):
        assert forbidden not in source


def test_stale_one_click_handoff_is_explicitly_non_authoritative():
    handoff = (
        ROOT / "docs" / "handoffs" / "2026-07-28-claude-one-click-google-ads-deployment.md"
    ).read_text()

    banner = handoff.split("## 1. Mission", 1)[0]
    assert "SUPERSEDED" in banner
    assert "docs/runbooks/google-growth-activation.md" in banner
    assert "Storefront\n> `run.jobs.run` is forbidden" in banner
    assert "automatic enablement is forbidden" in banner
