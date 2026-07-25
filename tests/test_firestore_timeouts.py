"""Tests for the shared Firestore RPC timeout (database/rpc_timeout.py) and
its enforcement in THODatabase (database/firestore_client.py).

Run: python -m pytest tests/test_firestore_timeouts.py -v
"""

import importlib
from unittest.mock import MagicMock

import pytest

import database.rpc_timeout as rpc_timeout
from database.firestore_client import THODatabase


class TestRpcTimeoutConfig:
    """The shared constant loads from env with safe fallbacks."""

    def test_default_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("FIRESTORE_RPC_TIMEOUT_SECONDS", raising=False)
        reloaded = importlib.reload(rpc_timeout)
        assert reloaded.FIRESTORE_RPC_TIMEOUT == 10.0

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("FIRESTORE_RPC_TIMEOUT_SECONDS", "5.5")
        reloaded = importlib.reload(rpc_timeout)
        assert reloaded.FIRESTORE_RPC_TIMEOUT == 5.5

    def test_invalid_env_falls_back(self, monkeypatch):
        monkeypatch.setenv("FIRESTORE_RPC_TIMEOUT_SECONDS", "not-a-number")
        reloaded = importlib.reload(rpc_timeout)
        assert reloaded.FIRESTORE_RPC_TIMEOUT == 10.0

    def test_non_positive_env_falls_back(self, monkeypatch):
        monkeypatch.setenv("FIRESTORE_RPC_TIMEOUT_SECONDS", "0")
        reloaded = importlib.reload(rpc_timeout)
        assert reloaded.FIRESTORE_RPC_TIMEOUT == 10.0

    @pytest.fixture(autouse=True)
    def _restore_module(self, monkeypatch):
        """Reload the module once more after each test so later imports see
        the env-independent value."""
        yield
        monkeypatch.delenv("FIRESTORE_RPC_TIMEOUT_SECONDS", raising=False)
        importlib.reload(rpc_timeout)


def _db_with_mock_client():
    """THODatabase with a MagicMock Firestore client injected."""
    db = THODatabase(project_id="tho-test-local")
    db._db = MagicMock()
    return db, db._db


class TestClientPassesTimeout:
    """Every THODatabase gRPC call must carry timeout=FIRESTORE_RPC_TIMEOUT."""

    def test_document_get(self):
        from database.rpc_timeout import FIRESTORE_RPC_TIMEOUT

        db, mock = _db_with_mock_client()
        mock.collection.return_value.document.return_value.get.return_value.exists = True
        db.get_customer("cust-1")
        mock.collection.return_value.document.return_value.get.assert_called_once_with(
            timeout=FIRESTORE_RPC_TIMEOUT
        )

    def test_query_stream(self):
        from database.rpc_timeout import FIRESTORE_RPC_TIMEOUT

        db, mock = _db_with_mock_client()
        mock.collection.return_value.stream.return_value = iter([])
        db.count_customers()
        mock.collection.return_value.stream.assert_called_once_with(
            timeout=FIRESTORE_RPC_TIMEOUT
        )

    def test_document_set(self):
        from database.rpc_timeout import FIRESTORE_RPC_TIMEOUT

        db, mock = _db_with_mock_client()
        db.create_customer({"full_name": "Alice"})
        mock.collection.return_value.document.return_value.set.assert_called_once()
        _, kwargs = mock.collection.return_value.document.return_value.set.call_args
        assert kwargs["timeout"] == FIRESTORE_RPC_TIMEOUT

    def test_document_update(self):
        from database.rpc_timeout import FIRESTORE_RPC_TIMEOUT

        db, mock = _db_with_mock_client()
        db.update_customer("cust-1", {"status": "ACTIVE"})
        mock.collection.return_value.document.return_value.update.assert_called_once()
        _, kwargs = mock.collection.return_value.document.return_value.update.call_args
        assert kwargs["timeout"] == FIRESTORE_RPC_TIMEOUT

    def test_document_delete(self):
        from database.rpc_timeout import FIRESTORE_RPC_TIMEOUT

        db, mock = _db_with_mock_client()
        db.delete_customer("cust-1")
        mock.collection.return_value.document.return_value.delete.assert_called_once_with(
            timeout=FIRESTORE_RPC_TIMEOUT
        )

    def test_batch_commit(self):
        from database.rpc_timeout import FIRESTORE_RPC_TIMEOUT

        db, mock = _db_with_mock_client()
        db.batch_create_customers([{"id": "cust-1", "full_name": "Alice"}])
        mock.batch.return_value.commit.assert_called_once_with(
            timeout=FIRESTORE_RPC_TIMEOUT
        )


