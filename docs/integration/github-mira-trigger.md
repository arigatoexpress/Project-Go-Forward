# GitHub → Mira Trigger Bridge

**Workstream:** `github-mira-trigger`  
**Status:** Implemented / ready for env wiring  
**Cutover PR:** #156

## What it does

Receives GitHub webhook events and forwards cutover-relevant events to the Mira
AI agent through two channels:

1. **Telegram** — posts a Markdown alert to the Mira group
   (`MIRA_GROUP_ID`, fallback `KIMI_RELAY_CHAT_ID`).
2. **HMAC-signed partner webhook** — dispatches an outbound event via
   `tools/partner_webhooks.dispatch_partner_event` to any configured
   `PARTNER_WEBHOOK_URL_MIRA`.

Every handled event is also logged to Firestore `activities/` for audit and
replay.

## Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| `POST` | `/api/github/mira/webhook` | GitHub `X-Hub-Signature-256` HMAC | Receive GitHub events |
| `GET`  | `/api/v1/github/mira/status` | Partner API key (`THO_API_KEY_*`) | Config/status check |

## Notable events

- `pull_request` (PR #156 gets special “CUTOVER PR” formatting)
- `issues`
- `workflow_run` / `workflow_job`
- `deployment_status`
- `release`
- `push` (only `refs/heads/main` to reduce noise)

Noisy actions (`labeled`, `assigned`, etc.) and non-main pushes are ignored.

## Required environment variables

```bash
# Telegram
TELEGRAM_BOT_TOKEN=         # THO bot token
KIMI_RELAY_CHAT_ID=         # legacy fallback chat/group
MIRA_GROUP_ID=              # dedicated Mira group (preferred)

# GitHub webhook validation
GITHUB_WEBHOOK_SECRET=      # shared secret from GitHub webhook settings

# Optional outbound partner webhook
PARTNER_WEBHOOK_URL_MIRA=   # Mira bridge API URL
PARTNER_WEBHOOK_SIGNING_KEY=# shared HMAC secret for X-THO-Signature
```

## GitHub webhook setup

In the GitHub repo settings, add a webhook:

- **Payload URL:** `https://tho.sapphirealpha.xyz/api/github/mira/webhook`
- **Content type:** `application/json`
- **Secret:** the value of `GITHUB_WEBHOOK_SECRET`
- **Events:** select the event types listed above (or “Send me everything”)

## Testing

```bash
python -m pytest tests/test_github_mira_trigger.py -v
python -m pytest tests/test_partner_webhooks.py -v
```

## Files changed

- `github_mira_trigger.py` — new trigger bridge
- `mira_notify.py` — Telegram notify helper, refactored to expose
  `send_mira_notification()` for reuse by the trigger
- `main.py` — mounts the trigger routers
- `.env.example` — documents required env vars
- `tests/test_github_mira_trigger.py` — unit tests for the bridge
