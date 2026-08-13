"""Owner-only WebAuthn step-up evidence primitives for future PAUSED creation.

This module records authentication evidence only. It contains no approval
transition, provider, worker, job, campaign, activation, or spend capability.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime
from functools import lru_cache
from threading import Lock
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from auth.store import CredentialStore, CredentialUsageCAS, FirestoreCredentialStore
from database.models import GoogleAdsAccessEvidenceRecord
from database.rpc_timeout import FIRESTORE_RPC_TIMEOUT, FIRESTORE_TRANSACTION_TIMEOUT
from scripts.google_ads_access_evidence import (
    AccessCheckKey,
    AccessEvidence,
    AccessEvidenceStatus,
    InvalidAccessEvidence,
    evidence_payload,
    validate_access_evidence,
)

PAUSED_CREATE_PURPOSE = "PAUSED_CREATE"
PAUSED_CREATE_PROOF_FLOW = "google-ads-paused-create-proof-v1"
MAX_NONCE_TTL_SECONDS = 300
_SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"
_DEPLOYMENT_PATTERN = r"^[a-z0-9][a-z0-9-]{0,62}--[0-9a-f]{64}$"
_T = TypeVar("_T")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def hash_value(value: str | bytes) -> str:
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def email_hash(email: str) -> str:
    return hash_value(email.strip().lower())


class StepUpCaps(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    average_daily_usd: int = Field(gt=0)
    max_single_day_charge_usd: int = Field(gt=0)
    monthly_charge_limit_usd: int = Field(gt=0)
    max_cpc_usd: int = Field(gt=0)


class StepUpContext(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    purpose: Literal["PAUSED_CREATE"] = PAUSED_CREATE_PURPOSE
    deployment_id: str = Field(pattern=_DEPLOYMENT_PATTERN)
    contract_hash: str = Field(pattern=_SHA256_PATTERN)
    caps: StepUpCaps
    evidence_digest: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def deployment_matches_contract(self):
        digest = self.contract_hash.removeprefix("sha256:")
        if not self.deployment_id.endswith(f"--{digest}"):
            raise ValueError("deployment identity does not match contract hash")
        return self


def _canonical_model(model: BaseModel) -> bytes:
    return json.dumps(
        model.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def context_digest(context: StepUpContext) -> str:
    return hash_value(_canonical_model(context))


class StepUpNonce(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal[1] = 1
    nonce_hash: str = Field(pattern=_SHA256_PATTERN)
    context_digest: str = Field(pattern=_SHA256_PATTERN)
    owner_email_hash: str = Field(pattern=_SHA256_PATTERN)
    issued_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def has_short_utc_lifetime(self):
        issued = _utc(self.issued_at)
        expires = _utc(self.expires_at)
        lifetime = (expires - issued).total_seconds()
        if lifetime <= 0 or lifetime > MAX_NONCE_TTL_SECONDS:
            raise ValueError("step-up nonce lifetime must be at most five minutes")
        return self


class StepUpEvidenceEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal[1] = 1
    evidence_id: str = Field(pattern=_SHA256_PATTERN)
    purpose: Literal["PAUSED_CREATE"] = PAUSED_CREATE_PURPOSE
    deployment_id: str = Field(pattern=_DEPLOYMENT_PATTERN)
    contract_hash: str = Field(pattern=_SHA256_PATTERN)
    caps: StepUpCaps
    evidence_digest: str = Field(pattern=_SHA256_PATTERN)
    context_digest: str = Field(pattern=_SHA256_PATTERN)
    nonce_hash: str = Field(pattern=_SHA256_PATTERN)
    owner_email_hash: str = Field(pattern=_SHA256_PATTERN)
    credential_id_hash: str = Field(pattern=_SHA256_PATTERN)
    verified_at: datetime

    @model_validator(mode="after")
    def identity_and_digest_are_consistent(self):
        context = StepUpContext(
            purpose=self.purpose,
            deployment_id=self.deployment_id,
            contract_hash=self.contract_hash,
            caps=self.caps,
            evidence_digest=self.evidence_digest,
        )
        if self.context_digest != context_digest(context):
            raise ValueError("evidence context digest mismatch")
        _utc(self.verified_at)
        return self


class StepUpProofReference(BaseModel):
    """Verified, purpose-bound fields recovered from one signed opaque reference."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    purpose: Literal["PAUSED_CREATE"] = PAUSED_CREATE_PURPOSE
    proof_id: str = Field(pattern=_SHA256_PATTERN)
    nonce_hash: str = Field(pattern=_SHA256_PATTERN)
    deployment_id: str = Field(pattern=_DEPLOYMENT_PATTERN)
    contract_hash: str = Field(pattern=_SHA256_PATTERN)
    access_evidence_id: str = Field(pattern=_SHA256_PATTERN)
    owner_email_hash: str = Field(pattern=_SHA256_PATTERN)
    proof_reference_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def deployment_matches_contract(self):
        if not self.deployment_id.endswith(f"--{self.contract_hash.removeprefix('sha256:')}"):
            raise ValueError("proof deployment identity does not match contract")
        return self


