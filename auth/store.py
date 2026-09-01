"""Credential storage backends for WebAuthn admin passkeys.

Ported from Sapphire analytics_dashboard auth scaffold.
"""

from __future__ import annotations

import logging
import os
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from threading import Lock
from typing import Any, Protocol

from google.api_core.exceptions import Conflict

from database.rpc_timeout import FIRESTORE_RPC_TIMEOUT

log = logging.getLogger(__name__)


def _b64url_encode(data: bytes) -> str:
    return urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    padding = "=" * (-len(s) % 4)
    return urlsafe_b64decode(s + padding)


@dataclass
class CredentialRecord:
    """One registered passkey."""

    credential_id: bytes
    public_key: bytes
    sign_count: int
    user_id: str
    aaguid: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_used_at: datetime | None = None

    @property
    def credential_id_b64(self) -> str:
        return _b64url_encode(self.credential_id)


@dataclass(frozen=True)
class CredentialUsageCAS:
    """Transient exact credential state bound to one verified assertion.

    Positive authenticator counters must strictly increase. Counterless
    authenticators may report 0 repeatedly; their exact last-used timestamp is
    the CAS fence so concurrent replays still have at most one winner.
    """

    credential_id: bytes
    credential_id_b64: str
    expected_user_id: str
    expected_sign_count: int
    expected_last_used_at: datetime | None
    new_sign_count: int

    def __post_init__(self) -> None:
        if not self.credential_id or self.credential_id_b64 != _b64url_encode(self.credential_id):
            raise ValueError("credential identity mismatch")
        if not self.expected_user_id or self.expected_user_id != self.expected_user_id.lower():
            raise ValueError("credential owner must be normalized")
        if (
            self.expected_sign_count < 0
            or self.new_sign_count < self.expected_sign_count
            or (self.expected_sign_count > 0 and self.new_sign_count == self.expected_sign_count)
        ):
            raise ValueError("credential sign count must advance monotonically")


class MinimumCredentialError(RuntimeError):
    """Raised when deletion would remove an owner's final recovery credential."""


class CredentialAlreadyExists(RuntimeError):
    """Raised when registration would replace an existing credential id."""


class CredentialStore(Protocol):
    """Pluggable persistence for registered passkeys."""

    def add(self, record: CredentialRecord) -> None: ...
    def get(self, credential_id: bytes) -> CredentialRecord | None: ...
    def list_all(self) -> list[CredentialRecord]: ...
    def list_for_user(self, user_id: str) -> list[CredentialRecord]: ...
    def prepare_usage_cas(
        self, record: CredentialRecord, *, new_sign_count: int
    ) -> CredentialUsageCAS: ...
    def compare_and_set_usage(self, usage: CredentialUsageCAS, *, used_at: datetime) -> bool: ...
    def usage_reference(self, usage: CredentialUsageCAS) -> Any: ...
    def usage_matches_snapshot(self, usage: CredentialUsageCAS, snapshot: Any) -> bool: ...
    def usage_update(self, usage: CredentialUsageCAS, *, used_at: datetime) -> dict: ...
    def delete(self, credential_id: bytes) -> bool: ...
    def delete_preserving_user_minimum(
        self, credential_id: bytes, *, user_id: str, minimum_remaining: int = 1
    ) -> bool: ...
    def count(self) -> int: ...


