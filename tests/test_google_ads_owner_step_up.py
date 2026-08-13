from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Lock

import pytest
from pydantic import ValidationError

from auth.google_ads_step_up import (
    MAX_NONCE_TTL_SECONDS,
    PAUSED_CREATE_PURPOSE,
    FakeStepUpEvidenceSigner,
    FirestoreStepUpStore,
    InMemoryStepUpStore,
    StepUpContext,
    StepUpEvidenceEnvelope,
    StepUpNonce,
    StepUpStoreError,
    build_evidence_envelope,
    context_digest,
    email_hash,
    hash_value,
)
from auth.store import CredentialRecord, FirestoreCredentialStore, InMemoryCredentialStore
from scripts.google_ads_access_evidence import (
    AccessCheckKey,
    AccessEvidenceStatus,
    build_access_evidence,
    evidence_payload,
)

DEPLOYMENT_KEY = "tho-search-high-intent-huffman-v1"
CONTRACT_DIGEST = "a" * 64
DEPLOYMENT_ID = f"{DEPLOYMENT_KEY}--{CONTRACT_DIGEST}"
NOW = datetime(2026, 8, 13, 3, 0, tzinfo=UTC)


class _Snapshot:
    def __init__(self, data, *, document_id=""):
        self._data = data
        self.exists = data is not None
        self.id = document_id

    def to_dict(self):
        return dict(self._data or {})


class _Reference:
    def __init__(self, rows, key):
        self.rows = rows
        self.key = key

    def get(self, transaction=None, timeout=None):
        del transaction, timeout
        return _Snapshot(self.rows.get(self.key), document_id=self.key[-1])

    def create(self, data, timeout=None):
        del timeout
        if self.key in self.rows:
            raise RuntimeError("already exists")
        self.rows[self.key] = dict(data)

    def set(self, data, timeout=None):
        del timeout
        self.rows[self.key] = dict(data)

    def collection(self, name):
        return _Collection(self.rows, (*self.key, name))


class _Collection:
    def __init__(self, rows, prefix):
        self.rows = rows
        self.prefix = prefix if isinstance(prefix, tuple) else (prefix,)

    def document(self, key):
        return _Reference(self.rows, (*self.prefix, key))


class _Transaction:
    def __init__(self, rows, *, fail_evidence=False):
        self.rows = rows
        self.start_rows = {key: dict(value) for key, value in rows.items()}
        self.pending = []
        self.fail_evidence = fail_evidence

    def update(self, reference, data):
        self.pending.append(("update", reference.key, dict(data)))

    def create(self, reference, data):
        if self.fail_evidence and "verified_evidence" in reference.key:
            raise RuntimeError("injected evidence failure")
        self.pending.append(("create", reference.key, dict(data)))

    def commit(self):
        candidate = {key: dict(value) for key, value in self.rows.items()}
        for operation, key, data in self.pending:
            if operation == "create":
                if key in candidate:
                    raise RuntimeError("already exists")
                candidate[key] = data
            else:
                candidate[key].update(data)
        self.rows.clear()
        self.rows.update(candidate)


class _Client:
    def __init__(self):
        self.rows = {}

    def collection(self, name):
        return _Collection(self.rows, name)


def _transaction_executor(client, *, fail_evidence=False):
    def execute(operation):
        transaction = _Transaction(client.rows, fail_evidence=fail_evidence)
        result = operation(transaction)
        transaction.commit()
        return result

    return execute


def _conflict_retry_transaction_executor(client):
    """Model Firestore optimistic conflict retry for exactly two callers."""
    barrier = Barrier(2)
    commit_lock = Lock()

    def execute(operation):
        transaction = _Transaction(client.rows)
        result = operation(transaction)
        barrier.wait(timeout=5)
        with commit_lock:
            if transaction.start_rows != client.rows:
                retry = _Transaction(client.rows)
                result = operation(retry)
                retry.commit()
            else:
                transaction.commit()
        return result

    return execute


