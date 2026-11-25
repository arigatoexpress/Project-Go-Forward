#!/usr/bin/env bash
# COD-254: turn the daily lead-SLA alert ON (owner yes required). Reverse with disable.sh.
set -euo pipefail
PROJECT=tho-ai-agent
TOKEN="$(gcloud auth print-access-token)"
CONFIG="${1:?usage: enable.sh projects/.../transferConfigs/<id> projects/.../alertPolicies/<id>}"
POLICY="${2:?alert policy name}"
curl -fsS -X PATCH -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
  --data '{"disabled": false}' "https://bigquerydatatransfer.googleapis.com/v1/${CONFIG}?updateMask=disabled" >/dev/null
gcloud alpha monitoring policies update "$POLICY" --project "$PROJECT" --enabled
echo "enabled ${CONFIG} and ${POLICY}"
