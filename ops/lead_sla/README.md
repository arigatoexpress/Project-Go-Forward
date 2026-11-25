# Daily lead-response SLA alert (COD-254)

Emails the owner once a day when any reachable lead (phone or email) is still `new`
with no first contact after 2 hours, or when the lead mirror is over 30 hours old.

| Piece | Where | State on 2026-11-25 |
|---|---|---|
| Scheduled query `tho-lead-sla-daily` | `projects/691674245427/locations/us/transferConfigs/6b2119cf-0000-2368-860e-24058882acd8` (runs as `tho-leads-bq@`, `every day 13:45` UTC = 6:45 AM MST) | **disabled** |
| Alert policy (log match on `LEAD_SLA_BREACH`) | `projects/tho-ai-agent/alertPolicies/11842036436148116018` -> channel "THO operator email" | **disabled** |
| History table | `tho_analytics.lead_sla_daily` (PII-free counts, created on first run) | not created yet |

How it works: `lead_sla_daily.sql` reads `tho_analytics.leads` (refreshed by `tho-leads-bq-daily` at 6:15 AM MT),
writes one row of counts, then calls `ERROR('LEAD_SLA_BREACH: …')` when there is a breach. The failed run logs an
ERROR line for `bigquery_dts_config`; the alert policy matches it and emails at most once a day.

Cost: one query a day over a few KB (BigQuery free tier) and one log-match policy (free). Nothing new is billed.

## Turn on (owner yes)
    bash ops/lead_sla/enable.sh projects/691674245427/locations/us/transferConfigs/6b2119cf-0000-2368-860e-24058882acd8 \
                projects/tho-ai-agent/alertPolicies/11842036436148116018
If the first run fails with a permission error (not LEAD_SLA_BREACH), the BigQuery Data Transfer service agent
needs token-creator rights on `tho-leads-bq@`. That is an IAM change and needs a separate owner yes.

## Pause / roll back
    bash ops/lead_sla/disable.sh  <config> <policy>    # pause both, keep config
    bash ops/lead_sla/rollback.sh <config> <policy>    # delete both; history table kept
Recreate from scratch with `bash ops/lead_sla/setup.sh` (creates both disabled; skips any that already exist).

## Why voicemail / no answer count as first contact
Staff speed-to-lead is measured from the first attempt (industry practice). Those leads stay `new`, so they stay in
the staff "Answer these people" queue until someone actually reaches them.
