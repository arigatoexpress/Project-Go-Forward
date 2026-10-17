"""Targeted coverage for auth/google_ads_step_up.py + auth/google_ads_step_up_routes.py.

Extends the golden tests in tests/test_google_ads_owner_step_up.py and
tests/test_google_ads_step_up_routes.py (helpers and fixtures are imported
rather than duplicated) with the remaining defensive branches: strict-model
validators, Firestore store plumbing (timeout/cancel, error mapping, lazy
client construction behind a fake google.cloud.firestore module), the
env-driven default store factory, and the step-up routes' fail-closed error
paths. Every HTTP error below is asserted on the {"success", "status_code",
"message"} envelope, matching the production exception handler in main.py.
"""

from __future__ import annotations

import base64
import sys
import time
import types
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import ValidationError

from auth import google_ads_step_up_routes as routes
from auth.google_ads_step_up import (
    FirestoreStepUpStore,
    InMemoryStepUpStore,
    StepUpEvidenceEnvelope,
    StepUpNonceDocument,
    StepUpProofReference,
    StepUpStoreError,
    _utc,
    build_evidence_envelope,
    context_digest,
    default_step_up_store,
    email_hash,
    hash_value,
    verify_proof_reference,
)
from auth.session import SessionManager
from auth.store import (
    CredentialRecord,
    FirestoreCredentialStore,
    InMemoryCredentialStore,
)
from scripts.google_ads_access_evidence import evidence_payload
from tests.test_google_ads_owner_step_up import (
    DEPLOYMENT_ID,
    NOW,
    _Client,
    _credential_store,
    _nonce,
    _Snapshot,
    _transaction_executor,
)
from tests.test_google_ads_owner_step_up import (
    _access_evidence as _owner_access_evidence,
)
from tests.test_google_ads_owner_step_up import (
    _context as _owner_context,
)
from tests.test_google_ads_step_up_routes import (
    CSRF,
    _AccessEvidenceLedger,
    _authenticate,
    _begin,
    _request_context,
)

OWNER_EMAIL = "aristotlespec@gmail.com"
STAFF_EMAIL = "mark@texashomeoutlet.com"
CONTRACT_DIGEST = "a" * 64
CONTRACT_HASH = f"sha256:{CONTRACT_DIGEST}"