class InMemoryCredentialStore:
    """Process-local credential store. Lost on restart — tests only."""

    backend_name = "memory"
    persistent = False

    def __init__(self, seed: Iterable[CredentialRecord] = ()) -> None:
        self._rows: dict[bytes, CredentialRecord] = {}
        self._lock = Lock()
        for r in seed:
            self.add(r)

    def add(self, record: CredentialRecord) -> None:
        with self._lock:
            if record.credential_id in self._rows:
                raise CredentialAlreadyExists("credential_id_already_exists")
            self._rows[record.credential_id] = record

    def get(self, credential_id: bytes) -> CredentialRecord | None:
        with self._lock:
            return self._rows.get(credential_id)

    def list_all(self) -> list[CredentialRecord]:
        with self._lock:
            return list(self._rows.values())

    def list_for_user(self, user_id: str) -> list[CredentialRecord]:
        with self._lock:
            return [r for r in self._rows.values() if r.user_id == user_id]

    @staticmethod
    def prepare_usage_cas(record: CredentialRecord, *, new_sign_count: int) -> CredentialUsageCAS:
        return CredentialUsageCAS(
            credential_id=record.credential_id,
            credential_id_b64=record.credential_id_b64,
            expected_user_id=record.user_id.strip().lower(),
            expected_sign_count=record.sign_count,
            expected_last_used_at=record.last_used_at,
            new_sign_count=new_sign_count,
        )

    def compare_and_set_usage(self, usage: CredentialUsageCAS, *, used_at: datetime) -> bool:
        with self._lock:
            current = self._rows.get(usage.credential_id)
            if not current or not self._usage_matches_record(usage, current):
                return False
            current.sign_count = usage.new_sign_count
            current.last_used_at = used_at
            return True

    @staticmethod
    def _usage_matches_record(usage: CredentialUsageCAS, record: CredentialRecord) -> bool:
        return (
            record.credential_id == usage.credential_id
            and record.user_id.strip().lower() == usage.expected_user_id
            and record.sign_count == usage.expected_sign_count
            and record.last_used_at == usage.expected_last_used_at
        )

    def usage_reference(self, usage: CredentialUsageCAS) -> Any:
        del usage
        raise TypeError("in-memory credentials have no Firestore reference")

    def usage_matches_snapshot(self, usage: CredentialUsageCAS, snapshot: Any) -> bool:
        del usage, snapshot
        return False

    @staticmethod
    def usage_update(usage: CredentialUsageCAS, *, used_at: datetime) -> dict:
        return {"sign_count": usage.new_sign_count, "last_used_at": used_at}

    def delete(self, credential_id: bytes) -> bool:
        with self._lock:
            return self._rows.pop(credential_id, None) is not None

    def delete_preserving_user_minimum(
        self, credential_id: bytes, *, user_id: str, minimum_remaining: int = 1
    ) -> bool:
        with self._lock:
            record = self._rows.get(credential_id)
            if record is None:
                return False
            normalized = user_id.strip().lower()
            if record.user_id.strip().lower() != normalized:
                return False
            owned = [
                row for row in self._rows.values() if row.user_id.strip().lower() == normalized
            ]
            if len(owned) <= minimum_remaining:
                raise MinimumCredentialError("owner_recovery_credential_required")
            del self._rows[credential_id]
            return True

    def count(self) -> int:
        with self._lock:
            return len(self._rows)


