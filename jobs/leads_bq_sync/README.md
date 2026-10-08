# tho-leads-bq — daily leads → BigQuery sync (Cloud Run Job)

Runs `scripts/sync_leads_to_bigquery.py` every day at **06:15 America/Denver**.
It reads the Firestore collections `leads`, `analytics_events`, `chat_sessions`
and `appointments`, builds a **PII-free** projection (no names, phones, emails,
messages, IPs or raw journey IDs), and reloads `tho-ai-agent.tho_analytics`:
tables `leads`, `events`, `daily_metrics` plus the reporting views
(`v_lead_summary`, `v_funnel_30d`, `v_home_conversion`, …) that Looker Studio reads.

Every load is `WRITE_TRUNCATE`, so a re-run (or a manual run on the same day)
replaces the tables instead of appending. Running it twice never duplicates rows.

Until 2026-10-07 this ran from the ops laptop (launchd `com.tho.leads-bq-sync`)
on the laptop owner's gcloud login, so analytics went stale whenever the laptop
was asleep. It now runs in Google Cloud on its own service account.

## What runs where

| Piece | Value |
|---|---|
| Cloud Run Job | `tho-leads-bq`, region `us-central1`, 1 task, max 1 retry, 30 min timeout (runs take ~3–6 min) |
| Image | `us-central1-docker.pkg.dev/tho-ai-agent/cloud-run-source-deploy/tho-leads-bq:<git sha>` (this folder's `Dockerfile`; only the sync script + 2 client libraries) |
| Runs as | `tho-leads-bq@tho-ai-agent.iam.gserviceaccount.com` (no keys) |
| Schedule | Cloud Scheduler `tho-leads-bq-daily`, `15 6 * * *`, time zone `America/Denver`, OAuth as the same SA |

## Permissions (least privilege)

| Grant | Scope | Why |
|---|---|---|
| `roles/bigquery.jobUser` | project `tho-ai-agent` | run load jobs |
| `roles/bigquery.dataEditor` | dataset `tho_analytics` only (dataset ACL) | replace tables, recreate views |
| `roles/datastore.viewer` | project `tho-ai-agent` (Firestore cannot be scoped per collection) | read the four source collections |
| `roles/run.invoker` | job `tho-leads-bq` only | lets Cloud Scheduler start the job |

The script looks the dataset up before creating it (`ensure_dataset`), so the
job does not need project-wide `bigquery.datasets.create`.

## Build and deploy

```bash
# from the repo root
TAG=$(git rev-parse --short=12 HEAD)
gcloud builds submit --project tho-ai-agent \
  --config jobs/leads_bq_sync/cloudbuild.yaml --substitutions _TAG=$TAG .

gcloud run jobs deploy tho-leads-bq --project tho-ai-agent --region us-central1 \
  --image us-central1-docker.pkg.dev/tho-ai-agent/cloud-run-source-deploy/tho-leads-bq:$TAG \
  --service-account tho-leads-bq@tho-ai-agent.iam.gserviceaccount.com \
  --tasks 1 --max-retries 1 --task-timeout 30m --cpu 1 --memory 1Gi
```

## Check it

```bash
# run now and wait (safe any time: full reload, no duplicates)
gcloud run jobs execute tho-leads-bq --project tho-ai-agent --region us-central1 --wait

# preview only, no BigQuery writes
gcloud run jobs execute tho-leads-bq --project tho-ai-agent --region us-central1 --wait --args=--dry-run

# freshness
bq query --project_id tho-ai-agent --use_legacy_sql=false \
  'SELECT * FROM `tho-ai-agent.tho_analytics.v_lead_freshness`'
```

Logs: Cloud Logging, resource `cloud_run_job`, job `tho-leads-bq`. A run with
zero leads in 3 days prints a `THO LEAD ALERT` line to stderr.