def _b64_id(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _install_fake_firestore(monkeypatch, *, client=None, error=None):
    """Install a minimal google.cloud.firestore fake for lazy imports.

    The step-up store imports firestore inside methods; the emulator is not
    available, so both sys.modules and the package attribute are patched.
    Returns the list of Client(project=...) calls.
    """
    fake = types.ModuleType("google.cloud.firestore")
    calls: list = []

    def Client(project=None):
        calls.append(project)
        if error is not None:
            raise error
        return client

    fake.Client = Client
    fake.transactional = lambda operation: (lambda transaction: operation(transaction))
    monkeypatch.setitem(sys.modules, "google.cloud.firestore", fake)
    monkeypatch.setattr("google.cloud.firestore", fake, raising=False)
    return calls


# ---------------------------------------------------------------------------
# Strict model validators and proof-reference verification
# ---------------------------------------------------------------------------


def test_utc_rejects_naive_datetimes():
    with pytest.raises(ValueError, match="timezone-aware"):
        _utc(datetime(2026, 1, 1))
    aware = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert _utc(aware) is aware


def test_envelope_validator_rejects_context_digest_mismatch():
    context = _owner_context()
    nonce = _nonce(context)
    envelope = build_evidence_envelope(
        nonce=nonce,
        context=context,
        credential_id_hash=hash_value(b"credential"),
        verified_at=NOW,
    )
    tampered = envelope.model_dump()
    tampered["context_digest"] = hash_value("some other context")
    with pytest.raises(ValidationError, match="evidence context digest mismatch"):
        StepUpEvidenceEnvelope.model_validate(tampered)


def test_proof_reference_deployment_must_match_contract():
    with pytest.raises(ValidationError, match="proof deployment identity"):
        StepUpProofReference.model_validate(
            {
                "purpose": "PAUSED_CREATE",
                "proof_id": hash_value("proof"),
                "nonce_hash": hash_value("nonce"),
                "deployment_id": f"tho-search-high-intent-huffman-v1--{'b' * 64}",
                "contract_hash": CONTRACT_HASH,
                "access_evidence_id": hash_value("access"),
                "owner_email_hash": email_hash(OWNER_EMAIL),
                "proof_reference_hash": hash_value("reference"),
            }
        )


class _ProofManager:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def unwrap_challenge_payload(self, reference, *, flow):
        self.calls.append((reference, flow))
        return self.payload


def _valid_proof_payload(challenge=None):
    proof_id = hash_value("proof")
    return {
        "purpose": "PAUSED_CREATE",
        "proof_id": proof_id,
        "nonce_hash": hash_value("nonce"),
        "deployment_id": DEPLOYMENT_ID,
        "contract_hash": CONTRACT_HASH,
        "access_evidence_id": hash_value("access"),
        "owner_email_hash": email_hash(OWNER_EMAIL),
        "challenge": challenge if challenge is not None else proof_id.encode("ascii"),
    }


def test_verify_proof_reference_accepts_valid_and_rejects_bad_inputs():
    manager = _ProofManager(_valid_proof_payload())
    proof = verify_proof_reference(manager, "opaque-reference")
    assert proof is not None
    assert proof.proof_reference_hash == hash_value("opaque-reference")
    assert manager.calls == [("opaque-reference", "google-ads-paused-create-proof-v1")]

    assert verify_proof_reference(_ProofManager(None), "ref") is None
    assert verify_proof_reference(_ProofManager({}), "ref") is None

    mismatched = _valid_proof_payload(challenge=b"not-the-proof-id")
    assert verify_proof_reference(_ProofManager(mismatched), "ref") is None

    invalid = {**_valid_proof_payload(), "proof_id": "not-a-sha256"}
    assert verify_proof_reference(_ProofManager(invalid), "ref") is None

    wrong_deployment = {
        **_valid_proof_payload(),
        "deployment_id": f"tho-search-high-intent-huffman-v1--{'c' * 64}",
    }
    assert verify_proof_reference(_ProofManager(wrong_deployment), "ref") is None


def test_build_evidence_envelope_rejects_nonce_from_another_context():
    context = _owner_context()
    other_context = _owner_context(evidence_digest=hash_value("other evidence"))
    nonce = _nonce(other_context)
    with pytest.raises(ValueError, match="step-up context does not match nonce"):
        build_evidence_envelope(
            nonce=nonce,
            context=context,
            credential_id_hash=hash_value(b"credential"),
            verified_at=NOW,
        )


def test_in_memory_create_nonce_rejects_duplicate():
    store = InMemoryStepUpStore(clock=lambda: NOW)
    nonce = _nonce()
    store.create_nonce(nonce)
    with pytest.raises(ValueError, match="already exists"):
        store.create_nonce(nonce)


def test_nonce_document_consumption_fields_must_be_coupled_and_in_lifetime():
    base = _nonce().model_dump()

    half_consumed = {**base, "consumed_at": NOW, "evidence_id": None}
    with pytest.raises(ValidationError, match="must be set together"):
        StepUpNonceDocument.model_validate(half_consumed)

    half_recorded = {**base, "consumed_at": None, "evidence_id": hash_value("evidence")}
    with pytest.raises(ValidationError, match="must be set together"):
        StepUpNonceDocument.model_validate(half_recorded)

    too_late = {
        **base,
        "consumed_at": NOW + timedelta(seconds=400),
        "evidence_id": hash_value("evidence"),
    }
    with pytest.raises(ValidationError, match="must occur inside its lifetime"):
        StepUpNonceDocument.model_validate(too_late)


# ---------------------------------------------------------------------------
# FirestoreStepUpStore construction and error mapping
# ---------------------------------------------------------------------------


def test_firestore_store_init_validates_timeout_and_injected_pool():
    with pytest.raises(ValueError, match="transaction timeout must be positive"):
        FirestoreStepUpStore(client=_Client(), transaction_timeout_seconds=0)
    pool = ThreadPoolExecutor(max_workers=8)
    try:
        with pytest.raises(ValueError, match="cannot exceed four workers"):
            FirestoreStepUpStore(client=_Client(), transaction_pool=pool)
    finally:
        pool.shutdown(wait=True)


def test_firestore_store_lazily_constructs_client_from_fake_module(monkeypatch):
    fake_client = _Client()
    calls = _install_fake_firestore(monkeypatch, client=fake_client)
    store = FirestoreStepUpStore(project="tho-test-project")
    assert store._client is fake_client
    assert calls == ["tho-test-project"]
    store.close()


def test_firestore_store_close_shuts_down_owned_pool():
    store = FirestoreStepUpStore(client=_Client())
    assert store._owns_transaction_pool is True
    store.close()
    assert store._transaction_pool._shutdown is True

    pool = ThreadPoolExecutor(max_workers=1)
    store = FirestoreStepUpStore(client=_Client(), transaction_pool=pool)
    assert store._owns_transaction_pool is False
    store.close()
    assert pool._shutdown is False
    pool.shutdown(wait=True)


def test_firestore_store_now_raises_store_error_for_naive_clock():
    store = FirestoreStepUpStore(client=_Client(), clock=lambda: datetime(2026, 1, 1))
    with pytest.raises(StepUpStoreError, match="step_up_store_clock_invalid"):
        store._now()
    store.close()


class _TxnClient(_Client):
    def transaction(self, max_attempts=None):
        assert max_attempts == 5
        return "fake-transaction"


def test_firestore_store_executes_transaction_via_real_helper(monkeypatch):
    _install_fake_firestore(monkeypatch)
    store = FirestoreStepUpStore(client=_TxnClient())
    result = store._execute_firestore_transaction(lambda txn: ("ran", txn))
    assert result == ("ran", "fake-transaction")
    store.close()


def test_run_transaction_cancels_future_on_timeout():
    def slow(_operation):
        time.sleep(1.0)

    store = FirestoreStepUpStore(
        client=_Client(),
        transaction_executor=slow,
        transaction_timeout_seconds=0.05,
    )
    try:
        with pytest.raises(StepUpStoreError, match="step_up_store_timeout"):
            store._run_transaction(lambda _txn: None)
    finally:
        store.close()


def test_run_transaction_reraises_store_error_and_wraps_generic():
    def store_error(_operation):
        raise StepUpStoreError("already sanitized")

    store = FirestoreStepUpStore(client=_Client(), transaction_executor=store_error)
    with pytest.raises(StepUpStoreError, match="already sanitized"):
        store._run_transaction(lambda _txn: None)
    store.close()

    def generic_error(_operation):
        raise RuntimeError("grpc down")

    store = FirestoreStepUpStore(client=_Client(), transaction_executor=generic_error)
    with pytest.raises(StepUpStoreError, match="step_up_store_write_failed"):
        store._run_transaction(lambda _txn: None)
    store.close()


def test_reference_rejects_malformed_nonce_hash():
    store = FirestoreStepUpStore(client=_Client())
    with pytest.raises(StepUpStoreError, match="step_up_nonce_invalid"):
        store._reference("not-a-sha256-hash")
    with pytest.raises(StepUpStoreError, match="step_up_nonce_invalid"):
        store._reference(None)
    store.close()


def test_document_raises_for_missing_or_invalid_snapshot():
    with pytest.raises(StepUpStoreError, match="step_up_nonce_not_found"):
        FirestoreStepUpStore._document(_Snapshot(None))
    with pytest.raises(StepUpStoreError, match="step_up_nonce_invalid"):
        FirestoreStepUpStore._document(_Snapshot({"garbage": True}))


def test_create_nonce_wraps_persistence_failure():
    client = _Client()
    store = FirestoreStepUpStore(client=client, transaction_executor=_transaction_executor(client))
    nonce = _nonce()
    store.create_nonce(nonce)
    with pytest.raises(StepUpStoreError, match="step_up_store_write_failed"):
        store.create_nonce(nonce)

    def invalid_reference(_nonce_hash):
        raise StepUpStoreError("step_up_nonce_invalid")

    monkeypatchable = FirestoreStepUpStore(
        client=_Client(), transaction_executor=_transaction_executor(_Client())
    )
    monkeypatchable._reference = invalid_reference
    with pytest.raises(StepUpStoreError, match="step_up_nonce_invalid"):
        monkeypatchable.create_nonce(_nonce())
    store.close()
    monkeypatchable.close()


def test_get_nonce_returns_none_for_missing_document():
    client = _Client()
    store = FirestoreStepUpStore(client=client, transaction_executor=_transaction_executor(client))
    assert store.get_nonce(hash_value("never-issued")) is None
    store.close()


def test_get_nonce_maps_read_failures():
    client = _Client()
    store = FirestoreStepUpStore(client=client, transaction_executor=_transaction_executor(client))
    with pytest.raises(StepUpStoreError, match="step_up_nonce_invalid"):
        store.get_nonce("malformed")
    store.close()

    class _ReadFailReference:
        def get(self, timeout=None):
            raise RuntimeError("rpc unavailable")

    class _ReadFailCollection:
        def document(self, key):
            return _ReadFailReference()

    class _ReadFailClient:
        def collection(self, name):
            return _ReadFailCollection()

    store = FirestoreStepUpStore(
        client=_ReadFailClient(), transaction_executor=_transaction_executor(_Client())
    )
    with pytest.raises(StepUpStoreError, match="step_up_store_read_failed"):
        store.get_nonce(hash_value("missing"))
    store.close()


def _firestore_consume_setup(monkeypatch=None):
    client = _Client()
    credentials = FirestoreCredentialStore(client=client)
    record = CredentialRecord(
        credential_id=b"credential",
        public_key=b"public-key",
        sign_count=0,
        user_id=OWNER_EMAIL,
        created_at=NOW,
    )
    credentials.add(record)
    store = FirestoreStepUpStore(
        client=client,
        transaction_executor=_transaction_executor(client),
        clock=lambda: NOW,
    )
    return client, credentials, record, store


def test_consume_rejects_mismatched_credential_store():
    _client, _credentials, _record, store = _firestore_consume_setup()
    context = _owner_context()
    nonce = _nonce(context)
    envelope = build_evidence_envelope(
        nonce=nonce,
        context=context,
        credential_id_hash=hash_value(b"credential"),
        verified_at=NOW,
    )
    memory_credentials, memory_record = _credential_store()
    with pytest.raises(StepUpStoreError, match="step_up_credential_store_mismatch"):
        store.consume_and_record(
            nonce.nonce_hash,
            expected_context_digest=context_digest(context),
            envelope=envelope,
            access_evidence=_owner_access_evidence(context),
            credential_store=memory_credentials,
            credential_usage=memory_credentials.prepare_usage_cas(
                memory_record, new_sign_count=1
            ),
        )
    store.close()


def test_consume_returns_false_when_nonce_document_is_missing():
    client, credentials, record, store = _firestore_consume_setup()
    context = _owner_context()
    access_evidence = _owner_access_evidence(context)
    client.rows[
        (
            "google_ads_deployments",
            context.deployment_id,
            "access_evidence",
            access_evidence.check_key.value,
        )
    ] = evidence_payload(access_evidence)
    envelope = build_evidence_envelope(
        nonce=_nonce(context),
        context=context,
        credential_id_hash=hash_value(record.credential_id),
        verified_at=NOW,
    )
    assert (
        store.consume_and_record(
            hash_value("never-issued"),
            expected_context_digest=context_digest(context),
            envelope=envelope,
            access_evidence=access_evidence,
            credential_store=credentials,
            credential_usage=credentials.prepare_usage_cas(record, new_sign_count=1),
        )
        is False
    )
    store.close()


def test_consume_returns_false_when_persisted_access_evidence_is_invalid():
    client, credentials, record, store = _firestore_consume_setup()
    context = _owner_context()
    nonce = _nonce(context)
    store.create_nonce(nonce)
    envelope = build_evidence_envelope(
        nonce=nonce,
        context=context,
        credential_id_hash=hash_value(record.credential_id),
        verified_at=NOW,
    )
    # The access-evidence document is intentionally absent: the snapshot does
    # not exist, the empty record fails validation, and the transaction
    # callback bails out without consuming anything.
    assert (
        store.consume_and_record(
            nonce.nonce_hash,
            expected_context_digest=context_digest(context),
            envelope=envelope,
            access_evidence=_owner_access_evidence(context),
            credential_store=credentials,
            credential_usage=credentials.prepare_usage_cas(record, new_sign_count=1),
        )
        is False
    )
    assert client.rows[(store.COLLECTION, nonce.nonce_hash)]["consumed_at"] is None
    store.close()


# ---------------------------------------------------------------------------
# default_step_up_store backend selection
# ---------------------------------------------------------------------------


def test_default_step_up_store_selects_backend_from_environment(monkeypatch):
    monkeypatch.delenv("THO_GOOGLE_ADS_STEP_UP_STORE", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)

    monkeypatch.setenv("THO_GOOGLE_ADS_STEP_UP_STORE", " Memory ")
    default_step_up_store.cache_clear()
    assert isinstance(default_step_up_store(), InMemoryStepUpStore)

    monkeypatch.setenv("K_SERVICE", "tho-api")
    default_step_up_store.cache_clear()
    with pytest.raises(StepUpStoreError, match="persistent step-up store required"):
        default_step_up_store()
    monkeypatch.delenv("K_SERVICE")

    monkeypatch.delenv("THO_GOOGLE_ADS_STEP_UP_STORE")
    default_step_up_store.cache_clear()
    client = _Client()
    store = default_step_up_store(credential_store=FirestoreCredentialStore(client=client))
    assert isinstance(store, FirestoreStepUpStore)
    assert store._client is client
    store.close()

    calls = _install_fake_firestore(monkeypatch, client=_Client())
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "tho-test-project")
    default_step_up_store.cache_clear()
    store = default_step_up_store()
    assert isinstance(store, FirestoreStepUpStore)
    assert calls == ["tho-test-project"]
    store.close()

    _install_fake_firestore(monkeypatch, error=RuntimeError("no application credentials"))
    default_step_up_store.cache_clear()
    with pytest.raises(StepUpStoreError, match="persistent step-up store unavailable"):
        default_step_up_store()