class FirestoreCredentialStore:
    """Persists credentials to Firestore in the THO project.

    Collection: ``tho_admin_credentials``
    Document id: ``urlsafe-base64(credential_id)`` (no padding)
    """

    COLLECTION = "tho_admin_credentials"
    backend_name = "firestore"
    persistent = True

    def __init__(self, project: str | None = None, *, client=None) -> None:
        if client is None:
            from google.cloud import firestore

            client = firestore.Client(project=project) if project else firestore.Client()
        self._client = client
        self._collection = client.collection(self.COLLECTION)

    @property
    def client(self):
        return self._client

    @staticmethod
    def _doc_id(credential_id: bytes) -> str:
        return _b64url_encode(credential_id)

    @classmethod
    def _to_record(cls, doc_id: str, data: dict) -> CredentialRecord:
        cid = data.get("credential_id")
        if isinstance(cid, str):
            cid_bytes = _b64url_decode(cid)
        elif cid is None:
            cid_bytes = _b64url_decode(doc_id)
        else:
            cid_bytes = bytes(cid)
        pk = data.get("public_key", b"")
        pk_bytes = _b64url_decode(pk) if isinstance(pk, str) else bytes(pk)
        return CredentialRecord(
            credential_id=cid_bytes,
            public_key=pk_bytes,
            sign_count=int(data.get("sign_count", 0)),
            user_id=str(data.get("user_id", "admin")),
            aaguid=str(data.get("aaguid", "")),
            created_at=data.get("created_at") or datetime.now(UTC),
            last_used_at=data.get("last_used_at"),
        )

    def add(self, record: CredentialRecord) -> None:
        try:
            self._collection.document(self._doc_id(record.credential_id)).create(
                {
                    "credential_id": record.credential_id_b64,
                    "public_key": _b64url_encode(record.public_key),
                    "sign_count": record.sign_count,
                    "user_id": record.user_id,
                    "aaguid": record.aaguid,
                    "created_at": record.created_at,
                    "last_used_at": record.last_used_at,
                },
                timeout=FIRESTORE_RPC_TIMEOUT,
            )
        except Conflict:
            raise CredentialAlreadyExists("credential_id_already_exists") from None

    def get(self, credential_id: bytes) -> CredentialRecord | None:
        snap = self._collection.document(self._doc_id(credential_id)).get(
            timeout=FIRESTORE_RPC_TIMEOUT
        )
        if not snap.exists:
            return None
        return self._to_record(snap.id, snap.to_dict() or {})

    def list_all(self) -> list[CredentialRecord]:
        return [
            self._to_record(s.id, s.to_dict() or {})
            for s in self._collection.stream(timeout=FIRESTORE_RPC_TIMEOUT)
        ]

    def list_for_user(self, user_id: str) -> list[CredentialRecord]:
        query = self._collection.where("user_id", "==", user_id)
        return [
            self._to_record(s.id, s.to_dict() or {})
            for s in query.stream(timeout=FIRESTORE_RPC_TIMEOUT)
        ]

    prepare_usage_cas = staticmethod(InMemoryCredentialStore.prepare_usage_cas)

    def compare_and_set_usage(self, usage: CredentialUsageCAS, *, used_at: datetime) -> bool:
        from google.cloud import firestore

        reference = self.usage_reference(usage)
        transaction = self._client.transaction(max_attempts=5)

        @firestore.transactional
        def operation(txn):
            snapshot = reference.get(transaction=txn, timeout=FIRESTORE_RPC_TIMEOUT)
            if not self.usage_matches_snapshot(usage, snapshot):
                return False
            txn.update(reference, self.usage_update(usage, used_at=used_at))
            return True

        return operation(transaction)

    def usage_reference(self, usage: CredentialUsageCAS):
        return self._collection.document(usage.credential_id_b64)

    def usage_matches_snapshot(self, usage: CredentialUsageCAS, snapshot: Any) -> bool:
        if not getattr(snapshot, "exists", False):
            return False
        try:
            record = self._to_record(snapshot.id, snapshot.to_dict() or {})
        except (AttributeError, TypeError, ValueError):
            return False
        return InMemoryCredentialStore._usage_matches_record(usage, record)

    @staticmethod
    def usage_update(usage: CredentialUsageCAS, *, used_at: datetime) -> dict:
        return {"sign_count": usage.new_sign_count, "last_used_at": used_at}

    def delete(self, credential_id: bytes) -> bool:
        doc_ref = self._collection.document(self._doc_id(credential_id))
        snap = doc_ref.get(timeout=FIRESTORE_RPC_TIMEOUT)
        if not snap.exists:
            return False
        doc_ref.delete(timeout=FIRESTORE_RPC_TIMEOUT)
        return True

    def delete_preserving_user_minimum(
        self, credential_id: bytes, *, user_id: str, minimum_remaining: int = 1
    ) -> bool:
        from google.cloud import firestore
        from google.cloud.firestore_v1.base_query import FieldFilter

        normalized = user_id.strip().lower()
        reference = self._collection.document(self._doc_id(credential_id))
        query = self._collection.where(filter=FieldFilter("user_id", "==", normalized))
        transaction = self._client.transaction(max_attempts=5)

        @firestore.transactional
        def operation(txn):
            snapshot = reference.get(transaction=txn, timeout=FIRESTORE_RPC_TIMEOUT)
            if not snapshot.exists:
                return False
            record = self._to_record(snapshot.id, snapshot.to_dict() or {})
            if record.user_id.strip().lower() != normalized:
                return False
            owned = list(txn.get(query, timeout=FIRESTORE_RPC_TIMEOUT))
            if len(owned) <= minimum_remaining:
                raise MinimumCredentialError("owner_recovery_credential_required")
            txn.delete(reference)
            return True

        return operation(transaction)

    def count(self) -> int:
        return sum(1 for _ in self._collection.stream(timeout=FIRESTORE_RPC_TIMEOUT))


class CredentialStoreUnavailable(RuntimeError):
    """Raised when production cannot reach a persistent passkey store."""


def _memory_fallback_allowed() -> bool:
    value = os.environ.get("THO_PASSKEY_ALLOW_MEMORY_STORE", "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    return not os.environ.get("K_SERVICE")


@lru_cache(maxsize=4)
def default_store(project: str | None = None) -> CredentialStore:
    """Return the configured passkey store.

    Cloud Run must use Firestore so passkeys survive restarts and multiple
    instances. Local development may fall back to memory for lightweight tests.
    """
    backend = os.environ.get("THO_PASSKEY_STORE", "firestore").strip().lower()
    if backend == "memory":
        if not _memory_fallback_allowed():
            raise CredentialStoreUnavailable("memory passkey store is disabled in production")
        return InMemoryCredentialStore()

    try:
        return FirestoreCredentialStore(project=project or os.environ.get("GOOGLE_CLOUD_PROJECT"))
    except Exception as exc:
        if _memory_fallback_allowed():
            log.warning("falling back to in-memory credential store (%s)", exc)
            return InMemoryCredentialStore()
        raise CredentialStoreUnavailable("persistent passkey credential store unavailable") from exc
