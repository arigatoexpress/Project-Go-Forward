# THO App — Documentation Index

This folder holds the canonical documentation for the Project-Go-Forward / THO App codebase.

## Read in this order

Start with [CLIENT_HANDOFF_ACCEPTANCE.md](CLIENT_HANDOFF_ACCEPTANCE.md) for the
remaining client ownership and workflow checks. [CLIENT_WALKTHROUGH.md](CLIENT_WALKTHROUGH.md)
is the staff guide. A dated readiness report does not prove current service health
or client acceptance.

1. [OPERATOR_DOCUSEAL_RESEND_CHECKLIST.md](OPERATOR_DOCUSEAL_RESEND_CHECKLIST.md) — Owner steps for the Resend key and DocuSeal deploy. **STATUS: NOT RUN.** Prepare-only; nothing in that checklist has been executed.
2. [SHOWCASE.md](SHOWCASE.md) — Live demo path, verified URLs, audience-specific surfaces, and screenshot safety notes.
3. [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md) — Read-only smoke checks, local gates, admin-token handling, and Cloud Run rollback path.
4. [PIN_ROTATION_RUNBOOK.md](PIN_ROTATION_RUNBOOK.md) — Operator-only admin PIN hash rotation without exposing the PIN, token, or hash.
5. [ARCHITECTURE.md](ARCHITECTURE.md) — System overview, tech stack, cloud topology, deployment, repo layout, guardrails.
6. [DATA_MODEL.md](DATA_MODEL.md) — Firestore collections, entity fields, relationships, canonical IDs.
7. [WORKFLOWS.md](WORKFLOWS.md) — Business workflows with Mermaid diagrams (lead to funded, document generation, auth, CI/CD).
8. [SECURITY.md](SECURITY.md) — Auth model, secret hygiene, PII handling, least-privilege access matrix, delete-protection posture.
9. [INTEGRATION_NOTION.md](INTEGRATION_NOTION.md) — Integration plan for Etai's Notion workspace: division of responsibility, naming conventions, API contract, webhook flows, open decisions.
10. [API_REFERENCE.md](API_REFERENCE.md) — Generated endpoint reference from the app's OpenAPI schema (regenerate: `python scripts/generate_api_reference.py`).

## Older docs

The following files exist in the repo root and are superseded by this folder. Do not treat as authoritative:

- `INTEGRATION_GUIDE.md` — describes Firestore as living in `sapphire-479610`. That was never true in production; Firestore is in `tho-ai-agent`. Replaced by ARCHITECTURE.md + INTEGRATION_NOTION.md.
- `AGENT_GUIDE.md`, `MIGRATION_PLAN.md`, `IMPROVEMENTS.md` — historical. Check git blame before relying on them.
- `CLAUDE.md` (repo root) — operational guardrails for AI coding agents. Still current; read alongside ARCHITECTURE.md.
