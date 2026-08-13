from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from database.models import GoogleAdsAccessEvidenceRecord
from scripts.google_ads_access_evidence import (
    AccessCheckKey,
    AccessEvidenceStatus,
    InvalidAccessEvidence,
    build_access_evidence,
    evidence_payload,
    validate_access_evidence,
)
from scripts.google_ads_access_evidence_job import main, run_access_evidence_job
from scripts.google_ads_launch_draft import contract_sha256
from scripts.google_ads_paused_worker import deployment_id

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "config" / "google_ads_launch_draft.json").read_text())
NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
SOURCE_REVISION = "a" * 40


def _safe_probe_result(*, validated=True):
    return {
        "account_access_validated": validated,
        "failure": None if validated else "authentication_or_access_denied",
        "http_status": 200 if validated else 403,
        "live_probe_executed": True,
        "request_id_present": True,
        "ready_to_spend": False,
        "spend_enabled": False,
    }


def _record(version=4):
    return SimpleNamespace(
        deployment_id=deployment_id(CONTRACT),
        contract_hash=f"sha256:{contract_sha256(CONTRACT)}",
        version=version,
    )


class _Ledger:
    def __init__(self):
        self.record = _record()
        self.writes = []

    def get(self, requested_deployment_id):
        assert requested_deployment_id == self.record.deployment_id
        return self.record

    def record_access_evidence(self, evidence, *, expected_version):
        self.writes.append((evidence, expected_version))
        return evidence


def test_evidence_schema_is_an_exact_allowlist_and_rejects_raw_provider_fields():
    evidence = build_access_evidence(
        deployment_id=deployment_id(CONTRACT),
        check_key=AccessCheckKey.GOOGLE_ADS_ACCOUNT_ACCESS_GREEN,
        status=AccessEvidenceStatus.PASSED,
        observed_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        source_revision=SOURCE_REVISION,
        now=NOW,
    )
    payload = evidence_payload(evidence)

    assert set(payload) == {
        "deployment_id",
        "check_key",
        "status",
        "observed_at",
        "expires_at",
        "source_revision",
        "evidence_digest",
    }
    assert GoogleAdsAccessEvidenceRecord.model_validate(payload).model_dump() == payload

    with pytest.raises(ValidationError):
        GoogleAdsAccessEvidenceRecord.model_validate(
            {**payload, "expires_at": NOW + timedelta(hours=1)}
        )

    for forbidden in (
        "customer_id",
        "login_customer_id",
        "account_id",
        "developer_token",
        "access_token",
        "request_id",
        "provider_reference",
        "provider_error",
        "raw_error",
        "unknown",
    ):
        with pytest.raises(ValidationError):
            GoogleAdsAccessEvidenceRecord.model_validate({**payload, forbidden: "raw-do-not-store"})


def test_evidence_rejects_unknown_enums_future_expired_or_non_utc_timestamps_and_bad_digest():
    deployment = deployment_id(CONTRACT)

    for kwargs in (
        {"check_key": "unknown_check"},
        {"status": "UNKNOWN"},
        {"observed_at": NOW + timedelta(seconds=1)},
        {"expires_at": NOW},
        {"observed_at": NOW.replace(tzinfo=None)},
        {"source_revision": "not-a-revision"},
    ):
        values = {
            "deployment_id": deployment,
            "check_key": AccessCheckKey.GOOGLE_ADS_ACCOUNT_ACCESS_GREEN,
            "status": AccessEvidenceStatus.PASSED,
            "observed_at": NOW,
            "expires_at": NOW + timedelta(minutes=5),
            "source_revision": SOURCE_REVISION,
            "now": NOW,
            **kwargs,
        }
        with pytest.raises(InvalidAccessEvidence):
            build_access_evidence(**values)

    evidence = build_access_evidence(
        deployment_id=deployment,
        check_key=AccessCheckKey.GOOGLE_ADS_ACCOUNT_ACCESS_GREEN,
        status=AccessEvidenceStatus.PASSED,
        observed_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        source_revision=SOURCE_REVISION,
        now=NOW,
    )
    with pytest.raises(InvalidAccessEvidence):
        validate_access_evidence(
            evidence.__class__(**{**evidence.__dict__, "evidence_digest": f"sha256:{'b' * 64}"}),
            now=NOW,
        )


def test_fixed_job_records_only_sanitized_read_only_access_evidence_with_cas():
    ledger = _Ledger()
    observed = []

    def probe():
        observed.append("read-only-probe")
        return _safe_probe_result()

    evidence = run_access_evidence_job(
        ledger=ledger,
        contract_loader=lambda: CONTRACT,
        access_probe=probe,
        source_revision=SOURCE_REVISION,
        clock=lambda: NOW,
    )

    assert observed == ["read-only-probe"]
    assert evidence.status is AccessEvidenceStatus.PASSED
    assert ledger.writes == [(evidence, 4)]
    serialized = json.dumps(evidence_payload(evidence), default=str)
    for forbidden in (
        "1234567890",
        "9999999999",
        "developer-token-do-not-leak",
        "access-token-do-not-leak",
        "request-id-do-not-leak",
        "provider-error-do-not-leak",
    ):
        assert forbidden not in serialized


