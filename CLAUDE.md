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
├── main.py                    # FastAPI server, all API endpoints
├── root_agent.py              # Multi-agent orchestration (Sales + Service agents)
├── config.yaml                # Business config (name, hours, agent settings, deployment)
├── config_loader.py           # YAML config loader with caching
├── conversation_memory.py     # Session context tracking + preference extraction
├── lead_management.py         # Lead capture and Firestore CRM
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
│   └── pii_guard.py           # PII protection for logging and LLM calls
├── schemas/
│   ├── document_schemas.py    # Pydantic models for document requests/responses
│   ├── output_schemas.py      # ADK structured output schemas
│   ├── inventory_schema.json  # Inventory data validation
│   └── customer_schema.json   # Customer data validation
├── database/
│   ├── firestore_client.py    # Firestore CRUD (THODatabase singleton)
│   └── models.py              # Pydantic data models (Customer, Property, Sale, etc.)
├── frontend/src/
│   ├── App.jsx                # Main SPA with chat interface + page routing
│   ├── pages/
│   │   ├── DocumentCenter.jsx # Document template browser + generation UI
│   │   ├── AdStudio.jsx       # AI marketing content creator
│   │   ├── Analytics.jsx      # Usage analytics dashboard
│   │   └── Contact.jsx        # Contact form
│   └── components/
│       ├── SmartForm.jsx      # Dynamic form fields driven by field_map.json
│       ├── PropertyCard.jsx   # Home listing card with images
│       ├── ComparisonDrawer.jsx # Side-by-side home comparison
│       ├── SafeMarkdown.jsx   # Markdown renderer with property card detection
│       ├── QuickActions.jsx   # Predefined action buttons
│       └── SearchFilters.jsx  # Inventory search filter panel
├── scripts/
│   └── batch_inspect_pdfs.py  # PDF AcroForm field discovery utility
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
- `POST /run` — Main chat interaction (ADK agent)
- `GET /api/documents/templates` — List available document templates
- `GET /api/documents/templates/{name}/fields` — Field definitions for a template
- `POST /api/documents/generate` — Generate any mapped document
- `POST /api/documents/generate-packet` — Generate merged closing packet
- `POST /api/documents/sales-contract` — Legacy: TMHA sales contract only
- `GET /api/documents/download/{filename}` — Download generated PDF
- `POST /api/documents/extract-fields` — AI-extract form data from chat history
- `POST /api/marketing/generate-script` — Ad Studio script generation
- `GET /api/marketing/trending-ideas` — Trending content ideas
- `GET /leads/export` — CSV export of leads
- `GET /leads/stats` — Lead statistics
