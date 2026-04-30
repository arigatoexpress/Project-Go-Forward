# Gmail Inbound Sync — Workspace Setup Checklist

**Audience:** Mark / Celeste (Workspace admin) plus the on-call engineer who flips the env flag.

**Estimated time:** ~30 minutes of Workspace admin work, plus ~10 minutes of Cloud Run env wiring.

**Status:** This integration ships **gated off** by default. The application code is inert until `GMAIL_INBOUND_ENABLED=true` is set in Cloud Run. Production behavior is unchanged before activation.

## Why this exists

Outbound email already works (Resend handles every customer-facing send from `email_service.py`). Inbound replies sent to addresses on `texashomeoutlet.com` are currently invisible to the CRM — if a customer replies to an appointment confirmation or a deal status email, no one inside the THO app sees it.

This integration polls a Workspace mailbox (default: `ops@texashomeoutlet.com`), parses each new message, and (in a later PR) routes it into the CRM timeline. This first PR ships the scaffold only — module, tests, ops doc — and is gated off until the steps below are complete.

## Architecture summary

- **Auth:** Service account in `tho-ai-agent` with **domain-wide delegation** to a Workspace user.
- **Scope:** `https://www.googleapis.com/auth/gmail.modify` (read messages, apply labels). We never send mail through this path.
- **Secret:** Service account JSON lives in Secret Manager as `gmail-service-account-json` and is mounted into Cloud Run.
- **Polling:** No scheduled task ships in this PR. A follow-up PR will add the polling loop after this checklist is signed off.
- **Idempotency:** Each processed message gets a Gmail label `tho-processed`. Subsequent polls skip labeled messages.

## Checklist

### 1. Create the service account

- [ ] In the Google Cloud console for project `tho-ai-agent`, open IAM & Admin -> Service Accounts.
- [ ] Create a new service account named `gmail-inbound-sync` (or similar). No project-level roles are required at this step; Gmail access is granted via Workspace, not IAM.
- [ ] On the new service account, **enable domain-wide delegation** (Show Domain-wide Delegation -> Enable).
- [ ] Note the service account's **Client ID** (a long numeric string). You will need it in step 3.
- [ ] Create a JSON key for the service account. Download it and treat it like a password — do NOT commit it to git, do NOT email it, do NOT paste it into Slack or Notion.

### 2. Enable the Gmail API

- [ ] In the same project, open APIs & Services -> Library.
- [ ] Search for "Gmail API" and click Enable.

### 3. Grant domain-wide delegation in Workspace Admin Console

This step requires the Workspace super-admin (Mark or Celeste). It cannot be done from the Cloud console — it must be done from `admin.google.com`.

- [ ] Go to `admin.google.com` -> Security -> Access and data control -> API controls -> Manage Domain-Wide Delegation.
- [ ] Click "Add new".
- [ ] Paste the service account **Client ID** from step 1.
- [ ] In the OAuth scopes field, paste exactly: `https://www.googleapis.com/auth/gmail.modify`
- [ ] Click Authorize.

### 4. Choose / confirm the delegated mailbox

- [ ] Decide which Workspace user the service account will impersonate. The default is `ops@texashomeoutlet.com`. This mailbox will be the one the integration reads from.
- [ ] Confirm that customer-facing addresses (replies to appointment confirmations, etc.) actually land in this mailbox. If the production reply path is a different address (group alias, individual rep, etc.), use that one instead — and update `GMAIL_DELEGATED_USER` accordingly in step 6.

### 5. Store the JSON key in Secret Manager

- [ ] In the Cloud console for `tho-ai-agent`, open Security -> Secret Manager.
- [ ] Create a new secret named `gmail-service-account-json`.
- [ ] Paste the contents of the JSON key file (downloaded in step 1) as the secret value.
- [ ] Grant the Cloud Run service account (`project-go-forward@tho-ai-agent.iam.gserviceaccount.com` or the runtime SA) the **Secret Manager Secret Accessor** role on this secret.
- [ ] Delete the local JSON key file from your machine. Secret Manager is now the only copy.

### 6. Wire Cloud Run env vars

- [ ] In Cloud Run -> `project-go-forward` -> Edit & Deploy New Revision -> Variables & Secrets, add:
  - Env var `GMAIL_SERVICE_ACCOUNT_JSON` = path where the secret is mounted (e.g. `/secrets/gmail-service-account-json/latest`)
  - Mount the `gmail-service-account-json` secret as a **file** at the path above (Variables & Secrets -> Reference a Secret -> Mount as volume).
  - Env var `GMAIL_DELEGATED_USER` = the address from step 4 (e.g. `ops@texashomeoutlet.com`)
  - Env var `GMAIL_INBOUND_LABEL` = `inbox` (or whatever Gmail label scopes the inbound queue)
  - Env var `GMAIL_INBOUND_ENABLED` = `false` (keep the flag OFF for now — we are only confirming readiness)
- [ ] Deploy the new revision.

### 7. Smoke test the readiness probe

- [ ] As an authenticated admin (PIN-validated session token), call:
  ```
  GET /api/admin/email/inbound-status
  ```
- [ ] Confirm the response shows `"configured": true` and `"enabled": false`.
- [ ] If `configured` is false, double-check that `GMAIL_SERVICE_ACCOUNT_JSON` and `GMAIL_DELEGATED_USER` are both set in the live Cloud Run revision.

### 8. Activate

- [ ] Once steps 1-7 are green, redeploy with `GMAIL_INBOUND_ENABLED=true`.
- [ ] The `/api/admin/email/inbound-status` endpoint should now show `"enabled": true`.
- [ ] **At this point the scaffold is live but no caller polls it yet.** A follow-up PR will land the polling loop and CRM-routing logic. That PR should reference this doc and confirm steps 1-8 are done before merging.

## Rollback

If anything misbehaves:

- Set `GMAIL_INBOUND_ENABLED=false` in Cloud Run and redeploy. The application becomes inert again.
- For a stronger kill, revoke the domain-wide delegation entry in step 3. The service account loses Gmail access immediately, regardless of the env flag.
- For nuclear rollback, delete the secret in step 5. Cloud Run will fail to mount it on the next revision; redeploy with the previous revision pinned.

## Security notes

- The chosen scope is `gmail.modify`, NOT `gmail.full` or `gmail.send`. The integration cannot send mail; outbound continues to flow through Resend.
- The service account is per-environment. Do not reuse the same key in any other project, repo, or laptop.
- Domain-wide delegation grants the service account access to **any user in the Workspace** within the granted scopes. Treat this like a privileged credential and limit who can edit the entry in Workspace Admin.
- The JSON key never lives on disk in this repo. It only exists in Secret Manager and is mounted read-only at runtime.

## Operational expectations after activation

- Polling cadence will be set in the follow-up PR. Initial recommendation: every 5 minutes via Cloud Scheduler.
- Each polled message gets a `tho-processed` Gmail label, so re-runs are idempotent.
- Failures and last-poll timestamps will surface on `/api/admin/email/inbound-status` once the loop ships.

## Open questions for Mark / Celeste

1. Confirm `ops@texashomeoutlet.com` is the right inbound queue, or supply the correct address.
2. Confirm we can apply a `tho-processed` label inside that mailbox without disrupting any human workflow.
3. Confirm acceptable polling cadence (every 5 min vs. on-demand).
