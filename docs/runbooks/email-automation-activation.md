# Email Automation — Activation Runbook

How to safely activate the inbound-email automation pipeline. The code ships
**inert**: merged code does nothing until every gate below is opened in order,
by a human, in production. Do not skip steps; each layer independently no-ops
when unconfigured.

Pipeline shape (all code merged, lanes 1–6):

```
Resend inbound (*.resend.app; root MX = Turbify, NEVER touched)
  └─ POST /api/email/inbound        (svix HMAC verify + fail-closed sender allowlist)
       └─ email_triage.classify()   (deterministic, fail-closed → substantive)
            ├─ safe_ack    ──► fixed ack template via the single send chokepoint
            └─ substantive ──► pending draft (Firestore email_reply_drafts)
                                 └─ Telegram approve/reject card (THO-own bot)
                                      ├─ ✅ approve → send via chokepoint + audit
                                      ├─ ❌ reject  → no send
                                      └─ timeout    → stays pending; NEVER auto-sends
```

## Prerequisites (before any activation)

- Code for lanes 1–6 merged: triage classifier, draft store, send chokepoint,
  Telegram gate, inbound wiring, admin visibility (this runbook's lane).
- Admin review surface: CRM → **Reply Drafts** tab (read-only) backed by
  `GET /api/admin/email-reply-drafts`. Use it to confirm drafts appear as
  expected after step 3.
- Familiarity with `docs/RUNBOOK.md` §3 (secrets) and the feature-flag
  mechanism (`tools/feature_flags.py`: env `FF_<NAME>` overrides
  `config.yaml feature_flags:`; flags default OFF).

## Activation order — each step is its own switch

Open the gates **in this order**. Verify each step before moving on.

### 1. Inbound webhook secret (already gated from PR #214 — unchanged)

- `RESEND_WEBHOOK_SECRET` set in Cloud Run prod env; Resend inbound domain
  (`*.resend.app`) configured.
- Verify: unsigned/invalid-signature POST to `/api/email/inbound` → 401.
  With the secret absent the route answers `{"status":"disabled"}`.
- **Root MX stays on Turbify — never touch it.**

### 2. Sender allowlist (fail-closed today, stays fail-closed)

- Populate `INBOUND_EMAIL_ALLOWLIST` with the exact sender addresses the
  pipeline may process.
- Verify: email from a non-allowlisted sender is dropped before triage
  (no lead, no draft, no ack).

### 3. Draft pipeline — `FF_EMAIL_DRAFT_PIPELINE=1`

- Substantive inbound email now creates **pending review drafts**.
  Still no sends, no Telegram.
- Verify: send a pricing/warranty-style email from an allowlisted address →
  a draft with `status=pending` appears in CRM → Reply Drafts (read-only)
  and in Firestore `email_reply_drafts`. Nothing is sent.

### 4. Telegram gate — `THO_TG_BOT_TOKEN` + `THO_TG_CHAT_ID` + `FF_EMAIL_TG_GATE=1`

- Create the THO-own Telegram bot (BotFather) and a private review chat; put
  token/chat-id in Cloud Run prod env. Set `THO_TG_WEBHOOK_SECRET` and point
  the bot webhook at `POST /api/telegram/webhook`.
- Approve cards now flow. An approved draft attempts a send through the
  single chokepoint — which still requires `RESEND_API_KEY` and
  `FF_EMAIL_REPLY_SEND` (both absent-safe / OFF by default), so nothing
  actually sends until those exist too.
- Verify: callbacks from any other chat id are rejected; replaying a decided
  card is a no-op (`already_decided`); decisions are audit-logged.

### 5. Auto-ack — `FF_EMAIL_AUTO_ACK=1` (LAST; the only unsupervised send)

- The **only** outward send without a human approve: the fixed ack template
  ("we received your message…"), zero LLM content, no body echo beyond the
  subject line, daily-capped by the chokepoint.
- Verify with the golden-set expectations in `tests/test_email_triage.py`:
  money/legal/complaint/order/attachment/ambiguous/non-English/injection
  emails must NEVER take the ack path.

## Hard rules (never auto; always gated on Ari)

- Resend domain verification + inbound `*.resend.app` receiving + webhook
  secret creation/wiring.
- `RESEND_API_KEY` in Cloud Run prod env (any outward-send capability at all).
- Creating the THO Telegram bot + placing its token/chat-id in prod env.
- Flipping each feature flag in prod — especially `FF_EMAIL_AUTO_ACK` — and
  any future safe-subset expansion beyond ack (each expansion = new flag +
  new golden tests + explicit sign-off).
- Prod traffic cutover after merge: merges deploy at 0% traffic;
  `update-traffic` is a gated manual step.

## Rollback

Every gate is independently reversible with zero code change:

- `FF_EMAIL_AUTO_ACK=0` — stops the only unsupervised send.
- `FF_EMAIL_TG_GATE=0` — stops cards; drafts keep accumulating for review.
- `FF_EMAIL_DRAFT_PIPELINE=0` — pipeline returns to classify-and-log only.
- Removing `RESEND_WEBHOOK_SECRET` disables the whole inbound route.

Pipeline exceptions are swallowed at the webhook boundary, so a failure in
any layer can never break lead capture.
