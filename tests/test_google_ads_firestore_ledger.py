from __future__ import annotations

import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event, Lock

import pytest
from pydantic import ValidationError

from database.google_ads_authority import FirestoreAuthorityLedger
from database.models import (
    GoogleAdsAuthorityEventRecord,
    GoogleAdsDeploymentRecord,
    GoogleAdsOperationKeyRecord,
)
from scripts.google_ads_paused_worker import (
    DeploymentRecord,
    DeploymentState,
    DraftReviewControlPlane,
    InMemoryAuthorityLedger,
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
        self._descending = False
        self._limit = None

    def document(self, document_id: str):
        return _Document(self._store, (*self.path, document_id))

    def order_by(self, field, *, direction):
        assert field == "record_version"
        assert direction == "DESCENDING"
        self._descending = True
        return self

    def limit(self, value):
        self._limit = value
        return self

    def stream(self, *, timeout=None):
        assert timeout is not None
        rows = [(path, value) for path, value in self._store.rows.items() if path[:-1] == self.path]
        rows.sort(key=lambda item: item[1]["record_version"], reverse=self._descending)
        for path, value in rows[: self._limit]:
            yield _Snapshot(path[-1], value)


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
        if self._store.fail_operation_create and "operation_keys" in document.path:
            raise RuntimeError("operation marker write failed")
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
        self.fail_operation_create = False

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

    def operation_key(self, deployment_id: str) -> dict | None:
        return copy.deepcopy(
            self.rows.get(
                (
                    "google_ads_deployments",
                    deployment_id,
                    "operation_keys",
                    "server-validation",
                )
            )
        )


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
    try:
        yield ledger, store, clock
    finally:
        ledger.close()


def _draft(ledger):
    return DraftReviewControlPlane(ledger, StaticContractSource(CONTRACT)).ensure_internal_draft()


def test_list_events_returns_strict_bounded_version_order(durable):
    ledger, _store, _clock = durable
    draft = _draft(ledger)
    DraftReviewControlPlane(ledger, StaticContractSource(CONTRACT)).server_validate(
        draft.deployment_id,
        expected_version=draft.version,
    )

    events = ledger.list_events(draft.deployment_id, limit=1)

    assert len(events) == 1
    assert events[0].event_type == "SERVER_VALIDATED"
    assert events[0].record_version == 2
    with pytest.raises(ValueError):
        ledger.list_events(draft.deployment_id, limit=0)


def test_server_validation_key_is_hashed_and_same_key_replays_while_different_key_conflicts(
    durable,
):
    ledger, store, _clock = durable
    review = DraftReviewControlPlane(ledger, StaticContractSource(CONTRACT))
    draft = review.ensure_internal_draft()

    first = review.server_validate(
        draft.deployment_id,
        expected_version=1,
        idempotency_key="offline-validation-one",
    )
    replay = review.server_validate(
        draft.deployment_id,
        expected_version=1,
        idempotency_key="offline-validation-one",
    )

    assert first == replay
    stored = store.deployment(draft.deployment_id)
    marker = store.operation_key(draft.deployment_id)
    assert "server_validation_key_hash" not in stored
    assert set(stored) == set(GoogleAdsDeploymentRecord.model_fields)
    assert marker["key_hash"].startswith("sha256:")
    assert marker["operation"] == "SERVER_VALIDATION"
    assert marker["record_version"] == 2
    assert "offline-validation-one" not in str(store.rows)
    assert GoogleAdsOperationKeyRecord.model_validate(marker).key_hash == marker["key_hash"]
    with pytest.raises(ValidationError):
        GoogleAdsOperationKeyRecord.model_validate({**marker, "idempotency_key": "raw"})
    with pytest.raises(InvalidStateTransition):
        review.server_validate(
            draft.deployment_id,
            expected_version=1,
            idempotency_key="offline-validation-two",
        )


def test_concurrent_same_server_validation_key_returns_one_durable_result(durable):
    ledger, store, _clock = durable
    review = DraftReviewControlPlane(ledger, StaticContractSource(CONTRACT))
    draft = review.ensure_internal_draft()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _index: review.server_validate(
                    draft.deployment_id,
                    expected_version=1,
                    idempotency_key="offline-validation-concurrent",
                ),
                range(2),
            )
        )

    assert results[0] == results[1]
    assert results[0].state is DeploymentState.SERVER_VALIDATED
    assert len(store.events(draft.deployment_id)) == 2


