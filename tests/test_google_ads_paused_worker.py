import copy
import inspect
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from threading import Barrier, Event

import pytest

from scripts.google_ads_launch_draft import build_mutate_request, contract_sha256
from scripts.google_ads_paused_worker import (
    AmbiguousProviderTimeout,
    DeploymentState,
    DraftReviewControlPlane,
    InMemoryAuthorityLedger,
    InvalidStateTransition,
    LedgerWriteError,
    PausedCreateApproval,
    PausedCreateControlPlane,
    PausedCreateWorker,
    ProviderPausedDeployment,
    ProviderValidationError,
    StaticContractSource,
    contract_label,
    deployment_id,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "google_ads_launch_draft.json"
FAKE_CUSTOMER_ID = "1234567890"


def _contract():
    return json.loads(CONTRACT_PATH.read_text())


def _request_builder(contract, *, validate_only):
    return build_mutate_request(contract, FAKE_CUSTOMER_ID, validate_only=validate_only)


class RecordingInvoker:
    def __init__(self):
        self.deployment_ids = []

    def invoke(self, deployment_id):
        self.deployment_ids.append(deployment_id)


class FakePausedProvider:
    def __init__(self):
        self.calls = []
        self.validation_error = None
        self.create_error = None
        self.find_results = []
        self.created = ProviderPausedDeployment(
            contract_hash="sha256:" + contract_sha256(_contract()),
            campaign_resource_name="customers/1234567890/campaigns/987654321",
            status="PAUSED",
        )

    @property
    def create_calls(self):
        return [call for call in self.calls if call[0] == "create_paused"]

    def validate(self, request):
        self.calls.append(("validate", copy.deepcopy(request)))
        if self.validation_error:
            raise self.validation_error

    def find_by_contract_label(self, label):
        self.calls.append(("find_by_contract_label", label))
        if self.find_results:
            return self.find_results.pop(0)
        return None

    def create_paused(self, request):
        self.calls.append(("create_paused", copy.deepcopy(request)))
        if self.create_error:
            raise self.create_error
        return self.created


def _approved(ledger, source, invoker=None):
    invoker = invoker or RecordingInvoker()
    draft_review = DraftReviewControlPlane(ledger=ledger, contract_source=source)
    paused_create = PausedCreateControlPlane(ledger=ledger, invoker=invoker)
    draft = draft_review.ensure_internal_draft()
    validated = draft_review.server_validate(draft.deployment_id)
    approval = PausedCreateApproval.for_record(validated)
    approved = paused_create.approve_paused_create(approval)
    return approved, invoker


def test_deployment_id_is_canonical_deployment_key_plus_contract_hash():
    contract = _contract()
    reordered = json.loads(json.dumps(contract, sort_keys=True))
    digest = contract_sha256(contract)

    expected = f"tho-search-high-intent-huffman-v1--{digest}"
    assert deployment_id(contract) == expected
    assert deployment_id(reordered) == expected
    assert contract_label(contract) == f"tho-contract-{digest[:12]}"


def test_state_machine_allows_only_paused_create_path_and_approval_has_no_spend_authority():
    ledger = InMemoryAuthorityLedger()
    source = StaticContractSource(_contract())
    draft_review = DraftReviewControlPlane(ledger, source)
    draft = draft_review.ensure_internal_draft()

    assert tuple(DeploymentState) == (
        DeploymentState.INTERNAL_DRAFT,
        DeploymentState.SERVER_VALIDATED,
        DeploymentState.PAUSED_CREATE_APPROVED,
        DeploymentState.PAUSED_CREATED,
    )
    with pytest.raises(InvalidStateTransition):
        ledger.transition(
            draft.deployment_id,
            expected=DeploymentState.INTERNAL_DRAFT,
            target=DeploymentState.PAUSED_CREATE_APPROVED,
        )

    validated = draft_review.server_validate(draft.deployment_id)
    approval = PausedCreateApproval.for_record(validated)

    assert approval.scope == "PAUSED_CREATE_ONLY"
    assert approval.activation_authorized is False
    assert approval.spend_authorized is False


def test_draft_review_and_paused_create_have_disjoint_authority_surfaces():
    ledger = InMemoryAuthorityLedger()
    source = StaticContractSource(_contract())
    invoker = RecordingInvoker()

    draft_review = DraftReviewControlPlane(ledger, source)
    paused_create = PausedCreateControlPlane(ledger, invoker)

    assert set(vars(draft_review)) == {"_ledger", "_contract_source"}
    assert not hasattr(draft_review, "_invoker")
    assert not hasattr(draft_review, "_provider")
    assert not hasattr(draft_review, "approve_paused_create")
    assert set(vars(paused_create)) == {"_ledger", "_invoker"}
    assert not hasattr(paused_create, "_contract_source")
    assert not hasattr(paused_create, "_provider")
    assert not hasattr(paused_create, "ensure_internal_draft")
    assert not hasattr(paused_create, "server_validate")

    assert {
        name
        for name, member in inspect.getmembers(DraftReviewControlPlane, inspect.isfunction)
        if not name.startswith("_")
    } == {"ensure_internal_draft", "server_validate"}
    assert {
        name
        for name, member in inspect.getmembers(PausedCreateControlPlane, inspect.isfunction)
        if not name.startswith("_")
    } == {"approve_paused_create"}


def test_transactional_claim_allows_exactly_one_concurrent_worker():
    ledger = InMemoryAuthorityLedger()
    source = StaticContractSource(_contract())
    approved, _invoker = _approved(ledger, source)
    barrier = Barrier(8)

    def claim(index):
        barrier.wait()
        return ledger.claim_paused_create(approved.deployment_id, f"worker-{index}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(claim, range(8)))

    assert claims.count(True) == 1
    assert claims.count(False) == 7
    assert ledger.get(approved.deployment_id).claimed_by is not None


def test_duplicate_approval_redispatches_durable_approved_work_safely():
    ledger = InMemoryAuthorityLedger()
    source = StaticContractSource(_contract())
    invoker = RecordingInvoker()
    draft_review = DraftReviewControlPlane(ledger, source)
    paused_create = PausedCreateControlPlane(ledger, invoker)

    first_draft = draft_review.ensure_internal_draft()
    duplicate_draft = draft_review.ensure_internal_draft()
    validated = draft_review.server_validate(first_draft.deployment_id)
    approval = PausedCreateApproval.for_record(validated)
    first = paused_create.approve_paused_create(approval)
    duplicate = paused_create.approve_paused_create(approval)

    assert duplicate_draft.deployment_id == first_draft.deployment_id
    assert duplicate == first
    assert invoker.deployment_ids == [first.deployment_id, first.deployment_id]


def test_commit_then_timeout_can_be_retried_to_dispatch_durable_approval():
    class CommitThenTimeoutLedger(InMemoryAuthorityLedger):
        def __init__(self):
            super().__init__()
            self.fail_after_approval_commit = True

        def transition(self, deployment_id, *, expected, target, expected_version=None):
            record = super().transition(
                deployment_id,
                expected=expected,
                target=target,
                expected_version=expected_version,
            )
            if target is DeploymentState.PAUSED_CREATE_APPROVED and self.fail_after_approval_commit:
                self.fail_after_approval_commit = False
                raise LedgerWriteError("ledger_write_timeout")
            return record

    ledger = CommitThenTimeoutLedger()
    source = StaticContractSource(_contract())
    invoker = RecordingInvoker()
    draft_review = DraftReviewControlPlane(ledger, source)
    paused_create = PausedCreateControlPlane(ledger, invoker)
    draft = draft_review.ensure_internal_draft()
    validated = draft_review.server_validate(draft.deployment_id)
    approval = PausedCreateApproval.for_record(validated)

    with pytest.raises(LedgerWriteError, match="ledger_write_timeout"):
        paused_create.approve_paused_create(approval)

    assert invoker.deployment_ids == []
    assert ledger.get(draft.deployment_id).state is DeploymentState.PAUSED_CREATE_APPROVED

    retried = paused_create.approve_paused_create(approval)

    assert retried.state is DeploymentState.PAUSED_CREATE_APPROVED
    assert invoker.deployment_ids == [draft.deployment_id]


def test_ledger_write_failure_blocks_worker_invocation():
    ledger = InMemoryAuthorityLedger()
    source = StaticContractSource(_contract())
    invoker = RecordingInvoker()
    draft_review = DraftReviewControlPlane(ledger, source)
    paused_create = PausedCreateControlPlane(ledger, invoker)
    draft = draft_review.ensure_internal_draft()
    validated = draft_review.server_validate(draft.deployment_id)
    approval = PausedCreateApproval.for_record(validated)
    ledger.fail_next_write()

    with pytest.raises(LedgerWriteError):
        paused_create.approve_paused_create(approval)

    assert invoker.deployment_ids == []
    assert ledger.get(draft.deployment_id).state is DeploymentState.SERVER_VALIDATED


def test_provider_validation_failure_performs_zero_create_calls():
    ledger = InMemoryAuthorityLedger()
    source = StaticContractSource(_contract())
    approved, _invoker = _approved(ledger, source)
    provider = FakePausedProvider()
    provider.validation_error = ProviderValidationError(
        "raw provider error for customers/1234567890/campaigns/987654321"
    )
    worker = PausedCreateWorker(ledger, source, _request_builder, provider)

    result = worker.run(approved.deployment_id)

    assert result.error_code == "provider_validation_failed"
    assert provider.create_calls == []
    assert ledger.get(approved.deployment_id).state is DeploymentState.PAUSED_CREATE_APPROVED
    assert ledger.get(approved.deployment_id).claimed_by is None


def test_worker_validates_then_creates_the_identical_paused_only_graph():
    ledger = InMemoryAuthorityLedger()
    source = StaticContractSource(_contract())
    approved, _invoker = _approved(ledger, source)
    provider = FakePausedProvider()
    worker = PausedCreateWorker(ledger, source, _request_builder, provider)

    result = worker.run(approved.deployment_id)

    assert [call[0] for call in provider.calls] == [
        "validate",
        "find_by_contract_label",
        "create_paused",
    ]
    validation_request = provider.calls[0][1]
    create_request = provider.calls[2][1]
    assert validation_request["mutateOperations"] == create_request["mutateOperations"]
    assert validation_request["validateOnly"] is True
    assert create_request["validateOnly"] is False
    assert validation_request["partialFailure"] is False
    assert create_request["partialFailure"] is False
    assert "ENABLED" not in json.dumps(create_request)
    assert result.state is DeploymentState.PAUSED_CREATED
    assert ledger.get(approved.deployment_id).create_fenced_at is not None


def test_provider_create_fence_prevents_reclaim_while_call_outlives_lease():
    now = [datetime(2026, 8, 12, 12, 0, tzinfo=UTC)]
    ledger = InMemoryAuthorityLedger(claim_lease_seconds=1, clock=lambda: now[0])
    source = StaticContractSource(_contract())
    approved, _invoker = _approved(ledger, source)
    create_started = Event()
    release_create = Event()

    class BlockingProvider(FakePausedProvider):
        def create_paused(self, request):
            self.calls.append(("create_paused", copy.deepcopy(request)))
            create_started.set()
            assert release_create.wait(timeout=2)
            return self.created

    provider = BlockingProvider()
    worker = PausedCreateWorker(ledger, source, _request_builder, provider)
    with ThreadPoolExecutor(max_workers=1) as pool:
        result_future = pool.submit(worker.run, approved.deployment_id)
        assert create_started.wait(timeout=2)
        now[0] += timedelta(seconds=2)
        assert ledger.claim_paused_create(approved.deployment_id, "replacement-worker") is False
        release_create.set()
        result = result_future.result(timeout=2)

    assert result.state is DeploymentState.PAUSED_CREATED


def test_commit_then_timeout_reconciles_by_contract_label_without_retrying_create():
    ledger = InMemoryAuthorityLedger()
    source = StaticContractSource(_contract())
    approved, _invoker = _approved(ledger, source)
    provider = FakePausedProvider()
    provider.create_error = AmbiguousProviderTimeout(
        "deadline after commit customers/1234567890/campaigns/987654321"
    )
    provider.find_results = [None, provider.created]
    worker = PausedCreateWorker(ledger, source, _request_builder, provider)

    result = worker.run(approved.deployment_id)

    assert [call[0] for call in provider.calls] == [
        "validate",
        "find_by_contract_label",
        "create_paused",
        "find_by_contract_label",
    ]
    assert len(provider.create_calls) == 1
    assert result.reconciled is True
    assert result.state is DeploymentState.PAUSED_CREATED


def test_provider_errors_and_resource_names_are_reduced_to_safe_codes_and_hashes():
    contract = _contract()
    source = StaticContractSource(contract)
    raw_resource_name = "customers/1234567890/campaigns/987654321"
    raw_error = f"token=do-not-leak resource={raw_resource_name}"

    failed_ledger = InMemoryAuthorityLedger()
    failed, _invoker = _approved(failed_ledger, source)
    failed_provider = FakePausedProvider()
    failed_provider.create_error = RuntimeError(raw_error)
    failed_result = PausedCreateWorker(
        failed_ledger, source, _request_builder, failed_provider
    ).run(failed.deployment_id)

    serialized_failure = repr(asdict(failed_result)) + repr(
        asdict(failed_ledger.get(failed.deployment_id))
    )
    assert failed_result.error_code == "provider_create_failed"
    assert failed_ledger.get(failed.deployment_id).error_code == "provider_create_failed"
    assert failed_ledger.get(failed.deployment_id).create_fenced_at is not None
    assert failed_ledger.get(failed.deployment_id).claimed_by is not None
    assert raw_error not in serialized_failure
    assert raw_resource_name not in serialized_failure

    success_ledger = InMemoryAuthorityLedger()
    success, _invoker = _approved(success_ledger, source)
    success_provider = FakePausedProvider()
    success_result = PausedCreateWorker(
        success_ledger, source, _request_builder, success_provider
    ).run(success.deployment_id)

    expected_hash = "sha256:" + sha256(raw_resource_name.encode()).hexdigest()
    serialized_success = repr(asdict(success_result)) + repr(
        asdict(success_ledger.get(success.deployment_id))
    )
    assert success_result.provider_reference_hash == expected_hash
    assert raw_resource_name not in serialized_success


def test_worker_exposes_no_activation_state_or_runtime_override_surface():
    ledger = InMemoryAuthorityLedger()
    source = StaticContractSource(_contract())
    provider = FakePausedProvider()
    worker = PausedCreateWorker(ledger, source, _request_builder, provider)

    assert "ENABLED" not in DeploymentState.__members__
    assert list(inspect.signature(worker.run).parameters) == ["deployment_id"]
    assert list(inspect.signature(RecordingInvoker().invoke).parameters) == ["deployment_id"]
    with pytest.raises(TypeError):
        worker.run("deployment", runtime_overrides={})