def issue_proof_reference(manager: Any, envelope: StepUpEvidenceEnvelope) -> str:
    """Issue a short-lived signed reference containing sanitized IDs only."""
    return manager.wrap_challenge(
        envelope.evidence_id.encode("ascii"),
        flow=PAUSED_CREATE_PROOF_FLOW,
        purpose=envelope.purpose,
        proof_id=envelope.evidence_id,
        nonce_hash=envelope.nonce_hash,
        deployment_id=envelope.deployment_id,
        contract_hash=envelope.contract_hash,
        access_evidence_id=envelope.evidence_digest,
        owner_email_hash=envelope.owner_email_hash,
    )


def verify_proof_reference(manager: Any, reference: str | None) -> StepUpProofReference | None:
    """Verify one signed proof reference without exposing signature/parser details."""
    payload = manager.unwrap_challenge_payload(reference, flow=PAUSED_CREATE_PROOF_FLOW)
    if not payload:
        return None
    try:
        proof = StepUpProofReference(
            purpose=payload.get("purpose"),
            proof_id=payload.get("proof_id"),
            nonce_hash=payload.get("nonce_hash"),
            deployment_id=payload.get("deployment_id"),
            contract_hash=payload.get("contract_hash"),
            access_evidence_id=payload.get("access_evidence_id"),
            owner_email_hash=payload.get("owner_email_hash"),
            proof_reference_hash=hash_value(reference or ""),
        )
        if payload.get("challenge") != proof.proof_id.encode("ascii"):
            return None
        return proof
    except (TypeError, UnicodeError, ValidationError, ValueError):
        return None


def build_evidence_envelope(
    *,
    nonce: StepUpNonce,
    context: StepUpContext,
    credential_id_hash: str,
    verified_at: datetime,
) -> StepUpEvidenceEnvelope:
    digest = context_digest(context)
    if nonce.context_digest != digest:
        raise ValueError("step-up context does not match nonce")
    evidence_id = hash_value(
        "|".join(
            (
                nonce.nonce_hash,
                digest,
                nonce.owner_email_hash,
                credential_id_hash,
            )
        )
    )
    return StepUpEvidenceEnvelope(
        schema_version=1,
        evidence_id=evidence_id,
        purpose=context.purpose,
        deployment_id=context.deployment_id,
        contract_hash=context.contract_hash,
        caps=context.caps,
        evidence_digest=context.evidence_digest,
        context_digest=digest,
        nonce_hash=nonce.nonce_hash,
        owner_email_hash=nonce.owner_email_hash,
        credential_id_hash=credential_id_hash,
        verified_at=_utc(verified_at),
    )


class StepUpStore(Protocol):
    def create_nonce(self, nonce: StepUpNonce) -> None: ...

    def get_nonce(self, nonce_hash: str) -> StepUpNonce | None: ...

    def consume_and_record(
        self,
        nonce_hash: str,
        *,
        expected_context_digest: str,
        envelope: StepUpEvidenceEnvelope,
        access_evidence: AccessEvidence,
        credential_store: CredentialStore,
        credential_usage: CredentialUsageCAS,
    ) -> bool: ...


