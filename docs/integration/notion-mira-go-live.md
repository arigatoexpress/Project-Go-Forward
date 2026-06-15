# Notion + Mira Go-Live Runbook

This doc bridges PR #186 (Notion source for the Mira API) and the Mira Telegram/GitHub integration. All secrets live in **Cloud Run environment variables / Secret Manager**; `.env.example` documents the keys.

## 1. Notion source for Mira bridge

### 1.1 Create the Notion internal integration
1. Go to https://www.notion.so/my-integrations and click **New integration**.
2. Name it `THO Mira Bridge` (or any recognizable name).
3. Associate it with the workspace that owns the **Delivery Tracker** and **CS survey** databases.
4. Capabilities: **Read content** only (no user info required).
5. Copy the **Internal Integration Token** (starts with `secret_`).

### 1.2 Share the databases with the integration
1. Open the **Delivery Tracker** DB in Notion → **...** → **Add connections** → select the integration.
2. Open the **CS survey** DB → repeat.
3. Copy each database ID from the URL:
   - `https://www.notion.so/workspace/<DB_ID>?v=...`
   - The DB ID is the 32-character string in the URL.

### 1.3 Set Cloud Run env vars
Set in the Cloud Run service (`project-go-forward`, `tho-ai-agent` project) or in `.env` for local dev:

```bash
NOTION_TOKEN=secret_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
NOTION_DELIVERY_TRACKER_DB_ID=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
NOTION_CS_SURVEY_DB_ID=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
```

### 1.4 Smoke test
```bash
# Get an API key that matches THO_API_KEY_MIRA (or generate one)
curl -H "Authorization: Bearer $THO_API_KEY_MIRA" \
  https://tho.sapphirealpha.xyz/api/v1/mira/installations/recent?limit=5

curl -H "Authorization: Bearer $THO_API_KEY_MIRA" \
  https://tho.sapphirealpha.xyz/api/v1/mira/feedback/recent?limit=5
```

Expected: `200 OK`, JSON with `"source": "notion"` and PII-redacted rows.
If Notion env vars are missing, the response will have `"source": "firestore"`.

---

## 2. Mira Telegram/GitHub go-live

### 2.1 Telegram bot
1. Message @BotFather on Telegram, create a bot, copy the **HTTP API token**.
2. Add the bot to the Mira group chat and send one message so the bot can see the chat.
3. Get the group chat ID:
   - Use `https://api.telegram.org/bot<TOKEN>/getUpdates` and look for `"chat":{"id":-123456789}`.

### 2.2 GitHub webhook
1. In the relevant GitHub repo (or organization), add a webhook:
   - Payload URL: `https://tho.sapphirealpha.xyz/api/github/mira/webhook`
   - Content type: `application/json`
   - Secret: generate a long random string (`GITHUB_WEBHOOK_SECRET`).
   - Events: choose **Issues**, **Pull requests**, and/or **Discussions** as needed.
2. Store `GITHUB_WEBHOOK_SECRET` in Cloud Run / Secret Manager.

### 2.3 Set Cloud Run env vars
```bash
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
MIRA_GROUP_ID=-123456789
GITHUB_WEBHOOK_SECRET=long-random-string
# Optional: if THO should push events back to Mira's outbound webhook
PARTNER_WEBHOOK_URL_MIRA=https://mira.example.com/webhook/tho
PARTNER_WEBHOOK_SIGNING_KEY=another-long-random-string
```

### 2.4 Smoke test
```bash
# Telegram
curl -X POST -H "Content-Type: application/json" \
  -d '{"chat_id":"$MIRA_GROUP_ID","text":"Mira bridge smoke test"}' \
  https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage

# GitHub webhook (send a ping from GitHub, or use the webhook delivery history)
```

---

## 3. Local development
Copy `.env.example` to `.env` and fill in the values you need. The Notion/Mira features are **env-gated**; leaving values blank simply disables those sources.