# ---------------------------------------------------------------------------
# Route provider seams and fail-closed error paths
# ---------------------------------------------------------------------------


@pytest.fixture
def step_up_envelope_client(monkeypatch):
    """Same wiring as step_up_client, plus the production error envelope."""
    monkeypatch.setenv("APP_VERSION", "a" * 40)
    monkeypatch.setenv(
        "THO_GOOGLE_ADS_OWNER_EMAILS",
        "aristotlespec@gmail.com,aribspector@gmail.com",
    )
    manager = SessionManager(secret_key="test-c8c-step-up-secret")
    credential_store = InMemoryCredentialStore(
        [
            CredentialRecord(
                credential_id=b"owner-credential",
                public_key=b"owner-public-key",
                sign_count=0,
                user_id=OWNER_EMAIL,
            ),
            CredentialRecord(
                credential_id=b"staff-credential",
                public_key=b"staff-public-key",
                sign_count=0,
                user_id=STAFF_EMAIL,
            ),
        ]
    )
    evidence_store = InMemoryStepUpStore(clock=lambda: NOW)
    access_ledger = _AccessEvidenceLedger()
    monkeypatch.setattr(routes, "_utc_now", lambda: NOW)
    app = FastAPI()

    @app.exception_handler(HTTPException)
    async def _envelope(_request, exc):
        return JSONResponse(
            {
                "success": False,
                "status_code": exc.status_code,
                "message": exc.detail,
            },
            status_code=exc.status_code,
        )

    app.dependency_overrides[routes.get_session_manager] = lambda: manager
    app.dependency_overrides[routes.get_credential_store] = lambda: credential_store
    app.dependency_overrides[routes.get_step_up_store] = lambda: evidence_store
    app.dependency_overrides[routes.get_access_evidence_ledger] = lambda: access_ledger
    app.include_router(routes.router)
    client = TestClient(app, base_url="https://tho.sapphirealpha.xyz")
    return client, manager, credential_store, evidence_store, access_ledger


