# DocuSeal Deployment Runbook — Turning E-Signature On

**Prepared for:** Texas Home Outlet (THO) — Project Go Forward
**Companion docs:** `DOCUSEAL_INTEGRATION_SPEC.md` (architecture/cost), `TX_MH_COMPLIANCE_RESEARCH.md` (legal basis)

The DocuSeal integration is **already built in code** — `docuseal_service.py`, the
`/api/docuseal/send` + `/api/docuseal/webhook` endpoints, the ESIGN consent step
(`esign_consent.py`), and the CRM **"Send for Signature"** button. It is **inert
until three env vars are set**. This runbook turns it on.

## What activates the feature

| Env var | Used by | Purpose |
|---|---|---|
| `DOCUSEAL_API_URL` | `docuseal_service.py`, uploader | Base URL of the DocuSeal instance (e.g. `https://sign.texashomeoutlet.com`). The code appends `/api/submissions`. |
| `DOCUSEAL_API_TOKEN` | `docuseal_service.py`, uploader | Admin API token (DocuSeal → Settings → API). Sent as the `X-Auth-Token` header. |
| `DOCUSEAL_WEBHOOK_SECRET` | `/api/docuseal/webhook` | Shared secret used to verify the `X-Docuseal-Signature` HMAC on completion webhooks. Until set, the webhook is a safe no-op (200). |

When all three are set, the app sends packets for signature and mirrors signed
PDFs to `gs://tho-secure-documents/signed_documents/<deal_id>/<file>`.

---

## Step 0 — Decide hosting

Per `DOCUSEAL_INTEGRATION_SPEC.md`: **self-host on Cloud Run (recommended)** to keep
PII in-project and avoid per-document fees, or use the **SaaS** ($200/yr Pro) to
skip infra. Pick one path below.

---

## Path A — Self-host on Cloud Run (recommended)

> Same GCP project as the app: `tho-ai-agent`, region `us-central1`.

### A1. Provision Postgres (Cloud SQL)
```bash
gcloud sql instances create docuseal-db \
  --project=tho-ai-agent --region=us-central1 \
  --database-version=POSTGRES_15 --tier=db-g1-small --storage-size=10GB
gcloud sql databases create docuseal --instance=docuseal-db --project=tho-ai-agent
gcloud sql users create docuseal --instance=docuseal-db --password='<STRONG_PW>' --project=tho-ai-agent
```

### A2. Deploy the DocuSeal container
Use the official image `docuseal/docuseal:latest`. It needs `DATABASE_URL` and a
persistent `SECRET_KEY_BASE`. Files/PDFs should go to GCS (DocuSeal supports
S3-compatible/Google Storage) so they survive restarts.
```bash
gcloud run deploy docuseal \
  --image=docuseal/docuseal:latest \
  --project=tho-ai-agent --region=us-central1 \
  --port=3000 --allow-unauthenticated \
  --min-instances=1 \
  --add-cloudsql-instances=tho-ai-agent:us-central1:docuseal-db \
  --set-env-vars=DATABASE_URL='postgresql://docuseal:<STRONG_PW>@/docuseal?host=/cloudsql/tho-ai-agent:us-central1:docuseal-db' \
  --set-secrets=SECRET_KEY_BASE=docuseal-secret-key-base:latest
```
> Generate `SECRET_KEY_BASE` once (`openssl rand -hex 64`) and store it in Secret
> Manager — losing it invalidates existing sessions/encrypted columns.

### A3. Custom domain (optional but recommended)
Map `sign.texashomeoutlet.com` to the `docuseal` Cloud Run service
(`gcloud run domain-mappings create`). Use this as `DOCUSEAL_API_URL`.

### A4. Create admin + API token
Open the DocuSeal URL → create the first admin account → **Settings → API** →
copy the API token. Store it in Secret Manager (A6).

---

## Path B — SaaS (docuseal.com)
Sign up, upgrade to **Pro** (API/embedding requires Pro), then **Settings → API**
to get the token. `DOCUSEAL_API_URL = https://api.docuseal.com`. Skip A1–A3.

---

## Step A5/B5 — Generate the webhook secret
```bash
openssl rand -hex 32     # this value is DOCUSEAL_WEBHOOK_SECRET
```

