from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event, Lock

import pytest
from pydantic import ValidationError

from database.google_ads_authority import FirestoreAuthorityLedger
from database.models import GoogleAdsAuthorityEventRecord, GoogleAdsDeploymentRecord
from scripts.google_ads_paused_worker import (
    DeploymentState,
    DraftReviewControlPlane,
    InvalidStateTransition,
    LedgerConflict,
    LedgerWriteError,
    PausedCreateApproval,
    PausedCreateControlPlane,
    StaticContractSource,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "config" / "google_ads_launch_draft.json").read_text())


class _Snapshot:
    def __init__(self, document_id: str, value: dict | None):
        self.id = document_id
        self._value = copy.deepcopy(value)
        self.exists = value is not None

    def to_dict(self):
        return copy.deepcopy(self._value)


class _Document:
    def __init__(self, store: _AtomicFirestore, path: tuple[str, ...]):
        self._store = store
        self.path = path
        self.id = path[-1]

    def collection(self, name: str):
        return _Collection(self._store, (*self.path, name))

    def get(self, *, transaction=None, timeout=None):
        assert timeout is not None
        if transaction is not None:
            return transaction.get(self)
        return _Snapshot(self.id, self._store.rows.get(self.path))


class _Collection:
    def __init__(self, store: _AtomicFirestore, path: tuple[str, ...]):
        self._store = store
        self.path = path

    def document(self, document_id: str):
        return _Document(self._store, (*self.path, document_id))


class _Transaction:
    def __init__(self, store: _AtomicFirestore):
        self._store = store
        self._staged: dict[tuple[str, ...], dict] = {}
        self._creates: set[tuple[str, ...]] = set()

    def get(self, document: _Document):
        value = self._staged.get(document.path, self._store.rows.get(document.path))
        return _Snapshot(document.id, value)

    def create(self, document: _Document, value: dict):
        if document.path in self._store.rows or document.path in self._staged:
            raise RuntimeError("already exists")
        if self._store.fail_event_create and "authority_events" in document.path:
            raise RuntimeError("event write failed")
        self._creates.add(document.path)
        self._staged[document.path] = copy.deepcopy(value)

    def set(self, document: _Document, value: dict):
        self._staged[document.path] = copy.deepcopy(value)

    def commit(self):
        self._store.rows.update(copy.deepcopy(self._staged))


class _AtomicFirestore:
    """Small serializable Firestore seam with commit-or-nothing transactions."""

    def __init__(self):
        self.rows: dict[tuple[str, ...], dict] = {}
        self.lock = Lock()
        self.fail_event_create = False

    def collection(self, name: str):
        return _Collection(self, (name,))

    def run_transaction(self, operation):
        with self.lock:
            transaction = _Transaction(self)
            result = operation(transaction)
            transaction.commit()
            return result

    def deployment(self, deployment_id: str) -> dict | None:
        return copy.deepcopy(self.rows.get(("google_ads_deployments", deployment_id)))

    def events(self, deployment_id: str) -> list[dict]:
        prefix = ("google_ads_deployments", deployment_id, "authority_events")
        return [
            copy.deepcopy(value) for path, value in sorted(self.rows.items()) if path[:3] == prefix
        ]


class _Clock:
    def __init__(self):
        self.value = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)

    def __call__(self):
        return self.value

    def advance(self, seconds: int):
        self.value += timedelta(seconds=seconds)


class _Invoker:
    def __init__(self):
        self.ids = []

    def invoke(self, deployment_id: str):
        self.ids.append(deployment_id)


@pytest.fixture
def durable():
    store = _AtomicFirestore()
    clock = _Clock()
    ledger = FirestoreAuthorityLedger(
        client=store,
        transaction_executor=store.run_transaction,
        clock=clock,
        claim_lease_seconds=30,
    )
    return ledger, store, clock


def _draft(ledger):
    return DraftReviewControlPlane(ledger, StaticContractSource(CONTRACT)).ensure_internal_draft()


def _approved(ledger):
    source = StaticContractSource(CONTRACT)
    review = DraftReviewControlPlane(ledger, source)
    invoker = _Invoker()
    create = PausedCreateControlPlane(ledger, invoker)
    draft = review.ensure_internal_draft()
    validated = review.server_validate(draft.deployment_id)
    approved = create.approve_paused_create(PausedCreateApproval.for_record(validated))
    return approved, invoker