def _post_complete(client, credential_id):
    return client.post(
        "/api/admin/passkey/google-ads-step-up/complete",
        headers={"X-CSRF-Token": CSRF},
        json={"context": _request_context(), "credential": {"id": credential_id, "response": {}}},
    )


def _patch_verified(monkeypatch, credential_id=b"owner-credential"):
    monkeypatch.setattr(routes.passkey_routes, "parse_authentication_credential_json", lambda raw: raw)
    monkeypatch.setattr(
        routes.passkey_routes,
        "verify_authentication_response",
        lambda **kwargs: types.SimpleNamespace(
            credential_id=credential_id, new_sign_count=1
        ),
    )


def test_provider_seams_delegate_to_passkey_routes(monkeypatch):
    assert routes._utc_now().tzinfo is not None

    session_sentinel = object()
    monkeypatch.setattr(
        routes.passkey_routes, "get_session_manager", lambda: session_sentinel
    )
    assert routes.get_session_manager() is session_sentinel

    credential_sentinel = object()
    monkeypatch.setattr(
        routes.passkey_routes, "get_credential_store", lambda: credential_sentinel
    )
    assert routes.get_credential_store() is credential_sentinel

    ledger_sentinel = object()
    monkeypatch.setattr(routes, "get_authority_ledger", lambda: ledger_sentinel)
    assert routes.get_access_evidence_ledger() is ledger_sentinel


