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
    MAX_GOOGLE_ADS_DISPATCH_ATTEMPTS,
    GoogleAdsAccessEvidenceRecord,
    GoogleAdsAuthorityEventRecord,
    GoogleAdsDeploymentRecord,
    GoogleAdsOperationKeyRecord,
    GoogleAdsOwnerStepUpEvidenceRecord,
    GoogleAdsPausedCreateApprovalProofRecord,
    GoogleAdsPausedCreateOutboxRecord,
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
    APPROVAL_PROOFS_SUBCOLLECTION = "approval_proofs"
    DISPATCH_OUTBOX_SUBCOLLECTION = "dispatch_outbox"
    PAUSED_CREATE_OUTBOX_ID = "paused-create"
    STEP_UP_NONCE_COLLECTION = "google_ads_owner_step_up_nonces"
    STEP_UP_EVIDENCE_SUBCOLLECTION = "verified_evidence"

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

    def _snapshot_to_access_evidence(
        self,
        snapshot: Any,
        *,
        expected_deployment_id: str,
        expected_check_key: AccessCheckKey,
        require_fresh: bool = True,
    ) -> AccessEvidence:
        if not getattr(snapshot, "exists", False):
            raise LedgerWriteError("access_evidence_missing")
        try:
            model = GoogleAdsAccessEvidenceRecord.model_validate(snapshot.to_dict() or {})
            if (
                model.deployment_id != expected_deployment_id
                or model.check_key != expected_check_key.value
            ):
                raise ValueError("access evidence document identity mismatch")
            evidence = AccessEvidence(
                deployment_id=model.deployment_id,
                check_key=AccessCheckKey(model.check_key),
                status=AccessEvidenceStatus(model.status),
                observed_at=model.observed_at,
                expires_at=model.expires_at,
                source_revision=model.source_revision,
                evidence_digest=model.evidence_digest,
            )
            if require_fresh:
                return validate_access_evidence(evidence, now=self._now())
            return evidence
        except (InvalidAccessEvidence, ValidationError, AttributeError, TypeError, ValueError):
            raise LedgerWriteError("access_evidence_invalid") from None

    @staticmethod
    def _snapshot_to_step_up_evidence(snapshot: Any) -> GoogleAdsOwnerStepUpEvidenceRecord:
        if not getattr(snapshot, "exists", False):
            raise InvalidStateTransition("owner proof is unavailable")
        try:
            return GoogleAdsOwnerStepUpEvidenceRecord.model_validate(snapshot.to_dict() or {})
        except (ValidationError, AttributeError, TypeError, ValueError):
            raise LedgerWriteError("owner_proof_invalid") from None

    @staticmethod
    def _snapshot_to_approval_proof(
        snapshot: Any,
    ) -> GoogleAdsPausedCreateApprovalProofRecord:
        if not getattr(snapshot, "exists", False):
            raise InvalidStateTransition("owner proof replay evidence is unavailable")
        try:
            return GoogleAdsPausedCreateApprovalProofRecord.model_validate(snapshot.to_dict() or {})
        except (ValidationError, AttributeError, TypeError, ValueError):
            raise LedgerWriteError("owner_proof_invalid") from None

    @staticmethod
    def _snapshot_to_outbox(snapshot: Any) -> GoogleAdsPausedCreateOutboxRecord:
        if not getattr(snapshot, "exists", False):
            raise InvalidStateTransition("paused-create outbox is unavailable")
        try:
            return GoogleAdsPausedCreateOutboxRecord.model_validate(snapshot.to_dict() or {})
        except (ValidationError, AttributeError, TypeError, ValueError):
            raise LedgerWriteError("paused_create_outbox_invalid") from None

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
            return self._snapshot_to_access_evidence(
                snapshot,
                expected_deployment_id=deployment_id,
                expected_check_key=check_key,
            )
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
                current = self._snapshot_to_access_evidence(
                    current_snapshot,
                    expected_deployment_id=evidence.deployment_id,
                    expected_check_key=evidence.check_key,
                    require_fresh=False,
                )
                if current.evidence_digest == evidence.evidence_digest:
                    return current
                if current.observed_at >= evidence.observed_at:
                    raise LedgerConflict("access evidence conflict")

            transaction.set(evidence_reference, payload)
            transaction.create(event_reference, payload)
            return evidence

        return self._run_transaction(operation)

    def approve_paused_create_with_proof(
        self,
        *,
        deployment_id: str,
        expected_version: int,
        contract_hash: str,
        expected_caps: dict[str, int],
        proof: Any,
        access_evidence_id: str,
    ) -> dict[str, Any]:
        """Atomically consume one UV proof and append approval plus outbox intent."""
        if isinstance(expected_version, bool) or expected_version != 2:
            raise InvalidStateTransition("stale deployment version")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", contract_hash):
            raise InvalidStateTransition("reviewed contract changed")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", access_evidence_id):
            raise InvalidStateTransition("access evidence changed")
        required_proof_fields = (
            "proof_id",
            "nonce_hash",
            "deployment_id",
            "contract_hash",
            "access_evidence_id",
            "owner_email_hash",
            "proof_reference_hash",
            "purpose",
        )
        if any(not hasattr(proof, field) for field in required_proof_fields):
            raise InvalidStateTransition("owner proof is invalid")
        if (
            proof.purpose != "PAUSED_CREATE"
            or proof.deployment_id != deployment_id
            or proof.contract_hash != contract_hash
            or proof.access_evidence_id != access_evidence_id
        ):
            raise InvalidStateTransition("owner proof changed")

        deployment_reference = self._reference(deployment_id)
        access_reference = deployment_reference.collection(
            self.ACCESS_EVIDENCE_SUBCOLLECTION
        ).document(AccessCheckKey.GOOGLE_ADS_ACCOUNT_ACCESS_AND_USD_GREEN.value)
        source_reference = (
            self._client.collection(self.STEP_UP_NONCE_COLLECTION)
            .document(proof.nonce_hash)
            .collection(self.STEP_UP_EVIDENCE_SUBCOLLECTION)
            .document(proof.proof_id)
        )
        marker_reference = deployment_reference.collection(
            self.APPROVAL_PROOFS_SUBCOLLECTION
        ).document(proof.proof_id)
        outbox_reference = deployment_reference.collection(
            self.DISPATCH_OUTBOX_SUBCOLLECTION
        ).document(self.PAUSED_CREATE_OUTBOX_ID)

        def operation(transaction):
            now = self._now()
            record = self._snapshot_to_domain(
                deployment_reference.get(
                    transaction=transaction,
                    timeout=FIRESTORE_RPC_TIMEOUT,
                )
            )
            marker_snapshot = marker_reference.get(
                transaction=transaction,
                timeout=FIRESTORE_RPC_TIMEOUT,
            )
            outbox_snapshot = outbox_reference.get(
                transaction=transaction,
                timeout=FIRESTORE_RPC_TIMEOUT,
            )
            if marker_snapshot.exists or record.state in {
                DeploymentState.PAUSED_CREATE_APPROVED,
                DeploymentState.PAUSED_CREATED,
            }:
                marker = self._snapshot_to_approval_proof(marker_snapshot)
                outbox = self._snapshot_to_outbox(outbox_snapshot)
                if (
                    marker.proof_id != proof.proof_id
                    or marker.proof_reference_hash != proof.proof_reference_hash
                    or marker.deployment_id != deployment_id
                    or marker.contract_hash != contract_hash
                    or marker.access_evidence_id != access_evidence_id
                    or marker.owner_email_hash != proof.owner_email_hash
                    or outbox.proof_id != proof.proof_id
                    or outbox.access_evidence_id != access_evidence_id
                    or outbox.deployment_id != deployment_id
                    or outbox.contract_hash != contract_hash
                    or expected_version != marker.authority_from_version
                ):
                    raise InvalidStateTransition("owner approval replay conflicts")
                return {
                    "deployment_id": record.deployment_id,
                    "contract_hash": record.contract_hash,
                    "state": record.state.value,
                    "version": record.version,
                    "outbox_state": outbox.state,
                    "replayed": True,
                }

            self._assert_version(record, expected_version)
            if (
                record.state is not DeploymentState.SERVER_VALIDATED
                or record.contract_hash != contract_hash
                or outbox_snapshot.exists
            ):
                raise InvalidStateTransition("deployment is not ready for paused approval")
            access = self._snapshot_to_access_evidence(
                access_reference.get(
                    transaction=transaction,
                    timeout=FIRESTORE_RPC_TIMEOUT,
                ),
                expected_deployment_id=deployment_id,
                expected_check_key=AccessCheckKey.GOOGLE_ADS_ACCOUNT_ACCESS_AND_USD_GREEN,
            )
            source = self._snapshot_to_step_up_evidence(
                source_reference.get(
                    transaction=transaction,
                    timeout=FIRESTORE_RPC_TIMEOUT,
                )
            )
            if (
                access.status is not AccessEvidenceStatus.PASSED
                or access.evidence_digest != access_evidence_id
                or source.purpose != "PAUSED_CREATE"
                or source.evidence_id != proof.proof_id
                or source.nonce_hash != proof.nonce_hash
                or source.deployment_id != deployment_id
                or source.contract_hash != contract_hash
                or source.evidence_digest != access_evidence_id
                or source.owner_email_hash != proof.owner_email_hash
                or source.caps.model_dump() != expected_caps
                or source.verified_at > now
                or now >= source.verified_at + timedelta(seconds=300)
            ):
                raise InvalidStateTransition("owner proof or access evidence changed")

            updated = replace(
                record,
                state=DeploymentState.PAUSED_CREATE_APPROVED,
                version=3,
                worker_claim_hash=None,
                claim_expires_at=None,
                create_fenced_at=None,
                create_fence_claim_hash=None,
                error_code=None,
                updated_at=now,
            )
            marker = GoogleAdsPausedCreateApprovalProofRecord(
                proof_id=proof.proof_id,
                proof_reference_hash=proof.proof_reference_hash,
                deployment_id=deployment_id,
                contract_hash=contract_hash,
                access_evidence_id=access_evidence_id,
                owner_email_hash=proof.owner_email_hash,
                authority_from_version=2,
                authority_to_version=3,
                consumed_at=now,
            )
            outbox = GoogleAdsPausedCreateOutboxRecord(
                outbox_id=self.PAUSED_CREATE_OUTBOX_ID,
                deployment_id=deployment_id,
                contract_hash=contract_hash,
                approval_record_version=3,
                proof_id=proof.proof_id,
                access_evidence_id=access_evidence_id,
                state="PENDING",
                attempt_count=0,
                dispatcher_claim_hash=None,
                claim_expires_at=None,
                error_code=None,
                created_at=now,
                updated_at=now,
                dispatched_at=None,
            )
            self._write_record_and_event(
                transaction,
                deployment_reference,
                updated,
                event_type="PAUSED_CREATE_APPROVED",
                from_state=DeploymentState.SERVER_VALIDATED,
            )
            transaction.create(marker_reference, marker.model_dump(mode="python"))
            transaction.create(outbox_reference, outbox.model_dump(mode="python"))
            return {
                "deployment_id": updated.deployment_id,
                "contract_hash": updated.contract_hash,
                "state": updated.state.value,
                "version": updated.version,
                "outbox_state": outbox.state,
                "replayed": False,
            }

        return self._run_transaction(operation)

    def get_paused_create_outbox(self, deployment_id: str) -> GoogleAdsPausedCreateOutboxRecord:
        try:
            snapshot = (
                self._reference(deployment_id)
                .collection(self.DISPATCH_OUTBOX_SUBCOLLECTION)
                .document(self.PAUSED_CREATE_OUTBOX_ID)
                .get(timeout=FIRESTORE_RPC_TIMEOUT)
            )
            return self._snapshot_to_outbox(snapshot)
        except ControlPlaneError:
            raise
        except Exception:
            raise LedgerWriteError("paused_create_outbox_read_failed") from None

    def reconcile_terminal_paused_create_outbox(self, deployment_id: str) -> str:
        """Heal only a terminal authority whose accepted dispatch settlement crashed."""
        deployment_reference = self._reference(deployment_id)
        outbox_reference = deployment_reference.collection(
            self.DISPATCH_OUTBOX_SUBCOLLECTION
        ).document(self.PAUSED_CREATE_OUTBOX_ID)

        def operation(transaction):
            authority = self._snapshot_to_domain(
                deployment_reference.get(
                    transaction=transaction,
                    timeout=FIRESTORE_RPC_TIMEOUT,
                )
            )
            outbox = self._snapshot_to_outbox(
                outbox_reference.get(
                    transaction=transaction,
                    timeout=FIRESTORE_RPC_TIMEOUT,
                )
            )
            if outbox.state == "DISPATCHED":
                return outbox.state
            if authority.state is not DeploymentState.PAUSED_CREATED:
                return outbox.state
            return self._settle_terminal_outbox(
                transaction,
                outbox_reference,
                outbox,
            )

        return self._run_transaction(operation)

    def _settle_terminal_outbox(self, transaction, outbox_reference, outbox) -> str:
        now = self._now()
        settled = GoogleAdsPausedCreateOutboxRecord.model_validate(
            {
                **outbox.model_dump(),
                "state": "DISPATCHED",
                "dispatcher_claim_hash": None,
                "claim_expires_at": None,
                "error_code": None,
                "updated_at": now,
                "dispatched_at": now,
            }
        )
        transaction.set(outbox_reference, settled.model_dump(mode="python"))
        return settled.state

    def claim_paused_create_outbox(self, deployment_id: str, claimant: str) -> bool:
        claimant_hash = _claimant_hash(claimant)
        deployment_reference = self._reference(deployment_id)
        outbox_reference = deployment_reference.collection(
            self.DISPATCH_OUTBOX_SUBCOLLECTION
        ).document(self.PAUSED_CREATE_OUTBOX_ID)

        def operation(transaction):
            now = self._now()
            authority = self._snapshot_to_domain(
                deployment_reference.get(
                    transaction=transaction,
                    timeout=FIRESTORE_RPC_TIMEOUT,
                )
            )
            outbox = self._snapshot_to_outbox(
                outbox_reference.get(
                    transaction=transaction,
                    timeout=FIRESTORE_RPC_TIMEOUT,
                )
            )
            if authority.state is DeploymentState.PAUSED_CREATED or outbox.state in {
                "DISPATCHED",
                "FAILED",
            }:
                return False
            live_claim = outbox.state == "DISPATCHING" and outbox.claim_expires_at > now
            if authority.state is not DeploymentState.PAUSED_CREATE_APPROVED or live_claim:
                return False
            if outbox.attempt_count >= MAX_GOOGLE_ADS_DISPATCH_ATTEMPTS:
                failed = GoogleAdsPausedCreateOutboxRecord.model_validate(
                    {
                        **outbox.model_dump(),
                        "state": "FAILED",
                        "dispatcher_claim_hash": None,
                        "claim_expires_at": None,
                        "error_code": "dispatch_attempts_exhausted",
                        "updated_at": now,
                    }
                )
                transaction.set(outbox_reference, failed.model_dump(mode="python"))
                return False
            claimed = GoogleAdsPausedCreateOutboxRecord.model_validate(
                {
                    **outbox.model_dump(),
                    "state": "DISPATCHING",
                    "attempt_count": outbox.attempt_count + 1,
                    "dispatcher_claim_hash": claimant_hash,
                    "claim_expires_at": now + timedelta(seconds=self._claim_lease_seconds),
                    "error_code": None,
                    "updated_at": now,
                }
            )
            transaction.set(outbox_reference, claimed.model_dump(mode="python"))
            return True

        return self._run_transaction(operation)

    def release_paused_create_outbox(self, deployment_id: str, claimant: str) -> None:
        claimant_hash = _claimant_hash(claimant)
        outbox_reference = (
            self._reference(deployment_id)
            .collection(self.DISPATCH_OUTBOX_SUBCOLLECTION)
            .document(self.PAUSED_CREATE_OUTBOX_ID)
        )

        def operation(transaction):
            outbox = self._snapshot_to_outbox(
                outbox_reference.get(
                    transaction=transaction,
                    timeout=FIRESTORE_RPC_TIMEOUT,
                )
            )
            if outbox.state != "DISPATCHING" or outbox.dispatcher_claim_hash != claimant_hash:
                raise InvalidStateTransition("paused-create outbox is not claimed")
            exhausted = outbox.attempt_count >= MAX_GOOGLE_ADS_DISPATCH_ATTEMPTS
            pending = GoogleAdsPausedCreateOutboxRecord.model_validate(
                {
                    **outbox.model_dump(),
                    "state": "FAILED" if exhausted else "PENDING",
                    "dispatcher_claim_hash": None,
                    "claim_expires_at": None,
                    "error_code": (
                        "dispatch_attempts_exhausted" if exhausted else "job_invocation_failed"
                    ),
                    "updated_at": self._now(),
                }
            )
            transaction.set(outbox_reference, pending.model_dump(mode="python"))

        self._run_transaction(operation)

    def record_paused_create_worker_failure(
        self,
        deployment_id: str,
        dispatcher_claim_hash: str,
    ) -> str:
        """Re-arm one accepted execution, bounded by the durable attempt cap."""
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", dispatcher_claim_hash):
            raise ValueError("dispatcher claim hash is invalid")
        deployment_reference = self._reference(deployment_id)
        outbox_reference = deployment_reference.collection(
            self.DISPATCH_OUTBOX_SUBCOLLECTION
        ).document(self.PAUSED_CREATE_OUTBOX_ID)

        def operation(transaction):
            authority = self._snapshot_to_domain(
                deployment_reference.get(
                    transaction=transaction,
                    timeout=FIRESTORE_RPC_TIMEOUT,
                )
            )
            outbox = self._snapshot_to_outbox(
                outbox_reference.get(
                    transaction=transaction,
                    timeout=FIRESTORE_RPC_TIMEOUT,
                )
            )
            if authority.state is DeploymentState.PAUSED_CREATED:
                return self._settle_terminal_outbox(
                    transaction,
                    outbox_reference,
                    outbox,
                )
            if authority.state is not DeploymentState.PAUSED_CREATE_APPROVED:
                raise InvalidStateTransition("paused-create authority is not retryable")
            if outbox.state in {"PENDING", "FAILED"}:
                return outbox.state
            if (
                outbox.state != "DISPATCHING"
                or outbox.dispatcher_claim_hash != dispatcher_claim_hash
            ):
                raise InvalidStateTransition("paused-create outbox is not executing")
            exhausted = outbox.attempt_count >= MAX_GOOGLE_ADS_DISPATCH_ATTEMPTS
            updated = GoogleAdsPausedCreateOutboxRecord.model_validate(
                {
                    **outbox.model_dump(),
                    "state": "FAILED" if exhausted else "PENDING",
                    "dispatcher_claim_hash": None,
                    "claim_expires_at": None,
                    "error_code": ("dispatch_attempts_exhausted" if exhausted else "worker_failed"),
                    "updated_at": self._now(),
                }
            )
            transaction.set(outbox_reference, updated.model_dump(mode="python"))
            return updated.state

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
        require_dispatch_outbox: bool = False,
    ) -> bool:
        claimant_hash = _claimant_hash(claimant)
        reference = self._reference(deployment_id)
        outbox_reference = reference.collection(self.DISPATCH_OUTBOX_SUBCOLLECTION).document(
            self.PAUSED_CREATE_OUTBOX_ID
        )

        def operation(transaction):
            record = self._snapshot_to_domain(
                reference.get(transaction=transaction, timeout=FIRESTORE_RPC_TIMEOUT)
            )
            self._assert_version(record, expected_version)
            if record.state is not DeploymentState.PAUSED_CREATE_APPROVED:
                return False
            if require_dispatch_outbox:
                outbox = self._snapshot_to_outbox(
                    outbox_reference.get(
                        transaction=transaction,
                        timeout=FIRESTORE_RPC_TIMEOUT,
                    )
                )
                if outbox.state != "DISPATCHING":
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
