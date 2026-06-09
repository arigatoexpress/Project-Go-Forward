# Project Go Forward — Texas Home Outlet AI Agent

## Architecture
- **Backend**: FastAPI (Python 3.11), Google ADK (Agent Development Kit), Gemini 2.0 Flash
- **Frontend**: React 19 + Vite + Tailwind CSS, served from `frontend/dist/`
- **Database**: Google Firestore (primary), JSON files (fallback), sample data (last resort)
- **Deployment**: Single Docker container on Google Cloud Run (project: tho-ai-agent, region: us-central1)
- **Config**: `config.yaml` is the single source of truth for all business-specific values

## Directory Layout
```
project-go-forward/
├── main.py                    # FastAPI server, all API endpoints, middleware
├── root_agent.py              # Multi-agent orchestration (Sales + Service agents)
├── config.yaml                # Business config (name, hours, agent settings, deployment)
├── config_loader.py           # YAML config loader with caching
├── conversation_memory.py     # Session context tracking + preference extraction
├── lead_management.py         # Lead capture and Firestore CRM
├── appointment_manager.py     # Appointment slot management + booking
├── email_service.py           # Transactional email via Resend
├── analytics_service.py       # Usage analytics tracking
├── structured_logging.py      # Request/response logging with IDs
├── caching.py                 # Dual-layer caching (Redis + local fallback)
├── config/
│   ├── field_map.json         # Central PDF template mapping registry
│   └── field_map_loader.py    # JSON loader + accessors for field_map
├── tools/
│   ├── document_engine.py     # Data-driven PDF generation engine
│   ├── document_tools.py      # PDF form filling (pypdf) + HTML generators
│   ├── inventory_tools.py     # Inventory search with caching
│   ├── marketing_tools.py     # Ad Studio script generation
│   ├── crm_tools.py           # Appointments, business hours, leads
│   ├── service_tools.py       # Warranty checks, defect analysis
│   ├── form_extraction.py     # AI-powered chat-to-form data extraction
│   ├── pii_guard.py           # PII protection for logging and LLM calls
│   ├── asset_scraper.py       # Property photo/asset catalog
│   └── scraper.py             # Website inventory scraper
├── schemas/
│   ├── document_schemas.py    # Pydantic models for document requests/responses
│   ├── output_schemas.py      # ADK structured output schemas
│   ├── inventory_schema.json  # Inventory data validation
│   └── customer_schema.json   # Customer data validation
├── database/
│   ├── firestore_client.py    # Firestore CRUD (THODatabase singleton)
│   └── models.py              # Pydantic data models (Customer, Property, Sale, Deal, etc.)
├── frontend/src/
│   ├── App.jsx                # Main SPA with chat interface + page routing
│   ├── constants.js           # Shared business constants (name, phone, hours)
│   ├── pages/
│   │   ├── InventoryBrowse.jsx # Home browsing with photos + 3D tours
│   │   ├── DocumentCenter.jsx # Document template browser + generation UI
│   │   ├── AdStudio.jsx       # AI marketing content creator
│   │   ├── Analytics.jsx      # Usage analytics dashboard
│   │   ├── CRM.jsx            # Lead + deal management dashboard
│   │   ├── Appointments.jsx   # Appointment scheduling page
│   │   └── Contact.jsx        # Contact form
│   └── components/
│       ├── SmartForm.jsx      # Dynamic form fields driven by field_map.json
│       ├── PropertyCard.jsx   # Home listing card with images
│       ├── ComparisonDrawer.jsx # Side-by-side home comparison
│       ├── SafeMarkdown.jsx   # Markdown renderer with property card detection
│       ├── QuickActions.jsx   # Predefined action buttons
│       ├── SearchFilters.jsx  # Inventory search filter panel
│       └── ErrorBoundary.jsx  # React error boundary
├── scripts/
│   └── import_fcd_deals.py    # FCD deal import utility
├── tests/                     # Test files
├── data/generated_docs/       # Output directory for generated PDFs
└── Dockerfile                 # Python 3.11-slim, port 8080
```

## Key Conventions
- **Config-driven**: All business-specific values go in `config.yaml`, never hardcoded
- **Field mappings in `config/field_map.json`**: NEVER hardcode PDF field names in Python code
- **pypdf for PDF form filling**: Do not introduce new PDF libraries; reuse `fill_pdf_form()` in `document_tools.py`
- **Pydantic for all schemas**: Request/response models in `schemas/`
- **New API endpoints**: Add in `main.py` ABOVE the SPA catch-all route at the bottom
- **Frontend pages in `src/pages/`**, reusable components in `src/components/`
- **CSS prefix `tho-`**: Used to avoid ad blocker interference (renamed from `ad-` prefix)
- **Agent responses via markdown**: Backend controls UI by returning markdown with embedded JSON for property cards

