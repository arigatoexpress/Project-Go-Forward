# Leads → BigQuery sync

**Gate:** owner. This job reads Firestore leads / analytics events and writes a
PII-free projection to BigQuery. Do not attach extra credentials or broaden
the job service account.

## What it loads

[`scripts/sync_leads_to_bigquery.py`](../../scripts/sync_leads_to_bigquery.py)
rebuilds dataset `tho_analytics` in project `tho-ai-agent` (full
`WRITE_TRUNCATE` reload). Each run loads:

- table `daily_metrics`
- views `v_funnel_30d`, `v_home_conversion`, `v_conversion_by_source`

(and the script's other reporting views). Names, phones, emails, and other PII
are never copied — only attribution, status, timing, and `has_*` flags.

## Runtime shape (owner-approved 2026-10-07)

The sync is being moved off the owner's laptop onto a scheduled Cloud Run Job:

| Piece | Value |
|---|---|
| Job | `tho-leads-bq` |
| Project / region | `tho-ai-agent` / `us-central1` |
| Trigger | Cloud Scheduler, daily **06:15 America/Denver** |
| Identity | dedicated least-privilege service account (not the public Cloud Run runtime SA) |
| Command | `python scripts/sync_leads_to_bigquery.py --project tho-ai-agent --dataset tho_analytics` |

Until the job is live, the same script can still be run by the owner from the
owner's laptop. Do not document or commit machine paths or credentials.

## Check the last run

Always pass `--project tho-ai-agent`.

```bash
# Latest Job executions (status, start, completion)
gcloud run jobs executions list --job tho-leads-bq \
  --project tho-ai-agent --region us-central1 --limit 5

# Scheduler last attempt (use the -daily suffix if that is how the job was created)
gcloud scheduler jobs describe tho-leads-bq \
  --project tho-ai-agent --location us-central1 \
  --format='yaml(state,schedule,timeZone,status)'
```

Then confirm BigQuery freshness without selecting customer rows:

```bash
bq query --project_id=tho-ai-agent --use_legacy_sql=false \
  'SELECT MAX(day) AS last_day FROM `tho-ai-agent.tho_analytics.daily_metrics`'
```

A missing job or a failed execution is an owner follow-up, not a storefront
outage. The public site does not depend on this dataset.

## Safety

- Always pass `--project tho-ai-agent`.
- Do not grant the job SA Secret Accessor or the public service's roles.
- Do not log or export Firestore lead documents. Use the script's projection
  only.
