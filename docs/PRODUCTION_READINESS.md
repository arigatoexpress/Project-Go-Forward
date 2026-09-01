# THO Production Readiness Runbook

This runbook is for the Texas Home Outlet production app. It is intentionally
read-only unless a command explicitly says it rotates a secret or deploys a new
revision.

## Production URLs

```bash
export THO_PROD_URL="https://www.texashomeoutlet.com"
export THO_CLOUD_RUN_URL="https://project-go-forward-trgi34bxuq-uc.a.run.app"
export THO_PROJECT="tho-ai-agent"
export THO_REGION="us-central1"
export THO_SERVICE="project-go-forward"
```

`THO_PROD_URL` is the only client/customer URL. `THO_CLOUD_RUN_URL` is for
operator health/API diagnostics; human-facing Cloud Run pages redirect to the
canonical THO domain.

## Read-Only Smoke

Run the repeatable public production smoke:

```bash
python3 scripts/production_smoke.py --base-url "$THO_PROD_URL"
```

The smoke checks:

- `/health` and `/healthz/`
- public SPA routes for the main app, documents, studio, CRM, and analytics
- `/api/marketing/inventory-context` has a healthy inventory payload
- admin API routes reject unauthenticated traffic
- `/healthz/` exposes only the public liveness/deployed-version envelope

Direct endpoint checks:

```bash
curl -fsS "$THO_PROD_URL/health" | python3 -m json.tool
curl -fsS "$THO_PROD_URL/healthz/" | python3 -m json.tool
curl -fsS "$THO_PROD_URL/api/marketing/inventory-context" | python3 -m json.tool
```

The deployed commit is the `version` field from `/healthz/`. Use the trailing
slash in external probes because the production load-balanced URL is verified on
`/healthz/`.

## Transactional Email Readiness

Appointment confirmations, lead welcome emails, deal-status emails, and CRM
custom emails require `RESEND_API_KEY`. Public `/healthz/` intentionally stays
minimal so liveness probes do not leak dependency posture. Detailed readiness is
available at `/healthz/detailed` with a valid admin token; it reports
`dependencies.email` and adds `email_not_configured` to `warnings` when the key
is absent. The public health endpoint stays HTTP 200 so Cloud Run liveness does
not restart a healthy app because of an operator-owned email-provider setup gap.

Bind the Resend key through Secret Manager:

```bash
read -rsp "Resend API key: " RESEND_API_KEY; echo
printf '%s' "$RESEND_API_KEY" | gcloud secrets create resend-api-key \
  --project "$THO_PROJECT" \
  --replication-policy=automatic \
  --data-file=-
unset RESEND_API_KEY

gcloud run services update "$THO_SERVICE" \
  --project "$THO_PROJECT" \
  --region "$THO_REGION" \
  --update-secrets=RESEND_API_KEY=resend-api-key:latest
```

If `resend-api-key` already exists, add a new version instead of recreating the
secret:

```bash
read -rsp "Resend API key: " RESEND_API_KEY; echo
printf '%s' "$RESEND_API_KEY" | gcloud secrets versions add resend-api-key \
  --project "$THO_PROJECT" \
  --data-file=-
unset RESEND_API_KEY
```

After binding, verify `dependencies.email` changes to `configured` through the
admin-only detailed probe:

```bash
# $COOKIE_JAR comes from the admin sign-in block under "Admin PIN Commands".
curl -fsS "$THO_PROD_URL/healthz/detailed" -b "$COOKIE_JAR" | python3 -m json.tool
python3 scripts/production_smoke.py --base-url "$THO_PROD_URL"
```

## Local Gates

Use the repo runtime, not the machine default Python:

```bash
uv run --python 3.11 --with-requirements requirements.txt --with-requirements requirements-dev.txt pytest -q
npm --prefix frontend ci
npm --prefix frontend run build
npm --prefix frontend run lint
npm --prefix frontend audit --audit-level=moderate
python3 -m py_compile scripts/production_smoke.py
pre-commit run --files <changed-files>
```

## Admin PIN Commands

Production stores `ADMIN_PIN_HASH`, not the plaintext PIN. A SHA-256 hash is
not reversible, so there is no command that can recover the current plaintext
PIN from production. Use these commands to identify the Secret Manager binding,
verify a candidate PIN, exchange a known-good PIN for an admin token, or rotate
the PIN.