def test_database_models_reject_extra_raw_provider_account_token_and_request_fields():
    now = datetime(2026, 8, 12, tzinfo=UTC)
    base = {
        "schema_version": 1,
        "deployment_id": f"draft--{'a' * 64}",
        "deployment_key": "draft",
        "contract_hash": f"sha256:{'a' * 64}",
        "contract_label": f"tho-contract-{'a' * 12}",
        "state": "INTERNAL_DRAFT",
        "version": 1,
        "worker_claim_hash": None,
        "claim_expires_at": None,
        "create_fenced_at": None,
        "provider_reference_hash": None,
        "error_code": None,
        "created_at": now,
        "updated_at": now,
    }

    record = GoogleAdsDeploymentRecord.model_validate(base)
    assert GoogleAdsDeploymentRecord.model_validate(record.model_dump()) == record

    for forbidden in (
        "customer_id",
        "login_customer_id",
        "developer_token",
        "access_token",
        "provider_resource_name",
        "request_id",
        "provider_response",
    ):
        with pytest.raises(ValidationError):
            GoogleAdsDeploymentRecord.model_validate({**base, forbidden: "raw-do-not-store"})

    event = {
        "schema_version": 1,
        "event_id": "00000000000000000001-internal-draft-created",
        "deployment_id": base["deployment_id"],
        "contract_hash": base["contract_hash"],
        "event_type": "INTERNAL_DRAFT_CREATED",
        "from_state": None,
        "to_state": "INTERNAL_DRAFT",
        "record_version": 1,
        "worker_claim_hash": None,
        "error_code": None,
        "occurred_at": now,
    }
    assert GoogleAdsAuthorityEventRecord.model_validate(event).event_type == (
        "INTERNAL_DRAFT_CREATED"
    )
    with pytest.raises(ValidationError):
        GoogleAdsAuthorityEventRecord.model_validate({**event, "raw_request": {"id": "secret"}})
    with pytest.raises(ValidationError):
        GoogleAdsAuthorityEventRecord.model_validate(
            {**event, "error_code": "raw_google_error_customer_123"}
        )


def test_create_or_get_is_deterministic_and_conflicting_identity_is_rejected(durable):
    ledger, store, _clock = durable
    first = _draft(ledger)
    replay = _draft(ledger)

    assert replay == first
    assert first.version == 1
    assert first.created_at == datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    assert [row["event_type"] for row in store.events(first.deployment_id)] == [
        "INTERNAL_DRAFT_CREATED"
    ]

    from dataclasses import replace

    with pytest.raises(LedgerConflict):
        ledger.create_or_get(replace(first, contract_label="tho-contract-deadbeefdead"))


def test_transition_uses_version_compare_and_swap_and_appends_atomic_events(durable):
    ledger, store, clock = durable
    draft = _draft(ledger)
    clock.advance(1)

    validated = ledger.transition(
        draft.deployment_id,
        expected=DeploymentState.INTERNAL_DRAFT,
        target=DeploymentState.SERVER_VALIDATED,
        expected_version=1,
    )

    assert validated.version == 2
    assert validated.updated_at == clock.value
    assert [row["event_type"] for row in store.events(draft.deployment_id)] == [
        "INTERNAL_DRAFT_CREATED",
        "SERVER_VALIDATED",
    ]
    with pytest.raises(InvalidStateTransition):
        ledger.transition(
            draft.deployment_id,
            expected=DeploymentState.SERVER_VALIDATED,
            target=DeploymentState.PAUSED_CREATE_APPROVED,
            expected_version=1,
        )


