# Data retention purge (R13)

The owner has not approved a retention policy. `data_retention.enabled` in
`config.yaml` is `false`. Every `retention_days` value is marked
**PROPOSED, owner to confirm**. While that flag is false, the purge reads
nothing and deletes nothing.

This note does not add a Cloud Run job, scheduler, Firestore TTL policy, index,
IAM change, or GitHub workflow. Run it by hand, against a non-production
project, after the decisions below are made.

## How to run

Shipped config (disabled). This does not connect to Firestore:

```bash
python -m retention_purge
python -m retention_purge --execute
```

After the owner confirms the periods and a reviewed change sets `enabled: true`,
a dry-run still deletes nothing. It prints counts and up to five document ids
per collection:

```bash
GOOGLE_CLOUD_PROJECT=<non-production-project> python -m retention_purge
```

Deleting also requires `--execute`. Each collection deletes at most
`batch_size` documents per run (default 100, hard cap 500), oldest expired
documents first. Run again to continue. A failed read deletes nothing in that
run. The report never includes document bodies.

```bash
GOOGLE_CLOUD_PROJECT=<non-production-project> python -m retention_purge --execute
```

The tool refuses `GOOGLE_CLOUD_PROJECT=tho-ai-agent` unless
`THO_RETENTION_PURGE_ALLOW_PRODUCTION=1`. Do not set that until the owner has
confirmed every period and a backup. It does not default to the production
project when the env var is unset. `retention_days` counts 24-hour days.
A document is expired only when its timestamp is strictly older than
`now - retention_days`. Missing or malformed timestamps are skipped, not deleted.

## Proposed periods

| Collection | Age field | Proposed days | What it holds |
|---|---|---|---|
| `chat_sessions` | `updated_at` | 90 | Chat transcripts |
| `conversation_contexts` | `updated_at` | 90 | Session preferences and budgets |
| `leads` | `created_at` | 730 | Lead and contact-form submissions |
| `appointments` | `created_at` | 730 | Showroom bookings (name, phone, email) |
| `feedback` | `created_at` | 730 | Customer feedback documents |
| `tho_admin_login_codes` | `created_at` (epoch seconds) | 7 | Leftover email sign-in codes |
| `google_ads_owner_step_up_nonces` | `issued_at` | 7 | Leftover passkey step-up nonces |
| `user_activity_log` | `timestamp` | 90 | IP address and user agent |
| `analytics_events` | `created_at` | 180 | Privacy-bounded analytics |
| `email_log` | `sent_at` | 365 | Sent-mail log, including recipient |
| `email_reply_drafts` | `created_at` | 90 | Inbound mail excerpts and drafts |
| `nurture_log` | `sent_at` | 365 | Re-engagement send log, including email |

Chat and conversation rows age from the last update. Leads, appointments,
feedback, and drafts age from creation, so a later edit does not extend them.
Login codes and step-up nonces expire in minutes in the live auth flow; seven
days only clears leftovers.

WebAuthn ceremony challenges are signed cookies (`tho_passkey_login`,
`tho_passkey_register`, and the Google Ads step-up cookie), not Firestore
documents. Deleting a step-up nonce does not delete its `verified_evidence`
subdocuments. Firestore does not cascade, and this purge does not walk
subcollections.

## Not covered

Do not add these here without a separate owner decision. Several have a longer
legal or operational life than a chat transcript (manufactured-home sale
records are often kept for years):

- `customers`, `deals`, `deal_notes`, `inventory`, `properties`, `sales`,
  `leases`, `tax_payments`, `service_requests`, `esign_requests`, `crm_tasks`
- `tho_admin_credentials` (registered passkeys) and `tho_admin_staff`
- `audit_log` and `activities`
- `google_ads_deployments` and its authority subcollections

The purge allowlist is in `retention_purge.py`. A config entry outside that
list is reported as rejected and is not read or deleted.

## What the owner must decide

1. Confirm or replace every proposed period. They are not an approved policy.
2. Whether converted, contacted, or still-open leads are exempt. This purge
   does not look at lead or appointment status.
3. Whether a later update should keep a lead, appointment, or draft. Today
   those collections age from `created_at`.
4. Whether `verified_evidence` under step-up nonces should be purged later.
5. A separate schedule, if any, for audit logs and business records.
6. When `enabled` may be set true, and whether production may ever be purged.
   That still requires `--execute` and `THO_RETENTION_PURGE_ALLOW_PRODUCTION=1`.