def _context(**overrides):
    access_evidence = build_access_evidence(
        deployment_id=DEPLOYMENT_ID,
        check_key=AccessCheckKey.GOOGLE_ADS_ACCOUNT_ACCESS_GREEN,
        status=AccessEvidenceStatus.PASSED,
        observed_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        source_revision="a" * 40,
        now=NOW,
    )
    values = {
        "purpose": PAUSED_CREATE_PURPOSE,
        "deployment_id": DEPLOYMENT_ID,
        "contract_hash": f"sha256:{CONTRACT_DIGEST}",
        "caps": {
            "average_daily_usd": 20,
            "max_single_day_charge_usd": 40,
            "monthly_charge_limit_usd": 608,
            "max_cpc_usd": 5,
        },
        "evidence_digest": access_evidence.evidence_digest,
    }
    values.update(overrides)
    return StepUpContext.model_validate(values)


def _nonce(context=None, *, expires_at=None):
    context = context or _context()
    return StepUpNonce.model_validate(
        {
            "schema_version": 1,
            "nonce_hash": hash_value("one-time-nonce"),
            "context_digest": context_digest(context),
            "owner_email_hash": email_hash("aristotlespec@gmail.com"),
            "issued_at": NOW,
            "expires_at": expires_at or NOW + timedelta(seconds=MAX_NONCE_TTL_SECONDS),
        }
    )


def _credential_store():
    record = CredentialRecord(
        credential_id=b"credential",
        public_key=b"public-key",
        sign_count=0,
        user_id="aristotlespec@gmail.com",
        created_at=NOW,
    )
    return InMemoryCredentialStore([record]), record


def _access_evidence(context=None):
    context = context or _context()
    return build_access_evidence(
        deployment_id=context.deployment_id,
        check_key=AccessCheckKey.GOOGLE_ADS_ACCOUNT_ACCESS_GREEN,
        status=AccessEvidenceStatus.PASSED,
        observed_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        source_revision="a" * 40,
        now=NOW,
    )


def test_context_is_exact_purpose_bound_and_rejects_extra_raw_or_mismatched_identity():
    context = _context()
    assert context.purpose == "PAUSED_CREATE"

    for unsafe in (
        {**context.model_dump(), "purpose": "ACTIVATE"},
        {**context.model_dump(), "customer_id": "123"},
        {**context.model_dump(), "provider": "google"},
        {**context.model_dump(), "contract_hash": f"sha256:{'f' * 64}"},
        {**context.model_dump(), "caps": {**context.caps.model_dump(), "spend": True}},
    ):
        with pytest.raises(ValidationError):
            StepUpContext.model_validate(unsafe)


def test_nonce_is_strict_one_time_and_never_lives_more_than_five_minutes():
    assert (_nonce().expires_at - _nonce().issued_at).total_seconds() == 300
    with pytest.raises(ValidationError, match="five minutes"):
        _nonce(expires_at=NOW + timedelta(seconds=301))
    with pytest.raises(ValidationError):
        StepUpNonce.model_validate({**_nonce().model_dump(), "raw_challenge": "secret"})


def test_atomic_consume_rejects_stale_mismatch_changed_caps_and_replay():
    store = InMemoryStepUpStore(clock=lambda: NOW)
    credentials, credential = _credential_store()
    context = _context()
    nonce = _nonce(context)
    store.create_nonce(nonce)
    envelope = build_evidence_envelope(
        nonce=nonce,
        context=context,
        credential_id_hash=hash_value("credential"),
        verified_at=NOW,
    )
    credential_usage = credentials.prepare_usage_cas(credential, new_sign_count=1)

    changed_caps = _context(caps={**context.caps.model_dump(), "max_cpc_usd": 6})
    assert (
        store.consume_and_record(
            nonce.nonce_hash,
            expected_context_digest=context_digest(changed_caps),
            envelope=envelope,
            access_evidence=_access_evidence(context),
            credential_store=credentials,
            credential_usage=credential_usage,
        )
        is False
    )
    assert (
        store.consume_and_record(
            nonce.nonce_hash,
            expected_context_digest=context_digest(context),
            envelope=envelope,
            access_evidence=_access_evidence(context),
            credential_store=credentials,
            credential_usage=credential_usage,
        )
        is True
    )
    assert (
        store.consume_and_record(
            nonce.nonce_hash,
            expected_context_digest=context_digest(context),
            envelope=envelope,
            access_evidence=_access_evidence(context),
            credential_store=credentials,
            credential_usage=credential_usage,
        )
        is False
    )

    stale_store = InMemoryStepUpStore(clock=lambda: NOW + timedelta(seconds=301))
    stale_credentials, stale_credential = _credential_store()
    stale_store.create_nonce(nonce)
    assert (
        stale_store.consume_and_record(
            nonce.nonce_hash,
            expected_context_digest=context_digest(context),
            envelope=envelope,
            access_evidence=_access_evidence(context),
            credential_store=stale_credentials,
            credential_usage=stale_credentials.prepare_usage_cas(
                stale_credential, new_sign_count=1
            ),
        )
        is False
    )


