"""Tests for the shared Firestore RPC timeout (database/rpc_timeout.py) and
its enforcement in THODatabase (database/firestore_client.py).

Run: python -m pytest tests/test_firestore_timeouts.py -v
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

from database.firestore_client import THODatabase

REPO_ROOT = Path(__file__).resolve().parents[1]


def _rpc_timeouts_in_subprocess(env_overrides: dict | None = None) -> dict:
    """Load database.rpc_timeout constants in a fresh interpreter.

    The constants are computed at import time from FIRESTORE_RPC_TIMEOUT_SECONDS,
    so an env-config test needs a fresh module evaluation. A subprocess keeps the
    pytest process's module state untouched — no importlib.reload / restore dance
    (reload here was never the coverage problem; the endpoint-coverage
    understatement tracked in this repo's cycles had a different root cause).
    """
    code = (
        "import json;"
        "import database.rpc_timeout as m;"
        "print(json.dumps({'rpc': m.FIRESTORE_RPC_TIMEOUT,"
        " 'txn': m.FIRESTORE_TRANSACTION_TIMEOUT}))"
    )
    env = {k: v for k, v in os.environ.items() if k != "FIRESTORE_RPC_TIMEOUT_SECONDS"}
    env.update(env_overrides or {})
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


class TestRpcTimeoutConfig:
    """The shared constant loads from env with safe fallbacks."""

    def test_default_when_env_unset(self):
        values = _rpc_timeouts_in_subprocess()
        assert values["rpc"] == 10.0

    def test_env_override(self):
        values = _rpc_timeouts_in_subprocess({"FIRESTORE_RPC_TIMEOUT_SECONDS": "5.5"})
        assert values["rpc"] == 5.5

    def test_invalid_env_falls_back(self):
        values = _rpc_timeouts_in_subprocess({"FIRESTORE_RPC_TIMEOUT_SECONDS": "not-a-number"})
        assert values["rpc"] == 10.0

    def test_non_positive_env_falls_back(self):
        values = _rpc_timeouts_in_subprocess({"FIRESTORE_RPC_TIMEOUT_SECONDS": "0"})
        assert values["rpc"] == 10.0


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

    def test_transaction_timeout_derives_from_rpc_timeout(self):
        values = _rpc_timeouts_in_subprocess()
        assert values["txn"] == values["rpc"] * 3
        assert values["txn"] == 30.0

    def test_transaction_timeout_scales_with_env_override(self):
        values = _rpc_timeouts_in_subprocess({"FIRESTORE_RPC_TIMEOUT_SECONDS": "5"})
        assert values["rpc"] == 5.0
        assert values["txn"] == 15.0

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
