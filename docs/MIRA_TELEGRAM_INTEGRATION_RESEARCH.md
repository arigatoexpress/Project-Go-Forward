# THO Mira Bot — Telegram Bot API 10.1+ Integration Research

**Date:** 2026-06-13  
**Scope:** Research/design only — no code changes, no deployments, no credential exposure.  
**Audience:** Engineering hand-off for the next implementation phase of the THO/Mira Telegram notification/approval layer.

---

## 1. Executive Summary

Telegram Bot API **10.1** (June 2026) introduces **Rich Messages** (`sendRichMessage`, `sendRichMessageDraft`, `rich_message` parameter on `editMessageText`). These are the most relevant new primitives for THO because they let a bot send structured, media-capable notifications with headings, tables, collapsible details, lists, and math — all within a single message.

The current THO/Mira stack has a working but limited integration:

* Mira is configured in the “Project Go Forward Bot” Telegram group.
* A `Mira_notify` webhook forwards backend alerts to the group, but the correct group/chat ID still needs to be pinned down.
* `/api/v1/mira/*` endpoints are read-only and there are hard-coded safety fences.
* GitHub and Notion integrations are already live.

**Key finding:** Mira is a *Telegram-native AI agent* built by The Open Platform. It is not a raw Bot API proxy. It almost certainly does **not** expose low-level methods such as `sendRichMessage`, inline-keyboard `callback_data`, or `editMessageText` directly to THO callers. Any design that depends on those methods must either (a) bypass Mira and talk directly to a THO-owned bot token, or (b) be constrained to whatever message-formatting surface Mira exposes.

**Recommendation:**

1. **Short term** — improve the existing plain-text/Markdown notifications sent through Mira with clearer schemas, compact tables, and approval URLs.
2. **Medium term** — run a parallel THO-owned bot on a dedicated token so THO can use Rich Messages, inline keyboards, and webhook callbacks.
3. **Long term** — use the THO-owned bot for interactive approvals and Mira only for conversational/agentic escalations.

---

## 2. Feature Matrix

| Feature | What it does | Relevance to THO | Mira support | Priority |
|---|---|---|---|---|
| **Rich Messages** (`sendRichMessage`, `sendRichMessageDraft`, `editMessageText` w/ `rich_message`) | Structured messages: paragraphs, headings, lists, tables, details, media, math. Drafts stream partial content as a 30-second ephemeral preview and must be finalized with `sendRichMessage`. | Very high. Could replace walls of text with compact deal-status cards, approval summaries, and audit tables. | **Unknown / unlikely.** Mira is an agent, not a raw API proxy. No public docs expose `sendRichMessage`. | High (for THO-owned bot) |
| **Classic `sendMessage` + `parse_mode`** (`HTML`, `MarkdownV2`, `Markdown`) | Plain text messages with formatting entities. Still the safest, best-supported path. | High. Works today through any Bot API wrapper or agent. | **Yes.** Mira can send formatted text replies. | High |
| **Inline keyboards + `callback_query`** | Attach clickable buttons (`callback_data`, URL) to a message; receive `callback_query` updates; edit the message/keyboard in place. | Very high. Enables Approve / Reject / Dry-Run buttons without leaving Telegram. | **Unknown.** Mira may expose buttons in some flows, but THO cannot rely on raw callback routing through Mira. | High (for THO-owned bot) |
| **`sendMessageDraft`** (Bot API 9.3/9.5/10.x) | Stream partial text to a chat with animated typing UX. Finalize with `sendMessage`. Empty text allowed as of 10.0. | Medium. Useful for long LLM-generated deal summaries; less important for short alerts. | **Unknown.** | Medium |
| **Message reactions** (`setMessageReaction`, `message_reaction` updates) | Lightweight 👍/👎 voting on a notification. Bot must be admin and subscribe to `message_reaction` in `allowed_updates`. | Medium. Faster than buttons for simple “ack/nack”. | **Unknown.** | Medium |
| **Polls (`sendPoll`)** | Native multi-option polls, now with media options, explanations, quizzes, `members_only`, etc. | Medium. Good for team decisions (e.g., “approve campaign A/B/C?”). | **Unknown.** | Low-Medium |
| **Checklists (`sendChecklist`, `editMessageChecklist`)** | Interactive task lists inside a message. | Low for THO. Docs state “on behalf of a connected business account,” making it inapplicable to a normal group. | No (business-account only) | Low |
| **Forum topics (`createForumTopic`, `message_thread_id`)** | Organize notifications by topic inside a supergroup. | Medium-High. Could split alerts by subsystem (deals, docs, infra). Requires the supergroup to have Topics enabled and the bot to have `can_manage_topics`. | **Unknown.** | Medium |
| **Guest Mode (`answerGuestQuery`)** | Reply to @mentions in chats the bot is **not** a member of. | None for THO. The bot is already a member of the internal group. | N/A | N/A |
| **Managed Bots / Bot-to-Bot messaging** | Parent bot spawns child bots or sends messages to other bots. | Low. Interesting for future multi-agent orchestration, but overkill for current scope. | Unknown | Low |
| **Paid media / Stars** | Monetized content. | None. Internal ops tool. | N/A | N/A |
| **Live Photos (`sendLivePhoto`)** | Photo + short video hybrid. | Low. Nice for marketing previews, not core ops. | Unknown | Low |
| **Webhooks (`setWebhook` + `secret_token`)** | HTTPS push updates. `X-Telegram-Bot-Api-Secret-Token` header verifies Telegram origin. | High. Required for interactive callbacks (buttons, reactions, polls). | Unknown whether Mira forwards raw updates. | High (for THO-owned bot) |

