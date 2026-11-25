#!/usr/bin/env bash
# COD-254: create the daily lead-SLA scheduled query and alert policy, both OFF.
# Idempotent-ish: prints existing ids instead of creating duplicates. Free tier:
# one tiny BigQuery query a day (KB scanned) + one log-match alert policy.
set -euo pipefail
PROJECT=tho-ai-agent
LOCATION=us
NAME=tho-lead-sla-daily
# Runs as the existing lead-mirror service account, which already has WRITER on
# tho_analytics and runs BigQuery jobs for tho-leads-bq. No IAM binding changes.
RUN_AS=tho-leads-bq@tho-ai-agent.iam.gserviceaccount.com
DIR="$(cd "$(dirname "$0")" && pwd)"
TOKEN="$(gcloud auth print-access-token)"
API="https://bigquerydatatransfer.googleapis.com/v1/projects/${PROJECT}/locations/${LOCATION}/transferConfigs"

existing="$(curl -fsS -H "Authorization: Bearer ${TOKEN}" "${API}?dataSourceIds=scheduled_query" \
  | python3 -c "import json,sys;print('\n'.join(c['name'] for c in json.load(sys.stdin).get('transferConfigs',[]) if c.get('displayName')=='${NAME}'))")"
if [ -n "${existing}" ]; then
  echo "scheduled query exists: ${existing}"
else
  python3 - "$DIR/lead_sla_daily.sql" "$NAME" > /tmp/lead_sla_transfer.json <<'PY'
import json, sys
sql = open(sys.argv[1]).read()
print(json.dumps({
    "displayName": sys.argv[2],
    "dataSourceId": "scheduled_query",
    "schedule": "every day 13:45",          # UTC = 6:45 AM MST, after tho-leads-bq-daily (6:15 AM MT)
    "disabled": True,                        # OFF until the owner says yes
    "params": {"query": sql},
    "emailPreferences": {"enableFailureEmail": False},
}))
PY
  curl -fsS -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    --data @/tmp/lead_sla_transfer.json "${API}?serviceAccountName=${RUN_AS}" | python3 -c "import json,sys;d=json.load(sys.stdin);print('created',d['name'],'disabled=',d.get('disabled'))"
fi

policy="$(gcloud alpha monitoring policies list --project "$PROJECT" \
  --filter='displayName:"THO leads: reachable lead with no first contact"' --format='value(name)')"
if [ -n "${policy}" ]; then
  echo "alert policy exists: ${policy}"
else
  gcloud alpha monitoring policies create --project "$PROJECT" --policy-from-file="$DIR/alert_policy.json" \
    --format='value(name,enabled)'
fi