@pytest.mark.parametrize("corruption", ["deployment", "contract", "version", "timestamp"])
def test_server_validation_replay_rejects_marker_not_exactly_bound_to_record(durable, corruption):
    ledger, store, clock = durable
    review = DraftReviewControlPlane(ledger, StaticContractSource(CONTRACT))
    draft = review.ensure_internal_draft()
    validated = review.server_validate(
        draft.deployment_id,
        expected_version=1,
        idempotency_key="offline-validation-bound-marker",
    )
    marker_path = (
        "google_ads_deployments",
        draft.deployment_id,
        "operation_keys",
        "server-validation",
    )
    marker = store.rows[marker_path]
    if corruption == "deployment":
        marker["deployment_id"] = f"other--{validated.contract_hash.removeprefix('sha256:')}"
    elif corruption == "contract":
        marker["contract_hash"] = f"sha256:{'f' * 64}"
        marker["deployment_id"] = f"other--{'f' * 64}"
    elif corruption == "version":
        marker["record_version"] = 1
    else:
        marker["created_at"] = clock.value + timedelta(seconds=1)

    with pytest.raises(InvalidStateTransition):
        review.server_validate(
            draft.deployment_id,
            expected_version=1,
            idempotency_key="offline-validation-bound-marker",
        )


def test_server_validation_marker_failure_rolls_back_record_and_event(durable):
    ledger, store, _clock = durable
    review = DraftReviewControlPlane(ledger, StaticContractSource(CONTRACT))
    draft = review.ensure_internal_draft()
    store.fail_operation_create = True

    with pytest.raises(LedgerWriteError):
        review.server_validate(
            draft.deployment_id,
            expected_version=1,
            idempotency_key="offline-validation-failing-marker",
        )

    assert ledger.get(draft.deployment_id).state is DeploymentState.INTERNAL_DRAFT
    assert len(store.events(draft.deployment_id)) == 1
    assert store.operation_key(draft.deployment_id) is None


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
        "create_fence_claim_hash": None,
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
        "idempotency_key",
        "server_validation_key_hash",
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


def test_database_model_couples_identity_claim_fence_state_and_error_semantics():
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    digest = "a" * 64
    base = {
        "schema_version": 1,
        "deployment_id": f"draft--{digest}",
        "deployment_key": "draft",
        "contract_hash": f"sha256:{digest}",
        "contract_label": f"tho-contract-{digest[:12]}",
        "state": "INTERNAL_DRAFT",
        "version": 1,
        "worker_claim_hash": None,
        "claim_expires_at": None,
        "create_fenced_at": None,
        "create_fence_claim_hash": None,
        "provider_reference_hash": None,
        "error_code": None,
        "created_at": now,
        "updated_at": now,
    }
    claim_hash = f"sha256:{'b' * 64}"

    for invalid in (
        {**base, "deployment_key": "other"},
        {**base, "contract_hash": f"sha256:{'c' * 64}"},
        {**base, "contract_label": f"tho-contract-{'d' * 12}"},
        {
            **base,
            "worker_claim_hash": claim_hash,
            "claim_expires_at": now + timedelta(seconds=10),
        },
        {**base, "error_code": "contract_mismatch"},
    ):
        with pytest.raises(ValidationError):
            GoogleAdsDeploymentRecord.model_validate(invalid)

    approved = {
        **base,
        "state": "PAUSED_CREATE_APPROVED",
        "version": 4,
        "worker_claim_hash": claim_hash,
        "claim_expires_at": now + timedelta(seconds=10),
        "create_fenced_at": now + timedelta(seconds=11),
        "create_fence_claim_hash": claim_hash,
        "updated_at": now + timedelta(seconds=11),
    }
    with pytest.raises(ValidationError):
        GoogleAdsDeploymentRecord.model_validate(approved)
    with pytest.raises(ValidationError):
        GoogleAdsDeploymentRecord.model_validate(
            {
                **approved,
                "create_fenced_at": now,
                "error_code": "worker_claimed_elsewhere",
            }
        )


def test_authority_event_model_couples_id_transition_claim_and_error_semantics():
    now = datetime(2026, 8, 12, tzinfo=UTC)
    digest = "a" * 64
    claim_hash = f"sha256:{'b' * 64}"
    base = {
        "schema_version": 1,
        "event_id": "00000000000000000004-paused-create-fenced-failed",
        "deployment_id": f"draft--{digest}",
        "contract_hash": f"sha256:{digest}",
        "event_type": "PAUSED_CREATE_FENCED_FAILED",
        "from_state": "PAUSED_CREATE_APPROVED",
        "to_state": "PAUSED_CREATE_APPROVED",
        "record_version": 4,
        "worker_claim_hash": claim_hash,
        "error_code": "provider_timeout_unresolved",
        "occurred_at": now,
    }
    assert GoogleAdsAuthorityEventRecord.model_validate(base).record_version == 4

    for invalid in (
        {**base, "event_id": "00000000000000000005-paused-create-fenced-failed"},
        {**base, "event_id": "00000000000000000004-paused-create-completed"},
        {**base, "from_state": "SERVER_VALIDATED"},
        {**base, "to_state": "PAUSED_CREATED"},
        {**base, "worker_claim_hash": None},
        {**base, "error_code": None},
        {**base, "deployment_id": f"draft--{'c' * 64}"},
    ):
        with pytest.raises(ValidationError):
            GoogleAdsAuthorityEventRecord.model_validate(invalid)

    created = {
        **base,
        "event_id": "00000000000000000001-internal-draft-created",
        "event_type": "INTERNAL_DRAFT_CREATED",
        "from_state": None,
        "to_state": "INTERNAL_DRAFT",
        "record_version": 1,
        "worker_claim_hash": None,
        "error_code": None,
    }
    assert GoogleAdsAuthorityEventRecord.model_validate(created).record_version == 1