class TestTransactionWallClockBound:
    """Firestore's @transactional helper performs Begin/Commit/Rollback RPCs
    internally with no per-RPC timeout hook, so request-path transactions must
    be bounded at the call site with asyncio.wait_for using
    FIRESTORE_TRANSACTION_TIMEOUT (see database/rpc_timeout.py)."""

    def test_transaction_timeout_derives_from_rpc_timeout(self, monkeypatch):
        monkeypatch.delenv("FIRESTORE_RPC_TIMEOUT_SECONDS", raising=False)
        reloaded = importlib.reload(rpc_timeout)
        assert reloaded.FIRESTORE_TRANSACTION_TIMEOUT == reloaded.FIRESTORE_RPC_TIMEOUT * 3
        assert reloaded.FIRESTORE_TRANSACTION_TIMEOUT == 30.0

    def test_transaction_timeout_scales_with_env_override(self, monkeypatch):
        monkeypatch.setenv("FIRESTORE_RPC_TIMEOUT_SECONDS", "5")
        reloaded = importlib.reload(rpc_timeout)
        assert reloaded.FIRESTORE_TRANSACTION_TIMEOUT == 15.0

    @pytest.fixture(autouse=True)
    def _restore_module(self, monkeypatch):
        yield
        monkeypatch.delenv("FIRESTORE_RPC_TIMEOUT_SECONDS", raising=False)
        importlib.reload(rpc_timeout)

    @staticmethod
    def _record_wait_for(monkeypatch, sink):
        """Swap asyncio.wait_for for a recording wrapper (restored by
        monkeypatch after the test)."""
        import asyncio

        real_wait_for = asyncio.wait_for

        async def recording(awaitable, timeout=None):
            sink["wait_for_timeout"] = timeout
            return await real_wait_for(awaitable, timeout=timeout)

        monkeypatch.setattr(asyncio, "wait_for", recording)

    def test_lead_transition_bounded_by_wait_for(self, monkeypatch):
        import asyncio

        import lead_management
        from database.rpc_timeout import FIRESTORE_TRANSACTION_TIMEOUT
        from lead_management import LeadManager

        monkeypatch.setattr(lead_management.firestore, "transactional", lambda fn: fn)

        class _Doc:
            def get(self, transaction=None, timeout=None):
                assert transaction is not None
                assert timeout is not None
                return type("Snap", (), {"exists": False, "to_dict": lambda s: {}})()

        class _Coll:
            def document(self, _id):
                return _Doc()

        class _DB:
            def collection(self, _name):
                return _Coll()

            def transaction(self):
                return object()

        sink = {}
        self._record_wait_for(monkeypatch, sink)

        lm = LeadManager.__new__(LeadManager)
        lm.db = _DB()
        lm.collection_name = "leads"
        asyncio.run(lm.transition_lead_status("L1", "contacted", actor="admin:ari"))

        assert sink["wait_for_timeout"] == FIRESTORE_TRANSACTION_TIMEOUT

    def test_appointment_booking_bounded_by_wait_for(self, monkeypatch):
        import asyncio

        import appointment_manager as am
        from database.rpc_timeout import FIRESTORE_TRANSACTION_TIMEOUT

        sink = {}
        self._record_wait_for(monkeypatch, sink)

        mgr = am.AppointmentManager.__new__(am.AppointmentManager)
        monkeypatch.setattr(
            am.AppointmentManager,
            "create_appointment_sync",
            lambda self, appt: appt,
        )

        appt = am.Appointment(
            appointment_id="a1", name="Jordan", phone="5125550123", date="2026-08-01"
        )
        result = asyncio.run(mgr.create_appointment(appt))

        assert result is appt
        assert sink["wait_for_timeout"] == FIRESTORE_TRANSACTION_TIMEOUT