### Rich Message limits (from core.telegram.org/bots/api)

* Up to **32,768 UTF-8 characters** in rich message text.
* Up to **500 blocks** total, including nested blocks, list items, table rows, quote blocks, and details blocks.
* Up to **16 levels** of nested formatting/blocks.
* Up to **50 media attachments** total (photos, videos, audio).
* Up to **20 columns** in a table.
* `InputRichMessage` requires **exactly one** of `html` or `markdown`.

---

## 3. Message Samples

### 3.1 Plain-text fallback (works through Mira today)

```
🚨 DEAL REQUIRES APPROVAL

Deal: 2026-06-13-A123
Customer: John Doe
Amount: $45,000
Status: PENDING_APPROVAL
Risk: Medium

Actions:
• Approve: https://tho.sapphirealpha.xyz/approve?id=2026-06-13-A123&t=token
• Reject:  https://tho.sapphirealpha.xyz/reject?id=2026-06-13-A123&t=token
• Dry-run: https://tho.sapphirealpha.xyz/dryrun?id=2026-06-13-A123&t=token

Reply APPROVE, REJECT, or DRYRUN to act.
```

### 3.2 HTML/Markdown formatted message (works through any Bot API wrapper)

```html
<b>🚨 Deal Requires Approval</b>

<b>Deal:</b> <code>2026-06-13-A123</code>
<b>Customer:</b> John Doe
<b>Amount:</b> $45,000
<b>Status:</b> ⏳ PENDING_APPROVAL
<b>Risk:</b> 🟡 Medium

<i>Please review the attached summary and choose an action.</i>
```

### 3.3 Inline keyboard for approval (requires raw Bot API / THO-owned bot)

```json
{
  "chat_id": -1001234567890,
  "text": "🚨 Deal 2026-06-13-A123 needs approval",
  "parse_mode": "HTML",
  "reply_markup": {
    "inline_keyboard": [
      [
        {"text": "✅ Approve", "callback_data": "deal:2026-06-13-A123:approve"},
        {"text": "❌ Reject",  "callback_data": "deal:2026-06-13-A123:reject"},
        {"text": "🧪 Dry-Run", "callback_data": "deal:2026-06-13-A123:dryrun"}
      ],
      [
        {"text": "📄 Open in CRM", "url": "https://tho.sapphirealpha.xyz/deals/2026-06-13-A123"}
      ]
    ]
  }
}
```

### 3.4 Rich Message (Bot API 10.1 — THO-owned bot only)

