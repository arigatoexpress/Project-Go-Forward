#!/usr/bin/env python3
"""Fixed-command job that records sanitized read-only Ads access and USD evidence.

There is no request payload or command-line configuration surface. Production
credentials are read from the three fixed managed-secret environment names and
are passed only to the existing v25 read-only probe. Firestore receives only
the strict evidence contract.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from scripts.google_ads_access_evidence import (
    DEFAULT_EVIDENCE_TTL,
    AccessCheckKey,
    AccessEvidence,
    AccessEvidenceStatus,
    InvalidAccessEvidence,
    build_access_evidence,
    evidence_payload,
    validate_source_revision,
)
from scripts.google_ads_access_probe import probe_access
from scripts.google_ads_launch_draft import DEFAULT_DRAFT, contract_sha256, validate_draft
from scripts.google_ads_paused_worker import deployment_id

_PROBE_RESULT_FIELDS = {
    "account_access_validated",
    "account_currency_usd",
    "failure",
    "http_status",
    "live_probe_executed",
    "request_id_present",
    "ready_to_spend",
    "spend_enabled",
}
_SAFE_PROBE_FAILURES = {
    None,
    "authentication_or_access_denied",
    "credential_or_network_error",
    "google_ads_unavailable",
    "request_rejected",
    "account_currency_not_usd_or_unverified",
}


class AccessEvidenceLedger(Protocol):
    def get(self, deployment_id: str) -> Any: ...

    def record_access_evidence(
        self,
        evidence: AccessEvidence,
        *,
        expected_version: int,
    ) -> AccessEvidence: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _validate_probe_result(result: Any) -> AccessEvidenceStatus:
    if not isinstance(result, Mapping) or set(result) != _PROBE_RESULT_FIELDS:
        raise InvalidAccessEvidence("probe_result_invalid")
    validated = result["account_access_validated"]
    currency_is_usd = result["account_currency_usd"]
    failure = result["failure"]
    http_status = result["http_status"]
    if (
        not isinstance(validated, bool)
        or not isinstance(currency_is_usd, bool)
        or not isinstance(result["live_probe_executed"], bool)
        or result["live_probe_executed"] is not True
        or not isinstance(result["request_id_present"], bool)
        or result["ready_to_spend"] is not False
        or result["spend_enabled"] is not False
        or failure not in _SAFE_PROBE_FAILURES
        or (
            http_status is not None
            and (isinstance(http_status, bool) or not isinstance(http_status, int))
        )
        or (isinstance(http_status, int) and not 100 <= http_status <= 599)
    ):
        raise InvalidAccessEvidence("probe_result_invalid")
    if validated:
        if http_status != 200:
            raise InvalidAccessEvidence("probe_result_invalid")
        if currency_is_usd and failure is None:
            return AccessEvidenceStatus.PASSED
        if not currency_is_usd and failure == "account_currency_not_usd_or_unverified":
            return AccessEvidenceStatus.FAILED
        raise InvalidAccessEvidence("probe_result_invalid")
    if currency_is_usd:
        raise InvalidAccessEvidence("probe_result_invalid")
    failure_status_valid = (
        (failure == "credential_or_network_error" and http_status is None)
        or (failure == "authentication_or_access_denied" and http_status in {401, 403})
        or (
            failure == "request_rejected"
            and isinstance(http_status, int)
            and 400 <= http_status < 500
            and http_status not in {401, 403}
        )
        or (
            failure == "google_ads_unavailable"
            and isinstance(http_status, int)
            and 500 <= http_status <= 599
        )
    )
    if not failure_status_valid:
        raise InvalidAccessEvidence("probe_result_invalid")
    return AccessEvidenceStatus.FAILED


def run_access_evidence_job(
    *,
    ledger: AccessEvidenceLedger,
    contract_loader: Callable[[], dict[str, Any]],
    access_probe: Callable[[], Mapping[str, Any]],
    source_revision: str,
    clock: Callable[[], datetime] = _utc_now,
) -> AccessEvidence:
    """Run the immutable contract's read check and CAS-write its safe result."""
    validate_source_revision(source_revision)
    try:
        contract = contract_loader()
    except Exception:
        raise InvalidAccessEvidence("contract_load_failed") from None
    if not isinstance(contract, dict) or validate_draft(contract):
        raise InvalidAccessEvidence("contract_invalid")
    requested_deployment_id = deployment_id(contract)
    expected_contract_hash = f"sha256:{contract_sha256(contract)}"
    try:
        authority = ledger.get(requested_deployment_id)
    except Exception:
        raise InvalidAccessEvidence("authority_record_unavailable") from None
    if (
        getattr(authority, "deployment_id", None) != requested_deployment_id
        or getattr(authority, "contract_hash", None) != expected_contract_hash
        or isinstance(getattr(authority, "version", None), bool)
        or not isinstance(getattr(authority, "version", None), int)
        or authority.version < 1
    ):
        raise InvalidAccessEvidence("authority_record_invalid")

    observed_at = clock()
    try:
        probe_result = access_probe()
    except Exception:
        status = AccessEvidenceStatus.ERROR
    else:
        status = _validate_probe_result(probe_result)
    evidence = build_access_evidence(
        deployment_id=requested_deployment_id,
        check_key=AccessCheckKey.GOOGLE_ADS_ACCOUNT_ACCESS_AND_USD_GREEN,
        status=status,
        observed_at=observed_at,
        expires_at=observed_at + DEFAULT_EVIDENCE_TTL,
        source_revision=source_revision,
        now=observed_at,
    )
    return ledger.record_access_evidence(evidence, expected_version=authority.version)


def _load_checked_in_contract() -> dict[str, Any]:
    return json.loads(DEFAULT_DRAFT.read_text(encoding="utf-8"))


def _managed_secret_probe() -> Mapping[str, Any]:
    return probe_access(
        customer_id=os.environ.get("GOOGLE_ADS_CUSTOMER_ID", ""),
        developer_token=os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", ""),
        login_customer_id=os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID") or None,
    )


def _run_production_job() -> AccessEvidence:
    from database.google_ads_authority import FirestoreAuthorityLedger

    with FirestoreAuthorityLedger() as ledger:
        return run_access_evidence_job(
            ledger=ledger,
            contract_loader=_load_checked_in_contract,
            access_probe=_managed_secret_probe,
            source_revision=os.environ.get("APP_VERSION", ""),
        )


def _failure_payload(reason: str) -> dict[str, Any]:
    return {
        "account_access_validated": False,
        "account_currency_usd": False,
        "evidence_recorded": False,
        "failure": reason,
        "ready_to_spend": False,
        "spend_enabled": False,
    }


def main(argv: list[str] | None = None) -> int:
    argv = [] if argv is None else argv
    if argv:
        print(json.dumps(_failure_payload("fixed_command_required"), sort_keys=True))
        return 2
    try:
        evidence = _run_production_job()
    except Exception:
        print(json.dumps(_failure_payload("access_evidence_job_failed"), sort_keys=True))
        return 1
    output = {
        "account_access_validated": evidence.status is AccessEvidenceStatus.PASSED,
        "account_currency_usd": evidence.status is AccessEvidenceStatus.PASSED,
        "evidence": evidence_payload(evidence),
        "evidence_recorded": True,
        "ready_to_spend": False,
        "spend_enabled": False,
    }
    print(json.dumps(output, default=str, sort_keys=True))
    return 0 if evidence.status is AccessEvidenceStatus.PASSED else 1


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