def test_get_step_up_store_caches_and_maps_store_errors(monkeypatch):
    sentinel = object()
    calls = []

    def fake_default(*, credential_store=None):
        calls.append(credential_store)
        return sentinel

    monkeypatch.setattr(routes, "_step_up_store", None)
    monkeypatch.setattr(routes, "default_step_up_store", fake_default)
    assert routes.get_step_up_store(None) is sentinel
    assert routes.get_step_up_store(None) is sentinel
    assert calls == [None]

    def failing_default(*, credential_store=None):
        raise StepUpStoreError("firestore down")

    monkeypatch.setattr(routes, "_step_up_store", None)
    monkeypatch.setattr(routes, "default_step_up_store", failing_default)
    with pytest.raises(HTTPException) as exc:
        routes.get_step_up_store()
    assert exc.value.status_code == 503
    assert exc.value.detail == "Owner step-up evidence store unavailable."


def test_configured_owner_emails_unavailable_is_a_503(monkeypatch):
    monkeypatch.setenv("THO_GOOGLE_ADS_OWNER_EMAILS", "owner@example.com")
    monkeypatch.setenv("THO_PASSKEY_OWNER_EMAILS", "owner@example.com")
    assert routes._configured_owner_emails() == {"owner@example.com"}
    monkeypatch.delenv("THO_GOOGLE_ADS_OWNER_EMAILS")
    monkeypatch.delenv("THO_PASSKEY_OWNER_EMAILS", raising=False)
    with pytest.raises(HTTPException) as exc:
        routes._configured_owner_emails()
    assert exc.value.status_code == 503
    assert exc.value.detail == "Owner step-up is not configured."


