"""Synthetic contact intake through the real email adapter and a fake provider."""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_api_v1 import create_client


def _client_with_email_transport(monkeypatch, *, persist=True, provider_error=False, key=True):
    monkeypatch.setenv("NOTION_LEAD_SYNC", "off")
    monkeypatch.setenv("RESEND_FROM", "Synthetic Sender <noreply@example.com>")
    if key:
        monkeypatch.setenv("RESEND_API_KEY", "synthetic-provider-key")
    else:
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
    client, main, *_ = create_client(monkeypatch)
    calls = []

    def send(payload):
        calls.append(payload)
        if provider_error:
            raise RuntimeError("synthetic provider unavailable")
        return {"id": "synthetic-message-id"}

    monkeypatch.setitem(
        sys.modules,
        "resend",
        types.SimpleNamespace(api_key="", Emails=types.SimpleNamespace(send=send)),
    )
    path = Path(__file__).resolve().parents[1] / "email_service.py"
    spec = importlib.util.spec_from_file_location("contact_test_email_adapter", path)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    monkeypatch.setattr(adapter, "NOTIFICATION_EMAILS", ["staff@example.com"])
    monkeypatch.setattr(adapter, "REPLY_TO", "staff@example.com")
    monkeypatch.setattr(adapter, "_log_email_activity", lambda *a, **k: None)
    monkeypatch.setattr(main, "notify_new_lead", adapter.notify_new_lead)

    async def no_docuseal(**kwargs):
        return None

    monkeypatch.setattr(main, "docuseal_auto_trigger", no_docuseal)
    if not persist:

        async def storage_failure(_lead):
            raise RuntimeError("synthetic storage unavailable")

        monkeypatch.setattr(main.lead_manager, "create_lead", storage_failure)
    return client, main, calls


def _submit(client, *, status=200):
    response = client.post("/api/contact", json={"name": "Synthetic Lead", "phone": "5125550123"})
    assert response.status_code == status
    return response.json()


def test_persisted_lead_with_provider_failure_still_warns(monkeypatch):
    client, main, calls = _client_with_email_transport(monkeypatch, provider_error=True)
    body = _submit(client)
    assert body["success"] is True
    assert body["lead_id"] == main.lead_manager.leads[-1].lead_id
    assert "owner_notify_failed" in body.get("warnings", [])
    assert len(calls) == 1
    assert calls[0]["to"] == ["staff@example.com"]
    assert "synthetic provider unavailable" not in str(body)


@pytest.mark.parametrize("key", [True, False])
def test_no_storage_and_no_accepted_staff_notification_is_not_success(monkeypatch, key):
    client, _, calls = _client_with_email_transport(
        monkeypatch, persist=False, provider_error=True, key=key
    )
    body = _submit(client, status=503)
    assert body["success"] is False
    assert "call" in body["error"].lower()
    assert "lead_id" not in body
    assert set(body["warnings"]) >= {"lead_storage_failed", "owner_notify_failed"}
    assert len(calls) == int(key)
    assert "synthetic storage unavailable" not in str(body)
    assert "synthetic provider unavailable" not in str(body)


def test_staff_notification_can_remain_fallback_when_storage_fails(monkeypatch):
    client, _, calls = _client_with_email_transport(monkeypatch, persist=False)
    body = _submit(client)
    assert body["success"] is True
    assert body["warnings"] == ["lead_storage_failed"]
    assert "lead_id" not in body
    assert len(calls) == 1


def test_persisted_lead_and_accepted_notification_stay_successful(monkeypatch):
    client, main, calls = _client_with_email_transport(monkeypatch)
    body = _submit(client)
    assert body["success"] is True
    assert body["lead_id"] == main.lead_manager.leads[-1].lead_id
    assert "warnings" not in body
    assert len(calls) == 1


@pytest.mark.parametrize("notification", [None, {}, {"success": True, "dry_run": True}])
def test_unconfirmed_or_dry_run_notification_is_not_an_acceptance(monkeypatch, notification):
    client, main, calls = _client_with_email_transport(monkeypatch, persist=False)
    monkeypatch.setattr(main, "notify_new_lead", lambda **kwargs: notification)
    body = _submit(client, status=503)
    assert body["success"] is False
    assert "owner_notify_failed" in body["warnings"]
    assert calls == []


def test_notification_exception_is_retryable_without_storage(monkeypatch):
    client, main, calls = _client_with_email_transport(monkeypatch, persist=False)

    def failure(**kwargs):
        raise RuntimeError("synthetic private provider detail")

    monkeypatch.setattr(main, "notify_new_lead", failure)
    body = _submit(client, status=503)
    assert body["success"] is False
    assert "owner_notify_failed" in body["warnings"]
    assert "synthetic private provider detail" not in str(body)
    assert calls == []
