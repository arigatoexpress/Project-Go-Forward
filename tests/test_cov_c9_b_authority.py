"""Coverage campaign C9-B: closes remaining branches in database/google_ads_authority.

Every test here exercises the hermetic Firestore fakes and ledger fixtures from
tests/test_google_ads_firestore_ledger.py; no emulator, no network.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from database.google_ads_authority import FirestoreAuthorityLedger
from database.models import (
    MAX_GOOGLE_ADS_DISPATCH_ATTEMPTS,
    GoogleAdsPausedCreateOutboxRecord,
)
from scripts.google_ads_access_evidence import AccessCheckKey
from scripts.google_ads_paused_worker import (
    DeploymentNotFound,
    DeploymentRecord,
    DeploymentState,
    InvalidStateTransition,
    LedgerWriteError,
)
from tests.test_google_ads_firestore_ledger import (
    _access_evidence,
    _approval_fixture,
    _approve,
    _approved,
    _AtomicFirestore,
    _Clock,
    _draft,
    _Transaction,
)


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


_VALID_ID = f"draft--{'a' * 64}"
_VALID_HASH = f"sha256:{'a' * 64}"
_OTHER_HASH = f"sha256:{'b' * 64}"


class _BoomDocument:
    def get(self, *, timeout=None):
        assert timeout is not None
        raise RuntimeError("backend down")


class _BoomCollection:
    """Collection whose reads fail with a raw (non-control-plane) error."""

    def document(self, _document_id):
        return _BoomDocument()


class _BoomStreamCollection:
    def order_by(self, _field, *, direction):
        return self

    def limit(self, _value):
        return self

    def stream(self, *, timeout=None):
        assert timeout is not None
        raise RuntimeError("backend down")


class _BoomStreamDocument:
    def collection(self, _name):
        return _BoomStreamCollection()


class _BoomRootCollection:
    def document(self, _document_id):
        return _BoomStreamDocument()


class _FakeFirestoreClient:
    def __init__(self, store: _AtomicFirestore):
        self._store = store

    def collection(self, name):
        return self._store.collection(name)

    def transaction(self, *, max_attempts):
        assert max_attempts == 5
        return _Transaction(self._store)


class _FakeFirestoreModule:
    """Stand-in for ``google.cloud.firestore`` behind the module's lazy imports."""

    def __init__(self, store: _AtomicFirestore):
        self._store = store
        self.created_projects: list[str | None] = []

    def Client(self, project=None):
        self.created_projects.append(project)
        return _FakeFirestoreClient(self._store)

    def transactional(self, operation):
        def run(transaction):
            result = operation(transaction)
            transaction.commit()
            return result

        return run


def _install_fake_firestore(monkeypatch, store: _AtomicFirestore) -> _FakeFirestoreModule:
    fake = _FakeFirestoreModule(store)
    monkeypatch.setitem(sys.modules, "google.cloud.firestore", fake)
    monkeypatch.setattr("google.cloud.firestore", fake, raising=False)
    return fake


def _candidate(deployment_id: str, contract_hash: str) -> DeploymentRecord:
    digest = contract_hash.removeprefix("sha256:")
    return DeploymentRecord(
        deployment_id=deployment_id,
        deployment_key="draft",
        contract_hash=contract_hash,
        contract_label=f"tho-contract-{digest[:12]}",
        state=DeploymentState.INTERNAL_DRAFT,
        version=1,
    )