class _ExplodingStore:
    def __init__(self, *, error):
        self.error = error

    def list_for_user(self, _user_id):
        raise self.error


def test_owner_credentials_fail_closed(monkeypatch):
    with pytest.raises(HTTPException) as exc:
        routes._owner_credentials(_ExplodingStore(error=RuntimeError("rpc down")), OWNER_EMAIL)
    assert exc.value.status_code == 503
    assert exc.value.detail == "Owner passkey store unavailable."

    with pytest.raises(HTTPException) as exc:
        routes._owner_credentials(InMemoryCredentialStore(), OWNER_EMAIL)
    assert exc.value.status_code == 409
    assert exc.value.detail == "Register an owner passkey before step-up."


def test_begin_and_complete_fail_closed_when_webauthn_unavailable(
    step_up_envelope_client, monkeypatch
):
    client, manager, _credentials, _evidence_store, _access = step_up_envelope_client
    monkeypatch.setattr(routes.passkey_routes, "_HAS_WEBAUTHN", False)
    _authenticate(client, manager)

    begun = _begin(client)
    assert begun.status_code == 503
    assert begun.json() == {
        "success": False,
        "status_code": 503,
        "message": "WebAuthn is unavailable.",
    }

    completed = _post_complete(client, _b64_id(b"owner-credential"))
    assert completed.status_code == 503
    assert completed.json()["message"] == "WebAuthn is unavailable."


def test_begin_maps_nonce_persistence_failure_to_503(
    step_up_envelope_client, monkeypatch
):
    client, manager, _credentials, evidence_store, _access = step_up_envelope_client
    _authenticate(client, manager)

    def failing_create(_nonce):
        raise RuntimeError("firestore unavailable")

    monkeypatch.setattr(evidence_store, "create_nonce", failing_create)
    response = _begin(client)
    assert response.status_code == 503
    assert response.json() == {
        "success": False,
        "status_code": 503,
        "message": "Owner step-up evidence store unavailable.",
    }
    assert evidence_store._nonces == {}


def test_complete_rejects_challenge_context_digest_mismatch(
    step_up_envelope_client, monkeypatch
):
    client, manager, _credentials, _evidence_store, _access = step_up_envelope_client
    _authenticate(client, manager)
    assert _begin(client).status_code == 200

    def wrong_payload(_reference, *, flow):
        return {
            "challenge": "any-challenge",
            "context_digest": hash_value("stale context"),
            "owner_email_hash": email_hash(OWNER_EMAIL),
        }

    monkeypatch.setattr(manager, "unwrap_challenge_payload", wrong_payload)
    response = _post_complete(client, _b64_id(b"owner-credential"))
    assert response.status_code == 409
    assert response.json() == {
        "success": False,
        "status_code": 409,
        "message": "Owner step-up context changed. Refresh and retry.",
    }


