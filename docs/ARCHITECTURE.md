# THO App — System Architecture

**Last updated**: 2026-04-22
**Canonical source**: this document. If it conflicts with other docs in the repo (e.g. the older `INTEGRATION_GUIDE.md`), this document wins.

## 1. What THO App is

The THO (Texas Home Outlet) App is a single-container FastAPI + React web app that runs the Sales/Service/CRM operations for Texas Home Outlet. It combines:

- A conversational agent (Google ADK + Gemini 2.0 Flash) for customer chat
- A CRM (Customers, Deals, Leads, Appointments, Service Requests)
- A regulatory PDF document engine (63 templates, XFA form fill)
- An inventory browse/compare experience
- A marketing "Ad Studio" (script/image/video generation)

## 2. Tech stack

| Layer | Choice | Notes |
|-----|-----|-----|
| Backend | FastAPI, Python 3.11 | Single `main.py` with all routes above a SPA catch-all |
| Agent framework | Google ADK + Gemini 2.0 Flash | Multi-agent (sales + service), Vertex AI |
| Database | Firestore (Google Cloud) | Primary store; JSON files as fallback |
| PDF engine | pypdf + reportlab | XFA form fill with AcroForm fallback |
| Frontend | React 19 + Vite + Tailwind | CSS prefix `tho-` (ad-blocker safe) |
| Email | Resend | Transactional only |
| Deployment | Cloud Run (single container) | Multi-stage Dockerfile |
| CI | GitHub Actions | WIF-based keyless deploy |

## 3. Cloud topology

Two GCP projects are in play. **Do not conflate them.**

| Project | Purpose | Billing | Critical state |
|-----|-----|-----|-----|
| `tho-ai-agent` | Cloud Run host **and live Firestore** (customer/deal/inventory data) | Cursor billing account | **Live prod data. DELETE_PROTECTION_DISABLED — must enable.** |
| `sapphire-479610` | **Cloud DNS zone** for `sapphirealpha.xyz` (E-pool NS delegation) | Main billing account | Must stay active to host the DNS zone; registrar's NS records are locked to the E-pool this zone owns. |

**Do not delete `sapphire-479610`**. Squarespace Domains delegates `sapphirealpha.xyz` to its nameservers, and those nameservers only work while that project is active.

### Cloud Run service

- **Service name**: `project-go-forward`
- **Region**: `us-central1`
- **Public URL**: `https://project-go-forward-trgi34bxuq-uc.a.run.app`
- **Custom domain**: `https://tho.sapphirealpha.xyz` (THO app subdomain)
- **Auth**: public endpoints + admin-only endpoints gated by `X-Admin-Token` header (JWT)

### Cloud DNS

- Zone: `sapphirealpha-xyz` in `sapphire-479610`
- NS pool: `ns-cloud-e1..e4.googledomains.com`
- Apex + www → Google Frontend anycast (216.239.32/34/36/38.21)
- Subdomain CNAMEs: `dashboard.`, `gateway.`, `pm.` → `ghs.googlehosted.com.`

## 4. Deployment

### Dockerfile

- Multi-stage: Python builder → Node/Vite builder → runtime
- Runs `scripts/check_document_templates.py` preflight — build fails if `tho_documents/` is missing or any template in `config/field_map.json` is absent
- Final image runs as non-root on port 8080

### CI/CD

- **Trigger**: push to `main`
- **Auth**: Workload Identity Federation (no service account keys)
- **Steps**: lint → test (admin auth + smoke + healthz, no Firestore) → build container → deploy to Cloud Run → readiness + liveness health checks
- **Workflow file**: `.github/workflows/deploy.yml`

### Environment variables (production)

Names only — values are in Cloud Run config.

