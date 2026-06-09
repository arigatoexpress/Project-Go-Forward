# Project Go Forward

[![Production](https://img.shields.io/badge/production-tho.sapphirealpha.xyz-0f766e)](https://tho.sapphirealpha.xyz/)
[![Backend](https://img.shields.io/badge/backend-FastAPI%20%2B%20Firestore-009688)](main.py)
[![Frontend](https://img.shields.io/badge/frontend-React%2019%20%2B%20Vite-111827)](frontend/)

**Texas Home Outlet's digital operating layer — a business system replacement concept.**

Serves the public storefront, internal CRM, document generation, marketing studio, and partner API from a single Cloud Run service. This repo is a concrete product, not a generic framework.

## What this does

A FastAPI + React application that powers the live THO site:
- Customer-facing inventory browsing and AI sales assistant
- Internal CRM (leads, customers, deals, appointments)
- Document Center (regulatory PDF packet generation)
- Ad Studio (inventory-aware marketing campaigns)
- Partner API (`/api/v1/*`) for Notion, n8n, and third-party integrations

## Quick start

```bash
git clone https://github.com/arigatoexpress/Project-Go-Forward.git
cd Project-Go-Forward
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
npm --prefix frontend install
npm --prefix frontend run build
python main.py
```

Local app: `http://127.0.0.1:8080`

Focused checks:
```bash
python -m pytest tests/test_healthz.py tests/test_api_v1.py tests/test_document_engine.py -q
npm --prefix frontend run build
ruff check .
```

## Architecture

```
Buyer / Staff
      │
      ▼
┌─────────────────┐
│ React 19 + Vite │
│   (SPA + Studio)│
└────────┬────────┘
         │
         ▼
┌─────────────────────────┐
│ FastAPI Cloud Run svc   │
│  - Public storefront    │
│  - Admin surfaces       │
│  - Partner API /api/v1  │
│  - Document + RAG       │
└────────┬────────────────┘
         │
    ┌────┴────┐
    ▼         ▼
Firestore   PDF templates
(tho-ai-agent)  (tho_documents/)
```

## Key features

- **Public storefront** — inventory search, comparison, AI chat
- **CRM** — leads, customers, deals, appointments, email activity
- **Document Center** — field-map driven PDF packets, batch generation
- **Ad Studio** — inventory-aware campaigns, scripts, media workflow
- **Analytics** — lead, document, inventory, and customer metrics
- **Partner API** — customers, inventory, leads, stats, webhook notify, regulatory RAG

## Tech stack

- Python 3.11, FastAPI, Firestore, Pydantic
- React 19 + Vite, vanilla CSS
- Google ADK + Gemini for AI assistant
- Cloud Run, Workload Identity Federation

## Live surfaces

| Surface | Path | Access |
|---|---|---|
| Storefront | `/` | Public |
| Ad Studio | `/studio` | Admin-gated |
| Document Center | `/documents` | Admin-gated |
| CRM | `/crm` | Admin-gated |
| Analytics | `/analytics` | Admin-gated |
| Partner API | `/api/v1/*` | `THO_API_KEY` |
| Health | `/health`, `/healthz/` | Public |

## Governance notes

- **Production-adjacent** — auto-deploys to Cloud Run on push to `main`
- **PII boundaries** — customer data is PII; sanitize before sending to Gemini
- **Admin PIN** — hashed with SHA-256; never paste plaintext into chat or docs
- **Regulatory PDFs** in `tho_documents/` are source documents; do not modify casually
- **Firestore** lives in `tho-ai-agent`; DNS in `sapphire-479610`

## Agent collaborators

See [AGENTS.md](AGENTS.md) for branch conventions, safety boundaries, rollback commands, and file ownership.

## Documentation

- [docs/SHOWCASE.md](docs/SHOWCASE.md) — demo script and screenshot safety
- [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md) — smoke checks and rollback
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — system overview and deployment
- [docs/SECURITY.md](docs/SECURITY.md) — auth model and PII handling
