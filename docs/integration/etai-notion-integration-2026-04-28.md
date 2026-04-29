# Etai-Notion <-> THO Integration Boundary Memo

## Provenance

- Repo path: Project-Go-Forward working tree
- Upstream repo: `arigatoexpress/Project-Go-Forward`
- Analyzed SHA: `aa83efe9fea75eb852740a9179f3a40f2af718c1`
- Date: 2026-04-29
- SHA command: `git rev-parse HEAD`
- Scope: current `origin/main` only. I did not use stale PR notes as truth.

## Recommendation

Use **Option A now**: Notion should be Etai's team wiki, operating manual,
project tracker, and implementation workspace while the THO production app
remains the source of truth for customers, deals, inventory, generated PDFs,
admin authentication, and partner APIs.

Keep a narrow, deliberate path toward **Option B later**: Notion may push
structured operational updates into PGF APIs after the exact write contract,
auth, idempotency, audit logging, and ownership rules are agreed. Do not let
Notion become a second customer/deal database. That would create source-of-truth
drift right where THO has the most legal and operational risk: buyer identity,
deal status, inventory availability, and signed document packages.

Use **Option C only as a bridge**: Drive can hold team-visible working folders,
floorplans, scans, and external documents, but GCS remains the production system
for generated regulatory PDFs and Firestore remains the production data store.

## Current THO System Summary

The THO app is a FastAPI + React production system served from one repo. The
current architecture document describes it as the sales/service/CRM app for
Texas Home Outlet, with Firestore as the primary database, Cloud Run as the
runtime, React/Vite as the admin UI, and pypdf/reportlab for document generation
(`docs/ARCHITECTURE.md`).

The admin surface is protected by `require_admin` in `main.py`. Admin clients
send `X-Admin-Token` or `Authorization: Bearer ...`; the token is derived from
`ADMIN_PIN_HASH`, with a default TTL controlled by `ADMIN_TOKEN_TTL`. This is
separate from the partner API. The partner API lives under `/api/v1/*` and uses
`THO_API_KEY` or per-partner `THO_API_KEY_*` values, but the code only logs
fingerprints and matched env var names, not raw key material (`main.py`).

The Document Center is real production functionality, not a prototype. The
frontend loads templates and packets from `/api/documents/templates`, deals from
`/api/deals?limit=100`, and available inventory from
`/api/inventory?limit=100&status=AVAILABLE`. It then posts selected templates
and shared form data to `/api/documents/generate-batch`
(`frontend/src/pages/DocumentCenter.jsx`). The backend exposes single document,
packet, batch, field-list, readiness, history, and download endpoints in
`main.py`. The engine resolves template mappings from `config/field_map.json`
and supports 63 templates and 5 packet definitions. `tools/document_engine.py`
generates single PDFs, packets, and batches; `tools/document_tools.py` fills PDF
forms and uploads generated files to the `GCS_DOCUMENTS_BUCKET` bucket under
`generated_docs/`. The download endpoint tries local disk first and then falls
back to GCS.

CRM/deal state is also production-backed. `database/models.py` defines
`Customer`, `Inventory`, `Deal`, `ServiceRequest`, and `Activity`. A Deal is the
sales transaction record and includes buyer/co-buyer identity, mailing address,
installation address, home identifiers, pricing, financing, timestamps, and
computed document fields through `Deal.to_document_data()`. Deal routes in
`main.py` create, list, update, transition status, and generate documents from
deals. Deal-based document and packet generation run `validate_for_documents`
before calling the document engine (`database/deal_validation.py`, `main.py`).

Inventory is production data. Admin `/api/inventory` lists Firestore inventory
for the admin UI, and `/api/inventory/bulk-import` validates each normalized row
through the `Inventory` model before writing. Public inventory context is a
separate unauthenticated marketing endpoint with public cache semantics; admin
inventory must remain no-cache (`main.py`).

Partner integration exists on current `main`. `/api/v1/customers`,
`/api/v1/inventory`, `/api/v1/leads`, `/api/v1/webhooks/notify`,
`/api/v1/stats`, and `/api/v1/rag/query` are present in `main.py`. Customer and
lead reads are redacted for partner use; inventory responses are normalized; and
inbound partner webhook notifications are logged to Firestore `activities/` with
idempotency support. Outbound partner webhooks are implemented in
`tools/partner_webhooks.py`: configured partner URLs receive signed JSON events
such as `deal.status_changed`, `deal.funded`, and `deal.complete`, and delivery
attempts are logged to `activities/`.

