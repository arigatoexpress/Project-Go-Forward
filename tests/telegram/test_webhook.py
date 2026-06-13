"""Unit tests for services/telegram webhook router and integration helpers.

All Telegram Bot API calls are mocked at the client layer; no outbound HTTP or
Firestore is used.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.telegram import (  # noqa: E402
    TelegramAPIError,
    TelegramClient,
    TelegramConfig,
    telegram_router,
)
from services.telegram.webhook import (  # noqa: E402
    send_deal_status_alert,
    send_partner_webhook_alert,
)

# ─── helpers ──────────────────────────────────────────────────────────────────


def _patch_telegram_client(monkeypatch, calls: list) -> None:
    """Patch :class:`TelegramClient` so every Bot API call is recorded and faked."""
    import services.telegram.client as client_module

    def _fake_call(self, method: str, **kwargs: Any) -> dict[str, Any]:
        calls.append({"method": method, **kwargs})
        return {"message_id": 42}

    monkeypatch.setattr(client_module.TelegramClient, "_call", _fake_call)


# ─── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def clean_env(monkeypatch):
    """Remove Telegram env vars inherited by the test process."""
    for name in list(os.environ):
        if name.startswith("TELEGRAM_"):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def enabled_config(clean_env, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:TEST-TOKEN")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1001234567890")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "webhook-secret")
    return TelegramConfig.from_env()


@pytest.fixture
def mock_client_calls(monkeypatch):
    """Patch ``TelegramClient._call`` and yield the list of captured calls."""
    calls: list[dict[str, Any]] = []
    _patch_telegram_client(monkeypatch, calls)
    return calls


@pytest.fixture
def no_firestore(monkeypatch):
    """Prevent Telegram approval handlers from touching real Firestore."""
    import services.telegram.approval as approval_module

    monkeypatch.setattr(approval_module, "_get_db", lambda: None)


@pytest.fixture
def client_app(enabled_config, mock_client_calls, no_firestore):
    app = FastAPI()
    app.include_router(telegram_router)
    return TestClient(app)


# ─── configuration ────────────────────────────────────────────────────────────


def test_config_disabled_by_default(clean_env):
    cfg = TelegramConfig.from_env()
    assert cfg.enabled is False
    assert cfg.bot_token == ""
    assert cfg.chat_id == ""


def test_config_parsed_from_env(clean_env, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "secret")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", "https://example.com/hook")

    cfg = TelegramConfig.from_env()
    assert cfg.enabled is True
    assert cfg.bot_token == "token"
    assert cfg.chat_id == "chat"
    assert cfg.webhook_secret == "secret"
    assert cfg.webhook_url == "https://example.com/hook"
    assert cfg.is_configured() is True


def test_config_not_configured_when_missing_chat_id(clean_env, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    # chat_id intentionally unset
    cfg = TelegramConfig.from_env()
    assert cfg.is_configured() is False


# ─── client ───────────────────────────────────────────────────────────────────


def test_get_me(enabled_config, mock_client_calls):
    client = TelegramClient(enabled_config)
    result = client.get_me()
    assert result["message_id"] == 42
    assert mock_client_calls[0]["method"] == "getMe"


def test_send_message(enabled_config, mock_client_calls):
    client = TelegramClient(enabled_config)
    result = client.send_message("-1001234567890", "<b>hello</b>", parse_mode="HTML")
    assert result["message_id"] == 42
    call = mock_client_calls[0]
    assert call["method"] == "sendMessage"
    assert call["json_payload"]["chat_id"] == "-1001234567890"
    assert call["json_payload"]["text"] == "<b>hello</b>"
    assert call["json_payload"]["parse_mode"] == "HTML"


def test_api_error_raises_telegram_api_error(enabled_config, monkeypatch):
    import services.telegram.client as client_module

    def _fake_call(self, method: str, **kwargs: Any) -> dict[str, Any]:
        raise TelegramAPIError("Bad Request")

    monkeypatch.setattr(client_module.TelegramClient, "_call", _fake_call)

    client = TelegramClient(enabled_config)
    with pytest.raises(TelegramAPIError) as exc_info:
        client.get_me()
    assert "Bad Request" in str(exc_info.value)


# ─── webhook router ───────────────────────────────────────────────────────────


def test_webhook_rejects_missing_secret(client_app):
    resp = client_app.post("/api/v1/telegram/webhook", json={"update_id": 1})
    assert resp.status_code == 401


def test_webhook_rejects_wrong_secret(client_app):
    resp = client_app.post(
        "/api/v1/telegram/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert resp.status_code == 401


def test_webhook_accepts_valid_secret(client_app):
    resp = client_app.post(
        "/api/v1/telegram/webhook",
        json={"update_id": 1, "message": {"message_id": 1}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_webhook_answers_approval_callback(client_app, mock_client_calls):
    update = {
        "update_id": 2,
        "callback_query": {
            "id": "cq-1",
            "data": "approve:DEAL-123",
            "from": {"id": 1, "username": "admin"},
            "message": {"message_id": 7, "chat": {"id": -1001234567890}},
        },
    }
    resp = client_app.post(
        "/api/v1/telegram/webhook",
        json=update,
        headers={"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    answer_call = [c for c in mock_client_calls if c["method"] == "answerCallbackQuery"]
    assert len(answer_call) == 1
    assert answer_call[0]["json_payload"]["callback_query_id"] == "cq-1"
    assert "DEAL-123" in answer_call[0]["json_payload"]["text"]


def test_webhook_ignores_unknown_callback_data(client_app, mock_client_calls):
    update = {
        "update_id": 3,
        "callback_query": {
            "id": "cq-2",
            "data": "unknown:DEAL-123",
            "from": {"id": 1, "username": "admin"},
            "message": {"message_id": 8, "chat": {"id": -1001234567890}},
        },
    }
    resp = client_app.post(
        "/api/v1/telegram/webhook",
        json=update,
        headers={"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"},
    )
    assert resp.status_code == 200
    assert not [c for c in mock_client_calls if c["method"] == "answerCallbackQuery"]


# ─── integration helpers ──────────────────────────────────────────────────────


def test_send_deal_status_alert_skips_when_disabled(clean_env):
    cfg = TelegramConfig(
        bot_token="dummy", chat_id="chat", webhook_secret=None, webhook_url=None, enabled=False
    )
    client = TelegramClient(cfg)
    assert send_deal_status_alert("deal.status_changed", {"id": "d1"}, client=client) is None


def test_send_deal_status_alert_sends_when_enabled(enabled_config, mock_client_calls):
    client = TelegramClient(enabled_config)
    result = send_deal_status_alert(
        "deal.status_changed",
        {
            "id": "DEAL-123",
            "model": "The Avalon",
            "manufacturer": "Clayton",
            "buyer_first_name": "Jane",
            "buyer_last_name": "Doe",
            "salesrep": "Alice",
            "sales_price": 99999.99,
        },
        client=client,
    )
    assert result is not None
    assert result["message_id"] == 42
    call = mock_client_calls[0]
    assert call["method"] == "sendMessage"
    assert "DEAL-123" in call["json_payload"]["text"]


def test_send_partner_webhook_alert(enabled_config, mock_client_calls):
    client = TelegramClient(enabled_config)
    result = send_partner_webhook_alert(
        "deal.status_changed",
        {"deal_id": "DEAL-456", "to": "funded"},
        client=client,
    )
    assert result is not None
    call = mock_client_calls[0]
    assert call["method"] == "sendMessage"
    assert "deal.status_changed" in call["json_payload"]["text"]
    assert "DEAL-456" in call["json_payload"]["text"]
