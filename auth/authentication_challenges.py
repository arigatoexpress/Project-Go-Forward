"""Single-use WebAuthn authentication (login) challenges.

A login challenge is valid for five minutes and for one verification attempt.
``consume`` deletes it on that attempt, whether the assertion later succeeds
or fails. An expired record is deleted and rejected, including when the
caller presents it after the deadline.

Opening the staff sign-in page mints a challenge even when the visitor later
uses email or leaves. Each ``issue`` also deletes already-expired challenges
so those abandoned rows do not accumulate until ``/login/complete``. The
Firestore sweep is a bounded batch: one range read and one commit, started
after the new challenge is stored. Sign-in does not wait for that sweep.
If the sweep fails, the new challenge stays recorded.

Cloud Run stores the outstanding id in Firestore so every instance sees the
same burn. Local development and tests use a process-local ledger. The raw
challenge is never stored; the id is its SHA-256 hex digest.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from threading import Lock, Thread
from typing import Protocol

from google.cloud.firestore_v1.base_query import FieldFilter
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

    Expired rows are removed after a new challenge is stored, at most
    ``EXPIRED_PURGE_LIMIT`` per sweep, oldest first. The range is a single
    field (``expires_at``), so it uses Firestore's automatic index. The sweep
    runs after the write and off the ``issue`` caller, so a slow or failed
    cleanup cannot delay or reject sign-in.
    """

    COLLECTION = "tho_admin_passkey_auth_challenges"
    # One indexed range read plus one batch commit. Large enough to drain
    # abandoned conditional-UI challenges, small enough that a sweep does
    # not scan the collection. This work is not on the login/begin path.
    EXPIRED_PURGE_LIMIT = 20
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
        self._sweep_guard = Lock()
        self._sweep_thread: Thread | None = None
        # Ids issued since the current sweep started. The sweep skips them,
        # including a brand-new row whose deadline is already past.
        self._sweep_protected: set[str] = set()

    def issue(self, challenge_id: str, *, expires_at: float) -> None:
        record = _validated(challenge_id, expires_at)
        self._protect_from_sweep(record.challenge_id)
        try:
            self._collection.document(record.challenge_id).set(
                record.model_dump(),
                timeout=FIRESTORE_RPC_TIMEOUT,
            )
        except Exception as exc:
            self._unprotect_from_sweep(record.challenge_id)
            raise AuthenticationChallengeStoreUnavailable(
                "authentication challenge could not be recorded"
            ) from exc
        # The row is durable before cleanup starts. Sign-in does not wait.
        self._sweep_expired_after_write(time.time(), exclude_id=record.challenge_id)

    def _protect_from_sweep(self, challenge_id: str) -> None:
        with self._sweep_guard:
            self._sweep_protected.add(challenge_id)

    def _unprotect_from_sweep(self, challenge_id: str) -> None:
        with self._sweep_guard:
            self._sweep_protected.discard(challenge_id)

    def _sweep_excludes(self, doc_id: str, exclude_id: str) -> bool:
        if doc_id == exclude_id:
            return True
        with self._sweep_guard:
            return doc_id in self._sweep_protected

    def _sweep_expired_after_write(self, now: float, *, exclude_id: str) -> None:
        """Start a bounded cleanup and return without waiting for it.

        One sweep runs at a time. Ids protected above stay out of that sweep,
        so a challenge recorded while cleanup is already in flight is not
        deleted by it.
        """

        def run() -> None:
            try:
                self._delete_expired(now, exclude_id=exclude_id)
            finally:
                with self._sweep_guard:
                    self._sweep_thread = None
                    self._sweep_protected.clear()

        with self._sweep_guard:
            active = self._sweep_thread
            if active is not None and active.is_alive():
                return
            worker = Thread(
                target=run,
                name="passkey-auth-challenge-sweep",
                daemon=True,
            )
            self._sweep_thread = worker
            worker.start()

    def _delete_expired(self, now: float, *, exclude_id: str) -> None:
        """Best-effort bounded delete. Never turns a sweep fault into a lockout."""
        try:
            self._delete_expired_bounded(now, exclude_id=exclude_id)
        except Exception as exc:
            log.warning("expired authentication challenge cleanup skipped: %s", exc)

    def _delete_expired_bounded(self, now: float, *, exclude_id: str) -> None:
        query = (
            self._collection.where(filter=FieldFilter("expires_at", "<", now))
            .order_by("expires_at")
            .limit(self.EXPIRED_PURGE_LIMIT)
        )
        doomed: list[str] = []
        for snapshot in query.stream(timeout=FIRESTORE_RPC_TIMEOUT):
            doc_id = getattr(snapshot, "id", None)
            if (
                not isinstance(doc_id, str)
                or not doc_id
                or self._sweep_excludes(doc_id, exclude_id)
            ):
                continue
            data = snapshot.to_dict() or {}
            expires_at = data.get("expires_at")
            # ``consume`` treats ``now <= expires_at`` as still valid. Only
            # strictly earlier numeric deadlines are abandoned.
            if isinstance(expires_at, bool) or not isinstance(expires_at, int | float):
                continue
            if expires_at >= now:
                continue
            doomed.append(doc_id)
            if len(doomed) >= self.EXPIRED_PURGE_LIMIT:
                break
        if not doomed:
            return
        batch = self._client.batch()
        for doc_id in doomed:
            batch.delete(self._collection.document(doc_id))
        batch.commit(timeout=FIRESTORE_RPC_TIMEOUT)

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