def test_concurrent_nonce_consumption_records_exactly_one_sanitized_envelope():
    store = InMemoryStepUpStore(clock=lambda: NOW)
    credentials, credential = _credential_store()
    context = _context()
    nonce = _nonce(context)
    envelope = build_evidence_envelope(
        nonce=nonce,
        context=context,
        credential_id_hash=hash_value("credential"),
        verified_at=NOW,
    )
    store.create_nonce(nonce)
    usages = [credentials.prepare_usage_cas(credential, new_sign_count=1) for _index in range(4)]

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(
            pool.map(
                lambda _index: store.consume_and_record(
                    nonce.nonce_hash,
                    expected_context_digest=context_digest(context),
                    envelope=envelope,
                    access_evidence=_access_evidence(context),
                    credential_store=credentials,
                    credential_usage=usages[_index],
                ),
                range(4),
            )
        )

    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 3
    assert store.get_evidence(envelope.evidence_id) == envelope
    assert credentials.get(credential.credential_id).sign_count == 1
    serialized = json.dumps(envelope.model_dump(mode="json"))
    for forbidden in (
        "aristotlespec@gmail.com",
        'credential"',
        "authenticatorData",
        "clientDataJSON",
        "signature",
        "customer_id",
        "account_id",
        "token",
    ):
        assert forbidden not in serialized


def test_signer_seam_produces_strict_fake_proof_without_cloud_or_secret_fields():
    context = _context()
    nonce = _nonce(context)
    envelope = build_evidence_envelope(
        nonce=nonce,
        context=context,
        credential_id_hash=hash_value("credential"),
        verified_at=NOW,
    )

    signed = FakeStepUpEvidenceSigner().sign(envelope)

    assert signed.algorithm == "TEST_SHA256"
    assert signed.envelope == envelope
    assert signed.signature.startswith("sha256:")
    assert StepUpEvidenceEnvelope.model_validate(signed.envelope.model_dump()) == envelope


