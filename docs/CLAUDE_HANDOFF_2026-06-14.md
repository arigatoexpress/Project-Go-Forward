# Claude Handoff — THO Business Systems Build-Out

**Date:** 2026-06-14  
**Prepared by:** Kimi Code CLI + parallel agent swarm  
**Recipient:** Claude (next-session take-over)  
**Scope:** Texas Home Outlet (`Project-Go-Forward`) production backend + Obsidian `~/Knowledge` sovereign-LLM brain  
**Status:** Integration work complete and tested; DNS/MX cutover is the remaining critical path to full production.  
**Note:** The code changes described below were subsequently committed on `feat/dns-mx-cutover-api` and merged to `main` via PR #169. This document is preserved as the original handoff snapshot.

---

## 1. Executive Summary

The THO AI-agent stack has been rebuilt around a **partner-facing bridge API** (`/api/v1/mira/*`) that feeds a Telegram bot called **Mira**, plus an **Obsidian knowledge vault** that acts as the long-term memory and sovereign-LLM brain for the operator (Ari). The current deploy (Cloud Run service `project-go-forward` in `tho-ai-agent`) is live at `https://tho.sapphirealpha.xyz`.

The bridge exposes health, metrics, lead, appointment, inventory, deal, customer, installation, and feedback summaries. A GitHub webhook forwards repository events to Mira, and a DNS/MX cutover monitor verifies that `texashomeoutlet.com` can move to Cloud Run without breaking Yahoo inbound email or Resend outbound email.

**133 tests pass.** At the time of writing the changes existed only in the working tree; they were committed and merged shortly after.

---

## 2. Production Status (as of 2026-06-14)

### Live & Working
- Public storefront + admin CRM at `https://tho.sapphirealpha.xyz`
- Cloud Run service `project-go-forward`, region `us-central1`
- `/health`, `/healthz/` probes
- Core CRM: customers, inventory, deals, service requests, document center
- Partner API under `/api/v1/*` with `THO_API_KEY` / `THO_API_KEY_<PARTNER>` auth
- Outbound signed partner webhooks (`tools/partner_webhooks.py`)

### Bridge Status
| Endpoint | Status | Notes |
|---|---|---|
| `GET /api/v1/mira/health` | ✅ | Public (no auth) |
| `GET /api/v1/mira/system` | ✅ | Partner key |
| `GET /api/v1/mira/metrics` | ✅ | Partner key |
| `GET /api/v1/mira/leads/summary` | ✅ | Partner key |
| `GET /api/v1/mira/leads/recent` | ✅ | Newly implemented |
| `GET /api/v1/mira/leads/triage` | ✅ | Newly implemented |
| `POST /api/v1/mira/leads/{id}/triage` | ✅ | Newly implemented; emits `lead.triage_updated` webhook |
| `GET /api/v1/mira/appointments/summary` | ✅ | Partner key |
| `GET /api/v1/mira/installations/summary` | ✅ | Newly implemented |
| `GET /api/v1/mira/installations/recent` | ✅ | Newly implemented |
| `GET /api/v1/mira/feedback/summary` | ✅ | Newly implemented |
| `GET /api/v1/mira/feedback/recent` | ✅ | Newly implemented |
| `GET /api/v1/mira/deals/summary` | ✅ | Partner key |
| `GET /api/v1/mira/customers/summary` | ✅ | Partner key |
| `GET /api/v1/mira/inventory/summary` | ✅ | Partner key |
| `POST /api/v1/mira/notify` | ⚠️ | Implemented; needs `MIRA_GROUP_ID` env var to route to Telegram |
| `POST /api/github/mira/webhook` | ✅ | Newly implemented; needs `GITHUB_WEBHOOK_SECRET` |
| `GET /api/v1/cutover/dns-status` | ✅ | Newly implemented |
| `GET /api/v1/cutover/mx-status` | ✅ | Newly implemented |
| `POST /api/v1/cutover/notify` | ✅ | Newly implemented |

