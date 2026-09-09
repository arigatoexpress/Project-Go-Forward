# AGENTS.md — Project-Go-Forward

## What this repo does

Texas Home Outlet's live business system. FastAPI + React app serving the public storefront, internal CRM, document generation, marketing studio, and partner API. The `main` workflow builds and verifies a Cloud Run candidate with `--no-traffic --tag=candidate`. Production traffic stays pinned to an explicitly selected revision until an approved promotion.

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
4. **Do NOT** push directly to `main`. Use a branch and PR; observe every applicable check finish green before merging. PR merges are authorized under the standing operator policy; a merge does not authorize production traffic promotion.
5. **Do NOT** modify Firestore schema without updating `database/models.py` and tests
6. Pre-commit before push: `pre-commit run --files <changed-files>`
7. Direct production deploys, traffic promotion or rollback, DNS changes, and outward messages require explicit operator approval. Prepare the exact candidate revision, expected commit, verification evidence, and rollback revision before requesting a traffic change; see `docs/RUNBOOK.md`.

## Current status

- Canonical storefront: `https://www.texashomeoutlet.com`; `https://tho.sapphirealpha.xyz` is a legacy origin
- Cloud Run service `project-go-forward` in `tho-ai-agent`
- Health: `/health` (readiness), `/healthz/` (liveness)
- Candidate and serving revision identity must be checked separately. Traffic changes use an approved explicit revision, never an unverified `--to-latest` target.
