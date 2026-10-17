"""Regression: Firestore-backed service managers must construct without GCP credentials.

The production image must boot (and /healthz must answer 200) in environments
without Application Default Credentials — e.g. a local container smoke, CI
images, or during a credentials/IAM outage. The managers are constructed at
main.py import time, so an eager ``firestore.Client()`` there killed the whole
process before the server could start.

The clients are now lazy: created on first request-path use, failing closed
there if credentials are missing. This mirrors the existing idiom in
``database/firestore_client.py``.
"""

import importlib
from unittest.mock import patch

import pytest

MANAGERS = [
    ("conversation_memory", "ConversationMemory"),
    ("chat_history", "ChatHistory"),
    ("lead_management", "LeadManager"),
    ("appointment_manager", "AppointmentManager"),
]


@pytest.mark.parametrize("module_name,class_name", MANAGERS)
def test_manager_constructs_without_credentials(module_name, class_name):
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)

    with patch.object(module.firestore, "Client", side_effect=RuntimeError("no ADC")):
        manager = cls(project_id="test-project")
        assert manager._db is None
        # Fail-closed at request time, not boot time.
        with pytest.raises(RuntimeError, match="no ADC"):
            _ = manager.db


@pytest.mark.parametrize("module_name,class_name", MANAGERS)
def test_manager_db_caches_client(module_name, class_name):
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    sentinel = object()

    with patch.object(module.firestore, "Client", return_value=sentinel) as client_mock:
        manager = cls(project_id="test-project")
        assert manager.db is sentinel
        assert manager.db is sentinel
        client_mock.assert_called_once_with(project="test-project")
