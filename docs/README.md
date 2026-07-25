# THO App — Documentation Index

This folder holds the canonical documentation for the Project-Go-Forward / THO App codebase.

## Read in this order

1. [SHOWCASE.md](SHOWCASE.md) — Live demo path, verified URLs, audience-specific surfaces, and screenshot safety notes.
2. [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md) — Read-only smoke checks, local gates, admin-token handling, and Cloud Run rollback path.
3. [PIN_ROTATION_RUNBOOK.md](PIN_ROTATION_RUNBOOK.md) — Operator-only admin PIN hash rotation without exposing the PIN, token, or hash.
4. [ARCHITECTURE.md](ARCHITECTURE.md) — System overview, tech stack, cloud topology, deployment, repo layout, guardrails.
5. [DATA_MODEL.md](DATA_MODEL.md) — Firestore collections, entity fields, relationships, canonical IDs.
6. [WORKFLOWS.md](WORKFLOWS.md) — Business workflows with Mermaid diagrams (lead to funded, document generation, auth, CI/CD).
7. [SECURITY.md](SECURITY.md) — Auth model, secret hygiene, PII handling, least-privilege access matrix, delete-protection posture.
8. [INTEGRATION_NOTION.md](INTEGRATION_NOTION.md) — Integration plan for Etai's Notion workspace: division of responsibility, naming conventions, API contract, webhook flows, open decisions.
9. [API_REFERENCE.md](API_REFERENCE.md) — Generated endpoint reference from the app's OpenAPI schema (regenerate: `python scripts/generate_api_reference.py`).

## Older docs

The following files exist in the repo root and are superseded by this folder. Do not treat as authoritative:

- `INTEGRATION_GUIDE.md` — describes Firestore as living in `sapphire-479610`. That was never true in production; Firestore is in `tho-ai-agent`. Replaced by ARCHITECTURE.md + INTEGRATION_NOTION.md.
- `AGENT_GUIDE.md`, `MIGRATION_PLAN.md`, `IMPROVEMENTS.md` — historical. Check git blame before relying on them.
- `CLAUDE.md` (repo root) — operational guardrails for AI coding agents. Still current; read alongside ARCHITECTURE.md.