## Step 6 — Store the three secrets in Secret Manager
```bash
printf '%s' 'https://sign.texashomeoutlet.com' | gcloud secrets create docuseal-api-url --data-file=- --project=tho-ai-agent
printf '%s' '<API_TOKEN>'                       | gcloud secrets create docuseal-api-token --data-file=- --project=tho-ai-agent
printf '%s' '<WEBHOOK_SECRET>'                  | gcloud secrets create docuseal-webhook-secret --data-file=- --project=tho-ai-agent
```
Then wire them into the **app** deploy. Add to `.github/workflows/deploy.yml`'s
`--update-secrets` line (alongside the existing `ADMIN_PIN_HASH` / `ADMIN_SESSION_SECRET`):
```
--update-secrets=...,DOCUSEAL_API_URL=docuseal-api-url:latest,DOCUSEAL_API_TOKEN=docuseal-api-token:latest,DOCUSEAL_WEBHOOK_SECRET=docuseal-webhook-secret:latest
```
Redeploy the app (push to `main`, or `gcloud run deploy`). Confirm the Cloud Run
service account has `roles/secretmanager.secretAccessor` on the three secrets.

## Step 7 — Upload the THO templates into DocuSeal
This creates `config/docuseal_templates.json` (filename → DocuSeal template ID),
which `docuseal_service.send_for_signature` uses for the template-based flow.
```bash
export DOCUSEAL_API_URL=https://sign.texashomeoutlet.com
export DOCUSEAL_API_TOKEN=<API_TOKEN>
python tools/docuseal_template_uploader.py            # dry-run first
python tools/docuseal_template_uploader.py --apply    # actually upload
git add config/docuseal_templates.json && git commit -m "chore(esign): add DocuSeal template mapping"
```
> The custom-packet flow (`send_file_for_signature`, used by the deal closing
> packet) does **not** need templates — it uploads the generated PDF directly and
> already prepends the ESIGN consent page.

## Step 8 — Configure the webhook in DocuSeal
DocuSeal → **Settings → Webhooks** → add URL:
`https://<app-domain>/api/docuseal/webhook`, subscribe to **form.completed /
submission.completed**.

> ⚠️ **Verify the signing scheme.** The app expects DocuSeal to send
> `X-Docuseal-Signature` = `HMAC_SHA256(raw_body, DOCUSEAL_WEBHOOK_SECRET)` (hex).
> Confirm your DocuSeal version signs webhooks this way and that its webhook
> secret equals `DOCUSEAL_WEBHOOK_SECRET`. If your version uses a different
> header/scheme, either set DocuSeal's secret to match, or adjust the
> verification in `main.py::docuseal_webhook`. **If this is wrong, completion
> webhooks are rejected (401) and signed PDFs won't mirror to GCS** — so test it
> (Step 9) before going live.

## Step 9 — End-to-end verification
1. In the CRM, open a test deal with a buyer email → **Send for Signature** (or
   `POST /api/deals/{id}/generate-packet`, which auto-dispatches).
2. Confirm the signer receives the email and sees the **ESIGN consent page first**,
   then the packet.
3. Complete signing. Confirm:
   - the webhook returns 200 (not 401) — check Cloud Run logs for
     `DocuSeal signed PDF mirrored`;
   - the signed PDF appears at `gs://tho-secure-documents/signed_documents/<deal_id>/`;
   - a note/audit entry is recorded on the deal.

## Step 10 — Disable / rollback
Unset (or remove from `--update-secrets`) `DOCUSEAL_API_URL` / `DOCUSEAL_API_TOKEN`
and redeploy. The send paths return "not configured" and the webhook no-ops — the
app is fully functional without e-sign.

---

## Security & compliance checklist
- [ ] All three values live **only** in Secret Manager (never committed).
- [ ] Webhook HMAC verified end-to-end (Step 8/9) — no 401s on real completions.
- [ ] Signed PDFs land in GCS with the **6-year retention** lifecycle (10 TAC §80.30).
- [ ] ESIGN consent page appears first in every consumer packet (Step 9.2).
- [ ] DocuSeal admin uses a strong password + 2FA; API token scoped/rotated.
- [ ] If self-hosting, run the **official image unmodified** (AGPL — see spec).

*Pricing/feature details as of June 2026; confirm at signup. This runbook covers
turning on existing code — no app code changes are required beyond Step 6/7.*
