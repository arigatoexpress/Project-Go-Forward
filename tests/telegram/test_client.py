"""Unit tests for services.telegram.client and services.telegram.config.

Run: python -m pytest tests/test_telegram_client.py -v
"""

from __future__ import annotations

import pytest
import requests
import requests_mock

from services.telegram.client import TelegramAPIError, TelegramClient
from services.telegram.config import TelegramConfig


@pytest.fixture
def config() -> TelegramConfig:
    return TelegramConfig(
        bot_token="test-token",
        chat_id="-1001234567890",
        webhook_secret="shhh",
        webhook_url="https://example.com/webhook",
        enabled=True,
    )


class TestTelegramConfig:
    def test_from_env_parses_all_variables(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot123")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1001")
        monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "secret123")
        monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", "https://example.com/hook")
        monkeypatch.setenv("TELEGRAM_ENABLED", "true")

        cfg = TelegramConfig.from_env()
        assert cfg.bot_token == "bot123"
        assert cfg.chat_id == "-1001"
        assert cfg.webhook_secret == "secret123"
        assert cfg.webhook_url == "https://example.com/hook"
        assert cfg.enabled is True

    @pytest.mark.parametrize("enabled_value", ["1", "TRUE", "True", "yes", "YES"])
    def test_from_env_enabled_truthy_values(self, monkeypatch, enabled_value):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot123")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1001")
        monkeypatch.setenv("TELEGRAM_ENABLED", enabled_value)
        cfg = TelegramConfig.from_env()
        assert cfg.enabled is True

    def test_from_env_defaults_to_disabled(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_ENABLED", raising=False)
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot123")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1001")
        cfg = TelegramConfig.from_env()
        assert cfg.enabled is False

    def test_from_env_missing_optional_fields_are_none(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot123")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1001")
        monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
        monkeypatch.delenv("TELEGRAM_WEBHOOK_URL", raising=False)
        cfg = TelegramConfig.from_env()
        assert cfg.webhook_secret is None
        assert cfg.webhook_url is None


class TestTelegramClient:
    def test_constructor_raises_without_token(self):
        cfg = TelegramConfig(bot_token="", chat_id="-1001")
        with pytest.raises(ValueError, match="bot token is required"):
            TelegramClient(cfg)

    def test_get_me(self, config: TelegramConfig):
        with requests_mock.Mocker() as m:
            m.get(
                "https://api.telegram.org/bottest-token/getMe",
                json={"ok": True, "result": {"id": 1, "is_bot": True, "username": "test_bot"}},
            )
            client = TelegramClient(config)
            result = client.get_me()
            assert result["username"] == "test_bot"

    def test_send_message(self, config: TelegramConfig):
        with requests_mock.Mocker() as m:
            m.post(
                "https://api.telegram.org/bottest-token/sendMessage",
                json={"ok": True, "result": {"message_id": 42, "text": "hello"}},
            )
            client = TelegramClient(config)
            result = client.send_message(config.chat_id, "hello", parse_mode="MarkdownV2")
            assert result["message_id"] == 42
            request = m.request_history[0]
            payload = request.json()
            assert payload["chat_id"] == config.chat_id
            assert payload["text"] == "hello"
            assert payload["parse_mode"] == "MarkdownV2"
            assert payload["disable_web_page_preview"] is False

    def test_send_rich_message_defaults_to_markdownv2(self, config: TelegramConfig):
        keyboard = {"inline_keyboard": [[{"text": "Approve", "callback_data": "approve:1"}]]}
        with requests_mock.Mocker() as m:
            m.post(
                "https://api.telegram.org/bottest-token/sendMessage",
                json={"ok": True, "result": {"message_id": 7}},
            )
            client = TelegramClient(config)
            result = client.send_rich_message(
                config.chat_id,
                "*Deal* alert",
                reply_markup=keyboard,
            )
            assert result["message_id"] == 7
            payload = m.request_history[0].json()
            assert payload["parse_mode"] == "MarkdownV2"
            assert payload["reply_markup"] == keyboard

    def test_edit_message_text(self, config: TelegramConfig):
        with requests_mock.Mocker() as m:
            m.post(
                "https://api.telegram.org/bottest-token/editMessageText",
                json={"ok": True, "result": {"message_id": 9, "text": "updated"}},
            )
            client = TelegramClient(config)
            result = client.edit_message_text(config.chat_id, 9, "updated", parse_mode="HTML")
            payload = m.request_history[0].json()
            assert payload["message_id"] == 9
            assert payload["parse_mode"] == "HTML"
            assert result["text"] == "updated"

    def test_answer_callback_query(self, config: TelegramConfig):
        with requests_mock.Mocker() as m:
            m.post(
                "https://api.telegram.org/bottest-token/answerCallbackQuery",
                json={"ok": True, "result": True},
            )
            client = TelegramClient(config)
            result = client.answer_callback_query("q1", text="Ack", show_alert=True)
            assert result is True
            payload = m.request_history[0].json()
            assert payload["callback_query_id"] == "q1"
            assert payload["text"] == "Ack"
            assert payload["show_alert"] is True

    def test_set_webhook(self, config: TelegramConfig):
        with requests_mock.Mocker() as m:
            m.post(
                "https://api.telegram.org/bottest-token/setWebhook",
                json={"ok": True, "result": True},
            )
            client = TelegramClient(config)
            result = client.set_webhook(
                "https://example.com/hook",
                secret_token="secret",
                drop_pending_updates=True,
            )
            assert result is True
            payload = m.request_history[0].json()
            assert payload["url"] == "https://example.com/hook"
            assert payload["secret_token"] == "secret"
            assert payload["drop_pending_updates"] is True

    def test_delete_webhook(self, config: TelegramConfig):
        with requests_mock.Mocker() as m:
            m.post(
                "https://api.telegram.org/bottest-token/deleteWebhook",
                json={"ok": True, "result": True},
            )
            client = TelegramClient(config)
            result = client.delete_webhook(drop_pending_updates=True)
            assert result is True

    def test_api_error_uses_description(self, config: TelegramConfig):
        with requests_mock.Mocker() as m:
            m.post(
                "https://api.telegram.org/bottest-token/sendMessage",
                json={"ok": False, "description": "Bad Request: chat not found"},
                status_code=400,
            )
            client = TelegramClient(config)
            with pytest.raises(TelegramAPIError, match="chat not found"):
                client.send_message(config.chat_id, "hello")

    def test_api_error_on_invalid_json(self, config: TelegramConfig):
        with requests_mock.Mocker() as m:
            m.get(
                "https://api.telegram.org/bottest-token/getMe",
                text="not json",
                status_code=200,
            )
            client = TelegramClient(config)
            with pytest.raises(TelegramAPIError, match="Invalid JSON response"):
                client.get_me()

    def test_api_error_on_network_failure(self, config: TelegramConfig):
        with requests_mock.Mocker() as m:
            m.get(
                "https://api.telegram.org/bottest-token/getMe",
                exc=requests.exceptions.ConnectionError,
            )
            client = TelegramClient(config)
            with pytest.raises(TelegramAPIError, match="request failed"):
                client.get_me()
