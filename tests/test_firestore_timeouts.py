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
