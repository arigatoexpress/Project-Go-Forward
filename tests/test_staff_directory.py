"""Tests for auth/staff_directory.py — the admin-controlled staff allowlist.

Security properties under test:
  * the Firestore document id is sha256(normalized email), so the collection
    never holds a recoverable list of staff addresses;
  * invalid statuses are rejected before any write;
  * stream/list skips malformed rows instead of crashing the directory;
  * default_directory never falls back to memory on Cloud Run — an outage
    raises StaffDirectoryUnavailable;
  * override_status fails OPEN to the domain/owner policy (returns None) on a
    directory read failure, so an outage cannot lock the whole team out.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# In-memory backend
# ---------------------------------------------------------------------------


class TestInMemoryStaffDirectory:
    def _directory(self, seed=()):
        from auth.staff_directory import InMemoryStaffDirectory

        return InMemoryStaffDirectory(seed=seed)

    def test_seed_constructor_populates_rows(self):
        from auth.staff_directory import STATUS_ALLOWED

        directory = self._directory(
            seed=[type("Row", (), {"email": "Staff@TexasHomeOutlet.com", "status": STATUS_ALLOWED})()]
        )
        row = directory.get("staff@texashomeoutlet.com")
        assert row is not None
        assert row.status == STATUS_ALLOWED

    def test_put_rejects_invalid_status(self):
        from auth.staff_directory import STATUS_BLOCKED

        directory = self._directory()
        directory.put("a@texashomeoutlet.com", STATUS_BLOCKED)
        with pytest.raises(ValueError, match="invalid staff status"):
            directory.put("a@texashomeoutlet.com", "superadmin")
        # The failed write must not have replaced the valid record.
        assert directory.get("a@texashomeoutlet.com").status == STATUS_BLOCKED

    def test_put_normalizes_email_and_stamps_updated_at(self):
        from auth.staff_directory import STATUS_ALLOWED

        directory = self._directory()
        directory.put("  Mixed@Case.COM ", STATUS_ALLOWED)
        row = directory.get("mixed@case.com")
        assert row is not None
        assert row.email == "mixed@case.com"
        assert row.updated_at

    def test_list_all_sorted_by_email(self):
        from auth.staff_directory import STATUS_ALLOWED, STATUS_BLOCKED

        directory = self._directory()
        directory.put("z@example.com", STATUS_ALLOWED)
        directory.put("a@example.com", STATUS_BLOCKED)
        assert [row.email for row in directory.list_all()] == [
            "a@example.com",
            "z@example.com",
        ]

    def test_delete_missing_email_is_noop(self):
        directory = self._directory()
        directory.delete("never-added@example.com")
        assert directory.list_all() == []


# ---------------------------------------------------------------------------
# Firestore backend (production)
# ---------------------------------------------------------------------------


class _FakeSnap:
    def __init__(self, data=None):
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return self._data


class _FakeDocRef:
    def __init__(self, backing, doc_id):
        self._backing = backing
        self._doc_id = doc_id

    def set(self, data, timeout=None):
        assert timeout is not None, "Firestore RPCs must carry the bounded timeout"
        self._backing[self._doc_id] = dict(data)

    def get(self, timeout=None):
        assert timeout is not None, "Firestore RPCs must carry the bounded timeout"
        return _FakeSnap(self._backing.get(self._doc_id))

    def delete(self, timeout=None):
        assert timeout is not None, "Firestore RPCs must carry the bounded timeout"
        self._backing.pop(self._doc_id, None)


class _FakeDocSnap:
    """Snapshot wrapper for collection.stream()."""

    def __init__(self, data):
        self._data = data

    def to_dict(self):
        return self._data


class _FakeCollection:
    def __init__(self, backing):
        self._backing = backing

    def document(self, doc_id):
        return _FakeDocRef(self._backing, doc_id)

    def stream(self, timeout=None):
        assert timeout is not None, "Firestore RPCs must carry the bounded timeout"
        return [_FakeDocSnap(dict(data)) for data in self._backing.values()]


class _FakeFirestoreClient:
    def __init__(self):
        self._backing = {}

    def collection(self, name):
        assert name == "tho_admin_staff"
        return _FakeCollection(self._backing)


def _firestore_directory_on(client):
    from auth.staff_directory import FirestoreStaffDirectory

    return FirestoreStaffDirectory(client=client)


def _doc_id_for(email):
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


class TestFirestoreStaffDirectory:
    def test_document_id_is_sha256_of_normalized_email(self):
        from auth.staff_directory import STATUS_ALLOWED

        client = _FakeFirestoreClient()
        directory = _firestore_directory_on(client)
        directory.put("Staff@TexasHomeOutlet.com", STATUS_ALLOWED)
        assert list(client._backing.keys()) == [_doc_id_for("staff@texashomeoutlet.com")]

    def test_put_get_roundtrip(self):
        from auth.staff_directory import STATUS_BLOCKED

        directory = _firestore_directory_on(_FakeFirestoreClient())
        directory.put("blocked@gmail.com", STATUS_BLOCKED)
        row = directory.get("BLOCKED@gmail.com")
        assert row is not None
        assert row.email == "blocked@gmail.com"
        assert row.status == STATUS_BLOCKED
        assert row.updated_at

    def test_get_missing_returns_none(self):
        directory = _firestore_directory_on(_FakeFirestoreClient())
        assert directory.get("missing@example.com") is None

    def test_put_rejects_invalid_status_before_writing(self):
        client = _FakeFirestoreClient()
        directory = _firestore_directory_on(client)
        with pytest.raises(ValueError, match="invalid staff status"):
            directory.put("a@example.com", "blocked-ish")
        assert client._backing == {}

    def test_delete_removes_record(self):
        from auth.staff_directory import STATUS_ALLOWED

        client = _FakeFirestoreClient()
        directory = _firestore_directory_on(client)
        directory.put("gone@example.com", STATUS_ALLOWED)
        directory.delete("gone@example.com")
        assert directory.get("gone@example.com") is None
        assert client._backing == {}

    def test_list_all_sorted_and_skips_malformed_rows(self):
        from auth.staff_directory import STATUS_ALLOWED, STATUS_BLOCKED

        client = _FakeFirestoreClient()
        directory = _firestore_directory_on(client)
        directory.put("z@example.com", STATUS_ALLOWED)
        directory.put("a@example.com", STATUS_BLOCKED)
        # Malformed rows must be skipped, not crash the listing.
        client._backing["bad-status"] = {"email": "bad@example.com", "status": "nonsense"}
        client._backing["missing-email"] = {"status": STATUS_ALLOWED}
        rows = directory.list_all()
        assert [(row.email, row.status) for row in rows] == [
            ("a@example.com", STATUS_BLOCKED),
            ("z@example.com", STATUS_ALLOWED),
        ]

    def test_constructor_creates_client_from_project_when_not_injected(self, monkeypatch):
        """The client=None path must build google.cloud.firestore.Client(project=...)."""
        import google.cloud

        fake_client = _FakeFirestoreClient()

        class _FakeFirestoreModule:
            Client = staticmethod(lambda project=None: fake_client)

        monkeypatch.setattr(google.cloud, "firestore", _FakeFirestoreModule())
        from auth.staff_directory import FirestoreStaffDirectory

        directory = FirestoreStaffDirectory(project="tho-ai-agent")
        from auth.staff_directory import STATUS_ALLOWED

        directory.put("ctor@example.com", STATUS_ALLOWED)
        assert _doc_id_for("ctor@example.com") in fake_client._backing


# ---------------------------------------------------------------------------
# default_directory selection + outage behavior
# ---------------------------------------------------------------------------


@pytest.fixture
def clear_directory_cache():
    import auth.staff_directory

    auth.staff_directory.default_directory.cache_clear()
    yield
    auth.staff_directory.default_directory.cache_clear()


class TestDefaultDirectory:
    def test_explicit_memory_choice_wins_even_on_cloud_run(self, monkeypatch, clear_directory_cache):
        import auth.staff_directory

        monkeypatch.setenv("K_SERVICE", "project-go-forward")
        monkeypatch.setenv("THO_STAFF_DIRECTORY_STORE", "memory")
        directory = auth.staff_directory.default_directory()
        assert directory.backend_name == "memory"

    def test_cloud_run_uses_firestore_backend(self, monkeypatch, clear_directory_cache):
        import auth.staff_directory

        sentinel = object()
        monkeypatch.setenv("K_SERVICE", "project-go-forward")
        monkeypatch.delenv("THO_STAFF_DIRECTORY_STORE", raising=False)
        monkeypatch.setattr(
            auth.staff_directory, "FirestoreStaffDirectory", lambda project=None: sentinel
        )
        assert auth.staff_directory.default_directory(project="tho-ai-agent") is sentinel

    def test_firestore_failure_on_cloud_run_raises_unavailable(
        self, monkeypatch, clear_directory_cache
    ):
        import auth.staff_directory

        monkeypatch.setenv("K_SERVICE", "project-go-forward")
        monkeypatch.delenv("THO_STAFF_DIRECTORY_STORE", raising=False)

        def _boom(project=None):
            raise RuntimeError("no credentials")

        monkeypatch.setattr(auth.staff_directory, "FirestoreStaffDirectory", _boom)
        with pytest.raises(auth.staff_directory.StaffDirectoryUnavailable):
            auth.staff_directory.default_directory()

    def test_off_cloud_run_defaults_to_memory(self, monkeypatch, clear_directory_cache):
        import auth.staff_directory

        monkeypatch.delenv("K_SERVICE", raising=False)
        monkeypatch.delenv("THO_STAFF_DIRECTORY_STORE", raising=False)
        assert auth.staff_directory.default_directory().backend_name == "memory"


class TestOverrideStatusOutageBehavior:
    def test_lookup_failure_returns_none_fail_open(self, monkeypatch):
        """A directory outage must not lock the team out: None = domain policy applies."""
        import auth.staff_directory

        class _FailingDirectory:
            def get(self, email):
                raise RuntimeError("firestore unreachable")

        monkeypatch.setattr(auth.staff_directory, "default_directory", lambda: _FailingDirectory())
        assert auth.staff_directory.override_status("anyone@texashomeoutlet.com") is None

    def test_no_override_returns_none(self, monkeypatch):
        import auth.staff_directory

        class _EmptyDirectory:
            def get(self, email):
                return None

        monkeypatch.setattr(auth.staff_directory, "default_directory", lambda: _EmptyDirectory())
        assert auth.staff_directory.override_status("owner@texashomeoutlet.com") is None

    def test_override_status_returns_stored_status(self, monkeypatch):
        import auth.staff_directory

        row = auth.staff_directory.StaffOverride(
            email="gmail-user@gmail.com", status=auth.staff_directory.STATUS_ALLOWED
        )

        class _Directory:
            def get(self, email):
                return row

        monkeypatch.setattr(auth.staff_directory, "default_directory", lambda: _Directory())
        assert auth.staff_directory.override_status("gmail-user@gmail.com") == "allowed"


class TestAdminMutators:
    def test_set_list_clear_roundtrip_via_memory_backend(
        self, monkeypatch, clear_directory_cache
    ):
        import auth.staff_directory

        monkeypatch.setenv("THO_STAFF_DIRECTORY_STORE", "memory")
        auth.staff_directory.set_override("Staff@Example.com", "blocked")
        rows = auth.staff_directory.list_overrides()
        assert [(row.email, row.status) for row in rows] == [("staff@example.com", "blocked")]
        auth.staff_directory.clear_override("staff@example.com")
        assert auth.staff_directory.list_overrides() == []
