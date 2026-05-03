# Project Go Forward — Texas Home Outlet AI Agent

> **Last verified: 2026-05-03**

## Architecture
- **Backend**: FastAPI (Python 3.11), Google ADK (Agent Development Kit), Gemini 2.0 Flash
- **Frontend**: React 19 + Vite + Tailwind CSS, served from `frontend/dist/`
- **Database**: Google Firestore (primary), JSON files (fallback), sample data (last resort)
- **Deployment**: Single Docker container on Google Cloud Run (project: tho-ai-agent, region: us-central1)
- **Production URL**: `https://sapphirealpha.xyz/` (apex domain; DNS lives in project `sapphire-479610`)
- **Config**: `config.yaml` is the single source of truth for all business-specific values

## Directory Layout
```
project-go-forward/
├── main.py                    # FastAPI server, all API endpoints, middleware
├── root_agent.py              # Multi-agent orchestration (Sales + Service agents)
├── chat_history.py            # Chat history persistence in Firestore (user + AI messages)
├── pm_routes.py               # Linear-inspired PM router (in-memory, experimental)
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
│   ├── scraper.py             # Website inventory scraper
│   ├── lead_nurture.py        # Stale-lead re-engagement digest (PR #39)
│   ├── image_url_backfill.py  # Dry-run image_url enricher for inventory cards (PR #59)
│   ├── lead_name_backfill.py  # Dry-run extractor for unnamed leads from chat history (PR #58)
│   └── video_generator.py     # Marketing video generation via Google Cloud
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
│   │   ├── ChatHistory.jsx    # Admin chat history viewer
│   │   └── Contact.jsx        # Contact form
│   └── components/
│       ├── SmartForm.jsx      # Dynamic form fields driven by field_map.json
│       ├── PropertyCard.jsx   # Home listing card with images
│       ├── ComparisonDrawer.jsx # Side-by-side home comparison
│       ├── SafeMarkdown.jsx   # Markdown renderer with property card detection
│       ├── QuickActions.jsx   # Predefined action buttons
│       ├── SearchFilters.jsx  # Inventory search filter panel
│       ├── ErrorBoundary.jsx  # React error boundary
│       ├── StatusBadge.jsx    # Reusable deal/lead status badge
│       ├── Toast.jsx          # Toast notification system
│       ├── NetworkStatus.jsx  # Online/offline indicator
│       ├── Skeleton.jsx       # Loading skeleton placeholders
│       └── ReportIssue.jsx    # In-app bug report widget
├── scripts/
│   ├── import_fcd_deals.py    # FCD deal import utility
│   ├── production_smoke.py    # 50-probe schema + text smoke test suite
│   ├── probe_healthz.py       # Healthz liveness probe helper
│   └── build_rag_index.py     # Pre-build RAG document index
├── tho_documents/             # 64 PDF templates — DO NOT MODIFY (regulatory originals)
├── data/generated_docs/       # Output directory for generated PDFs (.gitignored; also /tmp/generated_docs on Cloud Run)
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
- **Partner API (`/api/v1/*`)**: Versioned external contract, authenticated with `THO_API_KEY`; do not change response shape without a version bump

## Guardrails
- **Never modify PDF templates** in `tho_documents/` — they are regulatory originals
- **Never log PII** (SSN, financial account numbers) — use `pii_guard.py` for sanitization
- **Never send PII to LLM** — strip PII fields before Gemini API calls
- **Keep backward compatibility** with existing `/api/documents/sales-contract` endpoint
- **Test against TMHA_SalesContract.pdf** as baseline reference for document generation

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

# Smoke test against production
python scripts/production_smoke.py
```

## Recent Features (PRs #38–#76)

| Feature | PR | Notes |
|---|---|---|
| Sentry error tracking | #67 | Backend + frontend; opt-in via `SENTRY_DSN` env var; PII scrubbed before send |
| Installable PWA | #68 | `vite-plugin-pwa` service worker; admin paths excluded from SW cache |
| Chat history persistence | — | `chat_history.py` stores full user+AI turns in Firestore; admin-only endpoints |
| Lead nurture digest | #39 | Stale-lead re-engagement in `tools/lead_nurture.py` |
| Inventory backfill scripts | #58/#59 | `lead_name_backfill.py`, `image_url_backfill.py` — both dry-run by default |
| Exterior-first photos + Floorplan tab | #43/#44/#70 | URL-based floorplan classification; exterior sorted before interior |
| Funnel analytics | #72 | `/api/admin/crm/funnel` dashboard panel |
| Lead source attribution | #73 | `/api/admin/crm/lead-sources` endpoint + chart |
| Inventory photo-dedup audit | #71 | Read-only report at `/api/admin/inventory/photo-audit` |
| Inventory analytics | #76 | Admin panel + `/api/analytics/inventory` |
| Passkey team guide | #62 | Docs only (guide in repo); auth scaffold is in `feat/passkey-auth-scaffold` branch, **not yet merged** |
| Voiceover generation | — | Google Cloud TTS via `/api/marketing/generate-voiceover` |
| Video generation | — | `tools/video_generator.py`, `/api/marketing/generate-video` |

## API Endpoints

### Core
- `POST /run` — Main chat interaction (ADK agent)
- `GET /health` — Readiness health check
- `GET /healthz` and `GET /healthz/` — Cloud Run liveness probe with status, version, and uptime. External smoke checks use `/healthz/` because Google Frontend reserves exact `/healthz` before it reaches FastAPI.
- `POST /apps/{app_name}/users/{user_id}/sessions/{session_id}` — ADK session creation

### Documents
- `GET /api/documents/templates` — List available document templates
- `GET /api/documents/templates/{name}/fields` — Field definitions for a template
- `GET /api/documents/readiness` — Document system readiness check (output dir, templates)
- `GET /api/documents/fields` — All field definitions across templates
- `POST /api/documents/generate` — Generate any mapped document
- `POST /api/documents/generate-packet` — Generate merged closing packet
- `POST /api/documents/generate-batch` — Generate multiple documents in one request
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

### Customers
- `GET /api/customers/search` — Search customers
- `GET /api/customers/stats` — Customer statistics
- `GET /api/customers/count` — Customer count
- `GET /api/customers/{id}` — Get customer details
- `POST /api/customers` — Create customer
- `PUT /api/customers/{id}` — Update customer

### Marketing
- `POST /api/marketing/generate-script` — Ad Studio script generation
- `GET /api/marketing/trending-ideas` — Trending content ideas
- `POST /api/marketing/schedule` — Schedule social post
- `GET /api/marketing/analytics` — Content performance analytics
- `POST /api/marketing/generate-image` — AI image generation
- `GET /api/marketing/images/{filename}` — Serve generated images
- `POST /api/marketing/generate-voiceover` — Google Cloud TTS voiceover from script
- `GET /api/marketing/voiceover-voices` — Available TTS voices
- `POST /api/marketing/generate-video` — AI video generation
- `GET /api/marketing/videos/{filename}` — Serve generated videos
- `GET /api/marketing/inventory-context` — Inventory data for ad creation

### Analytics (admin-gated)
- `GET /api/analytics/leads` — Lead analytics
- `GET /api/analytics/documents` — Document generation analytics
- `GET /api/analytics/inventory` — Inventory analytics
- `GET /api/analytics/chat` — Chat session analytics
- `GET /api/analytics/customers` — Customer analytics
- `GET /api/admin/crm/lead-sources` — Lead source attribution
- `GET /api/admin/inventory/photo-audit` — Photo dedup audit report
- `GET /api/admin/inventory/analytics` — Inventory admin analytics

### Inventory
- `GET /api/inventory` — List inventory
- `POST /api/inventory/bulk-import` — Bulk inventory import

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

### Chat History (admin-gated)
- `GET /api/chat/history/{session_id}` — Full conversation for a session
- `GET /api/chat/sessions` — List recent sessions
- `POST /api/chat/search` — Search conversations by query

### Email & Contact
- `POST /api/email/send` — Send custom email from CRM
- `GET /api/email/log` — Email activity log
- `POST /api/contact` — Contact form submission
- `POST /api/feedback` — In-app feedback submission

### Admin
- `POST /api/admin/verify` — Validate admin PIN (returns session token)
- `GET /api/admin/check` — Verify admin session token

### Partner API (versioned, `THO_API_KEY` auth)
- `GET /api/v1/customers` — List customers (partner-safe subset)
- `GET /api/v1/customers/{id}` — Get customer (partner-safe subset)
- `POST /api/v1/customers` — Create customer via partner integration
