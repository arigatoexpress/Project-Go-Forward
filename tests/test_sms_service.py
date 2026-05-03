"""Tests for notifications.sms_service.SMSService."""

import time
from unittest.mock import MagicMock, patch

import pytest

import notifications.sms_service as sms_module
from notifications.sms_service import SMSService


@pytest.fixture(autouse=True)
def _clear_dedup():
    """Reset the module-level dedup map before each test."""
    sms_module._sent_digests.clear()
    yield
    sms_module._sent_digests.clear()


# ── Test 1: no-op when env vars are unset ───────────────────────────────────

def test_send_team_alert_noop_when_env_unset(monkeypatch):
    """SMSService.send_team_alert must be a silent no-op when Twilio env vars are absent."""
    for var in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", "TEAM_ALERT_PHONE_NUMBERS"):
        monkeypatch.delenv(var, raising=False)

    svc = SMSService()
    assert not svc._enabled

    # Calling send_team_alert must not raise even if twilio isn't installed
    with patch.dict("sys.modules", {"twilio": None, "twilio.rest": None}):
        svc.send_team_alert("Test alert", urgency="normal")  # must not raise


# ── Test 2: duplicate alerts within 60s are suppressed ──────────────────────

def test_send_team_alert_dedupes_within_60s(monkeypatch):
    """The same message sent twice within 60s should only dispatch once."""
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "authtest")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550000000")
    monkeypatch.setenv("TEAM_ALERT_PHONE_NUMBERS", "+15550000001")

    mock_client = MagicMock()
    mock_messages_create = MagicMock()
    mock_client.messages.create = mock_messages_create

    with patch("notifications.sms_service.SMSService._dispatch") as mock_dispatch:
        svc = SMSService()
        svc.send_team_alert("Duplicate message")
        svc.send_team_alert("Duplicate message")  # should be suppressed

        assert mock_dispatch.call_count == 1, (
            "Expected exactly one dispatch for identical messages within 60s"
        )


def test_send_team_alert_allows_resend_after_dedup_window(monkeypatch):
    """The same message should be sent again once the 60s dedup window has elapsed."""
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "authtest")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550000000")
    monkeypatch.setenv("TEAM_ALERT_PHONE_NUMBERS", "+15550000001")

    with patch("notifications.sms_service.SMSService._dispatch") as mock_dispatch:
        svc = SMSService()
        svc.send_team_alert("Resend test")
        assert mock_dispatch.call_count == 1

        # Backdate the digest timestamp so the window appears expired
        digest = sms_module._message_digest("Resend test")
        sms_module._sent_digests[digest] = time.time() - 61

        svc.send_team_alert("Resend test")
        assert mock_dispatch.call_count == 2, (
            "Expected a second dispatch after the dedup window expired"
        )


# ── Test 3: Twilio API failure does not propagate ───────────────────────────

def test_twilio_api_failure_does_not_propagate(monkeypatch):
    """A Twilio API error in _dispatch must be swallowed — never raised to caller."""
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "authtest")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550000000")
    monkeypatch.setenv("TEAM_ALERT_PHONE_NUMBERS", "+15550000001")

    svc = SMSService()

    # Simulate a Twilio client that raises on messages.create
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = Exception("Twilio 503 Service Unavailable")

    # Patch the lazy import of twilio.rest.Client inside _dispatch
    mock_twilio_module = MagicMock()
    mock_twilio_module.Client.return_value = mock_client

    with patch.dict("sys.modules", {"twilio": MagicMock(), "twilio.rest": mock_twilio_module}):
        # _dispatch must catch the exception internally — should not raise
        try:
            svc._dispatch("fail test", "normal")
        except Exception as exc:
            pytest.fail(f"_dispatch raised unexpectedly: {exc}")


def test_send_team_alert_swallows_twilio_exception(monkeypatch):
    """send_team_alert must not raise even when _dispatch raises."""
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "authtest")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550000000")
    monkeypatch.setenv("TEAM_ALERT_PHONE_NUMBERS", "+15550000001")

    svc = SMSService()

    with patch.object(svc, "_dispatch", side_effect=RuntimeError("network error")):
        # The exception must not propagate — caller should receive no exception
        try:
            svc.send_team_alert("network fail test")
        except Exception as exc:
            pytest.fail(f"send_team_alert raised unexpectedly: {exc}")
