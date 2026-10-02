# Operator checklist — DocuSeal deploy and Resend key

**STATUS: NOT RUN.**

This page is prepare-only. Nothing listed here has been executed. It does not
deploy DocuSeal, bind a Resend key, enable APIs, change DNS, change IAM, or
read, set, or rotate any live secret. The owner runs these steps later, in
order, and merges only when they choose to. A merge of the prepare PR is not
approval to promote Cloud Run traffic.

Do not paste secret values into git, issues, or chat. `.env.example` stays
empty for every secret below. Local `.env` is gitignored.

## What is already wired

| Piece | Where | What it does |
|---|---|---|
| Resend key read | `email_service.py` `_current_api_key()` | Reads env `RESEND_API_KEY` only. Secret Manager id to mount later: `resend-api-key`. |
| DocuSeal client read | `docuseal_service.py` | Reads `DOCUSEAL_API_URL`, `DOCUSEAL_API_TOKEN`, `DOCUSEAL_WEBHOOK_SECRET` only. |
| Empty placeholders | `.env.example` | Blank values. No key material. |
| Template shape | `config/docuseal_templates.example.json` | Example only. Runtime file is `config/docuseal_templates.json`, written by the uploader after a real upload. `config/field_map.json` stays the AcroForm registry and does not store DocuSeal ids. |
| Secret name list | `services/docuseal/secret-bindings.example.yaml` | Env var to Secret Manager id. No values. |
| App deploy | `.github/workflows/deploy.yml` | Still mounts only `admin-pin-hash` and `admin-session-secret`. Commented names are not active. |
| DocuSeal deploy | `.github/workflows/deploy-docuseal.yml` | `workflow_dispatch` only. Aborts unless `confirm` is exactly `YES`. |
| Commented sketch | `services/docuseal/cloudbuild.yaml` | **Do not uncomment.** It is not the activation path and uses different secret names. |

## Owner steps

### 1. Create or rotate the Resend key and store it in Secret Manager

**NOT RUN.**

1. In the Resend dashboard, create or rotate a sending API key.
2. Store that value in Secret Manager secret id `resend-api-key`. If the secret does not exist yet:

```bash
read -rsp "Resend API key: " RESEND_API_KEY; echo
printf '%s' "$RESEND_API_KEY" | gcloud secrets create resend-api-key \
  --project tho-ai-agent \
  --replication-policy=automatic \
  --data-file=-
unset RESEND_API_KEY
```

If `resend-api-key` already exists, add a version instead of creating it
(`docs/PRODUCTION_READINESS.md`). Unset the shell variable when finished.
Do not bind the secret onto Cloud Run until step 5.

### 2. Verify the sending domain

**NOT RUN.**

Follow `docs/resend_dns_records.md`. Leave the apex inbound MX unchanged.
In the Resend dashboard, confirm the sending domain status is verified before
any smoke send. The sender string is the existing `RESEND_FROM` env var
(`Texas Home Outlet <noreply@texashomeoutlet.com>`), not a new secret.

### 3. Deploy DocuSeal

**NOT RUN.**

Do not submit `services/docuseal/cloudbuild.yaml`.

When you intend to deploy, open GitHub Actions and run **Deploy DocuSeal
(e-sign server)** (`.github/workflows/deploy-docuseal.yml`):

- `confirm`: `YES` (any other value, including the default `NO`, aborts before APIs or Cloud Run)
- `region`: `us-central1`
- Leave the other inputs at their defaults unless you have a reason to change them

That workflow can create Secret Manager ids `docuseal-secret-key-base` and
`docuseal-db-password`. It does not put those values in git. Hand-run
equivalents, if you do not use the workflow, are Path A in
`docs/DOCUSEAL_DEPLOY_RUNBOOK.md`. Use the secret ids in
`services/docuseal/secret-bindings.example.yaml`, not `DOCUSEAL_SECRET_KEY` or
`DOCUSEAL_DATABASE_URL`.

Optional custom domain mapping is a separate DNS change. This checklist does
not authorize it. If you map one later, store that URL as `docuseal-api-url`.

### 4. Store the three app DocuSeal secrets

**NOT RUN.** After the DocuSeal URL is up, create the admin user in its UI and
copy the API token from Settings → API. Generate the webhook secret locally
and keep the same value for the DocuSeal webhook setting in step 6.

```bash
read -r DOCUSEAL_API_URL
printf '%s' "$DOCUSEAL_API_URL" | gcloud secrets create docuseal-api-url \
  --data-file=- --project=tho-ai-agent
unset DOCUSEAL_API_URL

read -rsp "DocuSeal API token: " DOCUSEAL_API_TOKEN; echo
printf '%s' "$DOCUSEAL_API_TOKEN" | gcloud secrets create docuseal-api-token \
  --data-file=- --project=tho-ai-agent
unset DOCUSEAL_API_TOKEN

printf '%s' "$(openssl rand -hex 32)" | gcloud secrets create docuseal-webhook-secret \
  --data-file=- --project=tho-ai-agent
```

Use `gcloud secrets versions add` instead of `create` when a secret id already
exists. The app reads these only after they are mounted as env vars.

### 5. Mount the app secrets

**NOT RUN.** Do this only after every secret id in step 1 and step 4 exists.
Appending a missing secret to `--update-secrets` fails the candidate deploy.

In `.github/workflows/deploy.yml`, extend the existing `--update-secrets` line
so it also includes:

```text
RESEND_API_KEY=resend-api-key:latest,DOCUSEAL_API_URL=docuseal-api-url:latest,DOCUSEAL_API_TOKEN=docuseal-api-token:latest,DOCUSEAL_WEBHOOK_SECRET=docuseal-webhook-secret:latest
```

Open that change as its own PR. The app workflow deploys with
`--no-traffic --tag=candidate`. Promoting traffic is a separate approval and
is not part of this checklist.

If the runtime service account cannot read the new secrets, granting
`roles/secretmanager.secretAccessor` is a separate owner IAM action. This
prepare change does not grant it.

### 6. Point the webhook and upload templates

**NOT RUN.**

1. In DocuSeal → Settings → Webhooks, set the URL to
   `https://<app-domain>/api/docuseal/webhook` and set the webhook secret to
   the value stored in `docuseal-webhook-secret`.
2. Dry-run, then upload. Export the URL and token in your shell. Do not commit them.

```bash
python tools/docuseal_template_uploader.py
python tools/docuseal_template_uploader.py --apply
```

`--apply` writes `config/docuseal_templates.json` (`docuseal_template_id` and
`fields_count` per PDF filename). Review that file for ids only, then commit
it. The shape is `config/docuseal_templates.example.json`. Do not put API
tokens in that file.

### 7. Smoke test

**NOT RUN.**

1. Email: with an admin token, `GET /healthz/detailed` and confirm
   `dependencies.email` is configured. Do not print the key. Send one message
   to an address you control. Commands are in `docs/PRODUCTION_READINESS.md`.
2. DocuSeal: one sandbox submission as in `docs/DOCUSEAL_DEPLOY_RUNBOOK.md`
   Step 9. Confirm the webhook returns 200 and the signed PDF lands where that
   step describes.

## Stop / rollback

**NOT RUN.** Remove the four app bindings from `--update-secrets` and deploy
another candidate. With the env vars unset, email send no-ops and DocuSeal
routes stay unconfigured. Deleting the DocuSeal Cloud Run service is a
separate owner action (`docs/DOCUSEAL_DEPLOY_RUNBOOK.md` rollback). It is not
part of this prepare change.