def test_concurrent_worker_claim_has_one_winner_and_hashes_claimant(durable):
    ledger, store, _clock = durable
    approved, _invoker = _approved(ledger)
    barrier = Barrier(8)

    def claim(index: int):
        barrier.wait()
        return ledger.claim_paused_create(approved.deployment_id, f"raw-worker-{index}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(claim, range(8)))

    assert results.count(True) == 1
    assert results.count(False) == 7
    stored = store.deployment(approved.deployment_id)
    serialized = json.dumps(stored, default=str)
    assert stored["worker_claim_hash"].startswith("sha256:")
    assert "raw-worker" not in serialized
    assert stored["claim_expires_at"] is not None


def test_expired_claim_is_reclaimable_after_worker_crash_and_old_worker_cannot_complete(durable):
    ledger, store, clock = durable
    approved, _invoker = _approved(ledger)
    assert ledger.claim_paused_create(approved.deployment_id, "worker-that-crashes") is True
    clock.advance(31)
    assert ledger.claim_paused_create(approved.deployment_id, "replacement-worker") is True

    with pytest.raises(InvalidStateTransition):
        ledger.complete_paused_create(
            approved.deployment_id,
            "worker-that-crashes",
            f"sha256:{'b' * 64}",
        )

    ledger.fence_paused_create(approved.deployment_id, "replacement-worker")
    completed = ledger.complete_paused_create(
        approved.deployment_id,
        "replacement-worker",
        f"sha256:{'b' * 64}",
    )
    assert completed.state is DeploymentState.PAUSED_CREATED
    assert completed.worker_claim_hash is None
    assert completed.claim_expires_at is None
    assert [row["event_type"] for row in store.events(approved.deployment_id)][-4:] == [
        "PAUSED_CREATE_CLAIMED",
        "PAUSED_CREATE_RECLAIMED",
        "PAUSED_CREATE_FENCED",
        "PAUSED_CREATE_COMPLETED",
    ]


def test_create_fence_blocks_expired_claim_reclaim_and_allows_original_completion(durable):
    ledger, store, clock = durable
    approved, _invoker = _approved(ledger)
    assert ledger.claim_paused_create(approved.deployment_id, "original-worker") is True

    fenced = ledger.fence_paused_create(approved.deployment_id, "original-worker")
    assert fenced.create_fenced_at == clock.value

    clock.advance(31)
    assert ledger.fence_paused_create(approved.deployment_id, "original-worker") == fenced
    assert ledger.claim_paused_create(approved.deployment_id, "replacement-worker") is False
    with pytest.raises(InvalidStateTransition):
        ledger.release_claim(
            approved.deployment_id,
            "original-worker",
            "provider_timeout_unresolved",
        )

    failed = ledger.mark_fenced_failure(
        approved.deployment_id,
        "original-worker",
        "provider_timeout_unresolved",
    )
    assert failed.error_code == "provider_timeout_unresolved"
    assert failed.worker_claim_hash is not None
    assert failed.create_fenced_at is not None

    completed = ledger.complete_paused_create(
        approved.deployment_id,
        "original-worker",
        f"sha256:{'c' * 64}",
    )

    assert completed.state is DeploymentState.PAUSED_CREATED
    assert completed.create_fenced_at == datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    assert [row["event_type"] for row in store.events(approved.deployment_id)][-3:] == [
        "PAUSED_CREATE_FENCED",
        "PAUSED_CREATE_FENCED_FAILED",
        "PAUSED_CREATE_COMPLETED",
    ]


def test_event_write_failure_rolls_back_state_change_and_surfaces_fail_closed(durable):
    ledger, store, _clock = durable
    draft = _draft(ledger)
    store.fail_event_create = True

    with pytest.raises(LedgerWriteError, match="ledger_write_failed"):
        ledger.transition(
            draft.deployment_id,
            expected=DeploymentState.INTERNAL_DRAFT,
            target=DeploymentState.SERVER_VALIDATED,
        )

    assert store.deployment(draft.deployment_id)["state"] == "INTERNAL_DRAFT"
    assert len(store.events(draft.deployment_id)) == 1


def test_corrupt_or_raw_firestore_document_fails_closed_without_echoing_payload(durable):
    ledger, store, _clock = durable
    draft = _draft(ledger)
    stored = store.deployment(draft.deployment_id)
    stored["customer_id"] = "1234567890"
    store.rows[("google_ads_deployments", draft.deployment_id)] = stored

    with pytest.raises(LedgerWriteError) as exc_info:
        ledger.get(draft.deployment_id)

    assert str(exc_info.value) == "ledger_record_invalid"
    assert "1234567890" not in str(exc_info.value)


def test_transaction_wall_clock_is_bounded_and_timeout_is_sanitized():
    store = _AtomicFirestore()
    clock = _Clock()
    release = Event()

    def blocked_executor(_operation):
        release.wait(timeout=1)

    ledger = FirestoreAuthorityLedger(
        client=store,
        transaction_executor=blocked_executor,
        clock=clock,
        transaction_timeout_seconds=0.01,
    )
    try:
        with pytest.raises(LedgerWriteError) as exc_info:
            _draft(ledger)
    finally:
        release.set()

    assert str(exc_info.value) == "ledger_write_timeout"
    assert store.rows == {}