Drive is already treated cautiously. `tools/drive_floorplan_sync.py` walks a
shared THO Drive folder for manufacturer floorplans only, explicitly skips
people/operations folders that may contain customer files, caches allowed files
locally, and uploads floorplan assets under `floorplans/` in GCS. That code is a
good model for Drive's role: useful bridge, not general data lake.

## Boundary Options

### Option A: Notion as wiki and operating manual

This option keeps PGF/Firestore/GCS as the production source of truth. Notion
owns SOPs, implementation checklists, views for installation/service work,
handoff notes, project status, contractor workflow descriptions, and training
materials. It can reference Deal IDs, Drive folder URLs, document package names,
and non-sensitive summaries, but it does not author customer records, overwrite
deal status, or store generated document packages as the canonical copy.

This is the best immediate option. It lets Etai move fast without asking PGF to
trust Notion writes before there is a hardened API contract. It also avoids
duplicating sensitive Firestore fields, generated PDF versions, inventory
availability, and admin-only workflows. Notion can still be operationally
useful: every workspace page can link back to the THO app, the relevant Deal ID,
the Drive folder if one exists, and the responsible owner.

The main limitation is that Option A is not automation-heavy. Humans still move
between THO, Notion, and Drive. That is acceptable now because the largest risk
is not manual effort; it is accidentally creating two conflicting customer/deal
systems.

### Option B: Notion as a structured front end into PGF APIs

In this option, Notion can trigger or push structured changes into PGF APIs.
Examples: logging an installation phase completion to `/api/v1/webhooks/notify`,
creating a low-risk activity note, or later updating a service request status
through a dedicated service-request endpoint. This should be limited to
post-sale operational events at first, not customer/deal master data.

Option B is viable later because PGF already has the right shape: partner API
key auth, partner-safe serialization, idempotent inbound webhook logging, and
outbound signed deal webhooks. But the current API surface is not enough for
Notion to be a broad front end. There is no dedicated partner-safe Deal read API
yet, no service request mutation endpoint in current `main`, and no field-level
contract that says which Notion fields may update which Firestore fields.

Move toward B only by adding small, typed endpoints with tests, audit logs,
idempotency keys, PII limits, and rollback behavior. The first write path should
be "append activity" or "update a Notion-owned operational status," not "edit a
Deal."

### Option C: Drive as bridge

Drive should bridge human files that do not belong naturally in Notion or PGF's
structured database: manufacturer floorplans, install photos, title scans,
insurance scans, signed external PDFs, and per-deal working folders. It should
not replace GCS for generated regulatory PDFs, and it should not become the
only place where document package versions are tracked.

Drive can help Etai because Notion pages can link to a Drive folder per Deal.
But Drive must be permission-scoped and folder-disciplined. The existing
floorplan sync code already encodes the right instincts: allow-list known
manufacturer folders, skip people folders, and avoid parsing customer file
contents. If Drive becomes a bridge, the canonical identifier should still be
the Firestore Deal ID in the folder name or metadata.

## Recommended Architecture

Use a hub-and-spoke model:

- PGF/Firestore/GCS is the production hub for customer, deal, inventory,
  generated PDF, and audit state.
- Notion is the operating workspace for Etai and the team.
- Drive is the file bridge for human-managed documents and floorplan/catalog
  assets.
- Partner APIs and webhooks are the only automation boundary between Notion and
  PGF.

The canonical key is the Firestore Deal ID. Notion pages may display a buyer
name only where the team truly needs it, but they should carry `deal_id` as the
primary relation key. Any customer/deal/inventory field copied into Notion should
be treated as a cached display value, not the source.

## What Notion Should Own

Notion should own SOPs, role definitions, onboarding docs, Etai's implementation
tasks, installation/service phase tracking, warranty/factory billing views,
title and insurance checklists, meeting notes, and team operating dashboards.
It may own operational statuses that PGF does not currently model, such as
"phase 4 photos requested" or "factory billing packet waiting on invoice."

