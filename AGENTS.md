# AGENTS.md — Project-Go-Forward

## What this repo does

Texas Home Outlet's live business system. FastAPI + React app serving the public storefront, internal CRM, document generation, marketing studio, and partner API. Every push to `main` builds, deploys, and smokes a zero-traffic Cloud Run candidate; production traffic promotion remains a separate operator action.

## Key directories and files

| Path | Role |
|---|---|
| `main.py` | FastAPI service, API routes, SPA hosting |
| `root_agent.py` | Google ADK agent orchestration |
| `frontend/src/` | React app, public app, admin surfaces |
| `database/` | Firestore client and Pydantic models |
| `tools/` | Inventory, CRM, document, marketing, RAG tools |
| `schemas/` | Request/response and document schemas |
| `config/field_map.json` | PDF template field registry |
| `tho_documents/` | Regulatory PDF originals (read-only) |
| `tests/` | Backend tests and smoke coverage |

## How to run tests / dev server

```bash
# Install
pip install -r requirements.txt -r requirements-dev.txt
npm --prefix frontend install

# Build frontend
npm --prefix frontend run build

# Run server
python main.py   # http://127.0.0.1:8080

# Tests
python -m pytest tests/test_healthz.py tests/test_api_v1.py tests/test_document_engine.py -q
```

## Safety boundaries

1. **Do NOT** expose secrets, API keys, or admin PINs in code, logs, or chat
2. **Do NOT** modify `tho_documents/` regulatory PDFs in agent work
3. **Do NOT** send unsanitized PII to Gemini; use `tools/pii_guard.py`
4. **Do NOT** push directly to `main`. Branch and open a PR. A green THO PR may be merged without a separate human approval under the canonical `/Users/aribs/.codex/AGENTS.md`, but first explicitly observe every applicable check finish green; the GitHub ruleset currently requires a PR but does not enforce the `test` status check.
5. **Do NOT** modify Firestore schema without updating `database/models.py` and tests
6. Pre-commit before push: `pre-commit run --files <changed-files>`

## Current status

- Canonical production site: `https://www.texashomeoutlet.com` (the `tho.sapphirealpha.xyz` alias remains available for operator diagnostics)
- Cloud Run service `project-go-forward` in `tho-ai-agent`
- Health: `/health` (readiness), `/healthz/` (liveness)
- Merges to `main` create a tested zero-traffic `candidate` revision; they do not promote production traffic
- Rollback via `gcloud run revisions list` + `gcloud run services update-traffic`