## Guardrails
- **Never modify PDF templates** in `tho_data/documents/` — they are regulatory originals
- **Never log PII** (SSN, financial account numbers) — use `pii_guard.py` for sanitization
- **Never send PII to LLM** — strip PII fields before Gemini API calls
- **Keep backward compatibility** with existing `/api/documents/sales-contract` endpoint
- **Test against TMHA_SalesContract.pdf** as baseline reference for document generation

## FCD Migration Handoff — 2026-06-04
- Old platform: `https://www.fastcontractdocs.com/manager` (FastContractDocs, singular `contract`). The old host currently has an expired TLS certificate, so treat live scraping as a one-off read-only recovery path, not a production integration.
- Secure local handoff: the latest timestamped differential bundle (`fcd_differential_latest`) lives outside the repo on the operator's machine — see `docs/LOCAL_DATA.md` for location conventions and handling rules.
- Refreshed canonical source CSV: `full_migration_export.csv` (local-only raw source kept outside the repo; do not commit, paste, or log rows — see `docs/LOCAL_DATA.md`).
- Refreshed Project-Go-Forward sanitized artifact: `data/migrated_customers.json` now represents 2,005 sanitized customers from 2,008 live FCD rows. The 2026-06-04 differential found 42 new sanitized customers, 5 existing sanitized records with updates, and 3 skipped rows handled by the sanitizer.
- Read the handoff Markdown in the secure bundle before touching FCD import work. Use `legacy_id` / `fcd_app_id` for idempotent matching, preserve existing customer IDs, and require an explicit dry-run plus human approval before any Firestore write.

## Build & Run Commands
```bash
# Frontend
cd frontend && npm install && npm run build

# Backend (local)
pip install -r requirements.txt
python main.py  # Starts on port 8080

# Deploy to Cloud Run
gcloud run deploy project-go-forward --source . --region us-central1

# Run tests
python -m pytest tests/

# Inspect PDF fields
python scripts/batch_inspect_pdfs.py
```

## API Endpoints

### Core
- `POST /run` — Main chat interaction (ADK agent)
- `GET /health` — Readiness health check
- `GET /healthz` and `GET /healthz/` — Cloud Run liveness probe with status, version, and uptime. External smoke checks use `/healthz/` because Google Frontend reserves exact `/healthz` before it reaches FastAPI.

### Documents
- `GET /api/documents/templates` — List available document templates
- `GET /api/documents/templates/{name}/fields` — Field definitions for a template
- `POST /api/documents/generate` — Generate any mapped document
- `POST /api/documents/generate-packet` — Generate merged closing packet
- `POST /api/documents/sales-contract` — Legacy: TMHA sales contract only
- `GET /api/documents/download/{filename}` — Download generated PDF
- `POST /api/documents/extract-fields` — AI-extract form data from chat history

### Deals (CRM)
- `GET /api/deals` — List deals with filters
- `POST /api/deals` — Create a new deal
- `GET /api/deals/{id}` — Get deal details
- `PUT /api/deals/{id}` — Update deal data
- `PUT /api/deals/{id}/status` — Change deal status
- `POST /api/deals/{id}/generate-document` — Generate document from deal data
- `POST /api/deals/{id}/generate-packet` — Generate closing packet from deal data

### Marketing
- `POST /api/marketing/generate-script` — Ad Studio script generation
- `GET /api/marketing/trending-ideas` — Trending content ideas
- `POST /api/marketing/schedule` — Schedule social post
- `GET /api/marketing/analytics` — Content performance analytics
- `POST /api/marketing/generate-image` — AI image generation
- `GET /api/marketing/inventory-context` — Inventory data for ad creation

### Leads
- `GET /api/leads` — List leads
- `GET /api/leads/{id}` — Get lead details
- `PUT /api/leads/{id}` — Update lead
- `GET /leads/export` — CSV export of leads
- `GET /leads/stats` — Lead statistics

### Appointments
- `GET /api/appointments/slots` — Available appointment slots
- `POST /api/appointments` — Book appointment
- `GET /api/appointments/{id}` — Get appointment details
- `POST /api/appointments/{id}/cancel` — Cancel appointment
- `GET /api/crm/appointments` — List appointments (CRM view)

### Email & Contact
- `POST /api/email/send` — Send custom email from CRM
- `GET /api/email/log` — Email activity log
- `POST /api/contact` — Contact form submission

### Admin
- `POST /api/admin/verify` — Validate admin PIN (returns session token)
- `GET /api/admin/check` — Verify admin session token
