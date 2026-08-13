"""Durable, fail-closed Firestore authority ledger for PAUSED Google Ads creation.

This adapter persists only the sanitized state machine defined in
``scripts.google_ads_paused_worker``. It has no provider, credential, HTTP,
route, UI, approval, or job-invocation capability.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from pydantic import ValidationError

from database.models import (
    GoogleAdsAccessEvidenceRecord,
    GoogleAdsAuthorityEventRecord,
    GoogleAdsDeploymentRecord,
    GoogleAdsOperationKeyRecord,
)
from database.rpc_timeout import FIRESTORE_RPC_TIMEOUT, FIRESTORE_TRANSACTION_TIMEOUT
from scripts.google_ads_access_evidence import (
    AccessCheckKey,
    AccessEvidence,
    AccessEvidenceStatus,
    InvalidAccessEvidence,
    evidence_payload,
    validate_access_evidence,
)
from scripts.google_ads_paused_worker import (
    PERSISTED_ERROR_CODES,
    ControlPlaneError,
    DeploymentNotFound,
    DeploymentRecord,
    DeploymentState,
    InvalidStateTransition,
    LedgerConflict,
    LedgerWriteError,
)

_T = TypeVar("_T")
_TRANSITION_EVENTS = {
    DeploymentState.SERVER_VALIDATED: "SERVER_VALIDATED",
    DeploymentState.PAUSED_CREATE_APPROVED: "PAUSED_CREATE_APPROVED",
}
_ALLOWED_TRANSITIONS = {
    DeploymentState.INTERNAL_DRAFT: DeploymentState.SERVER_VALIDATED,
    DeploymentState.SERVER_VALIDATED: DeploymentState.PAUSED_CREATE_APPROVED,
    DeploymentState.PAUSED_CREATE_APPROVED: DeploymentState.PAUSED_CREATED,
}
_DEPLOYMENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}--[0-9a-f]{64}$")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _claimant_hash(claimant: str) -> str:
    if not isinstance(claimant, str) or not claimant:
        raise ValueError("claimant is required")
    return "sha256:" + hashlib.sha256(claimant.encode("utf-8")).hexdigest()


def _event_id(version: int, event_type: str) -> str:
    return f"{version:020d}-{event_type.lower().replace('_', '-')}"


class FirestoreAuthorityLedger:
    """Firestore implementation of the paused-worker ``AuthorityLedger`` protocol.

    Each mutation reads the current record and atomically writes both the new
    record and one deterministic append-only authority event. Firestore retries
    transaction conflicts; the explicit ``version`` check also lets callers
    reject stale local decisions. Worker claims are leases so a crashed job
    cannot strand a deployment forever.
    """

    COLLECTION = "google_ads_deployments"
    EVENTS_SUBCOLLECTION = "authority_events"
    OPERATION_KEYS_SUBCOLLECTION = "operation_keys"
    ACCESS_EVIDENCE_SUBCOLLECTION = "access_evidence"
    ACCESS_EVIDENCE_EVENTS_SUBCOLLECTION = "access_evidence_events"

    def __init__(
        self,
        *,
        client: Any | None = None,
        project: str | None = None,
        transaction_executor: Callable[[Callable[[Any], _T]], _T] | None = None,
        clock: Callable[[], datetime] = _utc_now,
        claim_lease_seconds: int = 300,
        transaction_timeout_seconds: float = FIRESTORE_TRANSACTION_TIMEOUT,
        transaction_workers: int = 2,
        transaction_pool: ThreadPoolExecutor | None = None,
    ) -> None:
        if not 1 <= claim_lease_seconds <= 3600:
            raise ValueError("claim lease must be between 1 and 3600 seconds")
        if transaction_timeout_seconds <= 0:
            raise ValueError("transaction timeout must be positive")
        if not 1 <= transaction_workers <= 4:
            raise ValueError("transaction workers must be between 1 and 4")
        if transaction_pool is not None and transaction_pool._max_workers > 4:
            raise ValueError("injected transaction pool cannot exceed 4 workers")
        if client is None:
            from google.cloud import firestore

            client = firestore.Client(project=project) if project else firestore.Client()
        self._client = client
        self._collection = client.collection(self.COLLECTION)
        self._transaction_executor = transaction_executor or self._execute_firestore_transaction
        self._clock = clock
        self._claim_lease_seconds = claim_lease_seconds
        self._transaction_timeout_seconds = transaction_timeout_seconds
        self.transaction_thread_name_prefix = f"ads-ledger-{id(self):x}"
        self._owns_transaction_pool = transaction_pool is None
        self._transaction_pool = transaction_pool or ThreadPoolExecutor(
            max_workers=transaction_workers,
            thread_name_prefix=self.transaction_thread_name_prefix,
        )

    def close(self) -> None:
        """Release this ledger's bounded transaction workers and queued calls."""
        if self._owns_transaction_pool:
            self._transaction_pool.shutdown(wait=True, cancel_futures=True)

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.close()

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise LedgerWriteError("ledger_clock_invalid")
        return value.astimezone(UTC)

    def _execute_firestore_transaction(self, operation: Callable[[Any], _T]) -> _T:
        from google.cloud import firestore

        transaction = self._client.transaction(max_attempts=5)
        return firestore.transactional(operation)(transaction)

    def _run_transaction(self, operation: Callable[[Any], _T]) -> _T:
        # Firestore's transaction helper does not expose timeouts for its
        # Begin/Commit/Rollback RPCs. Bound the caller's wall clock, matching
        # the existing appointment/lead transaction policy. An ambiguous late
        # commit remains safe because every operation is deterministic and
        # replayable against the stored version/state. The shared pool caps
        # already-running RPCs; timed-out queued calls are cancelled below.
        future = self._transaction_pool.submit(self._transaction_executor, operation)
        try:
            return future.result(timeout=self._transaction_timeout_seconds)
        except FutureTimeoutError:
            future.cancel()
            raise LedgerWriteError("ledger_write_timeout") from None
        except ControlPlaneError:
            raise
        except Exception:
            raise LedgerWriteError("ledger_write_failed") from None

    def _reference(self, deployment_id: str):
        # Require the canonical key-plus-digest form before deriving any
        # Firestore path, including reads.
        if not isinstance(deployment_id, str) or not _DEPLOYMENT_ID_RE.fullmatch(deployment_id):
            raise DeploymentNotFound("deployment not found")
        return self._collection.document(deployment_id)

    @staticmethod
    def _model_to_domain(model: GoogleAdsDeploymentRecord) -> DeploymentRecord:
        return DeploymentRecord(
            deployment_id=model.deployment_id,
            deployment_key=model.deployment_key,
            contract_hash=model.contract_hash,
            contract_label=model.contract_label,
            state=DeploymentState(model.state),
            version=model.version,
            worker_claim_hash=model.worker_claim_hash,
            claim_expires_at=model.claim_expires_at,
            create_fenced_at=model.create_fenced_at,
            create_fence_claim_hash=model.create_fence_claim_hash,
            provider_reference_hash=model.provider_reference_hash,
            error_code=model.error_code,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    @staticmethod
    def _domain_to_model(record: DeploymentRecord) -> GoogleAdsDeploymentRecord:
        try:
            return GoogleAdsDeploymentRecord.model_validate(
                {
                    "schema_version": 1,
                    "deployment_id": record.deployment_id,
                    "deployment_key": record.deployment_key,
                    "contract_hash": record.contract_hash,
                    "contract_label": record.contract_label,
                    "state": record.state.value,
                    "version": record.version,
                    "worker_claim_hash": record.worker_claim_hash,
                    "claim_expires_at": record.claim_expires_at,
                    "create_fenced_at": record.create_fenced_at,
                    "create_fence_claim_hash": record.create_fence_claim_hash,
                    "provider_reference_hash": record.provider_reference_hash,
                    "error_code": record.error_code,
                    "created_at": record.created_at,
                    "updated_at": record.updated_at,
                }
            )
        except (ValidationError, AttributeError, TypeError, ValueError):
            raise LedgerWriteError("ledger_record_invalid") from None

    def _snapshot_to_domain(self, snapshot: Any) -> DeploymentRecord:
        if not getattr(snapshot, "exists", False):
            raise DeploymentNotFound("deployment not found")
        try:
            model = GoogleAdsDeploymentRecord.model_validate(snapshot.to_dict() or {})
        except (ValidationError, AttributeError, TypeError, ValueError):
            raise LedgerWriteError("ledger_record_invalid") from None
        return self._model_to_domain(model)

    def _snapshot_to_access_evidence(self, snapshot: Any) -> AccessEvidence:
        if not getattr(snapshot, "exists", False):
            raise LedgerWriteError("access_evidence_missing")
        try:
            model = GoogleAdsAccessEvidenceRecord.model_validate(snapshot.to_dict() or {})
            evidence = AccessEvidence(
                deployment_id=model.deployment_id,
                check_key=AccessCheckKey(model.check_key),
                status=AccessEvidenceStatus(model.status),
                observed_at=model.observed_at,
                expires_at=model.expires_at,
                source_revision=model.source_revision,
                evidence_digest=model.evidence_digest,
            )
            return validate_access_evidence(evidence, now=self._now())
        except (InvalidAccessEvidence, ValidationError, AttributeError, TypeError, ValueError):
            raise LedgerWriteError("access_evidence_invalid") from None

    @staticmethod
    def _event(
        record: DeploymentRecord,
        *,
        event_type: str,
        from_state: DeploymentState | None,
        occurred_at: datetime,
        worker_claim_hash: str | None = None,
    ) -> GoogleAdsAuthorityEventRecord:
        try:
            return GoogleAdsAuthorityEventRecord.model_validate(
                {
                    "schema_version": 1,
                    "event_id": _event_id(record.version, event_type),
                    "deployment_id": record.deployment_id,
                    "contract_hash": record.contract_hash,
                    "event_type": event_type,
                    "from_state": from_state.value if from_state else None,
                    "to_state": record.state.value,
                    "record_version": record.version,
                    "worker_claim_hash": (
                        worker_claim_hash
                        if worker_claim_hash is not None
                        else record.worker_claim_hash
                    ),
                    "error_code": record.error_code,
                    "occurred_at": occurred_at,
                }
            )
        except (ValidationError, AttributeError, TypeError, ValueError):
            raise LedgerWriteError("ledger_event_invalid") from None

    def _write_record_and_event(
        self,
        transaction: Any,
        reference: Any,
        record: DeploymentRecord,
        *,
        event_type: str,
        from_state: DeploymentState | None,
        create_record: bool = False,
        event_worker_claim_hash: str | None = None,
    ) -> None:
        model = self._domain_to_model(record)
        event = self._event(
            record,
            event_type=event_type,
            from_state=from_state,
            occurred_at=record.updated_at or self._now(),
            worker_claim_hash=event_worker_claim_hash,
        )
        record_data = model.model_dump(mode="python")
        event_data = event.model_dump(mode="python")
        if create_record:
            transaction.create(reference, record_data)
        else:
            transaction.set(reference, record_data)
        event_reference = reference.collection(self.EVENTS_SUBCOLLECTION).document(event.event_id)
        transaction.create(event_reference, event_data)

    @staticmethod
    def _assert_version(record: DeploymentRecord, expected_version: int | None) -> None:
        if expected_version is not None and record.version != expected_version:
            raise InvalidStateTransition("stale deployment version")

    def create_or_get(self, candidate: DeploymentRecord) -> tuple[DeploymentRecord, bool]:
        digest = candidate.contract_hash.removeprefix("sha256:")
        if (
            candidate.deployment_id != f"{candidate.deployment_key}--{digest}"
            or candidate.contract_label != f"tho-contract-{digest[:12]}"
        ):
            raise LedgerConflict("deployment identity conflict")
        now = self._now()
        candidate = replace(
            candidate,
            state=DeploymentState.INTERNAL_DRAFT,
            version=1,
            worker_claim_hash=None,
            claim_expires_at=None,
            create_fenced_at=None,
            create_fence_claim_hash=None,
            provider_reference_hash=None,
            error_code=None,
            created_at=now,
            updated_at=now,
        )
        # Validate before deriving a Firestore document path.
        candidate_model = self._domain_to_model(candidate)
        candidate = self._model_to_domain(candidate_model)
        reference = self._reference(candidate.deployment_id)

        def operation(transaction):
            snapshot = reference.get(transaction=transaction, timeout=FIRESTORE_RPC_TIMEOUT)
            if snapshot.exists:
                existing = self._snapshot_to_domain(snapshot)
                if (
                    existing.deployment_key,
                    existing.contract_hash,
                    existing.contract_label,
                ) != (
                    candidate.deployment_key,
                    candidate.contract_hash,
                    candidate.contract_label,
                ):
                    raise LedgerConflict("deployment identity conflict")
                return existing, False
            self._write_record_and_event(
                transaction,
                reference,
                candidate,
                event_type="INTERNAL_DRAFT_CREATED",
                from_state=None,
                create_record=True,
            )
            return candidate, True

        return self._run_transaction(operation)

    def get(self, deployment_id: str) -> DeploymentRecord:
        try:
            snapshot = self._reference(deployment_id).get(timeout=FIRESTORE_RPC_TIMEOUT)
            return self._snapshot_to_domain(snapshot)
        except ControlPlaneError:
            raise
        except Exception:
            raise LedgerWriteError("ledger_read_failed") from None

    def list_events(
        self, deployment_id: str, *, limit: int = 20
    ) -> list[GoogleAdsAuthorityEventRecord]:
        """Read a bounded, strictly validated append-only authority history."""
        if not 1 <= limit <= 100:
            raise ValueError("event limit must be between 1 and 100")
        try:
            collection = self._reference(deployment_id).collection(self.EVENTS_SUBCOLLECTION)
            snapshots = (
                collection.order_by("record_version", direction="DESCENDING")
                .limit(limit)
                .stream(timeout=FIRESTORE_RPC_TIMEOUT)
            )
            events = [
                GoogleAdsAuthorityEventRecord.model_validate(snapshot.to_dict() or {})
                for snapshot in snapshots
            ]
            events.sort(key=lambda event: event.record_version)
            return events[-limit:]
        except ControlPlaneError:
            raise
        except (ValidationError, AttributeError, TypeError, ValueError):
            raise LedgerWriteError("ledger_event_invalid") from None
        except Exception:
            raise LedgerWriteError("ledger_read_failed") from None

    def get_access_evidence(
        self,
        deployment_id: str,
        check_key: AccessCheckKey,
    ) -> AccessEvidence:
        if not isinstance(check_key, AccessCheckKey):
            raise LedgerWriteError("access_evidence_invalid")
        try:
            reference = self._reference(deployment_id)
            snapshot = (
                reference.collection(self.ACCESS_EVIDENCE_SUBCOLLECTION)
                .document(check_key.value)
                .get(timeout=FIRESTORE_RPC_TIMEOUT)
            )
            return self._snapshot_to_access_evidence(snapshot)
        except ControlPlaneError:
            raise
        except Exception:
            raise LedgerWriteError("access_evidence_read_failed") from None

    def record_access_evidence(
        self,
        evidence: AccessEvidence,
        *,
        expected_version: int,
    ) -> AccessEvidence:
        """CAS-write current evidence and its immutable event atomically."""
        try:
            evidence = validate_access_evidence(evidence, now=self._now())
            model = GoogleAdsAccessEvidenceRecord.model_validate(evidence_payload(evidence))
        except (InvalidAccessEvidence, ValidationError, AttributeError, TypeError, ValueError):
            raise LedgerWriteError("access_evidence_invalid") from None
        if isinstance(expected_version, bool) or not isinstance(expected_version, int):
            raise InvalidStateTransition("stale deployment version")

        deployment_reference = self._reference(evidence.deployment_id)
        evidence_reference = deployment_reference.collection(
            self.ACCESS_EVIDENCE_SUBCOLLECTION
        ).document(evidence.check_key.value)
        event_reference = deployment_reference.collection(
            self.ACCESS_EVIDENCE_EVENTS_SUBCOLLECTION
        ).document(evidence.evidence_digest.removeprefix("sha256:"))
        payload = model.model_dump(mode="python")

        def operation(transaction):
            deployment_snapshot = deployment_reference.get(
                transaction=transaction,
                timeout=FIRESTORE_RPC_TIMEOUT,
            )
            authority = self._snapshot_to_domain(deployment_snapshot)
            if authority.version != expected_version:
                raise InvalidStateTransition("stale deployment version")

            current_snapshot = evidence_reference.get(
                transaction=transaction,
                timeout=FIRESTORE_RPC_TIMEOUT,
            )
            if current_snapshot.exists:
                try:
                    current_model = GoogleAdsAccessEvidenceRecord.model_validate(
                        current_snapshot.to_dict() or {}
                    )
                    current = AccessEvidence(
                        deployment_id=current_model.deployment_id,
                        check_key=AccessCheckKey(current_model.check_key),
                        status=AccessEvidenceStatus(current_model.status),
                        observed_at=current_model.observed_at,
                        expires_at=current_model.expires_at,
                        source_revision=current_model.source_revision,
                        evidence_digest=current_model.evidence_digest,
                    )
                except (ValidationError, AttributeError, TypeError, ValueError):
                    raise LedgerWriteError("access_evidence_invalid") from None
                if current.evidence_digest == evidence.evidence_digest:
                    return current
                if current.observed_at >= evidence.observed_at:
                    raise LedgerConflict("access evidence conflict")

            transaction.set(evidence_reference, payload)
            transaction.create(event_reference, payload)
            return evidence

        return self._run_transaction(operation)

    def transition(
        self,
        deployment_id: str,
        *,
        expected: DeploymentState,
        target: DeploymentState,
        expected_version: int | None = None,
        server_validation_key_hash: str | None = None,
    ) -> DeploymentRecord:
        reference = self._reference(deployment_id)
        event_type = _TRANSITION_EVENTS.get(target)
        if _ALLOWED_TRANSITIONS.get(expected) is not target or event_type is None:
            raise InvalidStateTransition(f"invalid transition: {expected} -> {target}")

        def operation(transaction):
            record = self._snapshot_to_domain(
                reference.get(transaction=transaction, timeout=FIRESTORE_RPC_TIMEOUT)
            )
            operation_reference = reference.collection(self.OPERATION_KEYS_SUBCOLLECTION).document(
                "server-validation"
            )
            operation_snapshot = None
            if target is DeploymentState.SERVER_VALIDATED and server_validation_key_hash:
                operation_snapshot = operation_reference.get(
                    transaction=transaction,
                    timeout=FIRESTORE_RPC_TIMEOUT,
                )
            if (
                target is DeploymentState.SERVER_VALIDATED
                and record.state is target
                and expected_version in {None, record.version, record.version - 1}
            ):
                if server_validation_key_hash is None:
                    return record
                try:
                    marker = GoogleAdsOperationKeyRecord.model_validate(
                        operation_snapshot.to_dict() if operation_snapshot.exists else {}
                    )
                except (ValidationError, AttributeError, TypeError, ValueError):
                    raise InvalidStateTransition(
                        "server validation replay evidence is missing"
                    ) from None
                if (
                    marker.key_hash == server_validation_key_hash
                    and marker.deployment_id == record.deployment_id
                    and marker.contract_hash == record.contract_hash
                    and marker.record_version == record.version
                    and marker.created_at == record.updated_at
                ):
                    return record
                raise InvalidStateTransition("server validation idempotency key conflicts")
            self._assert_version(record, expected_version)
            if record.state is not expected:
                raise InvalidStateTransition(f"invalid transition: {expected} -> {target}")
            if operation_snapshot is not None and operation_snapshot.exists:
                raise InvalidStateTransition("server validation idempotency evidence conflicts")
            updated = replace(
                record,
                state=target,
                version=record.version + 1,
                worker_claim_hash=None,
                claim_expires_at=None,
                create_fenced_at=None,
                create_fence_claim_hash=None,
                error_code=None,
                updated_at=self._now(),
            )
            self._write_record_and_event(
                transaction,
                reference,
                updated,
                event_type=event_type,
                from_state=record.state,
            )
            if server_validation_key_hash:
                marker = GoogleAdsOperationKeyRecord.model_validate(
                    {
                        "schema_version": 1,
                        "operation": "SERVER_VALIDATION",
                        "deployment_id": updated.deployment_id,
                        "contract_hash": updated.contract_hash,
                        "key_hash": server_validation_key_hash,
                        "record_version": 2,
                        "created_at": updated.updated_at,
                    }
                )
                transaction.create(operation_reference, marker.model_dump(mode="python"))
            return updated

        return self._run_transaction(operation)

    def claim_paused_create(
        self,
        deployment_id: str,
        claimant: str,
        *,
        expected_version: int | None = None,
    ) -> bool:
        claimant_hash = _claimant_hash(claimant)
        reference = self._reference(deployment_id)

        def operation(transaction):
            record = self._snapshot_to_domain(
                reference.get(transaction=transaction, timeout=FIRESTORE_RPC_TIMEOUT)
            )
            self._assert_version(record, expected_version)
            if record.state is not DeploymentState.PAUSED_CREATE_APPROVED:
                return False
            now = self._now()
            live_claim = record.worker_claim_hash is not None and (
                record.claim_expires_at is None or record.claim_expires_at > now
            )
            if live_claim or record.create_fenced_at is not None:
                return False
            event_type = (
                "PAUSED_CREATE_RECLAIMED"
                if record.worker_claim_hash is not None
                else "PAUSED_CREATE_CLAIMED"
            )
            updated = replace(
                record,
                version=record.version + 1,
                worker_claim_hash=claimant_hash,
                claim_expires_at=now + timedelta(seconds=self._claim_lease_seconds),
                error_code=None,
                updated_at=now,
            )
            self._write_record_and_event(
                transaction,
                reference,
                updated,
                event_type=event_type,
                from_state=record.state,
            )
            return True

        return self._run_transaction(operation)

    def fence_paused_create(
        self,
        deployment_id: str,
        claimant: str,
        *,
        expected_version: int | None = None,
    ) -> DeploymentRecord:
        """Permanently fence one provider create before crossing that boundary.

        Once this atomic marker exists, an expired claim cannot be reclaimed.
        The original claimant may finish after lease expiry, but no replacement
        may start a second provider mutation. An ambiguous provider outcome must
        therefore be reconciled, never blindly retried.
        """
        claimant_hash = _claimant_hash(claimant)
        reference = self._reference(deployment_id)

        def operation(transaction):
            record = self._snapshot_to_domain(
                reference.get(transaction=transaction, timeout=FIRESTORE_RPC_TIMEOUT)
            )
            self._assert_version(record, expected_version)
            now = self._now()
            if record.state is not DeploymentState.PAUSED_CREATE_APPROVED:
                raise InvalidStateTransition("paused create is not actively claimed by this worker")
            if record.create_fenced_at is not None:
                if claimant_hash in {
                    record.worker_claim_hash,
                    record.create_fence_claim_hash,
                }:
                    return record
                raise InvalidStateTransition("paused create is fenced by another worker")
            if record.worker_claim_hash != claimant_hash:
                raise InvalidStateTransition("paused create is not actively claimed by this worker")
            if record.claim_expires_at is None or record.claim_expires_at <= now:
                raise InvalidStateTransition("paused create is not actively claimed by this worker")
            updated = replace(
                record,
                version=record.version + 1,
                create_fenced_at=now,
                create_fence_claim_hash=claimant_hash,
                error_code=None,
                updated_at=now,
            )
            self._write_record_and_event(
                transaction,
                reference,
                updated,
                event_type="PAUSED_CREATE_FENCED",
                from_state=record.state,
            )
            return updated

        return self._run_transaction(operation)

    def claim_fenced_reconciliation(
        self,
        deployment_id: str,
        claimant: str,
        *,
        expected_version: int | None = None,
    ) -> bool:
        """Lease fenced work for one reconciliation lookup, never a create."""
        claimant_hash = _claimant_hash(claimant)
        reference = self._reference(deployment_id)

        def operation(transaction):
            record = self._snapshot_to_domain(
                reference.get(transaction=transaction, timeout=FIRESTORE_RPC_TIMEOUT)
            )
            self._assert_version(record, expected_version)
            now = self._now()
            live_claim = record.worker_claim_hash is not None and (
                record.claim_expires_at is None or record.claim_expires_at > now
            )
            if (
                record.state is not DeploymentState.PAUSED_CREATE_APPROVED
                or record.create_fenced_at is None
                or live_claim
            ):
                return False
            updated = replace(
                record,
                version=record.version + 1,
                worker_claim_hash=claimant_hash,
                claim_expires_at=now + timedelta(seconds=self._claim_lease_seconds),
                error_code=None,
                updated_at=now,
            )
            self._write_record_and_event(
                transaction,
                reference,
                updated,
                event_type="PAUSED_CREATE_RECONCILIATION_CLAIMED",
                from_state=record.state,
            )
            return True

        return self._run_transaction(operation)

    def complete_paused_create(
        self,
        deployment_id: str,
        claimant: str,
        provider_reference_hash: str,
        *,
        expected_version: int | None = None,
    ) -> DeploymentRecord:
        claimant_hash = _claimant_hash(claimant)
        reference = self._reference(deployment_id)

        def operation(transaction):
            record = self._snapshot_to_domain(
                reference.get(transaction=transaction, timeout=FIRESTORE_RPC_TIMEOUT)
            )
            self._assert_version(record, expected_version)
            now = self._now()
            if record.state is DeploymentState.PAUSED_CREATED:
                if record.provider_reference_hash == provider_reference_hash:
                    return record
                raise InvalidStateTransition("provider reference conflicts with completed create")
            if (
                record.state is not DeploymentState.PAUSED_CREATE_APPROVED
                or record.create_fenced_at is None
                or record.claim_expires_at is None
                or claimant_hash not in {record.worker_claim_hash, record.create_fence_claim_hash}
            ):
                raise InvalidStateTransition("paused create is not actively claimed by this worker")
            updated = replace(
                record,
                state=DeploymentState.PAUSED_CREATED,
                version=record.version + 1,
                worker_claim_hash=None,
                claim_expires_at=None,
                create_fence_claim_hash=None,
                provider_reference_hash=provider_reference_hash,
                error_code=None,
                updated_at=now,
            )
            self._write_record_and_event(
                transaction,
                reference,
                updated,
                event_type="PAUSED_CREATE_COMPLETED",
                from_state=record.state,
                event_worker_claim_hash=claimant_hash,
            )
            return updated

        return self._run_transaction(operation)

    def release_claim(
        self,
        deployment_id: str,
        claimant: str,
        error_code: str,
        *,
        expected_version: int | None = None,
    ) -> DeploymentRecord:
        if error_code not in PERSISTED_ERROR_CODES:
            raise ValueError("error_code is not in the sanitized allowlist")
        claimant_hash = _claimant_hash(claimant)
        reference = self._reference(deployment_id)

        def operation(transaction):
            record = self._snapshot_to_domain(
                reference.get(transaction=transaction, timeout=FIRESTORE_RPC_TIMEOUT)
            )
            self._assert_version(record, expected_version)
            if (
                record.state is not DeploymentState.PAUSED_CREATE_APPROVED
                or record.worker_claim_hash != claimant_hash
                or record.create_fenced_at is not None
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
            self._write_record_and_event(
                transaction,
                reference,
                updated,
                event_type="PAUSED_CREATE_CLAIM_RELEASED",
                from_state=record.state,
                event_worker_claim_hash=record.worker_claim_hash,
            )
            return updated

        return self._run_transaction(operation)

    def mark_fenced_failure(
        self,
        deployment_id: str,
        claimant: str,
        error_code: str,
        *,
        expected_version: int | None = None,
    ) -> DeploymentRecord:
        """Persist a safe failure without reopening a fenced provider mutation."""
        if error_code not in PERSISTED_ERROR_CODES:
            raise ValueError("error_code is not in the sanitized allowlist")
        claimant_hash = _claimant_hash(claimant)
        reference = self._reference(deployment_id)

        def operation(transaction):
            record = self._snapshot_to_domain(
                reference.get(transaction=transaction, timeout=FIRESTORE_RPC_TIMEOUT)
            )
            self._assert_version(record, expected_version)
            if (
                record.state is not DeploymentState.PAUSED_CREATE_APPROVED
                or record.create_fenced_at is None
                or claimant_hash not in {record.worker_claim_hash, record.create_fence_claim_hash}
            ):
                raise InvalidStateTransition("paused create is not fenced by this worker")
            updated = replace(
                record,
                version=record.version + 1,
                error_code=error_code,
                updated_at=self._now(),
            )
            self._write_record_and_event(
                transaction,
                reference,
                updated,
                event_type="PAUSED_CREATE_FENCED_FAILED",
                from_state=record.state,
                event_worker_claim_hash=claimant_hash,
            )
            return updated

        return self._run_transaction(operation)