def test_firestore_store_atomically_consumes_nonce_and_persists_only_sanitized_evidence():
    client = _Client()
    credentials = FirestoreCredentialStore(client=client)
    credential = CredentialRecord(
        credential_id=b"credential",
        public_key=b"public-key",
        sign_count=0,
        user_id="aristotlespec@gmail.com",
        created_at=NOW,
    )
    credentials.add(credential)
    store = FirestoreStepUpStore(
        client=client,
        transaction_executor=_transaction_executor(client),
        clock=lambda: NOW,
    )
    context = _context()
    access_evidence = _access_evidence(context)
    client.rows[
        (
            "google_ads_deployments",
            context.deployment_id,
            "access_evidence",
            access_evidence.check_key.value,
        )
    ] = evidence_payload(access_evidence)
    nonce = _nonce(context)
    envelope = build_evidence_envelope(
        nonce=nonce,
        context=context,
        credential_id_hash=hash_value("credential"),
        verified_at=NOW,
    )
    credential_usage = credentials.prepare_usage_cas(credential, new_sign_count=1)

    store.create_nonce(nonce)
    assert store.get_nonce(nonce.nonce_hash) == nonce
    assert (
        store.consume_and_record(
            nonce.nonce_hash,
            expected_context_digest=context_digest(context),
            envelope=envelope,
            access_evidence=_access_evidence(context),
            credential_store=credentials,
            credential_usage=credential_usage,
        )
        is True
    )
    assert (
        store.consume_and_record(
            nonce.nonce_hash,
            expected_context_digest=context_digest(context),
            envelope=envelope,
            access_evidence=_access_evidence(context),
            credential_store=credentials,
            credential_usage=credential_usage,
        )
        is False
    )

    serialized = json.dumps(
        [value for key, value in client.rows.items() if key[0] == store.COLLECTION],
        default=str,
    )
    assert envelope.evidence_id in serialized
    assert credentials.get(credential.credential_id).sign_count == 1
    for forbidden in (
        "aristotlespec@gmail.com",
        "raw_challenge",
        "authenticatorData",
        "clientDataJSON",
        "customer_id",
        "account_id",
        "token",
    ):
        assert forbidden not in serialized


def test_firestore_evidence_failure_never_consumes_nonce():
    client = _Client()
    credentials = FirestoreCredentialStore(client=client)
    credential = CredentialRecord(
        credential_id=b"credential",
        public_key=b"public-key",
        sign_count=0,
        user_id="aristotlespec@gmail.com",
        created_at=NOW,
    )
    credentials.add(credential)
    store = FirestoreStepUpStore(
        client=client,
        transaction_executor=_transaction_executor(client, fail_evidence=True),
        clock=lambda: NOW,
    )
    context = _context()
    access_evidence = _access_evidence(context)
    client.rows[
        (
            "google_ads_deployments",
            context.deployment_id,
            "access_evidence",
            access_evidence.check_key.value,
        )
    ] = evidence_payload(access_evidence)
    nonce = _nonce(context)
    envelope = build_evidence_envelope(
        nonce=nonce,
        context=context,
        credential_id_hash=hash_value("credential"),
        verified_at=NOW,
    )
    store.create_nonce(nonce)

    with pytest.raises(StepUpStoreError, match="write_failed"):
        store.consume_and_record(
            nonce.nonce_hash,
            expected_context_digest=context_digest(context),
            envelope=envelope,
            access_evidence=_access_evidence(context),
            credential_store=credentials,
            credential_usage=credentials.prepare_usage_cas(credential, new_sign_count=1),
        )

    nonce_row = client.rows[(store.COLLECTION, nonce.nonce_hash)]
    assert nonce_row["consumed_at"] is None
    assert nonce_row["evidence_id"] is None
    assert credentials.get(credential.credential_id).sign_count == 0


def test_firestore_consume_rejects_access_evidence_changed_after_route_read():
    client = _Client()
    credentials = FirestoreCredentialStore(client=client)
    credential = CredentialRecord(
        credential_id=b"credential",
        public_key=b"public-key",
        sign_count=0,
        user_id="aristotlespec@gmail.com",
        created_at=NOW,
    )
    credentials.add(credential)
    store = FirestoreStepUpStore(
        client=client,
        transaction_executor=_transaction_executor(client),
        clock=lambda: NOW,
    )
    context = _context()
    route_read_evidence = _access_evidence(context)
    changed_evidence = build_access_evidence(
        deployment_id=context.deployment_id,
        check_key=AccessCheckKey.GOOGLE_ADS_ACCOUNT_ACCESS_GREEN,
        status=AccessEvidenceStatus.PASSED,
        observed_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        source_revision="b" * 40,
        now=NOW,
    )
    client.rows[
        (
            "google_ads_deployments",
            context.deployment_id,
            "access_evidence",
            changed_evidence.check_key.value,
        )
    ] = evidence_payload(changed_evidence)
    nonce = _nonce(context)
    envelope = build_evidence_envelope(
        nonce=nonce,
        context=context,
        credential_id_hash=hash_value(credential.credential_id),
        verified_at=NOW,
    )
    store.create_nonce(nonce)

    assert (
        store.consume_and_record(
            nonce.nonce_hash,
            expected_context_digest=context_digest(context),
            envelope=envelope,
            access_evidence=route_read_evidence,
            credential_store=credentials,
            credential_usage=credentials.prepare_usage_cas(
                credential,
                new_sign_count=1,
            ),
        )
        is False
    )
    nonce_row = client.rows[(store.COLLECTION, nonce.nonce_hash)]
    assert nonce_row["consumed_at"] is None
    assert nonce_row["evidence_id"] is None
    assert credentials.get(credential.credential_id).sign_count == 0