Show which secret backs `ADMIN_PIN_HASH`:

```bash
gcloud run services describe "$THO_SERVICE" \
  --project "$THO_PROJECT" \
  --region "$THO_REGION" \
  --format=json | python3 -c 'import json,sys; data=json.load(sys.stdin); env=data["spec"]["template"]["spec"]["containers"][0].get("env", []); item=next((e for e in env if e.get("name")=="ADMIN_PIN_HASH"), {}); ref=item.get("valueFrom", {}).get("secretKeyRef", {}); print("secret={} version={}".format(ref.get("name", "-"), ref.get("key", "-")))'
```

Optionally read the stored hash for admin-only verification. This prints the
hash, not the PIN; do not paste the hash into tickets, email, or chat:

```bash
gcloud secrets versions access latest \
  --secret=admin-pin-hash \
  --project "$THO_PROJECT"
```

Verify a candidate PIN without echoing it:

```bash
read -rsp "THO admin PIN: " THO_ADMIN_PIN; echo
ADMIN_PIN_HASH="$(gcloud secrets versions access latest --secret=admin-pin-hash --project "$THO_PROJECT")"
THO_ADMIN_PIN="$THO_ADMIN_PIN" ADMIN_PIN_HASH="$ADMIN_PIN_HASH" python3 - <<'PY'
import hashlib
import hmac
import os

candidate = hashlib.sha256(os.environ["THO_ADMIN_PIN"].encode()).hexdigest()
print("MATCH" if hmac.compare_digest(candidate, os.environ["ADMIN_PIN_HASH"].strip()) else "NO MATCH")
PY
unset THO_ADMIN_PIN ADMIN_PIN_HASH
```

Open an authenticated admin session without echoing the PIN.

`POST /api/admin/verify` does **not** return the bearer token in its body — it
answers `{"success": true, "csrf_token": "..."}` and sets the token as an
httpOnly `tho_admin_token` cookie. Capture it with a cookie jar and send the
cookie on subsequent calls:

```bash
COOKIE_JAR="$(mktemp)"
read -rsp "THO admin PIN: " THO_ADMIN_PIN; echo
THO_ADMIN_PIN="$THO_ADMIN_PIN" python3 -c \
  'import json,os; print(json.dumps({"pin": os.environ["THO_ADMIN_PIN"]}))' \
  | curl -sS -X POST "$THO_PROD_URL/api/admin/verify" \
      -H "Content-Type: application/json" \
      --data-binary @- \
      -c "$COOKIE_JAR" -o /dev/null -w 'HTTP %{http_code}\n'
unset THO_ADMIN_PIN
grep -q tho_admin_token "$COOKIE_JAR" && echo "signed in" || echo "PIN rejected"

curl -fsS "$THO_PROD_URL/api/admin/check" -b "$COOKIE_JAR" | python3 -m json.tool
```

`401` means the PIN is wrong. `429` means the rate limiter tripped (5/min per
IP, then a 10-attempt / 5-minute lockout) — wait it out rather than retrying.
`503` with `Admin auth not configured` means `ADMIN_PIN_HASH` is missing from
the serving revision.

Remove the jar when finished: `rm -f "$COOKIE_JAR"`.

Rotate the admin PIN:

```bash
read -rsp "New THO admin PIN: " THO_ADMIN_PIN; echo
ADMIN_PIN_HASH="$(THO_ADMIN_PIN="$THO_ADMIN_PIN" python3 - <<'PY'
import hashlib
import os

print(hashlib.sha256(os.environ["THO_ADMIN_PIN"].encode()).hexdigest())
PY
)"
printf '%s' "$ADMIN_PIN_HASH" | gcloud secrets versions add admin-pin-hash \
  --project "$THO_PROJECT" \
  --data-file=-
gcloud run services update "$THO_SERVICE" \
  --project "$THO_PROJECT" \
  --region "$THO_REGION" \
  --update-secrets=ADMIN_PIN_HASH=admin-pin-hash:latest
unset THO_ADMIN_PIN ADMIN_PIN_HASH
```

Do not send the admin PIN in email or chat. Share it by phone or a password
manager with an audit trail.

## Deployment Gate

This repository auto-deploys from `main`. Agent branches should open a draft PR
and wait for a human merge unless the operator explicitly authorizes a direct
production push.


*Last verified: 2026-05-04*