class InMemoryStepUpStore:
    """Atomic process-local test seam; never selected for production by default."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None):
        self._clock = clock or (lambda: datetime.now(UTC))
        self._nonces: dict[str, StepUpNonce] = {}
        self._consumed: set[str] = set()
        self._evidence: dict[str, StepUpEvidenceEnvelope] = {}
        self._lock = Lock()

    def create_nonce(self, nonce: StepUpNonce) -> None:
        with self._lock:
            if nonce.nonce_hash in self._nonces:
                raise ValueError("step-up nonce already exists")
            self._nonces[nonce.nonce_hash] = nonce

    def get_nonce(self, nonce_hash: str) -> StepUpNonce | None:
        with self._lock:
            return self._nonces.get(nonce_hash)

    def consume_and_record(
        self,
        nonce_hash: str,
        *,
        expected_context_digest: str,
        envelope: StepUpEvidenceEnvelope,
        access_evidence: AccessEvidence,
        credential_store: CredentialStore,
        credential_usage: CredentialUsageCAS,
    ) -> bool:
        with self._lock:
            nonce = self._nonces.get(nonce_hash)
            now = _utc(self._clock())
            try:
                validated_access = validate_access_evidence(access_evidence, now=now)
            except InvalidAccessEvidence:
                return False
            if (
                nonce is None
                or nonce_hash in self._consumed
                or now >= nonce.expires_at
                or nonce.context_digest != expected_context_digest
                or envelope.nonce_hash != nonce_hash
                or envelope.context_digest != nonce.context_digest
                or envelope.owner_email_hash != nonce.owner_email_hash
                or envelope.owner_email_hash != email_hash(credential_usage.expected_user_id)
                or envelope.credential_id_hash != hash_value(credential_usage.credential_id)
                or validated_access.deployment_id != envelope.deployment_id
                or validated_access.status is not AccessEvidenceStatus.PASSED
                or validated_access.evidence_digest != envelope.evidence_digest
                or not nonce.issued_at <= envelope.verified_at < nonce.expires_at
            ):
                return False
            if not credential_store.compare_and_set_usage(
                credential_usage,
                used_at=now,
            ):
                return False
            self._consumed.add(nonce_hash)
            self._evidence[envelope.evidence_id] = envelope
            return True

    def get_evidence(self, evidence_id: str) -> StepUpEvidenceEnvelope | None:
        with self._lock:
            return self._evidence.get(evidence_id)


class StepUpNonceDocument(StepUpNonce):
    """Strict Firestore representation; never contains a raw challenge or email."""

    consumed_at: datetime | None = None
    evidence_id: str | None = Field(default=None, pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def consumption_fields_are_coupled(self):
        if (self.consumed_at is None) != (self.evidence_id is None):
            raise ValueError("nonce consumption fields must be set together")
        if self.consumed_at is not None:
            consumed = _utc(self.consumed_at)
            if not self.issued_at <= consumed < self.expires_at:
                raise ValueError("nonce consumption must occur inside its lifetime")
        return self

    def nonce(self) -> StepUpNonce:
        return StepUpNonce.model_validate(self.model_dump(exclude={"consumed_at", "evidence_id"}))


class StepUpStoreError(RuntimeError):
    """Sanitized persistence failure safe for callers to map to a 503."""


class FirestoreStepUpStore:
    """Durable nonce/evidence store with atomic one-time consumption.

    Raw challenges and WebAuthn response material are deliberately absent from
    both strict persisted schemas. A bounded executor contains Firestore's
    otherwise-unbounded transaction helper; a late ambiguous commit remains
    replay-safe because the nonce can be consumed only once and evidence uses a
    deterministic id.
    """

    COLLECTION = "google_ads_owner_step_up_nonces"
    EVIDENCE_SUBCOLLECTION = "verified_evidence"

    def __init__(
        self,
        *,
        client: Any | None = None,
        project: str | None = None,
        transaction_executor: Callable[[Callable[[Any], _T]], _T] | None = None,
        clock: Callable[[], datetime] | None = None,
        transaction_timeout_seconds: float = FIRESTORE_TRANSACTION_TIMEOUT,
        transaction_pool: ThreadPoolExecutor | None = None,
    ) -> None:
        if transaction_timeout_seconds <= 0:
            raise ValueError("transaction timeout must be positive")
        if transaction_pool is not None and transaction_pool._max_workers > 4:
            raise ValueError("injected transaction pool cannot exceed four workers")
        if client is None:
            from google.cloud import firestore

            client = firestore.Client(project=project) if project else firestore.Client()
        self._client = client
        self._collection = client.collection(self.COLLECTION)
        self._transaction_executor = transaction_executor or self._execute_firestore_transaction
        self._clock = clock or (lambda: datetime.now(UTC))
        self._transaction_timeout_seconds = transaction_timeout_seconds
        self._owns_transaction_pool = transaction_pool is None
        self._transaction_pool = transaction_pool or ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix=f"ads-step-up-{id(self):x}",
        )

    def close(self) -> None:
        if self._owns_transaction_pool:
            self._transaction_pool.shutdown(wait=True, cancel_futures=True)

    def _now(self) -> datetime:
        try:
            return _utc(self._clock())
        except (TypeError, ValueError):
            raise StepUpStoreError("step_up_store_clock_invalid") from None

    def _execute_firestore_transaction(self, operation: Callable[[Any], _T]) -> _T:
        from google.cloud import firestore

        transaction = self._client.transaction(max_attempts=5)
        return firestore.transactional(operation)(transaction)

    def _run_transaction(self, operation: Callable[[Any], _T]) -> _T:
        future = self._transaction_pool.submit(self._transaction_executor, operation)
        try:
            return future.result(timeout=self._transaction_timeout_seconds)
        except FutureTimeoutError:
            future.cancel()
            raise StepUpStoreError("step_up_store_timeout") from None
        except StepUpStoreError:
            raise
        except Exception:
            raise StepUpStoreError("step_up_store_write_failed") from None

    def _reference(self, nonce_hash: str):
        if not isinstance(nonce_hash, str) or re.fullmatch(_SHA256_PATTERN, nonce_hash) is None:
            raise StepUpStoreError("step_up_nonce_invalid") from None
        return self._collection.document(nonce_hash)

    @staticmethod
    def _document(snapshot: Any) -> StepUpNonceDocument:
        if not getattr(snapshot, "exists", False):
            raise StepUpStoreError("step_up_nonce_not_found")
        try:
            return StepUpNonceDocument.model_validate(snapshot.to_dict() or {})
        except (AttributeError, TypeError, ValueError, ValidationError):
            raise StepUpStoreError("step_up_nonce_invalid") from None

    def create_nonce(self, nonce: StepUpNonce) -> None:
        document = StepUpNonceDocument(**nonce.model_dump())
        try:
            self._reference(nonce.nonce_hash).create(
                document.model_dump(mode="python"),
                timeout=FIRESTORE_RPC_TIMEOUT,
            )
        except StepUpStoreError:
            raise
        except Exception:
            raise StepUpStoreError("step_up_store_write_failed") from None

    def get_nonce(self, nonce_hash: str) -> StepUpNonce | None:
        try:
            snapshot = self._reference(nonce_hash).get(timeout=FIRESTORE_RPC_TIMEOUT)
            if not getattr(snapshot, "exists", False):
                return None
            return self._document(snapshot).nonce()
        except StepUpStoreError:
            raise
        except Exception:
            raise StepUpStoreError("step_up_store_read_failed") from None

    def consume_and_record(
        self,
        nonce_hash: str,
        *,
        expected_context_digest: str,
        envelope: StepUpEvidenceEnvelope,
        access_evidence: AccessEvidence,
        credential_store: CredentialStore,
        credential_usage: CredentialUsageCAS,
    ) -> bool:
        if (
            not isinstance(credential_store, FirestoreCredentialStore)
            or credential_store.client is not self._client
        ):
            raise StepUpStoreError("step_up_credential_store_mismatch")
        reference = self._reference(nonce_hash)
        credential_reference = credential_store.usage_reference(credential_usage)
        access_reference = (
            self._client.collection("google_ads_deployments")
            .document(envelope.deployment_id)
            .collection("access_evidence")
            .document(AccessCheckKey.GOOGLE_ADS_ACCOUNT_ACCESS_AND_USD_GREEN.value)
        )

        def operation(transaction):
            snapshot = reference.get(transaction=transaction, timeout=FIRESTORE_RPC_TIMEOUT)
            if not getattr(snapshot, "exists", False):
                return False
            document = self._document(snapshot)
            nonce = document.nonce()
            now = self._now()
            credential_snapshot = credential_reference.get(
                transaction=transaction,
                timeout=FIRESTORE_RPC_TIMEOUT,
            )
            access_snapshot = access_reference.get(
                transaction=transaction,
                timeout=FIRESTORE_RPC_TIMEOUT,
            )
            try:
                access_model = GoogleAdsAccessEvidenceRecord.model_validate(
                    access_snapshot.to_dict() if access_snapshot.exists else {}
                )
                persisted_access = validate_access_evidence(
                    AccessEvidence(
                        deployment_id=access_model.deployment_id,
                        check_key=AccessCheckKey(access_model.check_key),
                        status=AccessEvidenceStatus(access_model.status),
                        observed_at=access_model.observed_at,
                        expires_at=access_model.expires_at,
                        source_revision=access_model.source_revision,
                        evidence_digest=access_model.evidence_digest,
                    ),
                    now=now,
                )
            except (InvalidAccessEvidence, ValidationError, TypeError, ValueError):
                return False
            if (
                document.consumed_at is not None
                or now >= nonce.expires_at
                or nonce.context_digest != expected_context_digest
                or envelope.nonce_hash != nonce_hash
                or envelope.context_digest != nonce.context_digest
                or envelope.owner_email_hash != nonce.owner_email_hash
                or envelope.owner_email_hash != email_hash(credential_usage.expected_user_id)
                or envelope.credential_id_hash != hash_value(credential_usage.credential_id)
                or persisted_access.status is not AccessEvidenceStatus.PASSED
                or persisted_access.deployment_id != envelope.deployment_id
                or persisted_access.check_key
                is not AccessCheckKey.GOOGLE_ADS_ACCOUNT_ACCESS_AND_USD_GREEN
                or persisted_access.evidence_digest != envelope.evidence_digest
                or evidence_payload(persisted_access) != evidence_payload(access_evidence)
                or not nonce.issued_at <= envelope.verified_at < nonce.expires_at
                or not credential_store.usage_matches_snapshot(
                    credential_usage,
                    credential_snapshot,
                )
            ):
                return False
            evidence_reference = reference.collection(self.EVIDENCE_SUBCOLLECTION).document(
                envelope.evidence_id
            )
            transaction.create(evidence_reference, envelope.model_dump(mode="python"))
            transaction.update(
                reference,
                {"consumed_at": now, "evidence_id": envelope.evidence_id},
            )
            transaction.update(
                credential_reference,
                credential_store.usage_update(credential_usage, used_at=now),
            )
            return True

        return self._run_transaction(operation)


@lru_cache(maxsize=1)
def default_step_up_store(*, credential_store: CredentialStore | None = None) -> StepUpStore:
    """Production defaults to Firestore; process-local storage is explicit dev only."""
    backend = os.environ.get("THO_GOOGLE_ADS_STEP_UP_STORE", "firestore").strip().lower()
    if backend == "memory":
        if os.environ.get("K_SERVICE"):
            raise StepUpStoreError("persistent step-up store required")
        return InMemoryStepUpStore()
    try:
        if isinstance(credential_store, FirestoreCredentialStore):
            return FirestoreStepUpStore(client=credential_store.client)
        return FirestoreStepUpStore(project=os.environ.get("GOOGLE_CLOUD_PROJECT"))
    except Exception:
        raise StepUpStoreError("persistent step-up store unavailable") from None


class SignedStepUpProof(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    envelope: StepUpEvidenceEnvelope
    algorithm: Literal["TEST_SHA256", "GCP_KMS_ASYMMETRIC_SIGN"]
    signature: str = Field(pattern=_SHA256_PATTERN)


class StepUpEvidenceSigner(Protocol):
    def sign(self, envelope: StepUpEvidenceEnvelope) -> SignedStepUpProof: ...


class FakeStepUpEvidenceSigner:
    """Test-only signer seam. Production KMS provisioning remains external-gated."""

    def sign(self, envelope: StepUpEvidenceEnvelope) -> SignedStepUpProof:
        return SignedStepUpProof(
            envelope=envelope,
            algorithm="TEST_SHA256",
            signature=hash_value(b"test-only:" + _canonical_model(envelope)),
        )
