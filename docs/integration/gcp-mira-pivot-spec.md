# GCP-Native Mira Integration — Design Spec

**Status:** Scoped, not implemented  
**Supersedes:** `docs/integration/notion-bridge.md` (Notion source path)  
**Related:** PR #188 in-app Ops Copilot, `mira_routes.py`, `tools/notion_client.py`

## 1. Goal

Move the Mira bridge (`/api/v1/mira/*`) off the Notion "Delivery Tracker / CS survey" source and onto a GCP-native operational data layer that the in-app **Ops Copilot** can read and write. The existing response schema stays unchanged and PII-redacted; only the `source` field changes from `"notion"` to `"gcp"`.

## 2. Why pivot

- Notion requires manual workspace sharing, brittle column-name mapping, and a third-party token outside GCP IAM.
- PR #188 already added an in-app Ops Copilot that can query the same operational data; keeping that data in Firestore/Cloud SQL lets the copilot mutate it natively.
- Firestore is already the canonical store for customers, deals, inventory, and leads. Installations and feedback should live there too.

## 3. Current state

| Endpoint | Today | Source switch logic |
|----------|-------|---------------------|
| `/api/v1/mira/installations/*` | Notion Delivery Tracker if configured, else Firestore `service_requests` | `notion_client.is_installations_configured()` |
| `/api/v1/mira/feedback/*` | Notion CS survey DB if configured, else Firestore `feedback` | `notion_client.is_feedback_configured()` |
| `/api/v1/mira/leads/*` | Firestore `leads` | direct |
| `/api/v1/mira/appointments/*` | Firestore `appointments` | direct |
| `/api/v1/mira/notify` | Telegram | env-gated |

The Notion path is **additive/env-gated**; removing it does not break current behavior when the env vars are unset.

## 4. Proposed architecture

```
┌─────────────────┐     ┌──────────────────────────────────────┐
│  GitHub / Ops   │────▶│  Cloud Run  project-go-forward       │
│  Copilot UI     │     │  (existing Mira routes)              │
└─────────────────┘     └──────────────┬───────────────────────┘
                                       │
                    ┌──────────────────┼──────────────────┐
                    ▼                  ▼                  ▼
            Firestore          Cloud Pub/Sub          Cloud SQL
            (canonical)        (event ingestion)      (optional mirror
             service_requests   • mira-installations    for complex queries)
             feedback          • mira-feedback
             activities        • github-events
```

### 4.1 Data store — Firestore as canonical

Reuse the existing Firestore collections and enrich their schema to match what Notion currently provides.

**`service_requests` (installations)**
```
status: str               # e.g. NEW, SCHEDULED, COMPLETED, WARRANTY
issue_type: str           # e.g. DELIVERY, SETUP, WARRANTY_REPAIR
is_warranty_claim: bool
warranty_status: str | None
assigned_contractor: str | None
deal_id: str | None
created_at: datetime
updated_at: datetime
source: "gcp" | "import" | "notion"
```

**`feedback` (CS surveys)**
```
rating: int | None        # 1-5
sentiment: str | None     # positive | neutral | negative
source: str | None        # e.g. POST_INSTALL_EMAIL, PHONE_FOLLOWUP
deal_id: str | None
created_at: datetime
updated_at: datetime
```

These shapes are already what `mira_routes.py` expects when it falls back to Firestore.

### 4.2 Source module refactor

Create `tools/gcp_mira_source.py` with the same public interface as `tools/notion_client.py`:

```python
is_installations_configured() -> bool   # always True on GCP
is_feedback_configured() -> bool        # always True on GCP
fetch_installations(limit=1000) -> list[dict]
fetch_feedback(limit=1000) -> list[dict]
```

Change `mira_routes.py` to import `gcp_mira_source` as the preferred source and keep `notion_client` as an opt-in legacy fallback (or remove it in phase 2). The default `source` becomes `"gcp"`.

### 4.3 Event ingestion — Cloud Pub/Sub

For external systems (GitHub, DocuSeal e-sign, Resend events, partner webhooks), add an async Pub/Sub topic so Mira/Ops Copilot gets notified without blocking the HTTP request:

