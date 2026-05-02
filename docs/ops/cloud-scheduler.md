# Cloud Scheduler — Inventory Sync

Automatically hits `POST /api/admin/jobs/inventory-sync` twice daily so new
listings and price changes land in Firestore without manual intervention.

## Prerequisites

| What | Value |
|---|---|
| GCP project | `tho-ai-agent` |
| Cloud Run service | `project-go-forward` |
| Region | `us-central1` |
| Service account | `inventory-scheduler@tho-ai-agent.iam.gserviceaccount.com` |

Create the service account if it does not exist yet:

```bash
gcloud iam service-accounts create inventory-scheduler \
  --display-name="Inventory Sync Scheduler" \
  --project=tho-ai-agent
```

Grant it the Cloud Run Invoker role on the service:

```bash
gcloud run services add-iam-policy-binding project-go-forward \
  --region=us-central1 \
  --member="serviceAccount:inventory-scheduler@tho-ai-agent.iam.gserviceaccount.com" \
  --role="roles/run.invoker" \
  --project=tho-ai-agent
```

## Create the Scheduler Jobs

The endpoint requires an `X-Admin-Token` header.  Cloud Scheduler injects it
via an OIDC token from the service account **plus** a static `X-Admin-Token`
header that holds a long-lived token generated from the admin PIN.

### Step 1 — generate a long-lived admin token

```bash
# On a machine with the admin PIN available:
python - <<'PY'
import hashlib, hmac, base64, struct, time, os

PIN = os.environ["ADMIN_PIN"]          # e.g. export ADMIN_PIN=4832
pin_hash = hashlib.sha256(PIN.encode()).hexdigest()
secret   = hashlib.sha256(f"sapphire-jwt-{pin_hash[:16]}".encode()).digest()

TTL      = 365 * 24 * 3600            # 1-year token for scheduler use
expires  = int(time.time()) + TTL
payload  = struct.pack(">Q", expires)
sig      = hmac.new(secret, payload, hashlib.sha256).digest()[:16]
token    = base64.urlsafe_b64encode(payload + sig).decode().rstrip("=")
print(token)
PY
```

Store the printed token in Secret Manager:

```bash
echo -n "<TOKEN>" | gcloud secrets create inventory-sync-admin-token \
  --data-file=- \
  --project=tho-ai-agent
```

### Step 2 — create the 6 AM Mountain job

Mountain Time = UTC-6 (standard) / UTC-7 (daylight saving).
`12:00 UTC` covers 6 AM MDT; use `13:00 UTC` during MST.
The schedule below uses `12,13 * * *` so it fires at both offsets and the
server-side 30-minute circuit-breaker deduplicates the second trigger.

```bash
SERVICE_URL=$(gcloud run services describe project-go-forward \
  --region=us-central1 --format='value(status.url)' --project=tho-ai-agent)

ADMIN_TOKEN=$(gcloud secrets versions access latest \
  --secret=inventory-sync-admin-token --project=tho-ai-agent)

# 6 AM Mountain (runs at 12:00 UTC and 13:00 UTC to cover DST)
gcloud scheduler jobs create http inventory-sync-morning \
  --location=us-central1 \
  --schedule="0 12,13 * * *" \
  --time-zone="UTC" \
  --uri="${SERVICE_URL}/api/admin/jobs/inventory-sync" \
  --http-method=POST \
  --headers="Content-Type=application/json,X-Admin-Token=${ADMIN_TOKEN}" \
  --message-body="{}" \
  --oidc-service-account-email="inventory-scheduler@tho-ai-agent.iam.gserviceaccount.com" \
  --oidc-token-audience="${SERVICE_URL}" \
  --attempt-deadline=10m \
  --project=tho-ai-agent
```

### Step 3 — create the 6 PM Mountain job

```bash
# 6 PM Mountain (runs at 00:00 UTC and 01:00 UTC to cover DST)
gcloud scheduler jobs create http inventory-sync-evening \
  --location=us-central1 \
  --schedule="0 0,1 * * *" \
  --time-zone="UTC" \
  --uri="${SERVICE_URL}/api/admin/jobs/inventory-sync" \
  --http-method=POST \
  --headers="Content-Type=application/json,X-Admin-Token=${ADMIN_TOKEN}" \
  --message-body="{}" \
  --oidc-service-account-email="inventory-scheduler@tho-ai-agent.iam.gserviceaccount.com" \
  --oidc-token-audience="${SERVICE_URL}" \
  --attempt-deadline=10m \
  --project=tho-ai-agent
```

## Verify

```bash
# List jobs
gcloud scheduler jobs list --location=us-central1 --project=tho-ai-agent

# Trigger manually
gcloud scheduler jobs run inventory-sync-morning \
  --location=us-central1 --project=tho-ai-agent

# View recent run history
gcloud scheduler jobs describe inventory-sync-morning \
  --location=us-central1 --project=tho-ai-agent
```

## Circuit Breaker

The endpoint refuses runs that occur within **30 minutes** of the last
successful sync (HTTP 429 with `Retry-After` header).  This prevents
back-to-back Cloud Scheduler triggers (DST overlap above) from hammering
the source website.  The timestamp is stored in Firestore at
`system_state/inventory_sync_job.last_run_at`.

## Price-Change Audit Log

When a home's `sale_price` changes by more than **15 %** during a sync,
a record is written to `inventory_price_changes`:

| Field | Type | Description |
|---|---|---|
| `inventory_id` | string | Firestore document ID |
| `model_name` | string | Human-readable home name |
| `old_price` | number | Price before this sync |
| `new_price` | number | Price after this sync |
| `pct_change` | number | Signed ratio, e.g. `-0.20` = −20 % |
| `detected_at` | ISO-8601 | Timestamp of detection |

Query via the CRM dashboard widget or directly:

```
GET /api/admin/inventory/price-changes?days=7
```