```json
{
  "chat_id": -1001234567890,
  "rich_message": {
    "markdown": "# 🚨 Deal Requires Approval\n\n| Field | Value |\n|---|---|\n| **Deal** | `2026-06-13-A123` |\n| **Customer** | John Doe |\n| **Amount** | $45,000 |\n| **Status** | ⏳ PENDING_APPROVAL |\n| **Risk** | 🟡 Medium |\n\n> Please review the attached summary before acting.\n\n<details>\n<summary>Why am I seeing this?</summary>\nThis deal exceeded the auto-approval threshold of $40,000.\n</details>\n"
  },
  "reply_markup": {
    "inline_keyboard": [[
      {"text": "✅ Approve", "callback_data": "deal:2026-06-13-A123:approve"},
      {"text": "❌ Reject",  "callback_data": "deal:2026-06-13-A123:reject"},
      {"text": "🧪 Dry-Run", "callback_data": "deal:2026-06-13-A123:dryrun"}
    ]]
  }
}
```

### 3.5 Streaming draft for long LLM output

```json
{
  "chat_id": 123456789,
  "draft_id": 987654321,
  "text": "Drafting executive summary for deal 2026-06-13-A123..."
}
```

> Call `sendMessageDraft` repeatedly, then finalize with `sendMessage` (text) or `sendRichMessage` (rich content).

---

## 4. Ideal Approval Flow

This flow assumes a **THO-owned bot** with webhook access so callbacks can be received directly.

```
┌──────────────┐
│ THO Backend  │
│ (deal/alert) │
└──────┬───────┘
       │ 1. POST /telegram/notify with payload
       ▼
┌─────────────────────────┐
│ THO Telegram service    │
│ (FastAPI / Cloud Run)   │
└──────┬──────────────────┘
       │ 2. sendMessage/sendRichMessage + InlineKeyboardMarkup
       ▼
┌─────────────────────────┐
│ Internal Telegram group │
│ (Project Go Forward Bot)│
└──────┬──────────────────┘
       │ 3. User taps Approve/Reject/Dry-Run
       ▼
┌─────────────────────────┐
│ Telegram → setWebhook   │
│ X-Telegram-Bot-Api-     │
│ Secret-Token header     │
└──────┬──────────────────┘
       │ 4. POST callback_query
       ▼
┌─────────────────────────┐
│ THO Telegram service    │
│ validates token, parses │
│ callback_data           │
└──────┬──────────────────┘
       │ 5a. answerCallbackQuery (toast)
       │ 5b. editMessageText/editMessageReplyMarkup
       │     to show "Approved by @user at 20:30 UTC"
       ▼
┌─────────────────────────┐
│ THO Backend             │
│ executes/rejects action │
└─────────────────────────┘
```

### Approval `callback_data` convention

Use a colon-delimited, URL-safe format. `callback_data` is limited to **1-64 bytes**.

```
deal:<id>:<action>
```

Examples:

* `deal:A123:approve`
* `deal:A123:reject`
* `deal:A123:dryrun`

If more metadata is needed, store a short-lived token server-side and put only the token in `callback_data`:

```
approve:<short-token>
```

### In-place update after action

```json
{
  "chat_id": -1001234567890,
  "message_id": 42,
  "text": "✅ Deal 2026-06-13-A123 approved by @jane_doe at 20:30 UTC",
  "reply_markup": {
    "inline_keyboard": [
      [{"text": "📄 View in CRM", "url": "https://tho.sapphirealpha.xyz/deals/2026-06-13-A123"}]
    ]
  }
}
```

### Webhook security checklist

* Use `setWebhook` with a random `secret_token` (1-256 chars, `A-Z a-z 0-9 _ -`).
* Reject any incoming request whose `X-Telegram-Bot-Api-Secret-Token` header does not match.
* Use HTTPS only; Cloud Run already terminates TLS.
* Subscribe only to needed `allowed_updates`: `["callback_query", "message", "message_reaction"]`.

---

## 5. Gap Analysis: Current vs. Ideal

| Area | Current state | Ideal state | Gap |
|---|---|---|---|
| **Delivery channel** | `Mira_notify` webhook forwards alerts to the Telegram group. | THO-owned bot sends structured messages directly. | Mira may not expose Rich Messages, inline keyboards, or callbacks. |
| **Message formatting** | Likely plain text or basic Markdown. | Rich Messages for complex cards; MarkdownV2/HTML fallback. | Need to either move off Mira or accept Mira’s formatting limits. |
| **Interactivity** | Approval links in the message text. | Inline buttons with `callback_query` updates. | Callbacks require a THO-controlled token/webhook. |
| **Chat targeting** | Group ID may not be correctly configured. | Verified `chat_id` (and optional `message_thread_id`). | Need to resolve and persist the real group ID. |
| **Update handling** | Webhook probably pushes only outbound notifications. | Two-way: notifications + inbound callbacks/reactions. | Need a public webhook route and secret-token validation. |
| **Safety / auth** | Hard-coded fences exist. | Token-backed, group-scoped actions with audit logging. | Approval callbacks must be idempotent and signed/timed. |
| **Error handling** | Unknown. | Retry with exponential backoff, alert on permanent failure. | Need delivery-status logging. |
| **Group permissions** | Bot may not be admin. | Bot is admin (for reactions, deletions, topic management). | Update group settings if using reactions/topics. |

