#!/usr/bin/env python3
"""Fixed-command worker for one reviewed, inert Google Ads deployment.

The job accepts no caller payload or command-line configuration. It consumes
only an existing Firestore authority record and the checked-in contract. Ads
credentials are read from the fixed managed-secret environment names at job
runtime, are passed directly to the v25 adapter, and are never serialized.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from scripts.google_ads_access_evidence import (
    AccessCheckKey,
    AccessEvidenceStatus,
    validate_access_evidence,
)
from scripts.google_ads_access_probe import normalize_customer_id
from scripts.google_ads_launch_draft import (
    DEFAULT_DRAFT,
    build_mutate_request,
    contract_sha256,
    validate_draft,
)
from scripts.google_ads_paused_provider import (
    GoogleAdsV25PausedProvider,
    ProviderFailure,
)
from scripts.google_ads_paused_worker import (
    PERSISTED_ERROR_CODES,
    AuthorityLedger,
    DeploymentState,
    PausedCreateWorker,
    StaticContractSource,
    WorkerResult,
    contract_label,
    deployment_id,
)

_SOURCE_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ProviderFactory(Protocol):
    def __call__(
        self,
        *,
        customer_id: str,
        developer_token: str,
        login_customer_id: str | None,
        contract: dict[str, Any],
    ) -> Any: ...


def _load_checked_in_contract() -> dict[str, Any]:
    return json.loads(DEFAULT_DRAFT.read_text(encoding="utf-8"))


def _runtime_configuration(environ: Mapping[str, str]) -> tuple[str, str, str | None, str]:
    try:
        customer_id = normalize_customer_id(environ.get("GOOGLE_ADS_CUSTOMER_ID", ""))
        login_value = environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "")
        login_customer_id = normalize_customer_id(login_value) if login_value else None
        developer_token = environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", "")
        if not isinstance(developer_token, str) or not developer_token.strip():
            raise ValueError("developer token is required")
        source_revision = environ.get("APP_VERSION", "")
        if not _SOURCE_REVISION_RE.fullmatch(source_revision):
            raise ValueError("source revision is invalid")
    except (TypeError, ValueError):
        raise ValueError("invalid_configuration") from None
    return customer_id, developer_token, login_customer_id, source_revision


def _safe_result(authority: Any, *, error_code: str | None = None) -> WorkerResult:
    return WorkerResult(
        deployment_id=authority.deployment_id,
        state=authority.state,
        error_code=error_code,
        provider_reference_hash=authority.provider_reference_hash,
        existing=authority.state is DeploymentState.PAUSED_CREATED,
    )


def run_paused_create_job(
    *,
    ledger: AuthorityLedger,
    contract_loader: Callable[[], dict[str, Any]],
    provider_factory: ProviderFactory,
    environ: Mapping[str, str],
    clock: Callable[[], datetime] = _utc_now,
) -> WorkerResult:
    """Consume approved authority for the immutable checked-in graph."""
    try:
        contract = contract_loader()
    except Exception:
        raise ValueError("invalid_configuration") from None
    if not isinstance(contract, dict) or validate_draft(contract):
        raise ValueError("invalid_configuration")

    customer_id, developer_token, login_customer_id, source_revision = _runtime_configuration(
        environ
    )
    expected_deployment_id = deployment_id(contract)
    expected_contract_hash = f"sha256:{contract_sha256(contract)}"
    expected_contract_label = contract_label(contract)
    try:
        authority = ledger.get(expected_deployment_id)
    except Exception:
        raise ValueError("invalid_configuration") from None
    if (
        getattr(authority, "deployment_id", None) != expected_deployment_id
        or getattr(authority, "contract_hash", None) != expected_contract_hash
        or getattr(authority, "contract_label", None) != expected_contract_label
        or not isinstance(getattr(authority, "state", None), DeploymentState)
        or isinstance(getattr(authority, "version", None), bool)
        or not isinstance(getattr(authority, "version", None), int)
        or authority.version < 1
    ):
        raise ValueError("invalid_configuration")
    if authority.state is DeploymentState.PAUSED_CREATED:
        ledger.reconcile_terminal_paused_create_outbox(expected_deployment_id)
        return _safe_result(authority)
    if authority.state is not DeploymentState.PAUSED_CREATE_APPROVED:
        return _safe_result(authority, error_code="worker_not_approved")
    outbox = ledger.get_paused_create_outbox(expected_deployment_id)
    if outbox.state != "DISPATCHING" or not isinstance(outbox.dispatcher_claim_hash, str):
        return _safe_result(authority, error_code="worker_not_dispatched")

    try:
        currency_evidence = validate_access_evidence(
            ledger.get_access_evidence(
                expected_deployment_id,
                AccessCheckKey.GOOGLE_ADS_ACCOUNT_ACCESS_AND_USD_GREEN,
            ),
            now=clock(),
        )
    except Exception:
        currency_evidence = None
    if (
        currency_evidence is None
        or currency_evidence.deployment_id != expected_deployment_id
        or currency_evidence.check_key is not AccessCheckKey.GOOGLE_ADS_ACCOUNT_ACCESS_AND_USD_GREEN
        or currency_evidence.status is not AccessEvidenceStatus.PASSED
        or currency_evidence.source_revision != source_revision
    ):
        try:
            ledger.record_paused_create_worker_failure(
                expected_deployment_id,
                outbox.dispatcher_claim_hash,
            )
        except Exception:
            pass
        return _safe_result(authority, error_code="worker_currency_evidence_unavailable")

    provider = provider_factory(
        customer_id=customer_id,
        developer_token=developer_token,
        login_customer_id=login_customer_id,
        contract=contract,
    )
    source = StaticContractSource(contract)
    worker = PausedCreateWorker(
        ledger,
        source,
        lambda reviewed_contract, *, validate_only: build_mutate_request(
            reviewed_contract,
            customer_id,
            validate_only=validate_only,
        ),
        provider,
    )
    result = worker.run(expected_deployment_id)
    if result.state is DeploymentState.PAUSED_CREATED:
        ledger.reconcile_terminal_paused_create_outbox(expected_deployment_id)
    elif result.error_code in PERSISTED_ERROR_CODES:
        ledger.record_paused_create_worker_failure(
            expected_deployment_id,
            outbox.dispatcher_claim_hash,
        )
    return result


def _run_production_job() -> tuple[WorkerResult, ProviderFailure | None]:
    from database.google_ads_authority import FirestoreAuthorityLedger

    providers: list[GoogleAdsV25PausedProvider] = []

    def provider_factory(**kwargs: Any) -> GoogleAdsV25PausedProvider:
        provider = GoogleAdsV25PausedProvider(**kwargs)
        providers.append(provider)
        return provider

    with FirestoreAuthorityLedger() as ledger:
        result = run_paused_create_job(
            ledger=ledger,
            contract_loader=_load_checked_in_contract,
            provider_factory=provider_factory,
            environ=os.environ,
        )
    failure = providers[0].last_failure if providers else None
    return result, failure


def _failure_payload(reason: str) -> dict[str, Any]:
    return {
        "activation_authorized": False,
        "failure": reason,
        "paused_create_completed": False,
        "ready_to_spend": False,
        "spend_enabled": False,
    }


def main(argv: list[str] | None = None) -> int:
    argv = [] if argv is None else argv
    if argv:
        print(json.dumps(_failure_payload("fixed_command_required"), sort_keys=True))
        return 2
    try:
        result, provider_failure = _run_production_job()
    except Exception:
        print(json.dumps(_failure_payload("paused_create_job_failed"), sort_keys=True))
        return 1
    output = {
        "activation_authorized": False,
        "deployment_id": result.deployment_id,
        "error_code": result.error_code,
        "existing": result.existing,
        "paused_create_completed": result.state is DeploymentState.PAUSED_CREATED,
        "provider_failure": (
            None
            if provider_failure is None
            else {
                "code": provider_failure.code.value,
                "request_hash": provider_failure.request_hash,
            }
        ),
        "provider_reference_hash": result.provider_reference_hash,
        "ready_to_spend": False,
        "reconciled": result.reconciled,
        "spend_enabled": False,
        "state": result.state.value,
    }
    print(json.dumps(output, sort_keys=True))
    return 0 if result.state is DeploymentState.PAUSED_CREATED else 1


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