### Critical Path: DNS / MX Cutover
The domain `texashomeoutlet.com` is being moved to Cloud Run. Recent commits (#146–#161) are all domain-mapping, verification, SEO, and post-cutover hardening. The cutover endpoints can verify:
- Apex A records point to Cloud Run IPs.
- `www` CNAME points to `ghs.googlehosted.com`.
- Yahoo inbound MX records are preserved.
- Resend outbound MX records (`*.amazonses.com`) are present.

**Next action:** Ari/Kimi decide the cutover window, then update DNS at the registrar.

---

## 3. Completed Work

### 3.1 Mira Telegram Bridge (`mira_routes.py`, `mira_notify.py`)
- Reconstructed from compiled `.pyc` bytecode after source files were lost.
- Added missing `/leads/recent`, `/installations/*`, `/feedback/*` endpoints.
- Added lead-triage endpoints and `LeadManager` triage fields.
- Wired into `main.py` with partner-key auth.

### 3.2 GitHub → Mira Trigger (`github_mira_trigger.py`)
- `POST /api/github/mira/webhook` validates `X-Hub-Signature-256`.
- Filters noise (labels, assignments, non-`main` pushes).
- Posts notable events (PRs, issues, check suites, pushes, security alerts) to Telegram.
- Dispatches HMAC-signed partner webhook.
- Logs to Firestore `activities/`.
- Special formatting for cutover PR #156.

### 3.3 Lead Triage (`lead_management.py`)
- Added `priority`, `assigned_to`, `triage_notes`, `triage_reason`, `last_triage_at` to `Lead`.
- `LeadManager.list_leads_needing_triage(status, min_age_hours, limit)`.
- `LeadManager.triage_lead(lead_id, update)`.
- Mira endpoints expose the 63 "new" leads for categorization.

### 3.4 DNS/MX Cutover Monitor (`dns_mx_cutover.py`, `dns_mx_cutover_routes.py`)
- `dnspython==2.7.0` added to `requirements.txt`.
- Checks apex A, www CNAME, Yahoo inbound MX, Resend outbound MX.
- `POST /api/v1/cutover/notify` dispatches cutover events to partner webhooks.

### 3.5 Partner Webhook Dispatcher (`tools/partner_webhooks.py`)
- Added optional `partner_ids` filter so callers can target only `mira` (or another partner).

### 3.6 Obsidian Sovereign-LLM Brain (`~/Knowledge`)
- Standardized `domain:` taxonomy across 352 notes.
- Rewrote graph color groups by domain/type.
- Created 6 saved graph presets + `GRAPH-GUIDE.md`.
- Configured Breadcrumbs, Smart Connections, Omnisearch, Linter.
- Added `LAB-INDEX.md`, new seed synthesis notes, dashboard refreshes.
- Created `SOVEREIGN-LLM-IMPLEMENTATION-INDEX.md` and architecture diagrams.
- Re-ran `ai-knowledge pipeline`: 447 nodes, 2,484 chunks embedded.

---

## 4. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         USER FACING LAYER                            │
│  https://texashomeoutlet.com (pending DNS cutover)                   │
│  https://tho.sapphirealpha.xyz (current live)                        │
│  React SPA  →  FastAPI (`main.py`)  →  Firestore / GCS               │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      PARTNER / AUTOMATION LAYER                      │
│  /api/v1/*                require_partner_api_key                    │
│  /api/v1/mira/*           Mira bridge (monitoring + Telegram)        │
│  /api/v1/cutover/*        DNS/MX cutover monitor                     │
│  /api/github/mira/webhook GitHub → Telegram alerts                   │
│  tools/partner_webhooks.py  HMAC-signed outbound events              │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      OBSIDIAN KNOWLEDGE BRAIN                        │
│  ~/Knowledge vault                                                   │
│  - PARA + World Model + Agent Memory + Visual Graphs                 │
│  - Local LLM roster (Ollama) + embedding + RAG                       │
│  - ai-knowledge CLI pipeline                                         │
└─────────────────────────────────────────────────────────────────────┘
```

### Key Data Stores
| Store | Purpose | Source of Truth |
|---|---|---|
| Firestore | Customers, deals, inventory, leads, service requests, activities | **PGF** |
| GCS `GCS_DOCUMENTS_BUCKET` | Generated regulatory PDFs | **PGF** |
| Obsidian `~/Knowledge` | Long-term knowledge, SOPs, agent memory, LLM specs | **Vault** |
| Notion (Etai) | Operating workspace, Delivery Tracker, CS surveys | **Etai** (display cache) |
| Drive | Floorplans, install photos, external scans | **Working files** |
| Telegram | Real-time alerts via Mira bot | **Mira** |

---

## 5. File Inventory

### New Files
- `mira_notify.py`
- `mira_routes.py`
- `github_mira_trigger.py`
- `obsidian_routes.py`
- `dns_mx_cutover.py`
- `dns_mx_cutover_routes.py`
- `tests/test_mira_routes.py`
- `tests/test_github_mira_trigger.py`
- `tests/test_obsidian_routes.py`
- `tests/test_dns_mx_cutover.py`
- `docs/integration/github-mira-trigger.md`

### Modified Files
- `main.py` — router wiring for mira, obsidian, github, dns-mx
- `lead_management.py` — triage fields and methods
- `tools/partner_webhooks.py` — `partner_ids` filter
- `tests/test_api_v1.py` — lead-triage tests + env-var cleanup fix
- `.env.example` — new env vars documented
- `requirements.txt` — `dnspython==2.7.0`

### Obsidian Vault
- `~/Knowledge/CLAUDE-HANDOFF-2026-06-14.md` (this handoff mirror)
- `~/Knowledge/SOVEREIGN-LLM-IMPLEMENTATION-INDEX.md`
- `~/Knowledge/5-World-Model/System/SOVEREIGN-LLM-ARCHITECTURE.md`
- `~/Knowledge/6-Agent-Memory/2026-06-14-sovereign-llm-implementation-notes.md`
- Updated: `HUB.md`, `DASHBOARD.md`, `VISUAL-INDEX.md`, `CATEGORY-GUIDE.md`, `GRAPH-GUIDE.md`, etc.

---

## 6. Environment Variables

### Required for Mira Telegram
```bash
TELEGRAM_BOT_TOKEN=          # THO bot token
MIRA_GROUP_ID=                # Dedicated Mira group (preferred)
KIMI_RELAY_CHAT_ID=           # Legacy fallback
```

### Required for GitHub → Mira
```bash
GITHUB_WEBHOOK_SECRET=        # GitHub webhook HMAC secret
PARTNER_WEBHOOK_URL_MIRA=     # Optional outbound partner webhook URL
PARTNER_WEBHOOK_SIGNING_KEY=  # Shared HMAC key for outbound webhooks
```

### Required for DNS/MX Cutover
```bash
CUTOVER_DOMAIN=texashomeoutlet.com
CUTOVER_WWW_DOMAIN=www.texashomeoutlet.com
RESEND_SEND_SUBDOMAIN=        # if non-default
```

### Existing (must not break)
```bash
ADMIN_PIN_HASH=
GOOGLE_CLOUD_PROJECT=tho-ai-agent
GOOGLE_CLOUD_LOCATION=us-central1
GCS_DOCUMENTS_BUCKET=
RESEND_API_KEY=
THO_API_KEY=                  # Primary partner key
THO_API_KEY_MIRA=             # Mira-specific partner key
THO_API_KEY_ETAI=             # Etai partner key (if used)
REDIS_HOST=
SENTRY_DSN=
RATE_LIMIT_RPM=60
```

---

## 7. How to Run & Test

```bash
# Backend tests
python -m pytest tests/test_mira_routes.py tests/test_github_mira_trigger.py \
  tests/test_dns_mx_cutover.py tests/test_api_v1.py tests/test_lead_management.py \
  tests/test_partner_webhooks.py tests/test_healthz.py tests/test_document_engine.py -q

# Full smoke (requires local Firestore emulator or credentials)
python main.py
# http://127.0.0.1:8080

# Build frontend
npm --prefix frontend install
npm --prefix frontend run build
```

Current result: **133 passed**.

---

## 8. Open Workstreams & Recommended Priorities

### P0 — DNS/MX Cutover (Ari/Kimi own the registrar)
1. Confirm apex A records and www CNAME at registrar.
2. Verify `/api/v1/cutover/dns-status` and `/api/v1/cutover/mx-status` return green.
3. Lower TTL ahead of time; flip DNS; monitor `/api/v1/cutover/notify` events.
4. Verify `https://texashomeoutlet.com` resolves and serves the app.

### P1 — Mira Goes Live
1. Set `MIRA_GROUP_ID` and `TELEGRAM_BOT_TOKEN` in Cloud Run env / Secret Manager.
2. Smoke-test `POST /api/v1/mira/notify`:
   ```bash
   curl -X POST https://tho.sapphirealpha.xyz/api/v1/mira/notify \
     -H "Authorization: Bearer $THO_API_KEY_MIRA" \
     -H "Content-Type: application/json" \
     -d '{"message":"Mira is live","level":"info","source":"tho"}'
   ```
3. Set `GITHUB_WEBHOOK_SECRET` and register `https://tho.sapphirealpha.xyz/api/github/mira/webhook` in the GitHub repo settings.
4. Configure `PARTNER_WEBHOOK_URL_MIRA` if Mira needs outbound signed events.

### P2 — Notion Integration (real Notion API)
Current `/installations/*` and `/feedback/*` endpoints read from Firestore. The client's intent is to pull from Notion's Delivery Tracker and CS surveys. Build:
- `tools/notion_client.py` using `notion-client` or raw REST.
- Env vars: `NOTION_TOKEN`, `NOTION_DELIVERY_TRACKER_DB_ID`, `NOTION_CS_SURVEY_DB_ID`.
- Cache/denormalize into Firestore `service_requests` and `feedback` collections, **or** serve directly from Notion with PII redaction.
- Follow the boundary rules in `docs/integration/etai-notion-integration-2026-04-28.md`.

### P3 — Lead Triage Workflow
1. Run `GET /api/v1/mira/leads/triage?status=new&min_age_hours=0&limit=100`.
2. Categorize the 63 "new" leads via `POST /api/v1/mira/leads/{id}/triage`.
3. Build a daily Mira digest of hot leads (`priority=high`, `triage_reason=hot_lead`).

### P4 — Obsidian Brain Expansion
1. Install gated Obsidian plugins: Templater, Linter, Breadcrumbs, Omnisearch.
2. Tune `OLLAMA_KEEP_ALIVE` / prototype persistent embedding service.
3. Build conversational RAG thread memory and auto-link suggestions in `knowledge_lib.py`.
4. Distill creator profiles from ingestion queue: Preston Stewart, Ryan McBeth, Ben Cowen, Michael Nadeau.
5. Fix pre-existing broken links in `1-Projects/FedEx-Ops/`.

### P5 — Business Systems to Build Next
| System | Why | Likely Files |
|---|---|---|
| Installation scheduling | Bridge sales → ops | `appointment_manager.py`, Notion/Drive |
| Warranty & factory billing tracker | Post-sale ops | Firestore + Notion |
| Inventory procurement alerts | Avoid stockouts | `/api/v1/mira/inventory/summary` + thresholds |
| Customer CSAT follow-up | Close feedback loop | `/api/v1/mira/feedback/*` + Resend email |
| Sales rep assignment & commissions | Scale sales team | `lead_management.py`, CRM admin UI |
| Document package versioning | Compliance/audit | GCS + `tools/document_engine.py` |
| UTM / attribution reporting | Marketing ROI | existing analytics service |
| Self-serve buyer portal | Reduce manual chat | frontend public routes + deals API |

---

## 9. Runbooks

### Deploy to Cloud Run
```bash
gcloud run deploy project-go-forward \
  --source . \
  --region us-central1 \
  --project tho-ai-agent \
  --set-env-vars "MIRA_GROUP_ID=...,TELEGRAM_BOT_TOKEN=...,GITHUB_WEBHOOK_SECRET=..."
```

### Rollback
```bash
gcloud run revisions list --service project-go-forward --region us-central1
gcloud run services update-traffic project-go-forward --to-revisions REVISION=100 --region us-central1
```

### Verify Bridge Endpoints
```bash
# Health (public)
curl https://tho.sapphirealpha.xyz/api/v1/mira/health

# System (partner key)
curl -H "Authorization: Bearer $THO_API_KEY_MIRA" \
  https://tho.sapphirealpha.xyz/api/v1/mira/system

# Leads triage
curl -H "Authorization: Bearer $THO_API_KEY_MIRA" \
  "https://tho.sapphirealpha.xyz/api/v1/mira/leads/triage?status=new&limit=50"
```

---

## 10. Risks & Blockers

| Risk | Mitigation |
|---|---|
| DNS cutover breaks email | MX-status endpoint + pre-cutover verify Resend/Yahoo records |
| Mira bot token exposed | Secret Manager; never commit |
| PII leak via partner API | All Mira endpoints redact name/email/phone; tests enforce absence |
| Firestore costs from polling | Move to Cloud Tasks / event-driven webhooks later |
| Notion becomes second source of truth | Read-only or append-only from Notion; canonical IDs stay in Firestore |
| Obsidian vault grows unsearchable | Smart Connections + Omnisearch + periodic archive |

---

## 11. Decision Log

| Decision | Rationale |
|---|---|
| Reconstruct `mira_*.py` from `.pyc` | Source files were missing; bytecode had the full implementation. |
| Firestore-first for installations/feedback | Faster to ship; Notion API integration is the next iteration. |
| Partner-key auth for all bridge endpoints | Reuses existing `/api/v1/*` security model. |
| Separate `MIRA_GROUP_ID` from `KIMI_RELAY_CHAT_ID` | Allows a dedicated group for Mira alerts vs. legacy relay. |
| Mermaid diagrams in Obsidian | Version-controlled, editable, renders natively. |
| Single-value controlled `domain:` taxonomy | Cleaner graph colors and Dataview grouping. |

---

## 12. Where to Start Tomorrow

1. **If the cutover window is today:** run the DNS/MX checks, flip DNS, verify `texashomeoutlet.com`.
2. **If Mira is the priority:** set `MIRA_GROUP_ID` and `TELEGRAM_BOT_TOKEN`, then test `/api/v1/mira/notify`.
3. **If operations is the priority:** categorize the 63 new leads via `/api/v1/mira/leads/triage`.
4. **If the brain is the priority:** install the gated Obsidian plugins and tune the local LLM pipeline.

---

## 13. Contact & Context

- Vault: `~/Knowledge`
- Repo: `/Users/aribs/Code/Project-Go-Forward`
- Prod: `https://tho.sapphirealpha.xyz`
- Target prod domain: `https://texashomeoutlet.com`
- Cloud Run: `project-go-forward` in `tho-ai-agent`
- Health: `/health` (readiness), `/healthz/` (liveness)

Read `AGENTS.md` in the repo root for project conventions and safety boundaries.
