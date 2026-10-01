# DocuSeal sidecar

**STATUS: NOT RUN.** Nothing in this directory deploys by itself.

The activation path is the owner checklist
[docs/OPERATOR_DOCUSEAL_RESEND_CHECKLIST.md](../../docs/OPERATOR_DOCUSEAL_RESEND_CHECKLIST.md).
Detailed command reference: [docs/DOCUSEAL_DEPLOY_RUNBOOK.md](../../docs/DOCUSEAL_DEPLOY_RUNBOOK.md).

## What to use

| File | Role |
|---|---|
| `.github/workflows/deploy-docuseal.yml` | Gated `workflow_dispatch` deploy. Aborts unless `confirm` is `YES`. |
| `secret-bindings.example.yaml` | Env var names and Secret Manager ids. No values. |
| `Dockerfile` | Local image notes. The gated workflow runs `docuseal/docuseal:latest` directly. |
| `cloudbuild.yaml` | Commented sketch. **Do not uncomment or submit.** Its secret names are not canonical. |

## Canonical names

App env vars (values only from the environment):

- `DOCUSEAL_API_URL` ← secret `docuseal-api-url`
- `DOCUSEAL_API_TOKEN` ← secret `docuseal-api-token`
- `DOCUSEAL_WEBHOOK_SECRET` ← secret `docuseal-webhook-secret`
- `RESEND_API_KEY` ← secret `resend-api-key`

Sidecar secrets created by the gated workflow, not stored in git:

- `docuseal-secret-key-base` → DocuSeal `SECRET_KEY_BASE`
- `docuseal-db-password` → Cloud SQL password used to assemble `DATABASE_URL`

Do not create `DOCUSEAL_SECRET_KEY` or `DOCUSEAL_DATABASE_URL`. Those names
belong to the commented sketch only.

Template ids, after the owner runs the uploader, go in
`config/docuseal_templates.json`. The committed shape example is
`config/docuseal_templates.example.json`.
