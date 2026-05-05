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
from typing import Protocol

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


class CredentialStore(Protocol):
    """Pluggable persistence for registered passkeys."""

    def add(self, record: CredentialRecord) -> None: ...
    def get(self, credential_id: bytes) -> CredentialRecord | None: ...
    def list_all(self) -> list[CredentialRecord]: ...
    def list_for_user(self, user_id: str) -> list[CredentialRecord]: ...
    def update_usage(self, credential_id: bytes, *, sign_count: int) -> None: ...
    def count(self) -> int: ...


class InMemoryCredentialStore:
    """Process-local credential store. Lost on restart — tests only."""

    backend_name = "memory"
    persistent = False

    def __init__(self, seed: Iterable[CredentialRecord] = ()) -> None:
        self._rows: dict[bytes, CredentialRecord] = {}
        for r in seed:
            self.add(r)

    def add(self, record: CredentialRecord) -> None:
        self._rows[record.credential_id] = record

    def get(self, credential_id: bytes) -> CredentialRecord | None:
        return self._rows.get(credential_id)

    def list_all(self) -> list[CredentialRecord]:
        return list(self._rows.values())

    def list_for_user(self, user_id: str) -> list[CredentialRecord]:
        return [r for r in self._rows.values() if r.user_id == user_id]

    def update_usage(self, credential_id: bytes, *, sign_count: int) -> None:
        rec = self._rows.get(credential_id)
        if rec is None:
            return
        rec.sign_count = sign_count
        rec.last_used_at = datetime.now(UTC)

    def count(self) -> int:
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
        self._collection.document(self._doc_id(record.credential_id)).set(
            {
                "credential_id": record.credential_id_b64,
                "public_key": _b64url_encode(record.public_key),
                "sign_count": record.sign_count,
                "user_id": record.user_id,
                "aaguid": record.aaguid,
                "created_at": record.created_at,
                "last_used_at": record.last_used_at,
            }
        )

    def get(self, credential_id: bytes) -> CredentialRecord | None:
        snap = self._collection.document(self._doc_id(credential_id)).get()
        if not snap.exists:
            return None
        return self._to_record(snap.id, snap.to_dict() or {})

    def list_all(self) -> list[CredentialRecord]:
        return [self._to_record(s.id, s.to_dict() or {}) for s in self._collection.stream()]

    def list_for_user(self, user_id: str) -> list[CredentialRecord]:
        query = self._collection.where("user_id", "==", user_id)
        return [self._to_record(s.id, s.to_dict() or {}) for s in query.stream()]

    def update_usage(self, credential_id: bytes, *, sign_count: int) -> None:
        self._collection.document(self._doc_id(credential_id)).update(
            {"sign_count": sign_count, "last_used_at": datetime.now(UTC)}
        )

    def count(self) -> int:
        return sum(1 for _ in self._collection.stream())


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