def test_failed_probe_records_only_failed_status_and_never_grants_spend():
    ledger = _Ledger()

    evidence = run_access_evidence_job(
        ledger=ledger,
        contract_loader=lambda: CONTRACT,
        access_probe=lambda: _safe_probe_result(validated=False),
        source_revision=SOURCE_REVISION,
        clock=lambda: NOW,
    )

    assert evidence.status is AccessEvidenceStatus.FAILED
    assert set(evidence_payload(evidence)) == {
        "deployment_id",
        "check_key",
        "status",
        "observed_at",
        "expires_at",
        "source_revision",
        "evidence_digest",
    }


def test_probe_result_with_unknown_or_raw_fields_is_rejected_without_a_ledger_write():
    ledger = _Ledger()
    unsafe = {**_safe_probe_result(), "customer_id": "1234567890"}

    with pytest.raises(InvalidAccessEvidence, match="probe_result_invalid"):
        run_access_evidence_job(
            ledger=ledger,
            contract_loader=lambda: CONTRACT,
            access_probe=lambda: unsafe,
            source_revision=SOURCE_REVISION,
            clock=lambda: NOW,
        )

    assert ledger.writes == []


def test_probe_result_rejects_inconsistent_allowlisted_status_without_a_write():
    ledger = _Ledger()
    inconsistent = {
        **_safe_probe_result(validated=False),
        "failure": "google_ads_unavailable",
        "http_status": 200,
    }

    with pytest.raises(InvalidAccessEvidence, match="probe_result_invalid"):
        run_access_evidence_job(
            ledger=ledger,
            contract_loader=lambda: CONTRACT,
            access_probe=lambda: inconsistent,
            source_revision=SOURCE_REVISION,
            clock=lambda: NOW,
        )

    assert ledger.writes == []


def test_provider_exception_records_only_allowlisted_error_status():
    ledger = _Ledger()

    evidence = run_access_evidence_job(
        ledger=ledger,
        contract_loader=lambda: CONTRACT,
        access_probe=lambda: (_ for _ in ()).throw(
            RuntimeError("customer=1234567890 request-id=do-not-leak")
        ),
        source_revision=SOURCE_REVISION,
        clock=lambda: NOW,
    )

    assert evidence.status is AccessEvidenceStatus.ERROR
    assert "1234567890" not in json.dumps(evidence_payload(evidence), default=str)
    assert "do-not-leak" not in json.dumps(evidence_payload(evidence), default=str)


def test_job_rejects_contract_or_revision_drift_before_calling_provider():
    ledger = _Ledger()
    called = []
    changed_contract = json.loads(json.dumps(CONTRACT))
    changed_contract["campaign"]["name"] = "drifted"

    for contract_loader, revision in (
        (lambda: changed_contract, SOURCE_REVISION),
        (lambda: CONTRACT, "invalid-revision"),
    ):
        with pytest.raises(InvalidAccessEvidence):
            run_access_evidence_job(
                ledger=ledger,
                contract_loader=contract_loader,
                access_probe=lambda: called.append(True),
                source_revision=revision,
                clock=lambda: NOW,
            )

    assert called == []
    assert ledger.writes == []


def test_cli_accepts_no_arguments_or_runtime_overrides(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(
        "scripts.google_ads_access_evidence_job._run_production_job",
        lambda: called.append(True),
    )

    assert main(["--deployment-id", deployment_id(CONTRACT)]) == 2

    assert called == []
    assert json.loads(capsys.readouterr().out) == {
        "account_access_validated": False,
        "evidence_recorded": False,
        "failure": "fixed_command_required",
        "ready_to_spend": False,
        "spend_enabled": False,
    }


def test_cli_success_output_is_sanitized_and_inert(monkeypatch, capsys):
    evidence = build_access_evidence(
        deployment_id=deployment_id(CONTRACT),
        check_key=AccessCheckKey.GOOGLE_ADS_ACCOUNT_ACCESS_GREEN,
        status=AccessEvidenceStatus.PASSED,
        observed_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        source_revision=SOURCE_REVISION,
        now=NOW,
    )
    monkeypatch.setattr(
        "scripts.google_ads_access_evidence_job._run_production_job",
        lambda: evidence,
    )

    assert main([]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["account_access_validated"] is True
    assert output["evidence_recorded"] is True
    assert output["ready_to_spend"] is False
    assert output["spend_enabled"] is False
    assert set(output["evidence"]) == {
        "deployment_id",
        "check_key",
        "status",
        "observed_at",
        "expires_at",
        "source_revision",
        "evidence_digest",
    }


def test_cli_failure_never_echoes_raw_exception(monkeypatch, capsys):
    monkeypatch.setattr(
        "scripts.google_ads_access_evidence_job._run_production_job",
        lambda: (_ for _ in ()).throw(RuntimeError("customer=1234567890 token=do-not-leak")),
    )

    assert main([]) == 1

    output = capsys.readouterr().out
    assert json.loads(output) == {
        "account_access_validated": False,
        "evidence_recorded": False,
        "failure": "access_evidence_job_failed",
        "ready_to_spend": False,
        "spend_enabled": False,
    }
    assert "1234567890" not in output
    assert "do-not-leak" not in output


def test_job_entrypoint_has_no_route_dispatcher_or_campaign_mutation_surface():
    import scripts.google_ads_access_evidence_job as job

    source = inspect.getsource(job)
    for forbidden in (
        "fastapi",
        "APIRouter",
        "run.jobs.run",
        "runWithOverrides",
        "build_mutate_request",
        "PausedCreateApproval",
        "create_paused",
        "activate",
        "spend_authorized",
    ):
        assert forbidden not in source
