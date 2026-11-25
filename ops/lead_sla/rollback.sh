#!/usr/bin/env bash
# COD-254 full rollback: delete the scheduled query and alert policy. The
# lead_sla_daily history table is kept (drop it by hand only if the owner asks).
set -euo pipefail
PROJECT=tho-ai-agent
TOKEN="$(gcloud auth print-access-token)"
CONFIG="${1:?usage: rollback.sh projects/.../transferConfigs/<id> projects/.../alertPolicies/<id>}"
POLICY="${2:?alert policy name}"
curl -fsS -X DELETE -H "Authorization: Bearer ${TOKEN}" "https://bigquerydatatransfer.googleapis.com/v1/${CONFIG}" >/dev/null
gcloud alpha monitoring policies delete "$POLICY" --project "$PROJECT" --quiet
echo "removed ${CONFIG} and ${POLICY}"
