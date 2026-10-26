"""Single-use WebAuthn authentication (login) challenges.

A login challenge is valid for five minutes and for one verification attempt.
``consume`` deletes it on that attempt, whether the assertion later succeeds
or fails. An expired record is deleted and rejected, including when the
caller presents it after the deadline.

Cloud Run stores the outstanding id in Firestore so every instance sees the
same burn. Local development and tests use a process-local ledger. The raw
challenge is never stored; the id is its SHA-256 hex digest.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from threading import Lock
from typing import Protocol

from pydantic import ValidationError

from database.models import AdminPasskeyAuthChallenge
from database.rpc_timeout import FIRESTORE_RPC_TIMEOUT

log = logging.getLogger(__name__)

_CHALLENGE_ID_LENGTH = 64


class AuthenticationChallengeStoreUnavailable(RuntimeError):
    """Raised when a login challenge cannot be recorded or consumed."""


class AuthenticationChallengeLedger(Protocol):
    """Outstanding authentication challenges, keyed by sha256(challenge)."""

    def issue(self, challenge_id: str, *, expires_at: float) -> None: ...

    def consume(self, challenge_id: str, *, now: float) -> bool: ...


def authentication_challenge_id(challenge: bytes) -> str:
    """Return the stored id for a raw WebAuthn challenge."""
    return hashlib.sha256(challenge).hexdigest()


def _validated(challenge_id: str, expires_at: float) -> AdminPasskeyAuthChallenge:
    return AdminPasskeyAuthChallenge(challenge_id=challenge_id, expires_at=expires_at)


class InMemoryAuthenticationChallengeLedger:
    """Process-local ledger. Lost on restart — dev and tests only."""

    backend_name = "memory"
    persistent = False

    def __init__(self) -> None:
        self._rows: dict[str, float] = {}
        self._lock = Lock()

    def issue(self, challenge_id: str, *, expires_at: float) -> None:
        record = _validated(challenge_id, expires_at)
        with self._lock:
            self._purge_expired(time.time())
            self._rows[record.challenge_id] = record.expires_at

    def consume(self, challenge_id: str, *, now: float) -> bool:
        """Delete the record and report whether this caller won the single use."""
        with self._lock:
            self._purge_expired(now)
            expires_at = self._rows.pop(challenge_id, None)
            if expires_at is None:
                return False
            return now <= expires_at

    def _purge_expired(self, now: float) -> None:
        expired = [key for key, expires_at in self._rows.items() if now > expires_at]
        for key in expired:
            self._rows.pop(key, None)


class FirestoreAuthenticationChallengeLedger:
    """Persists outstanding login challenges in the THO project.

    Collection: ``tho_admin_passkey_auth_challenges``
    Document id: ``sha256(challenge).hexdigest()``
    Fields: ``challenge_id``, ``expires_at`` (epoch seconds).
    """

    COLLECTION = "tho_admin_passkey_auth_challenges"
    backend_name = "firestore"
    persistent = True

    def __init__(self, project: str | None = None, *, client=None) -> None:
        if client is None:
            # Same lazy client construction as auth/store.py: the gRPC client
            # is built only when Cloud Run actually opens the ledger.
            from google.cloud import firestore

            client = firestore.Client(project=project) if project else firestore.Client()
        self._client = client
        self._collection = client.collection(self.COLLECTION)

    def issue(self, challenge_id: str, *, expires_at: float) -> None:
        record = _validated(challenge_id, expires_at)
        try:
            self._collection.document(record.challenge_id).set(
                record.model_dump(),
                timeout=FIRESTORE_RPC_TIMEOUT,
            )
        except Exception as exc:
            raise AuthenticationChallengeStoreUnavailable(
                "authentication challenge could not be recorded"
            ) from exc

    def consume(self, challenge_id: str, *, now: float) -> bool:
        """Atomically delete the challenge. True only for the first in-window use."""
        if len(challenge_id) != _CHALLENGE_ID_LENGTH:
            return False
        try:
            from google.cloud import firestore
        except Exception as exc:
            raise AuthenticationChallengeStoreUnavailable(
                "authentication challenge store unavailable"
            ) from exc

        reference = self._collection.document(challenge_id)
        transaction = self._client.transaction(max_attempts=5)

        @firestore.transactional
        def operation(txn) -> bool:
            snapshot = reference.get(transaction=txn, timeout=FIRESTORE_RPC_TIMEOUT)
            if not getattr(snapshot, "exists", False):
                return False
            txn.delete(reference)
            data = snapshot.to_dict() or {}
            payload = dict(data)
            payload["challenge_id"] = challenge_id
            try:
                record = AdminPasskeyAuthChallenge.model_validate(payload)
            except ValidationError:
                return False
            return now <= record.expires_at

        try:
            return bool(operation(transaction))
        except AuthenticationChallengeStoreUnavailable:
            raise
        except Exception as exc:
            raise AuthenticationChallengeStoreUnavailable(
                "authentication challenge could not be consumed"
            ) from exc


_memory_ledger = InMemoryAuthenticationChallengeLedger()
_firestore_ledger: FirestoreAuthenticationChallengeLedger | None = None


def default_authentication_challenge_ledger() -> AuthenticationChallengeLedger:
    """Return the ledger for this process.

    Cloud Run must share one Firestore ledger. Anywhere else, including tests,
    uses the process-local ledger so a missing GCP client cannot turn login
    challenges back into replayable cookies.
    """
    if not os.environ.get("K_SERVICE"):
        return _memory_ledger
    global _firestore_ledger
    if _firestore_ledger is None:
        try:
            _firestore_ledger = FirestoreAuthenticationChallengeLedger(
                project=os.environ.get("GOOGLE_CLOUD_PROJECT")
            )
        except AuthenticationChallengeStoreUnavailable:
            raise
        except Exception as exc:
            log.warning("passkey authentication challenge store unavailable: %s", exc)
            raise AuthenticationChallengeStoreUnavailable(
                "persistent authentication challenge store unavailable"
            ) from exc
    return _firestore_ledger
