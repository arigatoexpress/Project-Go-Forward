# DocuSeal Deployment Runbook

> **Status:** NOT DEPLOYED — activation requires Ari's sign-off.  
> See [docs/integration/docuseal-design.md](../../docs/integration/docuseal-design.md) for architecture and full activation checklist.

---

## Pre-requisites

- GCP project `tho-ai-agent` with Cloud Run and Artifact Registry enabled
- `gcloud` CLI authenticated with `roles/run.admin` + `roles/secretmanager.admin`
- A dedicated service account `docuseal-sa@tho-ai-agent.iam.gserviceaccount.com`

## Step 1 — Create secrets

```bash
# 32-byte random key for DocuSeal's Rails session signing
gcloud secrets create DOCUSEAL_SECRET_KEY --replication-policy=automatic
echo -n "$(openssl rand -hex 32)" | \
  gcloud secrets versions add DOCUSEAL_SECRET_KEY --data-file=-

# Database URL (leave as default SQLite for MVP)
gcloud secrets create DOCUSEAL_DATABASE_URL --replication-policy=automatic
echo -n "sqlite3:///data/docuseal.sqlite3" | \
  gcloud secrets versions add DOCUSEAL_DATABASE_URL --data-file=-
```

## Step 2 — Service account permissions

```bash
SA=docuseal-sa@tho-ai-agent.iam.gserviceaccount.com

# Read secrets
gcloud projects add-iam-policy-binding tho-ai-agent \
  --member="serviceAccount:$SA" \
  --role="roles/secretmanager.secretAccessor"

# Write to GCS (for future direct-upload flows)
gsutil iam ch serviceAccount:$SA:objectCreator gs://tho-secure-documents
```

## Step 3 — Deploy

Uncomment `cloudbuild.yaml` and run:

```bash
gcloud builds submit --config services/docuseal/cloudbuild.yaml .
```

Or deploy directly:

```bash
gcloud run deploy docuseal \
  --image=docuseal/docuseal:latest \
  --region=us-central1 \
  --project=tho-ai-agent \
  --port=8080 \
  --memory=512Mi \
  --no-allow-unauthenticated \
  --set-secrets=SECRET_KEY_BASE=DOCUSEAL_SECRET_KEY:latest
```

## Step 4 — Wire up THO main service

Add the following env vars to the THO Cloud Run service (do NOT add them until DocuSeal is running):

```bash
DOCUSEAL_API_URL=https://docuseal-<hash>-uc.a.run.app
DOCUSEAL_API_TOKEN=<token from DocuSeal admin panel>
DOCUSEAL_WEBHOOK_SECRET=<random 32-byte hex>
```

## Step 5 — Upload templates

```bash
# Upload TMHA_SalesContract.pdf to DocuSeal
curl -X POST "$DOCUSEAL_API_URL/api/templates/pdf" \
  -H "X-Auth-Token: $DOCUSEAL_API_TOKEN" \
  -F "file=@tho_documents/TMHA_SalesContract.pdf" \
  -F "name=TMHA Sales Contract"
# Record the returned template ID → add to config/field_map.json
```

## Step 6 — Configure webhook

In the DocuSeal admin panel (Settings → Webhooks):
- URL: `https://<tho-service-url>/api/docuseal/webhook`
- Secret: value of `DOCUSEAL_WEBHOOK_SECRET`
- Events: `form.completed`

## Step 7 — Smoke test

```bash
# Create a test submission
curl -X POST "$DOCUSEAL_API_URL/api/submissions" \
  -H "X-Auth-Token: $DOCUSEAL_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "template_id": <template_id>,
    "send_email": false,
    "submitters": [{"role": "Buyer", "email": "test@example.com", "name": "Test Buyer"}],
    "metadata": {"deal_id": "test-deal-001"}
  }'
```

Complete the signing flow at the returned URL, then verify:
1. DocuSeal sends webhook to THO
2. Signed PDF appears at `gs://tho-secure-documents/signed_documents/test-deal-001/signed_<id>.pdf`
3. Deal note appears in Firestore `deal_notes` collection

## Rollback

```bash
gcloud run services delete docuseal --region=us-central1 --project=tho-ai-agent
```

THO main service continues working — the DocuSeal endpoints return `501 Not
Implemented` when `DOCUSEAL_API_URL` is unset, so removing the sidecar has no
impact on document generation.