| Topic | Purpose | Producer |
|-------|---------|----------|
| `mira-github-events` | PR/issue/deploy status | `github_mira_trigger.py` (optional async) |
| `mira-installations` | New/updated service request | Ops Copilot, staff UI, scheduled job |
| `mira-feedback` | New CS survey | Resend webhook, Ops Copilot |

Add a `POST /api/v1/mira/pubsub` endpoint (authenticated via Pub/Sub OIDC token) that updates Firestore. This keeps the public API read-only and resilient.

### 4.4 Optional Cloud SQL mirror

If Firestore aggregation becomes expensive for the monitoring check, mirror `service_requests` and `feedback` to a small Cloud SQL (PostgreSQL) instance via a nightly Cloud Scheduler job or CDC. The Mira endpoints would still read Firestore by default; Cloud SQL would be used only for analytics/historical dashboards.

### 4.5 Ops Copilot integration

PR #188 introduced the in-app Ops Copilot. Extend it with:
- `tools/ops_copilot.py` functions `create_service_request`, `update_service_request`, `record_feedback`.
- Firestore rules/audit logging identical to existing CRM writes.
- Admin UI at `/admin/ops` for staff to edit installations/feedback without Notion.

## 5. Env vars

No new secrets are required for the Firestore path (it uses the existing GCP service account). If Pub/Sub is added:

```bash
# Optional — enable async event ingestion
MIRA_PUBSUB_TOPIC_PROJECT=tho-ai-agent
MIRA_PUBSUB_INSTALLATIONS_TOPIC=mira-installations
MIRA_PUBSUB_FEEDBACK_TOPIC=mira-feedback
MIRA_PUBSUB_SUBSCRIPTION=mira-events-pull
```

If Cloud SQL is used later:

```bash
MIRA_CLOUD_SQL_INSTANCE=tho-ai-agent:us-central1:tho-ops
MIRA_CLOUD_SQL_DATABASE=mira_ops
# Password via Secret Manager, mounted as env var
MIRA_CLOUD_SQL_PASSWORD_SECRET=mira-cloud-sql-password:latest
```

## 6. Migration plan

1. **Phase 1 — Stop preferring Notion (safe, no deploy risk):**
   - Add `tools/gcp_mira_source.py`.
   - Modify `mira_routes.py` to prefer Firestore/`gcp_mira_source` and report `"source": "gcp"`.
   - Keep Notion env-gated but lower its precedence to fallback.
   - Tests: extend `tests/test_mira_routes.py` to assert `source == "gcp"` when Notion is unset.

2. **Phase 2 — Seed existing data:**
   - Export current Notion Delivery Tracker / CS survey rows to JSON.
   - Run a one-time idempotent import script `tools/migrate_notion_to_firestore.py`.
   - Verify counts match via `/api/v1/mira/installations/summary` and `/api/v1/mira/feedback/summary`.

3. **Phase 3 — Add async ingestion (optional):**
   - Create Pub/Sub topics and a push subscription to `POST /api/v1/mira/pubsub`.
   - Update `github_mira_trigger.py` to optionally publish instead of sending Telegram directly.

4. **Phase 4 — Deprecate Notion:**
   - Remove `tools/notion_client.py` and Notion env vars from `.env.example`.
   - Delete `docs/integration/notion-bridge.md` or mark it archived.

## 7. Safety / reversibility

- Default `INVENTORY_SOURCE=legacy`-style flag is not needed here because Notion is already opt-in; unsetting `NOTION_TOKEN` instantly reverts to Firestore.
- Every write path is audited to the `activities` collection.
- PII remains redacted on Mira endpoints; only `deal_id`, status, and operational fields are exposed.
- No external sends: Pub/Sub is internal GCP; Telegram remains disabled when `MIRA_GROUP_ID` is unset.

## 8. Testing checklist

- [ ] `tests/test_mira_routes.py` passes with `source == "gcp"`.
- [ ] `scripts/live_monitoring_check.py` still reports all Mira endpoints healthy.
- [ ] Migration script dry-run shows correct row counts before writing.
- [ ] Ops Copilot smoke test creates a service request and feedback record.

## 9. Decision needed from Ari

1. Confirm we should **deprecate Notion** as the installations/feedback source.
2. Decide whether to invest in **Pub/Sub async ingestion** now or keep synchronous webhooks.
3. Decide whether a **Cloud SQL mirror** is justified for monitoring/dashboard queries.