Notion should not own SSNs, raw phone/email dumps, financing terms, generated
regulatory PDF versions, inventory source catalogs, or Deal status transitions.
If a Notion property is copied from THO, label it as synced/display-only.

## What PGF Should Own

PGF owns customers, deals, inventory, generated PDF packages, document template
mapping, admin auth, partner auth, partner webhook delivery logs, and the
readiness/health surface. It also owns source-of-truth Deal status:
`pending`, `approved`, `contract`, `funded`, `complete`, `denied`, `archived`.
When PGF status changes to `funded`, it can notify Notion to start or update
the installation tracker through signed outbound webhooks.

PGF should continue to validate data before writes. Bulk import should keep
validating through `Inventory`. Deal document generation should keep validating
missing fields before packet generation. Admin APIs should stay no-cache.

## What Drive Should Own

Drive may own team-visible folders, manufacturer floorplans, external scans,
install photos, signed documents that arrive from outside PGF, and a per-deal
folder link surfaced in Notion. Drive should not own structured deal state or
the authoritative generated PDF archive. If PGF writes generated PDFs to Drive
later, that should be a mirror/export step; GCS remains authoritative.

## API Surface PGF May Need Later

- `GET /api/v1/deals/{deal_id}` returning a partner-safe, PII-limited Deal
  summary with status, inventory ID, manufacturer/model, salesrep, and links.
- `GET /api/v1/deals?status=funded&updated_since=...` for reconciliation.
- `POST /api/v1/activities` as a general append-only partner activity endpoint,
  separate from the current webhook-specific route.
- `POST /api/v1/service-requests/{id}/resolve` or a narrower status update
  endpoint for Notion-owned warranty/service workflows.
- `POST /api/v1/deals/{deal_id}/drive-folder` to attach a Drive folder URL,
  if Drive folder creation is external.
- Optional `POST /api/v1/notion/sync-check` that validates Notion payload shape
  without mutating anything.

Every write endpoint should require partner auth, reject unknown fields, accept
an idempotency key, log to `activities/`, avoid PII by default, and have tests
with fake Firestore.

## Boundary Risks

- Duplicated customer/deal records: if Notion creates its own customers or
  deals, staff will eventually update the wrong record.
- Stale floorplan/catalog data: Drive and Notion can show old floorplans unless
  PGF inventory/catalog remains canonical and sync jobs are explicit.
- Document package versioning: generated PDFs must stay tied to PGF template
  mappings and GCS objects; Drive copies are mirrors.
- PII leakage: Notion is convenient, so teams may paste phone numbers, SSNs,
  financing details, or generated PDFs unless the workspace explicitly says not
  to.
- Unclear ownership: "Notion says funded" must never beat "PGF says approved."
  PGF wins for Deal status; Notion may own downstream task states.
- Automation ambiguity: inbound webhooks that mutate records without
  idempotency, field allow-lists, and audit logs will be hard to unwind.

## Next steps for Etai

1. Build Notion databases around `deal_id`, not a Notion-generated deal key.
2. Mark copied THO fields as display-only unless Ari explicitly approves a write
   contract.
3. Keep customer PII out of Notion by default; link back to THO for sensitive
   details.
4. Create Installation/Service, Warranty/Factory Billing, Title, Insurance, and
   Contractor workspaces with clear owners and status definitions.
5. Add a Drive URL property, but treat Drive as a document workspace, not a data
   source.
6. Prepare sample webhook payloads for phase completion and warranty updates
   using synthetic Deal IDs only.
7. Document exactly which Notion actions Etai wants to automate before asking
   Ari for new PGF write endpoints.

## Next steps for Ari

1. Confirm Option A as the immediate boundary with Celeste, Mark, and Etai.
2. Give Etai the non-secret integration contract: Deal ID as key, current status
   enums, allowed Notion-owned states, and PII rules.
3. Decide whether Drive per-deal folders are required now or only after Notion
   is stable.
4. Add a partner-safe Deal read endpoint before any Notion dashboard needs Deal
   reconciliation.
5. Add only one Notion write path first: append an activity or log installation
   phase completion, with idempotency and tests.
6. Keep generated PDFs canonical in GCS; add Drive mirroring later only if the
   team needs human browsing.
7. Review `/api/v1/*` auth and webhook docs before issuing or rotating any
   partner key; never send secrets in chat, email, or docs.
