# ADMIN_PIN_HASH Rotation Runbook

Audience: production operator with Google Cloud access to project `tho-ai-agent`.

Scope: rotate the admin PIN hash used by Cloud Run without recording the PIN itself. This invalidates existing admin sessions because session signing is derived from `ADMIN_PIN_HASH`.

Do not paste the PIN into chat, tickets, shell history, logs, or docs.

## Prerequisites

- Human operator has chosen the new PIN out of band.
- Operator is authenticated to the correct Google account:

```bash
gcloud auth list
gcloud config set project tho-ai-agent
```

- Confirm current service and Secret Manager binding:

```bash
gcloud run services describe project-go-forward \
  --project=tho-ai-agent \
  --region=us-central1 \
  --format='value(spec.template.spec.containers[0].env)'

gcloud secrets describe admin-pin-hash --project=tho-ai-agent
gcloud secrets versions list admin-pin-hash --project=tho-ai-agent
```

## Rotate

Generate the SHA-256 hash locally without echoing the PIN. This command reads the PIN interactively and prints only the hash.

```bash
read -rsp "New admin PIN: " ADMIN_PIN; echo
NEW_ADMIN_PIN_HASH="$(ADMIN_PIN="$ADMIN_PIN" python3 - <<'PY'
import hashlib
import os
pin = os.environ["ADMIN_PIN"]
print(hashlib.sha256(pin.encode()).hexdigest())
PY
)"
unset ADMIN_PIN
printf '%s\n' "$NEW_ADMIN_PIN_HASH"
```

Add the printed hash as a new Secret Manager version.

```bash
printf '%s' "$NEW_ADMIN_PIN_HASH" | gcloud secrets versions add admin-pin-hash \
  --project=tho-ai-agent \
  --data-file=-
unset NEW_ADMIN_PIN_HASH
```

Make Cloud Run read the latest secret version and create a new revision:

```bash
gcloud run services update project-go-forward \
  --project=tho-ai-agent \
  --region=us-central1 \
  --update-secrets=ADMIN_PIN_HASH=admin-pin-hash:latest
```

Record the new revision name:

```bash
gcloud run services describe project-go-forward \
  --project=tho-ai-agent \
  --region=us-central1 \
  --format='value(status.latestReadyRevisionName,status.url)'
```

## Verify

Public health, no credentials required:

```bash
BASE_URL="https://tho.sapphirealpha.xyz"
curl -fsS "$BASE_URL/health"
curl -fsS "$BASE_URL/healthz/"
```

Admin login requires a human to enter the new PIN locally. The response sets an
httpOnly admin cookie; do not paste the PIN, cookie jar, or any session value
into tickets or docs.

```bash
COOKIE_JAR="$(mktemp)"
read -rsp "Admin PIN: " ADMIN_PIN; echo
ADMIN_PIN="$ADMIN_PIN" python3 - <<'PY' > /tmp/tho-admin-pin-body.json
import json
import os
print(json.dumps({"pin": os.environ["ADMIN_PIN"]}))
PY
unset ADMIN_PIN

curl -fsS -c "$COOKIE_JAR" -X POST "$BASE_URL/api/admin/verify" \
  -H 'Content-Type: application/json' \
  --data @/tmp/tho-admin-pin-body.json
rm -f /tmp/tho-admin-pin-body.json

curl -fsS "$BASE_URL/api/admin/check" \
  -b "$COOKIE_JAR"

curl -fsS -D - -o /dev/null "$BASE_URL/api/documents/templates" \
  -b "$COOKIE_JAR" | sed -n '1,12p'

rm -f "$COOKIE_JAR"
```

Expected:

- `/health` returns `{"status":"ok"}`.
- `/healthz/` returns minimal public `status` and `version`.
- `/healthz/detailed` returns `status`, `version`, `sha`, `uptime_s`, dependency statuses for `drive`, `secrets`, `db`, and `email`, plus non-secret warnings such as `email_not_configured` when called with a valid `X-Admin-Token`.
- `/api/admin/verify` succeeds only with the new PIN.
- `/api/admin/check` returns `{"valid":true}` for the new cookie-backed session.
- Existing sessions minted before rotation fail and users must re-authenticate.

Check Cloud Run logs for startup/auth errors without exposing secret values:

```bash
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="project-go-forward" AND severity>=WARNING' \
  --project=tho-ai-agent \
  --limit=50 \
  --format='table(timestamp,severity,textPayload,jsonPayload.message)'
```

## Rollback

Rollback should restore the previous known-good Secret Manager version, not a real PIN value.

Find candidate versions:

```bash
gcloud secrets versions list admin-pin-hash \
  --project=tho-ai-agent \
  --sort-by='~createTime'
```

Point Cloud Run at the previous enabled version:

```bash
PREVIOUS_VERSION="<previous-enabled-version-number>"
gcloud run services update project-go-forward \
  --project=tho-ai-agent \
  --region=us-central1 \
  --update-secrets=ADMIN_PIN_HASH=admin-pin-hash:$PREVIOUS_VERSION
```

Verify health and admin auth with the previous operator-held PIN using the same verification commands above.

If the service revision itself is bad, shift traffic back to the last known-good revision:

```bash
gcloud run revisions list \
  --service=project-go-forward \
  --project=tho-ai-agent \
  --region=us-central1

gcloud run services update-traffic project-go-forward \
  --project=tho-ai-agent \
  --region=us-central1 \
  --to-revisions=<known-good-revision>=100
```

## Closeout Notes

- Notify internal users out of band that admin sessions were invalidated and the new PIN is available through the approved human channel.
- Do not include the PIN, token, or hash in the closeout note.
- Record only: rotation timestamp, operator, Secret Manager version number, Cloud Run revision, health result, admin-check result, and rollback version.