def test_in_memory_create_normalizes_unsafe_caller_state_and_rejects_bad_identity():
    ledger = InMemoryAuthorityLedger()
    digest = "a" * 64
    candidate = DeploymentRecord(
        deployment_id=f"draft--{digest}",
        deployment_key="draft",
        contract_hash=f"sha256:{digest}",
        contract_label=f"tho-contract-{digest[:12]}",
        state=DeploymentState.PAUSED_CREATED,
        version=999,
        worker_claim_hash=f"sha256:{'b' * 64}",
        claim_expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        create_fenced_at=datetime(2029, 1, 1, tzinfo=UTC),
        create_fence_claim_hash=f"sha256:{'b' * 64}",
        provider_reference_hash=f"sha256:{'c' * 64}",
        error_code="provider_create_failed",
    )

    stored, created = ledger.create_or_get(candidate)

    assert created is True
    assert stored.state is DeploymentState.INTERNAL_DRAFT
    assert stored.version == 1
    assert stored.worker_claim_hash is None
    assert stored.claim_expires_at is None
    assert stored.create_fenced_at is None
    assert stored.create_fence_claim_hash is None
    assert stored.provider_reference_hash is None
    assert stored.error_code is None

    with pytest.raises(ValueError):
        ledger.create_or_get(replace(candidate, deployment_key="other"))


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


def test_expired_fenced_claim_has_exactly_one_reconciliation_only_successor(durable):
    ledger, store, clock = durable
    approved, _invoker = _approved(ledger)
    assert ledger.claim_paused_create(approved.deployment_id, "crashed-worker") is True
    fenced = ledger.fence_paused_create(approved.deployment_id, "crashed-worker")
    clock.advance(31)
    barrier = Barrier(8)

    def claim(index: int):
        barrier.wait()
        return ledger.claim_fenced_reconciliation(
            approved.deployment_id,
            f"reconciler-{index}",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(claim, range(8)))

    assert results.count(True) == 1
    assert results.count(False) == 7
    stored = store.deployment(approved.deployment_id)
    assert stored["create_fenced_at"] == fenced.create_fenced_at
    assert stored["worker_claim_hash"] != fenced.worker_claim_hash
    assert stored["create_fence_claim_hash"] == fenced.worker_claim_hash
    assert [row["event_type"] for row in store.events(approved.deployment_id)][-1] == (
        "PAUSED_CREATE_RECONCILIATION_CLAIMED"
    )

    completed = ledger.complete_paused_create(
        approved.deployment_id,
        "crashed-worker",
        f"sha256:{'d' * 64}",
    )
    assert completed.state is DeploymentState.PAUSED_CREATED
    assert completed.worker_claim_hash is None
    assert completed.create_fence_claim_hash is None


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
        ledger.close()

    assert str(exc_info.value) == "ledger_write_timeout"
    assert store.rows == {}


def test_many_transaction_timeouts_use_one_bounded_pool_and_cancel_queued_work():
    store = _AtomicFirestore()
    release = Event()
    started = 0
    started_lock = Lock()

    def blocked_executor(_operation):
        nonlocal started
        with started_lock:
            started += 1
        release.wait(timeout=2)

    ledger = FirestoreAuthorityLedger(
        client=store,
        transaction_executor=blocked_executor,
        transaction_workers=2,
        transaction_timeout_seconds=0.02,
    )
    barrier = Barrier(12)

    def timeout(_index):
        barrier.wait()
        with pytest.raises(LedgerWriteError, match="ledger_write_timeout"):
            _draft(ledger)

    try:
        with ThreadPoolExecutor(max_workers=12) as callers:
            list(callers.map(timeout, range(12)))
        worker_threads = [
            thread
            for thread in threading.enumerate()
            if thread.name.startswith(ledger.transaction_thread_name_prefix)
        ]
        assert len(worker_threads) <= 2
        assert started <= 2
    finally:
        release.set()
        ledger.close()


def test_transaction_pool_rejects_more_than_four_workers():
    store = _AtomicFirestore()
    with pytest.raises(ValueError, match="between 1 and 4"):
        FirestoreAuthorityLedger(client=store, transaction_workers=5)

    with ThreadPoolExecutor(max_workers=5) as oversized_pool:
        with pytest.raises(ValueError, match="cannot exceed 4"):
            FirestoreAuthorityLedger(client=store, transaction_pool=oversized_pool)
