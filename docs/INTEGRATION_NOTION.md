# Notion Workspace ↔ THO App — Integration Plan

**Audience**: Etai Z. (contractor) and the THO team.
**Principle**: least privilege. Etai integrates through a scoped API, never directly against Firestore or GCS.
**Canonical business key**: the Firestore Deal ID. Notion references Deals by ID; THO never imports Notion-side IDs back.

## 1. Division of responsibility

| Domain | Owned by | System of record |
|-----|-----|-----|
| Customers | THO App | Firestore `customers/` |
| Deals (pre-funded) | THO App | Firestore `deals/` |
| Inventory | THO App | Firestore `inventory/` |
| Generated regulatory PDFs | THO App | GCS `tho-secure-documents/` |
| Lead intake from storefront | THO App | Firestore `leads/` |
| **Installation & Service (11 phases)** | **Notion** | **Notion workspace** |
| **Warranty & Factory Billing (AR)** | **Notion** | **Notion workspace** |
| **Title tracking** | **Notion** | **Notion workspace** |
| **Insurance tracking** | **Notion** | **Notion workspace** |
| Per-deal document repository | Google Drive | Drive folder per deal |

Notion builds the post-funding operational spine. THO retains customer/deal source of truth. They meet at the Deal ID.

## 2. Naming conventions to mirror in Notion

To keep the two systems speaking the same language, Notion databases should mirror THO's field names case-for-case where possible. Notion property names are the visual label; use these internal names for relation keys and formula properties.

### Deal properties to surface in Notion (non-PII only)

| Notion property | Source (Deal) | Type in Notion |
|-----|-----|-----|
| `deal_id` | `id` | Title (primary) — use the Firestore UUID as the page title |
| `status` | `status` | Select (`pending`, `approved`, `contract`, `funded`, `complete`, `denied`, `archived`) |
| `salesrep` | `salesrep` | Select |
| `buyer_name` | computed: `buyer_first_name + buyer_last_name` | Text |
| `inventory_id` | `inventory_id` | Relation → Inventory DB (if Notion mirrors inventory) |
| `manufacturer_model` | computed | Text |
| `sales_price` | `sales_price` | Number (currency) |
| `funded_at` | derived from status transition | Date |
| `thor_deal_url` | `https://sapphirealpha.xyz/crm/deals/{id}` | URL |
| `drive_folder` | Drive folder URL | URL |

### Do not mirror in Notion

- `buyer_ssn`, `co_buyer_ssn`
- Full bank/financing details (`apr`, `finance_charge`, `creditor_*`)
- Reference phone numbers
- Any Customer email/phone unless specifically needed for an operational notification

If an operational step needs PII (e.g., a contractor needs a phone number to schedule an install), fetch it on-demand from the API, don't mirror into Notion.

### Status enum parity

Notion select options must use the exact same values as Firestore:

- Customer status: `ENROLLED`, `NON_ENROLLED`, `LEAD`, `SOLD`
- Deal status: `pending`, `approved`, `contract`, `funded`, `complete`, `denied`, `archived`
- Inventory status: `AVAILABLE`, `SOLD`, `PENDING`, `RESERVED`
- ServiceRequest status: `open`, `in_progress`, `scheduled`, `resolved`, `closed`

Case matters. Customer status is uppercase; Deal/ServiceRequest are lowercase.

## 3. Suggested Notion database structure

### DB: Deals (synced from THO)

- Primary key: `deal_id` (Firestore UUID)
- Do not create deals in Notion — they flow from THO App only
- Relation to Installation DB, Warranty DB, Title DB, Insurance DB

### DB: Installation & Service Tracker

- 11 phases as a status/progress field, or as related Phase entries
- Per-phase contractor assignment (relation → Contractors DB)
- Relation to Deal
- Fields: phase number, phase name, assigned contractor, start date, completion date, notes, blocker flag

### DB: Warranty & Factory Billing

- Filtered views per manufacturer
- Claim number, claim date, manufacturer, factory billing status, amount, paid date
- Relation to Deal and to the Service Request (if applicable)

### DB: Title

- Deal relation
- Title status (ordered, received, recorded)
- Document scans (Drive link)

### DB: Insurance

- Deal relation
- Carrier, policy number, start/end dates, premium
- Document scans (Drive link)

### DB: Contractors

- Name, company, contact, specialties (assigned to Installation phases)

## 4. Integration contract (API)

### Authentication

- Bearer token via `Authorization: Bearer <key>` or `X-API-Key` header.
- Primary secret: `THO_API_KEY`.
- Per-partner secrets: any env var matching `THO_API_KEY_*` (e.g., `THO_API_KEY_ETAI`, `THO_API_KEY_N8N`) is also accepted. Each one is independently revocable — rotate its Secret Manager entry and the others keep working. The audit log records the matched env var name as `partner_id` so per-partner usage is traceable.
- All `/api/v1/*` requests require a valid key (fail-closed).
- Storage: all keys live in GCP Secret Manager in the `tho-ai-agent` project (e.g., `tho-api-key`, `tho-api-key-etai`). Cloud Run mounts each via `secretKeyRef`; no plaintext env values.

### Endpoints (status: implemented on `feat/api-v1-integration`; see [PR #4](https://github.com/arigatoexpress/Project-Go-Forward/pull/4))