def test_complete_maps_credential_read_failure_to_503(
    step_up_envelope_client, monkeypatch
):
    client, manager, credential_store, _evidence_store, _access = step_up_envelope_client
    _authenticate(client, manager)
    assert _begin(client).status_code == 200

    def failing_get(_credential_id):
        raise RuntimeError("rpc down")

    monkeypatch.setattr(credential_store, "get", failing_get)
    response = _post_complete(client, _b64_id(b"owner-credential"))
    assert response.status_code == 503
    assert response.json() == {
        "success": False,
        "status_code": 503,
        "message": "Owner passkey store unavailable.",
    }


def test_complete_rejects_passkey_registered_to_another_user(step_up_envelope_client):
    client, manager, _credentials, _evidence_store, _access = step_up_envelope_client
    _authenticate(client, manager)
    assert _begin(client).status_code == 200

    response = _post_complete(client, _b64_id(b"staff-credential"))
    assert response.status_code == 403
    assert response.json() == {
        "success": False,
        "status_code": 403,
        "message": "This passkey is not registered to the current owner.",
    }


def test_complete_rejects_verified_credential_identity_mismatch(
    step_up_envelope_client, monkeypatch
):
    client, manager, _credentials, _evidence_store, _access = step_up_envelope_client
    _authenticate(client, manager)
    assert _begin(client).status_code == 200
    _patch_verified(monkeypatch, credential_id=b"someone-elses-credential")

    response = _post_complete(client, _b64_id(b"owner-credential"))
    assert response.status_code == 400
    assert response.json() == {
        "success": False,
        "status_code": 400,
        "message": "Owner passkey user verification failed.",
    }


def test_complete_maps_nonce_read_failure_to_503(step_up_envelope_client, monkeypatch):
    client, manager, _credentials, evidence_store, _access = step_up_envelope_client
    _authenticate(client, manager)
    assert _begin(client).status_code == 200
    _patch_verified(monkeypatch)

    def failing_get(_nonce_hash):
        raise RuntimeError("rpc down")

    monkeypatch.setattr(evidence_store, "get_nonce", failing_get)
    response = _post_complete(client, _b64_id(b"owner-credential"))
    assert response.status_code == 503
    assert response.json() == {
        "success": False,
        "status_code": 503,
        "message": "Owner step-up evidence store unavailable.",
    }


def test_complete_rejects_challenge_without_stored_nonce(step_up_envelope_client, monkeypatch):
    client, manager, _credentials, evidence_store, _access = step_up_envelope_client
    _authenticate(client, manager)
    assert _begin(client).status_code == 200
    _patch_verified(monkeypatch)

    evidence_store._nonces.clear()
    response = _post_complete(client, _b64_id(b"owner-credential"))
    assert response.status_code == 409
    assert response.json() == {
        "success": False,
        "status_code": 409,
        "message": "Owner step-up challenge expired or was already used.",
    }


def test_complete_maps_changed_sign_counter_to_409(step_up_envelope_client, monkeypatch):
    client, manager, credential_store, _evidence_store, _access = step_up_envelope_client
    _authenticate(client, manager)
    assert _begin(client).status_code == 200
    _patch_verified(monkeypatch)

    def stale_counter(_record, *, new_sign_count):
        raise ValueError("sign counter regressed")

    monkeypatch.setattr(credential_store, "prepare_usage_cas", stale_counter)
    response = _post_complete(client, _b64_id(b"owner-credential"))
    assert response.status_code == 409
    assert response.json() == {
        "success": False,
        "status_code": 409,
        "message": "Owner passkey counter changed. Refresh and retry.",
    }


def test_complete_maps_consume_failure_to_503(step_up_envelope_client, monkeypatch):
    client, manager, _credentials, evidence_store, _access = step_up_envelope_client
    _authenticate(client, manager)
    assert _begin(client).status_code == 200
    _patch_verified(monkeypatch)

    def failing_consume(*args, **kwargs):
        raise RuntimeError("transaction failed")

    monkeypatch.setattr(evidence_store, "consume_and_record", failing_consume)
    response = _post_complete(client, _b64_id(b"owner-credential"))
    assert response.status_code == 503
    assert response.json() == {
        "success": False,
        "status_code": 503,
        "message": "Owner step-up evidence store unavailable.",
    }