### What Mira is good for

* Conversational/agentic responses inside Telegram.
* Summaries, memory, and integration orchestration (GitHub/Notion already live).
* Natural-language follow-ups from users.

### What Mira is probably not good for

* Raw Bot API message rendering.
* Stateful inline-keyboard workflows owned by THO.
* Reliable, auditable approval callbacks.

---

## 6. Roadmap

### Phase 0 — Stabilize current Mira channel (1-2 days)

* Pin down the exact Telegram group/chat ID.
* Add structured plain-text templates for the most common alerts.
* Add approval URLs with short-lived signed tokens.
* Add smoke tests for the `/api/v1/mira/notify` path.

### Phase 1 — THO-owned bot for notifications (1 week)

* Create a dedicated THO bot via @BotFather; keep the token in Secret Manager.
* Add a new service module (`services/telegram/`) with:
  * `send_message`, `send_rich_message`, `edit_message_text`, `answer_callback_query`.
  * Webhook handler with `secret_token` validation.
* Migrate non-interactive alerts to the THO bot using HTML/Markdown.
* Keep Mira for conversational/agentic use cases.

### Phase 2 — Interactive approvals (1 week)

* Build the inline-keyboard approval flow described in Section 4.
* Implement `callback_query` parser and `callback_data` token store.
* Update messages in place after an action.
* Add audit logging to Firestore (`telegram_callback_logs`).

### Phase 3 — Rich Messages & topics (2-3 weeks)

* Adopt `sendRichMessage` for deal-summary cards, audit tables, and collapsible details.
* Optionally enable forum topics in the supergroup and route alerts by subsystem.
* Evaluate `sendMessageDraft` for LLM-generated summaries.

### Phase 4 — Advanced interactions (future)

* Message reactions for lightweight voting.
* Native polls for multi-option decisions.
* Managed-bot or bot-to-bot patterns if multi-agent orchestration is needed.

---

## 7. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Mira cannot render Rich Messages or inline keyboards** | High | High | Route structured notifications through a THO-owned bot; keep Mira for chat only. |
| **Group ID changes or migration to supergroup** | Medium | High | Store `chat_id` and `message_thread_id` in config/Secret Manager; validate on startup. |
| **Callback replay / spoofing** | Medium | High | Validate `secret_token`; use short-lived signed `callback_data` tokens; idempotent handlers. |
| **Rate limiting (30 msg/sec global per bot)** | Medium | Medium | Add a small queue/batcher; avoid bursts; use `disable_notification` where appropriate. |
| **Bot loses admin rights / privacy mode** | Low | Medium | Monitor `my_chat_member` updates; alert if status changes. |
| **Draft/ Rich Message client support gaps** | Medium | Low-Medium | Always provide a plain-text or HTML fallback. |
| **Vendor lock-in to Mira/TOP** | Medium | Medium | Abstract the notification layer behind an interface so Mira and a THO bot are swappable channels. |
| **PII in Telegram messages** | Medium | High | Strip or mask PII before sending; avoid sending full customer records; route sensitive actions to the CRM, not chat. |

---

## References

* Telegram Bot API official docs: https://core.telegram.org/bots/api
* Bot API 10.1 changelog (Rich Messages, Guest Mode, etc.): https://core.telegram.org/bots/api#june-11-2026
* `@gramio/types` Bot API 10.1 generated types: https://jsr.io/@gramio/types/doc
* Mira / The Open Platform: https://top.co/insights/mira-ai-assistant-telegram, https://mira.tg/blog/how-to-use-ai-in-telegram-group-chats
* `sendMessageDraft` streaming background (OpenClaw issue): https://github.com/openclaw/openclaw/issues/32041
* Telegram Bot API webhooks guide: https://core.telegram.org/bots/webhooks