def test_concurrent_distinct_nonces_with_same_counter_have_one_atomic_winner():
    store = InMemoryStepUpStore(clock=lambda: NOW)
    credentials, credential = _credential_store()
    context = _context()
    nonces = [
        StepUpNonce(
            nonce_hash=hash_value(f"nonce-{index}"),
            context_digest=context_digest(context),
            owner_email_hash=email_hash(credential.user_id),
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )
        for index in range(2)
    ]
    envelopes = [
        build_evidence_envelope(
            nonce=nonce,
            context=context,
            credential_id_hash=hash_value(credential.credential_id),
            verified_at=NOW,
        )
        for nonce in nonces
    ]
    for nonce in nonces:
        store.create_nonce(nonce)
    usages = [
        credentials.prepare_usage_cas(credential, new_sign_count=1) for _nonce_value in nonces
    ]

    def consume(index):
        return store.consume_and_record(
            nonces[index].nonce_hash,
            expected_context_digest=context_digest(context),
            envelope=envelopes[index],
            access_evidence=_access_evidence(context),
            credential_store=credentials,
            credential_usage=usages[index],
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(consume, range(2)))

    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 1
    assert len(store._evidence) == 1
    assert credentials.get(credential.credential_id).sign_count == 1


def test_firestore_conflict_retry_allows_one_stale_counter_winner_across_distinct_nonces():
    client = _Client()
    credentials = FirestoreCredentialStore(client=client)
    credential = CredentialRecord(
        credential_id=b"credential",
        public_key=b"public-key",
        sign_count=0,
        user_id="aristotlespec@gmail.com",
        created_at=NOW,
    )
    credentials.add(credential)
    store = FirestoreStepUpStore(
        client=client,
        transaction_executor=_conflict_retry_transaction_executor(client),
        clock=lambda: NOW,
    )
    context = _context()
    access_evidence = _access_evidence(context)
    client.rows[
        (
            "google_ads_deployments",
            context.deployment_id,
            "access_evidence",
            access_evidence.check_key.value,
        )
    ] = evidence_payload(access_evidence)
    nonces = [
        StepUpNonce(
            nonce_hash=hash_value(f"firestore-nonce-{index}"),
            context_digest=context_digest(context),
            owner_email_hash=email_hash(credential.user_id),
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )
        for index in range(2)
    ]
    envelopes = [
        build_evidence_envelope(
            nonce=nonce,
            context=context,
            credential_id_hash=hash_value(credential.credential_id),
            verified_at=NOW,
        )
        for nonce in nonces
    ]
    for nonce in nonces:
        store.create_nonce(nonce)
    usages = [
        credentials.prepare_usage_cas(credential, new_sign_count=1) for _nonce_value in nonces
    ]

    def consume(index):
        return store.consume_and_record(
            nonces[index].nonce_hash,
            expected_context_digest=context_digest(context),
            envelope=envelopes[index],
            access_evidence=access_evidence,
            credential_store=credentials,
            credential_usage=usages[index],
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(consume, range(2)))

    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 1
    assert credentials.get(credential.credential_id).sign_count == 1
    consumed = [
        client.rows[(store.COLLECTION, nonce.nonce_hash)]["consumed_at"] is not None
        for nonce in nonces
    ]
    assert consumed.count(True) == 1
    evidence_rows = [key for key in client.rows if "verified_evidence" in key]
    assert len(evidence_rows) == 1