def _proof(**overrides):
    fields = {
        "proof_id": f"sha256:{'c' * 64}",
        "nonce_hash": f"sha256:{'d' * 64}",
        "deployment_id": _VALID_ID,
        "contract_hash": _VALID_HASH,
        "access_evidence_id": _OTHER_HASH,
        "owner_email_hash": f"sha256:{'e' * 64}",
        "proof_reference_hash": f"sha256:{'f' * 64}",
        "purpose": "PAUSED_CREATE",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _outbox_row(deployment_id: str, contract_hash: str, now: datetime, **overrides):
    fields = {
        "outbox_id": "paused-create",
        "deployment_id": deployment_id,
        "contract_hash": contract_hash,
        "approval_record_version": 3,
        "proof_id": f"sha256:{'c' * 64}",
        "access_evidence_id": _OTHER_HASH,
        "state": "PENDING",
        "attempt_count": 0,
        "dispatcher_claim_hash": None,
        "claim_expires_at": None,
        "error_code": None,
        "created_at": now,
        "updated_at": now,
        "dispatched_at": None,
    }
    fields.update(overrides)
    return GoogleAdsPausedCreateOutboxRecord.model_validate(fields).model_dump(mode="python")


# ---------------------------------------------------------------------------
# Constructor, context manager, clock, lazy firestore import (L70, L112, L114,
# L120-122, L142, L145, L150, L154-157)
# ---------------------------------------------------------------------------


def test_claimant_must_be_non_empty_string(durable):
    ledger, _store, _clock = durable
    with pytest.raises(ValueError, match="claimant is required"):
        ledger.claim_paused_create(_VALID_ID, "")
    with pytest.raises(ValueError, match="claimant is required"):
        ledger.claim_paused_create(_VALID_ID, 123)  # type: ignore[arg-type]


def test_constructor_validates_lease_seconds_and_transaction_timeout():
    store = _AtomicFirestore()
    with pytest.raises(ValueError, match="between 1 and 3600"):
        FirestoreAuthorityLedger(client=store, claim_lease_seconds=0)
    with pytest.raises(ValueError, match="between 1 and 3600"):
        FirestoreAuthorityLedger(client=store, claim_lease_seconds=3601)
    with pytest.raises(ValueError, match="timeout must be positive"):
        FirestoreAuthorityLedger(client=store, transaction_timeout_seconds=0)


def test_constructor_lazily_builds_firestore_client(monkeypatch):
    store = _AtomicFirestore()
    fake = _install_fake_firestore(monkeypatch, store)
    with FirestoreAuthorityLedger(project="tho-test") as ledger:
        assert fake.created_projects == ["tho-test"]
        draft = _draft(ledger)
    assert store.deployment(draft.deployment_id) is not None


def test_default_transaction_executor_runs_firestore_transactionally(monkeypatch):
    store = _AtomicFirestore()
    fake = _install_fake_firestore(monkeypatch, store)
    ledger = FirestoreAuthorityLedger(client=fake.Client())
    try:
        draft = _draft(ledger)
    finally:
        ledger.close()
    assert store.deployment(draft.deployment_id) is not None
    assert [row["event_type"] for row in store.events(draft.deployment_id)] == [
        "INTERNAL_DRAFT_CREATED"
    ]


def test_context_manager_closes_owned_pool():
    store = _AtomicFirestore()
    with FirestoreAuthorityLedger(
        client=store, transaction_executor=store.run_transaction
    ) as ledger:
        draft = _draft(ledger)
    assert draft.version == 1


def test_naive_clock_fails_closed_as_invalid_ledger_clock():
    store = _AtomicFirestore()
    ledger = FirestoreAuthorityLedger(
        client=store,
        transaction_executor=store.run_transaction,
        clock=lambda: datetime(2026, 8, 12),
    )
    try:
        with pytest.raises(LedgerWriteError, match="ledger_clock_invalid"):
            _draft(ledger)
    finally:
        ledger.close()


# ---------------------------------------------------------------------------
# Reference/snapshot guards (L181, L225-226, L230, L246, L272, L275-276, L283,
# L286-287, L292, L295-296, L327-328)
# ---------------------------------------------------------------------------


def test_get_rejects_malformed_deployment_id(durable):
    ledger, _store, _clock = durable
    with pytest.raises(DeploymentNotFound, match="deployment not found"):
        ledger.get("not-a-deployment-id")


def test_get_missing_document_raises_deployment_not_found(durable):
    ledger, _store, _clock = durable
    with pytest.raises(DeploymentNotFound, match="deployment not found"):
        ledger.get(_VALID_ID)


def test_get_generic_backend_failure_is_sanitized(monkeypatch, durable):
    ledger, _store, _clock = durable
    draft = _draft(ledger)
    monkeypatch.setattr(ledger, "_collection", _BoomCollection())
    with pytest.raises(LedgerWriteError, match="ledger_read_failed"):
        ledger.get(draft.deployment_id)


def test_create_or_get_rejects_candidate_that_fails_record_validation(durable):
    ledger, _store, _clock = durable
    bad_hash = "sha256:" + "g" * 64  # identity-consistent but non-hex digest
    candidate = _candidate(f"draft--{'g' * 64}", bad_hash)
    with pytest.raises(LedgerWriteError, match="ledger_record_invalid"):
        ledger.create_or_get(candidate)


def test_event_builder_rejects_inconsistent_transition(durable):
    ledger, _store, clock = durable
    draft = _draft(ledger)
    with pytest.raises(LedgerWriteError, match="ledger_event_invalid"):
        FirestoreAuthorityLedger._event(
            draft,
            event_type="INTERNAL_DRAFT_CREATED",
            from_state=DeploymentState.SERVER_VALIDATED,
            occurred_at=clock.value,
        )


# ---------------------------------------------------------------------------
# list_events failure mapping (L444-449)
# ---------------------------------------------------------------------------


def test_list_events_reraises_control_plane_errors(durable):
    ledger, _store, _clock = durable
    with pytest.raises(DeploymentNotFound, match="deployment not found"):
        ledger.list_events("not-a-deployment-id")


def test_list_events_rejects_corrupt_event_rows(durable):
    ledger, store, _clock = durable
    draft = _draft(ledger)
    corrupt_path = (
        "google_ads_deployments",
        draft.deployment_id,
        "authority_events",
        "00000000000000000001-internal-draft-created",
    )
    row = dict(store.rows[corrupt_path])
    row["raw_request"] = {"id": "secret"}
    store.rows[corrupt_path] = row

    with pytest.raises(LedgerWriteError, match="ledger_event_invalid"):
        ledger.list_events(draft.deployment_id)


def test_list_events_sanitizes_generic_backend_failures(monkeypatch, durable):
    ledger, _store, _clock = durable
    draft = _draft(ledger)
    monkeypatch.setattr(ledger, "_collection", _BoomRootCollection())
    with pytest.raises(LedgerWriteError, match="ledger_read_failed"):
        ledger.list_events(draft.deployment_id)


# ---------------------------------------------------------------------------
# Access evidence guards (L457, L472-473, L485-486, L488)
# ---------------------------------------------------------------------------


def test_get_access_evidence_rejects_non_enum_check_key(durable):
    ledger, _store, _clock = durable
    draft = _draft(ledger)
    with pytest.raises(LedgerWriteError, match="access_evidence_invalid"):
        ledger.get_access_evidence(draft.deployment_id, "raw-check-key")  # type: ignore[arg-type]


def test_get_access_evidence_missing_row_fails_closed(durable):
    ledger, _store, _clock = durable
    draft = _draft(ledger)
    with pytest.raises(LedgerWriteError, match="access_evidence_missing"):
        ledger.get_access_evidence(
            draft.deployment_id, AccessCheckKey.GOOGLE_ADS_ACCOUNT_ACCESS_AND_USD_GREEN
        )


def test_get_access_evidence_sanitizes_generic_backend_failures(monkeypatch, durable):
    ledger, _store, _clock = durable
    draft = _draft(ledger)
    monkeypatch.setattr(ledger, "_collection", _BoomCollection())
    with pytest.raises(LedgerWriteError, match="access_evidence_read_failed"):
        ledger.get_access_evidence(
            draft.deployment_id, AccessCheckKey.GOOGLE_ADS_ACCOUNT_ACCESS_AND_USD_GREEN
        )


def test_record_access_evidence_rejects_invalid_evidence_and_bool_version(durable):
    ledger, _store, clock = durable
    draft = _draft(ledger)
    evidence = _access_evidence(draft.deployment_id, clock)
    expired = replace(evidence, expires_at=clock.value)
    with pytest.raises(LedgerWriteError, match="access_evidence_invalid"):
        ledger.record_access_evidence(expired, expected_version=draft.version)
    with pytest.raises(InvalidStateTransition, match="stale deployment version"):
        ledger.record_access_evidence(evidence, expected_version=True)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# approve_paused_create_with_proof pre-transaction guards
# (L544, L546, L558, L565, L635) and replay-evidence guards
# (L272, L275-276, L283, L286-287, L292, L295-296)
# ---------------------------------------------------------------------------


def test_approve_rejects_changed_reviewed_contract(durable):
    ledger, _store, _clock = durable
    with pytest.raises(InvalidStateTransition, match="reviewed contract changed"):
        ledger.approve_paused_create_with_proof(
            deployment_id=_VALID_ID,
            expected_version=2,
            contract_hash="sha256:" + "F" * 64,
            expected_caps={},
            proof=_proof(),
            access_evidence_id=_OTHER_HASH,
        )


def test_approve_rejects_changed_access_evidence_reference(durable):
    ledger, _store, _clock = durable
    with pytest.raises(InvalidStateTransition, match="access evidence changed"):
        ledger.approve_paused_create_with_proof(
            deployment_id=_VALID_ID,
            expected_version=2,
            contract_hash=_VALID_HASH,
            expected_caps={},
            proof=_proof(),
            access_evidence_id="evidence-not-a-digest",
        )


def test_approve_rejects_proof_missing_required_fields(durable):
    ledger, _store, _clock = durable
    with pytest.raises(InvalidStateTransition, match="owner proof is invalid"):
        ledger.approve_paused_create_with_proof(
            deployment_id=_VALID_ID,
            expected_version=2,
            contract_hash=_VALID_HASH,
            expected_caps={},
            proof=SimpleNamespace(),
            access_evidence_id=_OTHER_HASH,
        )


def test_approve_rejects_proof_whose_binding_changed(durable):
    ledger, _store, _clock = durable
    with pytest.raises(InvalidStateTransition, match="owner proof changed"):
        ledger.approve_paused_create_with_proof(
            deployment_id=_VALID_ID,
            expected_version=2,
            contract_hash=_VALID_HASH,
            expected_caps={},
            proof=_proof(purpose="OTHER_PURPOSE"),
            access_evidence_id=_OTHER_HASH,
        )


def test_approve_rejects_contract_that_does_not_match_record(durable):
    ledger, store, _clock, validated, _access, _envelope, _proof_obj = _approval_fixture(durable)
    changed_proof = _proof(
        deployment_id=validated.deployment_id,
        contract_hash=_OTHER_HASH,
        access_evidence_id=_OTHER_HASH,
    )
    with pytest.raises(InvalidStateTransition, match="not ready for paused approval"):
        ledger.approve_paused_create_with_proof(
            deployment_id=validated.deployment_id,
            expected_version=validated.version,
            contract_hash=_OTHER_HASH,
            expected_caps={},
            proof=changed_proof,
            access_evidence_id=_OTHER_HASH,
        )
    assert store.deployment(validated.deployment_id)["state"] == "SERVER_VALIDATED"


def test_approve_rejects_unavailable_owner_proof_source(durable):
    ledger, store, _clock, validated, access, envelope, proof = _approval_fixture(durable)
    del store.rows[
        (
            "google_ads_owner_step_up_nonces",
            envelope.nonce_hash,
            "verified_evidence",
            envelope.evidence_id,
        )
    ]

    with pytest.raises(InvalidStateTransition, match="owner proof is unavailable"):
        _approve(ledger, validated, access, proof)


def test_approve_rejects_corrupt_owner_proof_source(durable):
    ledger, store, _clock, validated, access, envelope, proof = _approval_fixture(durable)
    source_path = (
        "google_ads_owner_step_up_nonces",
        envelope.nonce_hash,
        "verified_evidence",
        envelope.evidence_id,
    )
    store.rows[source_path] = {**store.rows[source_path], "raw_credential": "do-not-store"}

    with pytest.raises(LedgerWriteError, match="owner_proof_invalid"):
        _approve(ledger, validated, access, proof)


def _proof_paths(deployment_id: str, proof_id: str):
    root = ("google_ads_deployments", deployment_id)
    return (
        (*root, "approval_proofs", proof_id),
        (*root, "dispatch_outbox", "paused-create"),
    )


def test_approve_replay_rejects_missing_marker(durable):
    ledger, store, _clock, validated, access, envelope, proof = _approval_fixture(durable)
    _approve(ledger, validated, access, proof)
    marker_path, outbox_path = _proof_paths(validated.deployment_id, envelope.evidence_id)
    del store.rows[marker_path]
    del store.rows[outbox_path]

    with pytest.raises(InvalidStateTransition, match="owner proof replay evidence is unavailable"):
        _approve(ledger, validated, access, proof)


def test_approve_replay_rejects_corrupt_marker(durable):
    ledger, store, _clock, validated, access, envelope, proof = _approval_fixture(durable)
    _approve(ledger, validated, access, proof)
    marker_path, _outbox_path = _proof_paths(validated.deployment_id, envelope.evidence_id)
    store.rows[marker_path] = {**store.rows[marker_path], "raw_owner_email": "do-not-store"}

    with pytest.raises(LedgerWriteError, match="owner_proof_invalid"):
        _approve(ledger, validated, access, proof)


def test_approve_replay_rejects_missing_outbox(durable):
    ledger, store, _clock, validated, access, envelope, proof = _approval_fixture(durable)
    _approve(ledger, validated, access, proof)
    _marker_path, outbox_path = _proof_paths(validated.deployment_id, envelope.evidence_id)
    del store.rows[outbox_path]

    with pytest.raises(InvalidStateTransition, match="paused-create outbox is unavailable"):
        _approve(ledger, validated, access, proof)


def test_approve_replay_rejects_corrupt_outbox(durable):
    ledger, store, _clock, validated, access, envelope, proof = _approval_fixture(durable)
    _approve(ledger, validated, access, proof)
    _marker_path, outbox_path = _proof_paths(validated.deployment_id, envelope.evidence_id)
    store.rows[outbox_path] = {**store.rows[outbox_path], "raw_request": {"id": "secret"}}

    with pytest.raises(LedgerWriteError, match="paused_create_outbox_invalid"):
        _approve(ledger, validated, access, proof)


# ---------------------------------------------------------------------------
# get_paused_create_outbox failure mapping (L733-736)
# ---------------------------------------------------------------------------


def test_get_paused_create_outbox_reraises_control_plane_errors(durable):
    ledger, _store, _clock = durable
    with pytest.raises(DeploymentNotFound, match="deployment not found"):
        ledger.get_paused_create_outbox("not-a-deployment-id")


def test_get_paused_create_outbox_missing_row_fails_closed(durable):
    ledger, _store, _clock = durable
    draft = _draft(ledger)
    with pytest.raises(InvalidStateTransition, match="outbox is unavailable"):
        ledger.get_paused_create_outbox(draft.deployment_id)


def test_get_paused_create_outbox_sanitizes_generic_backend_failures(monkeypatch, durable):
    ledger, _store, _clock = durable
    draft = _draft(ledger)
    monkeypatch.setattr(ledger, "_collection", _BoomCollection())
    with pytest.raises(LedgerWriteError, match="paused_create_outbox_read_failed"):
        ledger.get_paused_create_outbox(draft.deployment_id)


# ---------------------------------------------------------------------------
# Outbox lifecycle branches (L759, L761, L816-827, L860, L885, L905, L911, L913)
# ---------------------------------------------------------------------------


def test_reconcile_returns_early_for_already_dispatched_outbox(durable):
    ledger, store, _clock, validated, access, _envelope, proof = _approval_fixture(durable)
    _approve(ledger, validated, access, proof)
    outbox_path = (
        "google_ads_deployments",
        validated.deployment_id,
        "dispatch_outbox",
        "paused-create",
    )
    store.rows[outbox_path] = _outbox_row(
        validated.deployment_id,
        validated.contract_hash,
        _clock.value,
        state="DISPATCHED",
        dispatched_at=_clock.value,
    )

    assert ledger.reconcile_terminal_paused_create_outbox(validated.deployment_id) == "DISPATCHED"


def test_reconcile_leaves_live_dispatching_outbox_unchanged(durable):
    ledger, _store, _clock, validated, access, _envelope, proof = _approval_fixture(durable)
    _approve(ledger, validated, access, proof)
    assert ledger.claim_paused_create_outbox(validated.deployment_id, "dispatcher-1") is True

    assert ledger.reconcile_terminal_paused_create_outbox(validated.deployment_id) == "DISPATCHING"
    outbox = ledger.get_paused_create_outbox(validated.deployment_id)
    assert outbox.state == "DISPATCHING"
    assert outbox.attempt_count == 1


def test_claim_outbox_marks_attempts_exhausted_and_returns_false(durable):
    ledger, store, clock, validated, access, _envelope, proof = _approval_fixture(durable)
    _approve(ledger, validated, access, proof)
    outbox_path = (
        "google_ads_deployments",
        validated.deployment_id,
        "dispatch_outbox",
        "paused-create",
    )
    store.rows[outbox_path] = _outbox_row(
        validated.deployment_id,
        validated.contract_hash,
        clock.value,
        state="DISPATCHING",
        attempt_count=MAX_GOOGLE_ADS_DISPATCH_ATTEMPTS,
        dispatcher_claim_hash=f"sha256:{'d' * 64}",
        claim_expires_at=clock.value - timedelta(seconds=1),
    )

    assert ledger.claim_paused_create_outbox(validated.deployment_id, "dispatcher-late") is False
    failed = ledger.get_paused_create_outbox(validated.deployment_id)
    assert failed.state == "FAILED"
    assert failed.error_code == "dispatch_attempts_exhausted"


def test_release_unclaimed_outbox_fails_closed(durable):
    ledger, _store, _clock, validated, access, _envelope, proof = _approval_fixture(durable)
    _approve(ledger, validated, access, proof)

    with pytest.raises(InvalidStateTransition, match="outbox is not claimed"):
        ledger.release_paused_create_outbox(validated.deployment_id, "dispatcher-stranger")


def test_worker_failure_requires_sha256_dispatcher_claim(durable):
    ledger, _store, _clock = durable
    with pytest.raises(ValueError, match="dispatcher claim hash is invalid"):
        ledger.record_paused_create_worker_failure(_VALID_ID, "raw-dispatcher-claim")


def test_worker_failure_settles_terminal_create_after_completion(durable):
    ledger, _store, _clock, validated, access, _envelope, proof = _approval_fixture(durable)
    _approve(ledger, validated, access, proof)
    assert ledger.claim_paused_create_outbox(validated.deployment_id, "dispatcher-1") is True
    assert (
        ledger.claim_paused_create(
            validated.deployment_id, "worker-1", require_dispatch_outbox=True
        )
        is True
    )
    fenced = ledger.fence_paused_create(validated.deployment_id, "worker-1")
    completed = ledger.complete_paused_create(
        validated.deployment_id, "worker-1", _OTHER_HASH, expected_version=fenced.version
    )
    assert completed.state is DeploymentState.PAUSED_CREATED
    dispatching = ledger.get_paused_create_outbox(validated.deployment_id)
    claim_hash = dispatching.dispatcher_claim_hash
    assert claim_hash is not None

    assert ledger.record_paused_create_worker_failure(validated.deployment_id, claim_hash) == (
        "DISPATCHED"
    )
    assert ledger.get_paused_create_outbox(validated.deployment_id).state == "DISPATCHED"


def test_worker_failure_rejects_non_retryable_authority(durable):
    ledger, store, clock, _validated, _access, _envelope, _proof = _approval_fixture(durable)
    draft = _draft(ledger)
    store.rows[
        ("google_ads_deployments", draft.deployment_id, "dispatch_outbox", "paused-create")
    ] = _outbox_row(draft.deployment_id, draft.contract_hash, clock.value)

    with pytest.raises(InvalidStateTransition, match="not retryable"):
        ledger.record_paused_create_worker_failure(draft.deployment_id, f"sha256:{'e' * 64}")


def test_worker_failure_returns_terminal_outbox_states_without_rearm(durable):
    ledger, _store, _clock, validated, access, _envelope, proof = _approval_fixture(durable)
    _approve(ledger, validated, access, proof)

    assert (
        ledger.record_paused_create_worker_failure(validated.deployment_id, f"sha256:{'e' * 64}")
        == "PENDING"
    )


# ---------------------------------------------------------------------------
# transition / claim / fence / complete / release branches
# (L947, L968, L988, L990, L1046, L1110, L1117, L1119, L1121, L1204-1206)
# ---------------------------------------------------------------------------


def test_transition_rejects_pairs_outside_the_allowed_graph(durable):
    ledger, _store, _clock = durable
    draft = _draft(ledger)
    with pytest.raises(InvalidStateTransition, match="invalid transition"):
        ledger.transition(
            draft.deployment_id,
            expected=DeploymentState.PAUSED_CREATED,
            target=DeploymentState.INTERNAL_DRAFT,
        )


def test_server_validation_transition_is_idempotent_without_key(durable):
    ledger, store, _clock = durable
    draft = _draft(ledger)
    first = ledger.transition(
        draft.deployment_id,
        expected=DeploymentState.INTERNAL_DRAFT,
        target=DeploymentState.SERVER_VALIDATED,
        expected_version=1,
    )
    replay = ledger.transition(
        draft.deployment_id,
        expected=DeploymentState.INTERNAL_DRAFT,
        target=DeploymentState.SERVER_VALIDATED,
    )

    assert replay == first
    assert len(store.events(draft.deployment_id)) == 2


def test_transition_rejects_state_that_no_longer_matches_expected(durable):
    ledger, _store, _clock = durable
    draft = _draft(ledger)
    approved, _invoker = _approved(ledger)
    assert approved.deployment_id == draft.deployment_id

    with pytest.raises(InvalidStateTransition, match="invalid transition"):
        ledger.transition(
            approved.deployment_id,
            expected=DeploymentState.SERVER_VALIDATED,
            target=DeploymentState.PAUSED_CREATE_APPROVED,
            expected_version=approved.version,
        )


def test_server_validation_key_conflicts_with_existing_marker(durable):
    ledger, store, _clock = durable
    draft = _draft(ledger)
    marker_path = (
        "google_ads_deployments",
        draft.deployment_id,
        "operation_keys",
        "server-validation",
    )
    store.rows[marker_path] = {"operation": "SERVER_VALIDATION", "key_hash": _OTHER_HASH}

    with pytest.raises(InvalidStateTransition, match="idempotency evidence conflicts"):
        ledger.transition(
            draft.deployment_id,
            expected=DeploymentState.INTERNAL_DRAFT,
            target=DeploymentState.SERVER_VALIDATED,
            server_validation_key_hash=_VALID_HASH,
        )


def test_claim_paused_create_returns_false_before_approval(durable):
    ledger, _store, _clock = durable
    draft = _draft(ledger)
    assert ledger.claim_paused_create(draft.deployment_id, "early-worker") is False


def test_fence_requires_paused_create_approved_state(durable):
    ledger, _store, _clock = durable
    draft = _draft(ledger)
    with pytest.raises(InvalidStateTransition, match="not actively claimed"):
        ledger.fence_paused_create(draft.deployment_id, "worker-1")


def test_fence_rejects_stranger_while_create_is_fenced(durable):
    ledger, _store, _clock = durable
    approved, _invoker = _approved(ledger)
    assert ledger.claim_paused_create(approved.deployment_id, "worker-a") is True
    ledger.fence_paused_create(approved.deployment_id, "worker-a")

    with pytest.raises(InvalidStateTransition, match="fenced by another worker"):
        ledger.fence_paused_create(approved.deployment_id, "worker-b")


def test_fence_rejects_worker_without_the_live_claim(durable):
    ledger, _store, _clock = durable
    approved, _invoker = _approved(ledger)
    assert ledger.claim_paused_create(approved.deployment_id, "worker-a") is True

    with pytest.raises(InvalidStateTransition, match="not actively claimed"):
        ledger.fence_paused_create(approved.deployment_id, "worker-b")


def test_fence_rejects_expired_claim_even_for_original_claimant(durable):
    ledger, _store, clock = durable
    approved, _invoker = _approved(ledger)
    assert ledger.claim_paused_create(approved.deployment_id, "worker-a") is True
    clock.advance(31)

    with pytest.raises(InvalidStateTransition, match="not actively claimed"):
        ledger.fence_paused_create(approved.deployment_id, "worker-a")


def test_completed_create_replays_only_for_identical_provider_reference(durable):
    ledger, _store, _clock = durable
    approved, _invoker = _approved(ledger)
    assert ledger.claim_paused_create(approved.deployment_id, "worker-1") is True
    fenced = ledger.fence_paused_create(approved.deployment_id, "worker-1")

    completed = ledger.complete_paused_create(
        approved.deployment_id, "worker-1", _OTHER_HASH, expected_version=fenced.version
    )
    replay = ledger.complete_paused_create(approved.deployment_id, "worker-1", _OTHER_HASH)
    assert replay == completed

    with pytest.raises(InvalidStateTransition, match="provider reference conflicts"):
        ledger.complete_paused_create(approved.deployment_id, "worker-1", _VALID_HASH)


def test_release_claim_rejects_error_codes_outside_allowlist(durable):
    ledger, _store, _clock = durable
    with pytest.raises(ValueError, match="not in the sanitized allowlist"):
        ledger.release_claim(_VALID_ID, "worker-1", "raw_provider_error")


def test_mark_fenced_failure_rejects_error_codes_outside_allowlist(durable):
    ledger, _store, _clock = durable
    with pytest.raises(ValueError, match="not in the sanitized allowlist"):
        ledger.mark_fenced_failure(_VALID_ID, "worker-1", "raw_provider_error")


def test_mark_fenced_failure_requires_an_existing_fence(durable):
    ledger, _store, _clock = durable
    approved, _invoker = _approved(ledger)
    with pytest.raises(InvalidStateTransition, match="not fenced by this worker"):
        ledger.mark_fenced_failure(
            approved.deployment_id, "worker-1", "provider_timeout_unresolved"
        )
