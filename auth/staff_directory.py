"""Admin-controlled staff allowlist overrides.

The default policy still lets owner emails and ``@texashomeoutlet.com`` addresses
sign in. Admins can add someone outside that domain, or block an address even
when it matches the domain. Owner emails cannot be blocked here.

Records live in Firestore in production (collection ``tho_admin_staff``) and in
memory during local development and tests. The stored email is visible only to
an already signed-in admin. Login checks read the status; they do not log the
address.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Protocol

from database.rpc_timeout import FIRESTORE_RPC_TIMEOUT

log = logging.getLogger(__name__)

COLLECTION = "tho_admin_staff"
STATUS_ALLOWED = "allowed"
STATUS_BLOCKED = "blocked"


class StaffDirectoryUnavailable(RuntimeError):
    """Raised when a persistent staff directory cannot be read or written."""


def _normalize_email(value: object) -> str:
    return str(value or "").strip().lower()


def _doc_id(email: str) -> str:
    return hashlib.sha256(_normalize_email(email).encode("utf-8")).hexdigest()


@dataclass
class StaffOverride:
    email: str
    status: str
    updated_at: str = ""


class StaffDirectory(Protocol):
    def get(self, email: str) -> StaffOverride | None: ...
    def list_all(self) -> list[StaffOverride]: ...
    def put(self, email: str, status: str) -> None: ...
    def delete(self, email: str) -> None: ...


class InMemoryStaffDirectory:
    """Process-local overrides. Dev and tests only."""

    backend_name = "memory"

    def __init__(self, seed: Iterable[StaffOverride] = ()) -> None:
        self._rows: dict[str, StaffOverride] = {}
        for row in seed:
            self.put(row.email, row.status)

    def get(self, email: str) -> StaffOverride | None:
        return self._rows.get(_normalize_email(email))

    def list_all(self) -> list[StaffOverride]:
        return sorted(self._rows.values(), key=lambda row: row.email)

    def put(self, email: str, status: str) -> None:
        normalized = _normalize_email(email)
        if status not in {STATUS_ALLOWED, STATUS_BLOCKED}:
            raise ValueError("invalid staff status")
        self._rows[normalized] = StaffOverride(
            email=normalized,
            status=status,
            updated_at=datetime.now(UTC).isoformat(),
        )

    def delete(self, email: str) -> None:
        self._rows.pop(_normalize_email(email), None)


class FirestoreStaffDirectory:
    """Persists add/block overrides so every Cloud Run instance sees them."""

    backend_name = "firestore"

    def __init__(self, project: str | None = None, *, client=None) -> None:
        if client is None:
            from google.cloud import firestore

            client = firestore.Client(project=project) if project else firestore.Client()
        self._collection = client.collection(COLLECTION)

    def get(self, email: str) -> StaffOverride | None:
        snap = self._collection.document(_doc_id(email)).get(timeout=FIRESTORE_RPC_TIMEOUT)
        if not snap.exists:
            return None
        return _row_from_dict(snap.to_dict() or {})

    def list_all(self) -> list[StaffOverride]:
        rows = []
        for snap in self._collection.stream(timeout=FIRESTORE_RPC_TIMEOUT):
            row = _row_from_dict(snap.to_dict() or {})
            if row is not None:
                rows.append(row)
        return sorted(rows, key=lambda row: row.email)

    def put(self, email: str, status: str) -> None:
        normalized = _normalize_email(email)
        if status not in {STATUS_ALLOWED, STATUS_BLOCKED}:
            raise ValueError("invalid staff status")
        self._collection.document(_doc_id(normalized)).set(
            {
                "email": normalized,
                "status": status,
                "updated_at": datetime.now(UTC).isoformat(),
            },
            timeout=FIRESTORE_RPC_TIMEOUT,
        )

    def delete(self, email: str) -> None:
        self._collection.document(_doc_id(email)).delete(timeout=FIRESTORE_RPC_TIMEOUT)


def _row_from_dict(data: dict) -> StaffOverride | None:
    email = _normalize_email(data.get("email"))
    status = str(data.get("status") or "")
    if not email or status not in {STATUS_ALLOWED, STATUS_BLOCKED}:
        return None
    return StaffOverride(email=email, status=status, updated_at=str(data.get("updated_at") or ""))


def _memory_fallback_allowed() -> bool:
    choice = os.environ.get("THO_STAFF_DIRECTORY_STORE", "").strip().lower()
    if choice == "memory":
        return True
    if choice == "firestore":
        return False
    return not os.environ.get("K_SERVICE")


@lru_cache(maxsize=1)
def default_directory(project: str | None = None) -> StaffDirectory:
    """Return the staff-directory backend.

    Cloud Run uses Firestore. Local runs and tests use memory so a missing
    emulator cannot hang a login check.
    """
    if _memory_fallback_allowed():
        return InMemoryStaffDirectory()
    try:
        return FirestoreStaffDirectory(project=project or os.environ.get("GOOGLE_CLOUD_PROJECT"))
    except Exception as exc:
        raise StaffDirectoryUnavailable("persistent staff directory unavailable") from exc


def override_status(email: str) -> str | None:
    """Return ``allowed``, ``blocked``, or ``None`` when no override is stored.

    A directory outage returns ``None`` so the domain and owner policy still
    lets the team in. Explicit adds and blocks apply again once the directory
    is reachable.
    """
    try:
        row = default_directory().get(email)
    except Exception as exc:
        log.warning("staff directory lookup failed: %s", exc)
        return None
    if row is None:
        return None
    return row.status


def list_overrides() -> list[StaffOverride]:
    return list(default_directory().list_all())


def set_override(email: str, status: str) -> None:
    default_directory().put(email, status)


def clear_override(email: str) -> None:
    default_directory().delete(email)