| Method | Path | Purpose |
|-----|-----|-----|
| GET | `/api/v1/customers` | List customers (PII-redacted unless key grants otherwise) |
| GET | `/api/v1/customers/{id}` | Get customer by ID |
| POST | `/api/v1/customers` | Create customer (intake from Notion if needed) |
| GET | `/api/v1/inventory` | List inventory with filters |
| GET | `/api/v1/leads` | List leads |
| POST | `/api/v1/webhooks/notify` | Accept inbound webhook from Notion or n8n |
| GET | `/api/v1/stats` | Topline counts for dashboarding |
| POST | `/api/v1/rag/query` | Semantic search over the 63 regulatory PDF templates. See [RAG_INTEGRATION.md](RAG_INTEGRATION.md). |

**Merge plan**: the `/api/v1/*` surface now lives on `feat/api-v1-integration` and is tracked in [PR #4](https://github.com/arigatoexpress/Project-Go-Forward/pull/4) against `main`. The RAG endpoint is stacked on top in `feat/rag-document-search`.

### Webhook flow (THO → Notion)

Events fired by THO to partner webhooks:

- **`deal.status_changed`** — any Deal status transition. Always fired.
- **`deal.funded`** — additional event when `Deal.status` transitions to `funded`. Notion's automation should create Installation phase 1 on this event.
- **`deal.complete`** — additional event when `Deal.status` transitions to `complete`.
- **`service_request.created`** *(not yet implemented)* — warranty-flagged service requests will trigger the Notion Warranty DB.

**Partner registration**: each partner gets a URL slot via env var. Example — to register Etai:

```bash
gcloud run services update project-go-forward \
  --region=us-central1 --project=tho-ai-agent \
  --update-env-vars=PARTNER_WEBHOOK_URL_ETAI=https://notion.example.com/hooks/deals
```

**Request shape** (JSON body):

```json
{
  "event": "deal.funded",
  "delivered_at": "2026-04-24T00:31:00.123456+00:00",
  "idempotency_key": "<uuid4 — same key in retries>",
  "data": {
    "deal_id": "<Firestore UUID>",
    "from": "approved",
    "to": "funded",
    "inventory_id": "...",
    "customer_id": "...",
    "manufacturer": "...",
    "model": "...",
    "salesrep": "...",
    "updated_at": "..."
  }
}
```

**Headers**:

```
Content-Type: application/json
X-THO-Event: deal.funded
X-THO-Partner: etai
X-THO-Delivery: <delivery uuid>
X-THO-Signature: sha256=<hmac-sha256 hex of the raw body using PARTNER_WEBHOOK_SIGNING_KEY>
```

**Signature verification** (Python):

```python
import hmac, hashlib
expected = "sha256=" + hmac.new(
    signing_key.encode(),
    raw_body,
    hashlib.sha256,
).hexdigest()
if not hmac.compare_digest(expected, request.headers["X-THO-Signature"]):
    reject()
```

**Delivery semantics**:

- Fire-and-forget (partner endpoint should respond in ≤ 8 s).
- No retry — partners should be idempotent; reconcile via `GET /api/v1/stats` or poll on the canonical Deal ID.
- Every attempt is logged to the Firestore `activities/` collection as `activity_type = "partner_webhook_delivery.<event>"` with `success`, `status_code`, and `error` fields.

**Alternative path (n8n)**: if a partner prefers workflow-builder semantics, the existing `N8N_API_TOKEN` is wired server-side; dispatch can route through an n8n instance instead of hitting the partner's endpoint directly.

### Webhook flow (Notion → THO)

For state changes originating in Notion:

- **Installation phase completed** → Notion webhook → `/api/v1/webhooks/notify` → THO records an Activity log entry linked to the Deal. Does not mutate the Deal itself (Notion owns installation state).
- **Warranty claim closed** → update `ServiceRequest.status` = `resolved` via `POST /api/v1/service-requests/{id}/resolve` (endpoint to add).

## 5. Google Drive per-deal folder

Open question for Celeste/Mark. Proposed structure if one doesn't exist:

```text
THO/
  Deals/
    {deal_id}/
      contracts/
      disclosures/
      insurance/
      title/
      correspondence/
      installation_photos/
```

The `{deal_id}` is the Firestore UUID. The THO App would write generated PDFs here **in addition to** GCS (GCS remains system-of-record; Drive is the team-visible mirror). Etai references the `{deal_id}` folder URL from Notion.

## 6. What Etai needs to receive

A handoff email/Upwork message to Etai should include:

1. This document (or a summary) as the architectural reference
2. A URL to the Notion workspace (invite from Celeste)
3. The scoped `THO_API_KEY` (post-rotation, delivered via a password manager or encrypted channel — never in plaintext email)
4. The Drive parent folder URL (after Mark provisions)
5. The webhook URL he should POST to when Notion fires events (or we set up n8n flows for him)

## 7. Work items to complete the integration

These are THO-side. Tracked in the dev packet's "What I need from you" section for prioritization.

| # | Task | Owner | Depends on |
|-----|-----|-----|-----|
| 1 | Review and merge `/api/v1/*` from `claude/clever-murdock-371154` into `main` | Ari | — |
| 2 | Rotate `THO_API_KEY`; move to Secret Manager | Ari | — |
| 3 | Generate a second, partner-scoped `THO_API_KEY` for Etai | Ari | #2 |
| 4 | Enable Firestore `DELETE_PROTECTION` on `tho-ai-agent` | Ari | — |
| 5 | Decide: Drive folder structure per deal | Celeste + Mark | — |
| 6 | Provision Drive access for Etai (read/write on `THO/Deals/`) | Mark | #5 |
| 7 | Add `deal.funded` webhook dispatch to THO App | Ari | #1 |
| 8 | Agree on canonical Deal ID surfacing in Notion (title field) | Ari + Etai | — |
| 9 | Add Drive-write step to document generation flow | Ari | #5 |
| 10 | First integration test: create test deal → Notion picks it up | Ari + Etai | #1–#9 |
