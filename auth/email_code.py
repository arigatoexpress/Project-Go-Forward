"""Storage + helpers for the EMAIL one-time-code admin login fallback.

Mirrors the structure of ``auth/store.py`` (the passkey credential store): a
``Protocol`` plus an in-memory and a Firestore backend, selected by
``default_code_store()`` with the same production-safety gating.

Security notes
--------------
* Codes are stored ONLY as a SHA-256 hash; the plaintext code never touches the
  store, the logs, or the response. Verification is constant-time
  (``hmac.compare_digest``) at the call site.
* The document id is ``sha256(normalized_email)`` so the collection never holds
  a recoverable list of which emails have requested codes.
* Records carry an ``attempts`` counter and an ``expires_at`` so the caller can
  enforce single-use + per-code attempt caps + TTL.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from time import time
from typing import Protocol

from database.firestore_timeouts import firestore_timeout

log = logging.getLogger(__name__)

# Tunables. Kept here so the endpoint imports one source of truth.
EMAIL_CODE_TTL_SECONDS = 600  # 10 minutes
MAX_CODE_ATTEMPTS = 5
CODE_LENGTH = 6


def generate_code() -> str:
    """Return a cryptographically-random, zero-padded 6-digit numeric code."""
    return str(secrets.randbelow(10**CODE_LENGTH)).zfill(CODE_LENGTH)


def hash_code(code: str) -> str:
    """Return the SHA-256 hex digest of a code. Never store the plaintext."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _normalize_email(value: object) -> str:
    return str(value or "").strip().lower()


def _email_doc_id(email: str) -> str:
    return hashlib.sha256(_normalize_email(email).encode("utf-8")).hexdigest()


@dataclass
class EmailLoginCodeRecord:
    """One outstanding email login code (hashed)."""

    code_hash: str
    expires_at: float
    attempts: int = 0
    created_at: float = field(default_factory=time)

    @property
    def is_expired(self) -> bool:
        return time() >= self.expires_at


class EmailLoginCodeStore(Protocol):
    """Pluggable persistence for outstanding email login codes."""

    def put(self, email: str, code_hash: str, expires_at: float) -> None: ...
    def get(self, email: str) -> EmailLoginCodeRecord | None: ...
    def increment_attempts(self, email: str) -> int: ...
    def delete(self, email: str) -> None: ...


class InMemoryEmailLoginCodeStore:
    """Process-local store. Lost on restart — dev/tests only."""

    backend_name = "memory"
    persistent = False

    def __init__(self, seed: Iterable[tuple[str, EmailLoginCodeRecord]] = ()) -> None:
        self._records: dict[str, EmailLoginCodeRecord] = {}
        for email, rec in seed:
            self._records[_normalize_email(email)] = rec

    def put(self, email: str, code_hash: str, expires_at: float) -> None:
        # Overwrite any prior record (resets the attempt counter) so a fresh
        # request always supersedes a stale code.
        self._records[_normalize_email(email)] = EmailLoginCodeRecord(
            code_hash=code_hash, expires_at=expires_at
        )

    def get(self, email: str) -> EmailLoginCodeRecord | None:
        rec = self._records.get(_normalize_email(email))
        if rec is None:
            return None
        if rec.is_expired:
            # Lazily evict expired records so the store never serves them.
            self._records.pop(_normalize_email(email), None)
            return None
        return rec

    def increment_attempts(self, email: str) -> int:
        rec = self._records.get(_normalize_email(email))
        if rec is None:
            return 0
        rec.attempts += 1
        return rec.attempts

    def delete(self, email: str) -> None:
        self._records.pop(_normalize_email(email), None)


class FirestoreEmailLoginCodeStore:
    """Persists outstanding codes to Firestore in the THO project.

    Collection: ``tho_admin_login_codes``
    Document id: ``sha256(normalized_email).hexdigest()``
    Fields: ``code_hash``, ``expires_at`` (epoch float), ``attempts``,
    ``created_at`` (epoch float).
    """

    COLLECTION = "tho_admin_login_codes"
    backend_name = "firestore"
    persistent = True

    def __init__(self, project: str | None = None, *, client=None) -> None:
        if client is None:
            from google.cloud import firestore

            client = firestore.Client(project=project) if project else firestore.Client()
        self._client = client
        self._collection = client.collection(self.COLLECTION)

    def put(self, email: str, code_hash: str, expires_at: float) -> None:
        self._collection.document(_email_doc_id(email)).set(
            {
                "code_hash": code_hash,
                "expires_at": float(expires_at),
                "attempts": 0,
                "created_at": datetime.now(UTC).timestamp(),
            },
            timeout=firestore_timeout(),
        )

    def get(self, email: str) -> EmailLoginCodeRecord | None:
        snap = self._collection.document(_email_doc_id(email)).get(timeout=firestore_timeout())
        if not snap.exists:
            return None
        data = snap.to_dict() or {}
        rec = EmailLoginCodeRecord(
            code_hash=str(data.get("code_hash", "")),
            expires_at=float(data.get("expires_at", 0.0)),
            attempts=int(data.get("attempts", 0)),
            created_at=float(data.get("created_at", 0.0)),
        )
        if rec.is_expired:
            # Best-effort cleanup; never serve an expired code.
            try:
                self.delete(email)
            except Exception:
                pass
            return None
        return rec

    def increment_attempts(self, email: str) -> int:
        doc_ref = self._collection.document(_email_doc_id(email))
        snap = doc_ref.get(timeout=firestore_timeout())
        if not snap.exists:
            return 0
        attempts = int((snap.to_dict() or {}).get("attempts", 0)) + 1
        doc_ref.update({"attempts": attempts}, timeout=firestore_timeout())
        return attempts

    def delete(self, email: str) -> None:
        self._collection.document(_email_doc_id(email)).delete(timeout=firestore_timeout())


class EmailLoginCodeStoreUnavailable(RuntimeError):
    """Raised when production cannot reach a persistent login-code store."""


def _memory_fallback_allowed() -> bool:
    value = os.environ.get("THO_EMAIL_CODE_ALLOW_MEMORY_STORE", "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    # Outside Cloud Run (no K_SERVICE) memory is acceptable for local dev/tests.
    return not os.environ.get("K_SERVICE")


@lru_cache(maxsize=4)
def default_code_store(project: str | None = None) -> EmailLoginCodeStore:
    """Return the configured email login-code store.

    Cloud Run must use Firestore so codes survive restarts and are visible to
    every instance. Local development may fall back to memory.
    """
    backend = os.environ.get("THO_EMAIL_CODE_STORE", "firestore").strip().lower()
    if backend == "memory":
        if not _memory_fallback_allowed():
            raise EmailLoginCodeStoreUnavailable(
                "memory email login-code store is disabled in production"
            )
        return InMemoryEmailLoginCodeStore()

    try:
        return FirestoreEmailLoginCodeStore(
            project=project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        )
    except Exception as exc:
        if _memory_fallback_allowed():
            log.warning("falling back to in-memory email login-code store (%s)", exc)
            return InMemoryEmailLoginCodeStore()
        raise EmailLoginCodeStoreUnavailable(
            "persistent email login-code store unavailable"
        ) from exc
