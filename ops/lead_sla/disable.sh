#!/usr/bin/env bash
# COD-254: pause the daily lead-SLA alert (rollback step 1).
set -euo pipefail
PROJECT=tho-ai-agent
TOKEN="$(gcloud auth print-access-token)"
CONFIG="${1:?usage: disable.sh projects/.../transferConfigs/<id> projects/.../alertPolicies/<id>}"
POLICY="${2:?alert policy name}"
curl -fsS -X PATCH -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
  --data '{"disabled": true}' "https://bigquerydatatransfer.googleapis.com/v1/${CONFIG}?updateMask=disabled" >/dev/null
gcloud alpha monitoring policies update "$POLICY" --project "$PROJECT" --no-enabled
echo "disabled ${CONFIG} and ${POLICY}"
