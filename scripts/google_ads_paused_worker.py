#!/usr/bin/env python3
"""Offline, fail-closed authority ledger and PAUSED-create worker.

This module contains only pure orchestration and in-memory test seams. It has
no Google SDK, credential, Firestore, network, job, storefront, or activation
integration. A later adapter may implement the protocols, but cannot widen the
state machine or pass runtime overrides through these interfaces.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import Lock
from typing import Any, Protocol

from scripts.google_ads_launch_draft import (
    canonical_contract_json,
    contract_sha256,
    validate_draft,
)

PAUSED_CREATE_SCOPE = "PAUSED_CREATE_ONLY"
SAFE_ERROR_CODES = frozenset(
    {
        "contract_mismatch",
        "invalid_create_graph",
        "ledger_write_failed",
        "provider_contract_mismatch",
        "provider_create_failed",
        "provider_not_paused",
        "provider_reconciliation_failed",
        "provider_timeout_unresolved",
        "provider_validation_failed",
        "worker_claimed_elsewhere",
        "worker_not_approved",
    }
)


class DeploymentState(StrEnum):
    """The complete authority surface for this paused-create-only slice."""

    INTERNAL_DRAFT = "INTERNAL_DRAFT"
    SERVER_VALIDATED = "SERVER_VALIDATED"
    PAUSED_CREATE_APPROVED = "PAUSED_CREATE_APPROVED"
    PAUSED_CREATED = "PAUSED_CREATED"


_ALLOWED_TRANSITIONS = {
    DeploymentState.INTERNAL_DRAFT: DeploymentState.SERVER_VALIDATED,
    DeploymentState.SERVER_VALIDATED: DeploymentState.PAUSED_CREATE_APPROVED,
    DeploymentState.PAUSED_CREATE_APPROVED: DeploymentState.PAUSED_CREATED,
}


class ControlPlaneError(RuntimeError):
    """Base class for safe, non-provider control-plane failures."""


class DeploymentNotFound(ControlPlaneError):
    pass


class LedgerConflict(ControlPlaneError):
    pass


class LedgerWriteError(ControlPlaneError):
    pass


class InvalidStateTransition(ControlPlaneError):
    pass


class ContractValidationError(ControlPlaneError):
    pass


class InvalidPausedCreateApproval(ControlPlaneError):
    pass


class ProviderValidationError(RuntimeError):
    """Provider rejected the validation-only request."""


class AmbiguousProviderTimeout(RuntimeError):
    """Provider response was lost and commit status is unknown."""


@dataclass(frozen=True)
class DeploymentRecord:
    """Sanitized Firestore-style authority record.

    Provider account IDs, raw resource names, tokens, and raw responses are not
    fields, so implementations cannot accidentally persist them here.
    """

    deployment_id: str
    deployment_key: str
    contract_hash: str
    contract_label: str
    state: DeploymentState = DeploymentState.INTERNAL_DRAFT
    version: int = 1
    worker_claim_hash: str | None = None
    claim_expires_at: datetime | None = None
    provider_reference_hash: str | None = None
    error_code: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def claimed_by(self) -> str | None:
        """Compatibility alias; values are one-way hashes, never raw worker IDs."""
        return self.worker_claim_hash


@dataclass(frozen=True)
class PausedCreateApproval:
    """Purpose-bound authority that can create only inert PAUSED resources."""

    deployment_id: str
    contract_hash: str
    scope: str = field(default=PAUSED_CREATE_SCOPE, init=False)
    activation_authorized: bool = field(default=False, init=False)
    spend_authorized: bool = field(default=False, init=False)

    @classmethod
    def for_record(cls, record: DeploymentRecord) -> PausedCreateApproval:
        if record.state is not DeploymentState.SERVER_VALIDATED:
            raise InvalidPausedCreateApproval("deployment is not server validated")
        return cls(deployment_id=record.deployment_id, contract_hash=record.contract_hash)


@dataclass(frozen=True)
class ProviderPausedDeployment:
    """Internal provider boundary value; the raw name must be hashed at rest."""

    contract_hash: str
    campaign_resource_name: str = field(repr=False)
    status: str = "PAUSED"


@dataclass(frozen=True)
class WorkerResult:
    deployment_id: str
    state: DeploymentState
    error_code: str | None = None
    provider_reference_hash: str | None = None
    existing: bool = False
    reconciled: bool = False


class ContractSource(Protocol):
    def load(self) -> dict[str, Any]: ...


class WorkerInvoker(Protocol):
    def invoke(self, deployment_id: str) -> None: ...


class MutateRequestBuilder(Protocol):
    def __call__(self, contract: dict[str, Any], *, validate_only: bool) -> dict[str, Any]: ...


class PausedProvider(Protocol):
    def validate(self, request: dict[str, Any]) -> None: ...

    def find_by_contract_label(self, label: str) -> ProviderPausedDeployment | None: ...

    def create_paused(self, request: dict[str, Any]) -> ProviderPausedDeployment: ...


class AuthorityLedger(Protocol):
    def create_or_get(self, candidate: DeploymentRecord) -> tuple[DeploymentRecord, bool]: ...

    def get(self, deployment_id: str) -> DeploymentRecord: ...

    def transition(
        self,
        deployment_id: str,
        *,
        expected: DeploymentState,
        target: DeploymentState,
        expected_version: int | None = None,
    ) -> DeploymentRecord: ...

    def claim_paused_create(
        self,
        deployment_id: str,
        claimant: str,
        *,
        expected_version: int | None = None,
    ) -> bool: ...

    def complete_paused_create(
        self,
        deployment_id: str,
        claimant: str,
        provider_reference_hash: str,
        *,
        expected_version: int | None = None,
    ) -> DeploymentRecord: ...

    def release_claim(
        self,
        deployment_id: str,
        claimant: str,
        error_code: str,
        *,
        expected_version: int | None = None,
    ) -> DeploymentRecord: ...


class StaticContractSource:
    """Immutable server-owned contract source for offline workers and tests."""

    def __init__(self, contract: dict[str, Any]):
        self._canonical_json = canonical_contract_json(contract)

    def load(self) -> dict[str, Any]:
        return json.loads(self._canonical_json)


def _claimant_hash(claimant: str) -> str:
    if not isinstance(claimant, str) or not claimant:
        raise ValueError("claimant is required")
    return "sha256:" + hashlib.sha256(claimant.encode("utf-8")).hexdigest()


class InMemoryAuthorityLedger:
    """Thread-safe Firestore transaction semantics without external I/O."""

    def __init__(self, *, claim_lease_seconds: int = 300):
        if not 1 <= claim_lease_seconds <= 3600:
            raise ValueError("claim lease must be between 1 and 3600 seconds")
        self._records: dict[str, DeploymentRecord] = {}
        self._lock = Lock()
        self._fail_next_write = False
        self._claim_lease_seconds = claim_lease_seconds

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    def fail_next_write(self) -> None:
        """Test seam that makes the next transaction fail before mutation."""
        with self._lock:
            self._fail_next_write = True

    def _assert_write_available(self) -> None:
        if self._fail_next_write:
            self._fail_next_write = False
            raise LedgerWriteError("ledger_write_failed")

    def _get_locked(self, deployment_id: str) -> DeploymentRecord:
        try:
            return self._records[deployment_id]
        except KeyError as exc:
            raise DeploymentNotFound("deployment not found") from exc

    def create_or_get(self, candidate: DeploymentRecord) -> tuple[DeploymentRecord, bool]:
        with self._lock:
            existing = self._records.get(candidate.deployment_id)
            if existing is not None:
                immutable_identity = (
                    existing.deployment_key,
                    existing.contract_hash,
                    existing.contract_label,
                )
                candidate_identity = (
                    candidate.deployment_key,
                    candidate.contract_hash,
                    candidate.contract_label,
                )
                if immutable_identity != candidate_identity:
                    raise LedgerConflict("deployment identity conflict")
                return existing, False
            self._assert_write_available()
            now = self._now()
            stored = replace(candidate, created_at=now, updated_at=now)
            self._records[candidate.deployment_id] = stored
            return stored, True

    def get(self, deployment_id: str) -> DeploymentRecord:
        with self._lock:
            return self._get_locked(deployment_id)

    def transition(
        self,
        deployment_id: str,
        *,
        expected: DeploymentState,
        target: DeploymentState,
        expected_version: int | None = None,
    ) -> DeploymentRecord:
        with self._lock:
            self._assert_write_available()
            record = self._get_locked(deployment_id)
            if (
                record.state is not expected
                or _ALLOWED_TRANSITIONS.get(expected) is not target
                or (expected_version is not None and record.version != expected_version)
            ):
                raise InvalidStateTransition(f"invalid transition: {expected} -> {target}")
            updated = replace(
                record,
                state=target,
                version=record.version + 1,
                worker_claim_hash=None,
                claim_expires_at=None,
                error_code=None,
                updated_at=self._now(),
            )
            self._records[deployment_id] = updated
            return updated

    def claim_paused_create(
        self,
        deployment_id: str,
        claimant: str,
        *,
        expected_version: int | None = None,
    ) -> bool:
        claimant_hash = _claimant_hash(claimant)
        with self._lock:
            self._assert_write_available()
            record = self._get_locked(deployment_id)
            now = self._now()
            if expected_version is not None and record.version != expected_version:
                raise InvalidStateTransition("stale deployment version")
            live_claim = record.worker_claim_hash is not None and (
                record.claim_expires_at is None or record.claim_expires_at > now
            )
            if record.state is not DeploymentState.PAUSED_CREATE_APPROVED or live_claim:
                return False
            self._records[deployment_id] = replace(
                record,
                worker_claim_hash=claimant_hash,
                claim_expires_at=now + timedelta(seconds=self._claim_lease_seconds),
                version=record.version + 1,
                error_code=None,
                updated_at=now,
            )
            return True

    def complete_paused_create(
        self,
        deployment_id: str,
        claimant: str,
        provider_reference_hash: str,
        *,
        expected_version: int | None = None,
    ) -> DeploymentRecord:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", provider_reference_hash):
            raise ValueError("provider reference must be a SHA-256 digest")
        with self._lock:
            self._assert_write_available()
            record = self._get_locked(deployment_id)
            if (
                record.state is not DeploymentState.PAUSED_CREATE_APPROVED
                or record.worker_claim_hash != _claimant_hash(claimant)
                or record.claim_expires_at is None
                or record.claim_expires_at <= self._now()
                or (expected_version is not None and record.version != expected_version)
            ):
                raise InvalidStateTransition("paused create is not claimed by this worker")
            updated = replace(
                record,
                state=DeploymentState.PAUSED_CREATED,
                version=record.version + 1,
                worker_claim_hash=None,
                claim_expires_at=None,
                provider_reference_hash=provider_reference_hash,
                error_code=None,
                updated_at=self._now(),
            )
            self._records[deployment_id] = updated
            return updated

    def release_claim(
        self,
        deployment_id: str,
        claimant: str,
        error_code: str,
        *,
        expected_version: int | None = None,
    ) -> DeploymentRecord:
        if error_code not in SAFE_ERROR_CODES:
            raise ValueError("error_code is not in the sanitized allowlist")
        with self._lock:
            self._assert_write_available()
            record = self._get_locked(deployment_id)
            if (
                record.state is not DeploymentState.PAUSED_CREATE_APPROVED
                or record.worker_claim_hash != _claimant_hash(claimant)
                or (expected_version is not None and record.version != expected_version)
            ):
                raise InvalidStateTransition("paused create is not claimed by this worker")
            updated = replace(
                record,
                version=record.version + 1,
                worker_claim_hash=None,
                claim_expires_at=None,
                error_code=error_code,
                updated_at=self._now(),
            )
            self._records[deployment_id] = updated
            return updated


def deployment_id(contract: dict[str, Any]) -> str:
    """Return the Firestore-safe deployment key plus full canonical digest."""
    deployment = contract.get("deployment")
    key = deployment.get("key") if isinstance(deployment, dict) else None
    if not isinstance(key, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", key):
        raise ValueError("deployment.key must be a lowercase Firestore-safe slug")
    return f"{key}--{contract_sha256(contract)}"


def contract_label(contract: dict[str, Any]) -> str:
    return f"tho-contract-{contract_sha256(contract)[:12]}"


def _record_for_contract(contract: dict[str, Any]) -> DeploymentRecord:
    deployment = contract["deployment"]
    digest = contract_sha256(contract)
    return DeploymentRecord(
        deployment_id=deployment_id(contract),
        deployment_key=deployment["key"],
        contract_hash=f"sha256:{digest}",
        contract_label=contract_label(contract),
    )


class DraftReviewControlPlane:
    """Create and server-validate drafts without approval or worker authority."""

    def __init__(
        self,
        ledger: AuthorityLedger,
        contract_source: ContractSource,
    ):
        self._ledger = ledger
        self._contract_source = contract_source

    def ensure_internal_draft(self) -> DeploymentRecord:
        candidate = _record_for_contract(self._contract_source.load())
        record, _created = self._ledger.create_or_get(candidate)
        return record

    def server_validate(self, requested_deployment_id: str) -> DeploymentRecord:
        contract = self._contract_source.load()
        candidate = _record_for_contract(contract)
        if candidate.deployment_id != requested_deployment_id:
            raise ContractValidationError("contract_mismatch")
        errors = validate_draft(contract)
        if errors:
            raise ContractValidationError("server contract validation failed")
        record = self._ledger.get(requested_deployment_id)
        if record.contract_hash != candidate.contract_hash:
            raise ContractValidationError("contract_mismatch")
        if record.state is DeploymentState.INTERNAL_DRAFT:
            return self._ledger.transition(
                requested_deployment_id,
                expected=DeploymentState.INTERNAL_DRAFT,
                target=DeploymentState.SERVER_VALIDATED,
            )
        return record


class PausedCreateControlPlane:
    """Approve PAUSED creation and invoke a deployment-ID-only worker seam."""

    def __init__(
        self,
        ledger: AuthorityLedger,
        invoker: WorkerInvoker,
    ):
        self._ledger = ledger
        self._invoker = invoker

    def approve_paused_create(self, approval: PausedCreateApproval) -> DeploymentRecord:
        record = self._ledger.get(approval.deployment_id)
        if (
            approval.scope != PAUSED_CREATE_SCOPE
            or approval.activation_authorized
            or approval.spend_authorized
            or approval.contract_hash != record.contract_hash
        ):
            raise InvalidPausedCreateApproval("approval is not bound to paused creation")
        if record.state in {
            DeploymentState.PAUSED_CREATE_APPROVED,
            DeploymentState.PAUSED_CREATED,
        }:
            return record
        if record.state is not DeploymentState.SERVER_VALIDATED:
            raise InvalidPausedCreateApproval("deployment is not server validated")
        approved = self._ledger.transition(
            record.deployment_id,
            expected=DeploymentState.SERVER_VALIDATED,
            target=DeploymentState.PAUSED_CREATE_APPROVED,
        )
        self._invoker.invoke(approved.deployment_id)
        return approved


def _safe_result(
    record: DeploymentRecord,
    *,
    error_code: str | None = None,
    existing: bool = False,
    reconciled: bool = False,
) -> WorkerResult:
    return WorkerResult(
        deployment_id=record.deployment_id,
        state=record.state,
        error_code=error_code,
        provider_reference_hash=record.provider_reference_hash,
        existing=existing,
        reconciled=reconciled,
    )


def _provider_reference_hash(resource_name: str) -> str:
    if not isinstance(resource_name, str) or not resource_name:
        raise ValueError("provider resource name is missing")
    return "sha256:" + hashlib.sha256(resource_name.encode("utf-8")).hexdigest()


def _assert_paused_request_pair(
    validation_request: dict[str, Any], create_request: dict[str, Any]
) -> None:
    validation_operations = validation_request.get("mutateOperations")
    create_operations = create_request.get("mutateOperations")
    if not isinstance(validation_operations, list) or not validation_operations:
        raise ValueError("mutate graph is empty")
    if validation_operations != create_operations:
        raise ValueError("validation and creation graphs differ")
    if validation_request.get("validateOnly") is not True:
        raise ValueError("validation request must be validate-only")
    if create_request.get("validateOnly") is not False:
        raise ValueError("create request must disable validate-only")
    if validation_request.get("partialFailure") is not False:
        raise ValueError("validation request must be atomic")
    if create_request.get("partialFailure") is not False:
        raise ValueError("create request must be atomic")

    paused_operation_names = {
        "campaignOperation",
        "adGroupOperation",
        "adGroupCriterionOperation",
        "adGroupAdOperation",
    }
    seen_paused_types: set[str] = set()
    for operation in create_operations:
        if not isinstance(operation, dict) or len(operation) != 1:
            raise ValueError("invalid mutate operation")
        operation_name, body = next(iter(operation.items()))
        if not isinstance(body, dict) or set(body) != {"create"}:
            raise ValueError("paused worker accepts create operations only")
        if operation_name in paused_operation_names:
            create = body.get("create")
            if not isinstance(create, dict) or create.get("status") != "PAUSED":
                raise ValueError("all serving resources must be created paused")
            seen_paused_types.add(operation_name)
    if seen_paused_types != paused_operation_names:
        raise ValueError("paused create graph is incomplete")


class PausedCreateWorker:
    """Validate, reconcile, and create only the immutable PAUSED graph."""

    def __init__(
        self,
        ledger: AuthorityLedger,
        contract_source: ContractSource,
        request_builder: MutateRequestBuilder,
        provider: PausedProvider,
    ):
        self._ledger = ledger
        self._contract_source = contract_source
        self._request_builder = request_builder
        self._provider = provider

    def _release_failure(
        self, record: DeploymentRecord, claimant: str, error_code: str
    ) -> WorkerResult:
        try:
            released = self._ledger.release_claim(record.deployment_id, claimant, error_code)
        except (ControlPlaneError, ValueError):
            return _safe_result(record, error_code="ledger_write_failed")
        return _safe_result(released, error_code=error_code)

    def _accept_provider_deployment(
        self,
        record: DeploymentRecord,
        claimant: str,
        provider_deployment: ProviderPausedDeployment,
        *,
        reconciled: bool,
    ) -> WorkerResult:
        if provider_deployment.contract_hash != record.contract_hash:
            return self._release_failure(record, claimant, "provider_contract_mismatch")
        if provider_deployment.status != "PAUSED":
            return self._release_failure(record, claimant, "provider_not_paused")
        try:
            reference_hash = _provider_reference_hash(provider_deployment.campaign_resource_name)
            completed = self._ledger.complete_paused_create(
                record.deployment_id,
                claimant,
                reference_hash,
            )
        except (ControlPlaneError, ValueError):
            return self._release_failure(
                record,
                claimant,
                "ledger_write_failed",
            )
        return _safe_result(completed, reconciled=reconciled)

    def run(self, deployment_id: str) -> WorkerResult:
        """Run one deployment ID with no caller-provided configuration overrides."""
        record = self._ledger.get(deployment_id)
        if record.state is DeploymentState.PAUSED_CREATED:
            return _safe_result(record, existing=True)
        if record.state is not DeploymentState.PAUSED_CREATE_APPROVED:
            return _safe_result(record, error_code="worker_not_approved")

        claimant = f"paused-worker-{uuid.uuid4().hex}"
        try:
            claimed = self._ledger.claim_paused_create(deployment_id, claimant)
        except ControlPlaneError:
            return _safe_result(record, error_code="ledger_write_failed")
        if not claimed:
            refreshed = self._ledger.get(deployment_id)
            if refreshed.state is DeploymentState.PAUSED_CREATED:
                return _safe_result(refreshed, existing=True)
            return _safe_result(refreshed, error_code="worker_claimed_elsewhere")

        claimed_record = self._ledger.get(deployment_id)
        contract = self._contract_source.load()
        try:
            candidate = _record_for_contract(contract)
            if (
                candidate.deployment_id != claimed_record.deployment_id
                or candidate.contract_hash != claimed_record.contract_hash
            ):
                return self._release_failure(claimed_record, claimant, "contract_mismatch")
            validation_request = self._request_builder(contract, validate_only=True)
            create_request = self._request_builder(contract, validate_only=False)
            _assert_paused_request_pair(validation_request, create_request)
        except Exception:
            return self._release_failure(claimed_record, claimant, "invalid_create_graph")

        try:
            self._provider.validate(validation_request)
        except Exception:
            return self._release_failure(
                claimed_record,
                claimant,
                "provider_validation_failed",
            )

        try:
            existing = self._provider.find_by_contract_label(claimed_record.contract_label)
        except Exception:
            return self._release_failure(
                claimed_record,
                claimant,
                "provider_reconciliation_failed",
            )
        if existing is not None:
            return self._accept_provider_deployment(
                claimed_record,
                claimant,
                existing,
                reconciled=True,
            )

        try:
            created = self._provider.create_paused(create_request)
        except AmbiguousProviderTimeout:
            try:
                reconciled = self._provider.find_by_contract_label(claimed_record.contract_label)
            except Exception:
                return self._release_failure(
                    claimed_record,
                    claimant,
                    "provider_reconciliation_failed",
                )
            if reconciled is None:
                return self._release_failure(
                    claimed_record,
                    claimant,
                    "provider_timeout_unresolved",
                )
            return self._accept_provider_deployment(
                claimed_record,
                claimant,
                reconciled,
                reconciled=True,
            )
        except Exception:
            return self._release_failure(
                claimed_record,
                claimant,
                "provider_create_failed",
            )

        return self._accept_provider_deployment(
            claimed_record,
            claimant,
            created,
            reconciled=False,
        )
