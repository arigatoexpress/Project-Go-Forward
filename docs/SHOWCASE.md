# Project Go Forward Showcase

Use this as the short demo and diligence script for the live Texas Home Outlet app at [https://tho.sapphirealpha.xyz](https://tho.sapphirealpha.xyz).

## Current Live Facts

Verified on May 5, 2026:

| Check | Result |
|---|---|
| `GET /health` | HTTP 200, `status: ok` |
| `GET /healthz/` | HTTP 200, exposes minimal public liveness plus deployed `version` |
| `GET /healthz/detailed` | Admin-only; exposes `sha`, `uptime_s`, dependency statuses, and non-secret readiness warnings |
| `GET /api/marketing/inventory-context` | HTTP 200, `success: true`, 44 homes |
| Repo `origin/main` during this doc refresh | `526c788a705557e37b5ca0646a09c945ee864d82` |

Use `/healthz/` with the trailing slash for external smoke checks. Before an
external demo, rerun the smoke and use `/healthz/` as the deployed-revision
source of truth; do not claim the latest `main` is live unless the `version` value
matches the commit you are discussing.

```bash
export THO_PROD_URL="https://tho.sapphirealpha.xyz"
python3 scripts/production_smoke.py --base-url "$THO_PROD_URL"
```

## Demo Path

### 1. Public Buyer Experience

Open [https://tho.sapphirealpha.xyz](https://tho.sapphirealpha.xyz).

Show:

- inventory browsing,
- property cards and comparison flow,
- AI assistant entry point,
- contact and appointment paths.

Do not submit real customer information during a demo.

### 2. Inventory-Aware Marketing

Open [https://tho.sapphirealpha.xyz/studio](https://tho.sapphirealpha.xyz/studio).

Show the route and positioning first. Only continue into authenticated Ad Studio actions for an authorized audience:

- campaign and script workflow,
- inventory picker,
- generated social or video concepts,
- marketing analytics tab.

Standalone entry: [https://tho.sapphirealpha.xyz/studio.html](https://tho.sapphirealpha.xyz/studio.html).

### 3. Internal Operations

Open these only with the right admin credentials:

| Surface | Path | What to show |
|---|---|---|
| Document Center | `/documents` | deal/customer lookup, template selection, normalized fill data, batch packet generation, generated document history |
| CRM | `/crm` | leads, customers, deals, appointments, tasks, email activity, and document actions |
| Analytics | `/analytics` | lead, document, inventory, chat, and customer analytics |

### 4. Partner And Automation Story

Point to the code and docs rather than live credentials:

- `/api/v1/customers`
- `/api/v1/inventory`
- `/api/v1/leads`
- `/api/v1/stats`
- `/api/v1/webhooks/notify`
- `/api/v1/rag/query`

Reference docs:

- [SECURITY.md](SECURITY.md)
- [INTEGRATION_NOTION.md](INTEGRATION_NOTION.md)
- [RAG_INTEGRATION.md](RAG_INTEGRATION.md)

## Public Vs Admin Boundary

Public and read-only enough for unauthenticated smoke:

- `/`
- `/health`
- `/healthz/`
- `/api/marketing/inventory-context`
- contact, appointment, and feedback submit paths when intentionally testing with approved data

Admin-gated:

- `/documents`
- `/crm`
- `/analytics`
- Ad Studio data/actions under `/studio`
- `/api/documents/*`, `/api/inventory`, `/api/deals`, `/api/leads`, `/api/customers`, `/api/analytics/*`, and protected marketing APIs

Partner-gated:

- `/api/v1/*` with `THO_API_KEY` or the configured integration token

## Safety Notes

- Do not expose admin credentials in screenshots, URLs, or screen shares.
- Do not show real customer PII unless the audience is authorized.
- Do not paste partner API keys, admin tokens, PINs, or PIN hashes into demos.
- Do not modify `tho_documents/` regulatory originals during showcase work.
- Prefer screenshots with demo or redacted data for external sharing.

## Fallback Talking Points

If admin auth is unavailable, the story still works:

1. Public site proves the customer-facing SPA is deployed.
2. Public inventory context proves live inventory-backed marketing data.
3. README product map shows where CRM, documents, analytics, and partner APIs live.
4. Production readiness docs show the read-only smoke, local gates, admin-token handling, and rollback path.


*Last verified: 2026-05-04*
