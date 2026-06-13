# Telegram / Mira Bot Setup

This document explains how to configure and run the Telegram bot integration for
Texas Home Outlet (THO) deal alerts and approvals.

## What it does

The integration lives under `services.telegram` and provides:

- **Outbound deal alerts** — send deal summaries to a Telegram chat when deals
  change status or partner webhooks arrive.
- **Approval requests** — render an inline keyboard with **Approve / Reject /
  View Deal** buttons for manager sign-off.
- **Inbound webhook handler** — receive Telegram callback queries at
  `POST /api/v1/telegram/webhook` and dispatch them to the approval handler.

## Required environment variables

Add the following to your `.env` file (see `.env.example`):

```bash
# Telegram Bot (Mira)
TELEGRAM_ENABLED=0                       # set to 1/true/yes to enable outbound messages
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...     # BotFather-issued token
TELEGRAM_CHAT_ID=-1001234567890          # default destination chat or user id
TELEGRAM_WEBHOOK_URL=https://tho.example.com/api/v1/telegram/webhook
TELEGRAM_WEBHOOK_SECRET=random-secret    # sent by Telegram as X-Telegram-Bot-Api-Secret-Token
```

> **Security:** never commit real tokens.  Only edit `.env.example` with empty
> placeholders.

## Wiring the router into the FastAPI app

`services.telegram.webhook.telegram_router` is a standard FastAPI `APIRouter`.
It is already mounted in `main.py`:

```python
from services.telegram.webhook import telegram_router

app.include_router(telegram_router)
```

The router defines the full path `/api/v1/telegram/webhook`, so do not add an
additional prefix.

## Registering the webhook with Telegram

Use the `TelegramClient` helper from a one-off script or admin endpoint:

```python
from services.telegram.config import TelegramConfig
from services.telegram.client import TelegramClient

cfg = TelegramConfig.from_env()
client = TelegramClient(cfg)
client.set_webhook(url=cfg.webhook_url, secret_token=cfg.webhook_secret)
```

To remove the webhook and switch back to polling:

```python
client.delete_webhook()
# or equivalently
client.set_webhook(url="")
```

## Sending a deal approval message

```python
from services.telegram.client import TelegramClient
from services.telegram.config import TelegramConfig
from services.telegram.approval import send_deal_approval_message

cfg = TelegramConfig.from_env()
client = TelegramClient(cfg)
result = send_deal_approval_message(
    client,
    deal,
    base_url="https://tho.example.com",
)
```

`deal` may be a `database.models.Deal`-style object or a plain dict with the
expected fields.  When `base_url` is provided a **View Deal** URL button is
added to the inline keyboard.

## Callback handling

When a manager taps **Approve** or **Reject**, Telegram delivers a callback
query update to `/api/v1/telegram/webhook`.  The router validates the secret
header, passes the update to `services.telegram.approval.handle_callback_query`,
and answers the callback query.

Callback data uses the compact format `approve:<deal_id>` and `reject:<deal_id>`.
Legacy `tho:deal:approve:<deal_id>` payloads are also supported, and `decline`
is normalised to `reject`.

## Fail-closed behavior

- Outbound helpers return `None` when `TELEGRAM_ENABLED` is false or when a
  required config value (`BOT_TOKEN`, `CHAT_ID`) is missing.
- The webhook endpoint rejects requests when a secret is configured and the
  `X-Telegram-Bot-Api-Secret-Token` header does not match.
- The `TelegramClient` raises `TelegramAPIError` on network errors and on
  non-OK Telegram responses.
- `TelegramClient` requires a non-empty `bot_token` at construction time.

## Running the tests

```bash
source .venv/bin/activate
python -m pytest tests/telegram/ -v
```

The tests use mocked HTTP sessions and the FastAPI `TestClient`, so no real
Telegram account or Firestore connection is required.