| Name | Purpose | Source |
|-----|-----|-----|
| `GOOGLE_CLOUD_PROJECT` | Firestore + Vertex AI project | plaintext env |
| `GOOGLE_CLOUD_LOCATION` | Vertex AI region | plaintext env |
| `GOOGLE_GENAI_USE_VERTEXAI` | Enable Vertex AI path | plaintext env |
| `ADMIN_PIN_HASH` | SHA256 of admin PIN | ✓ Secret Manager (`admin-pin-hash`) |
| `ADMIN_TOKEN_TTL` | JWT lifetime (default 2h) | plaintext env |
| `RATE_LIMIT_RPM` | Requests/min (default 60) | plaintext env |
| `MAX_REQUEST_BODY_BYTES` | Upload cap (default 1 MB) | plaintext env |
| `ALLOWED_ORIGINS` | CORS allowlist | plaintext env |
| `GCS_DOCUMENTS_BUCKET` | PDF output bucket (`tho-secure-documents`) | plaintext env |
| `RESEND_API_KEY` | Transactional email | Secret Manager target: `resend-api-key`; admin-only `/healthz/detailed` reports `dependencies.email` |
| `PII_ENCRYPTION_KEY` | Field-level encryption | ✓ Secret Manager |
| `N8N_API_TOKEN` | n8n webhook auth | **currently plaintext — rotate + move to Secret Manager** |
| `THO_API_KEY` | External API v1 auth | **currently plaintext — rotate + move to Secret Manager** |

See [SECURITY.md](SECURITY.md) for the secret-hardening plan.

## 5. Repository layout

```
Project-Go-Forward/
├── main.py                    # FastAPI server, all routes + middleware
├── root_agent.py              # Multi-agent orchestration (Sales + Service)
├── config.yaml                # Single source of truth for business config
├── config/
│   ├── field_map.json         # PDF template field registry (63 templates)
│   └── pdf_field_inventory.json
├── database/
│   ├── firestore_client.py    # THODatabase singleton
│   └── models.py              # Pydantic models (Customer, Deal, Inventory, …)
├── schemas/                   # Pydantic request/response schemas
├── tools/
│   ├── document_tools.py      # PDF form fill (pypdf)
│   ├── form_extraction.py     # AI-powered chat → form data
│   ├── document_engine.py     # Higher-level generation orchestrator
│   ├── inventory_tools.py     # Inventory search + caching
│   ├── marketing_tools.py     # Ad Studio scripts + media
│   ├── crm_tools.py           # Appointments, leads, hours
│   ├── service_tools.py       # Warranty + defect checks
│   ├── pii_guard.py           # PII scrubbing for logs and LLM input
│   └── asset_scraper.py
├── frontend/
│   ├── src/pages/             # DocumentCenter, CRM, Analytics, AdStudio, …
│   ├── src/components/        # SmartForm, PropertyCard, SafeMarkdown, …
│   └── dist/                  # Vite build output, served by FastAPI
├── tho_documents/             # 63 regulatory PDF templates (do not edit)
├── scripts/
│   ├── check_document_templates.py  # Docker preflight
│   └── seed_inventory_from_marketing.py
├── tests/                     # pytest
└── Dockerfile                 # Multi-stage build
```

## 6. Observability

- **Structured logging**: `structured_logging.py` tags every request with a request ID and user session ID.
- **Security headers**: CSP, HSTS, X-Frame-Options, X-Content-Type-Options set in middleware (main.py ~lines 100–127).
- **Rate limit**: in-memory RPM counter per IP (default 60); `/health` and `/healthz` exempt.
- **Analytics**: `analytics_service.py` tracks endpoint usage, surfaced at `/api/analytics/*`.

## 7. Guardrails

- **Never edit templates in `tho_documents/`** — regulatory originals.
- **Never log PII** — use `pii_guard.py` before any log call or LLM prompt.
- **Never hardcode PDF field names in Python** — go through `config/field_map.json`.
- **Preserve `/api/documents/sales-contract`** — legacy consumer; keep backward compatible.
- **Always verify document generation against `TMHA_SalesContract.pdf`** — canonical baseline.

## 8. Pointers

- Data model: [DATA_MODEL.md](DATA_MODEL.md)
- Workflows + diagrams: [WORKFLOWS.md](WORKFLOWS.md)
- Security + least-privilege: [SECURITY.md](SECURITY.md)
- Notion integration (for Etai): [INTEGRATION_NOTION.md](INTEGRATION_NOTION.md)


*Last verified: 2026-05-04*
