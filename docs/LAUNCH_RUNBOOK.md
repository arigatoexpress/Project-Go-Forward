# Texas Home Outlet Launch And Cutover Runbook

Audience: Ari, THO leadership, Mark/Celeste, and production operators.

Production app:

- Current production URL: `https://tho.sapphirealpha.xyz`
- Cloud Run service: `project-go-forward`
- Google Cloud project: `tho-ai-agent`
- Region: `us-central1`
- Target official domain: `texashomeoutlet.com`

## Current Launch Posture

App-side status: ready for continued client testing and go-live approval after final human review.

External cutover blockers remain separate from app readiness:

- Provider lead export from manufacturedhomes.com
- Final detailed billing statement
- Instagram `@txhomeoutlet` credential/reset handoff
- Facebook admin transfer to Celeste
- Full AWS Route 53 hosted-zone record export for `texashomeoutlet.com`
- Final THO approval and exact DNS cutover window
- Old-site takedown coordination after THO says the new site is live

Do not change DNS until the Route 53 export is captured and Mark/Celeste/Ari approve the cutover window.

## Pre-Launch Verification

Run these from `/Users/aribs/Code/Project-Go-Forward` or a clean worktree based on `origin/main`.

```bash
curl -fsS https://tho.sapphirealpha.xyz/health
curl -fsS https://tho.sapphirealpha.xyz/healthz/
python3 scripts/production_smoke.py
```

Expected:

- `/health` returns `{"status":"ok"}`
- `/healthz/` returns `status=ok` with the deployed version
- `production_smoke.py` returns `ok=true`
- Public routes load
- Admin routes reject unauthenticated requests
- Inventory context reports the current/orderable catalog split

To verify incomplete document payloads fail closed, authenticate without printing credentials:

```bash
read -rsp "Admin PIN: " THO_ADMIN_PIN
echo
export THO_ADMIN_PIN

ADMIN_TOKEN=$(/opt/homebrew/bin/python3 - <<'PY'
import json
import os
import ssl
import http.cookiejar
import urllib.request

ctx = ssl._create_unverified_context()
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(jar),
    urllib.request.HTTPSHandler(context=ctx),
)
req = urllib.request.Request(
    "https://tho.sapphirealpha.xyz/api/admin/verify",
    data=json.dumps({"pin": os.environ["THO_ADMIN_PIN"]}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
opener.open(req, timeout=20)
for cookie in jar:
    if cookie.name == "tho_admin_token":
        print(cookie.value)
        break
PY
)

/opt/homebrew/bin/python3 scripts/production_smoke.py \
  --check-empty-doc-rejection \
  --admin-token "$ADMIN_TOKEN"

unset ADMIN_TOKEN THO_ADMIN_PIN
```

Expected empty-document result:

- status `400`
- error `missing_required_fields`
- no download URL

## Document Center Write Smoke

Use synthetic data only.

```bash
read -rsp "Admin PIN: " THO_ADMIN_PIN
echo
export THO_ADMIN_PIN

python3 scripts/doccenter_smoke.py --write-all

unset THO_ADMIN_PIN
```

Expected:

- Admin auth succeeds
- Document readiness is `ready`
- Templates and packets are present
- Single-document generation succeeds
- Packet generation succeeds
- Downloaded PDFs have a nonzero size and start with `%PDF-`
- Customer save/search works without raw SSN exposure

## Local Verification

Focused Document Center regression suite:

```bash
python3 -m pytest \
  tests/test_doccenter_autofill.py \
  tests/test_document_quality.py \
  tests/test_document_engine_v2.py \
  tests/test_smoke_empty_deal.py \
  tests/test_doccenter_smoke_script.py \
  tests/test_deal_document_validation.py \
  -q
```

Frontend build:

```bash
npm --prefix frontend run build
```

Full backend suite requires the Python 3.11 dependency environment from `requirements.txt` and `requirements-dev.txt`.

## DNS Cutover Rules

Hard stops:

- Do not change DNS before the AWS Route 53 hosted-zone export is captured.
- Do not touch MX, SPF, DKIM, DMARC, Yahoo/Turbify mail, Google verification, or other verification records unless Mark/Celeste/Ari explicitly approve.
- Do not ask the legacy provider to take the old site down until the new official domain is serving correctly.

Expected DNS goal:

- Move website traffic for `texashomeoutlet.com` and `www.texashomeoutlet.com` to the new Cloud Run-backed app.
- Preserve mail and verification records.

Before changing records:

1. Capture/export the full current hosted zone.
2. Identify only the web-serving apex and `www` records.
3. Confirm Cloud Run custom-domain mapping or load-balancer target.
4. Lower TTL if needed before the window.
5. Get written go-live approval from THO leadership.

After changing records:

```bash
dig texashomeoutlet.com
dig www.texashomeoutlet.com
curl -I https://texashomeoutlet.com
curl -I https://www.texashomeoutlet.com
```

Then rerun:

```bash
python3 scripts/production_smoke.py --base-url https://texashomeoutlet.com
```

Run the authenticated Document Center smoke only after confirming the official domain session/auth behavior is correct.

## Rollback Plan

If the app revision is bad but DNS is correct:

```bash
gcloud run revisions list \
  --service=project-go-forward \
  --project=tho-ai-agent \
  --region=us-central1

gcloud run services update-traffic project-go-forward \
  --project=tho-ai-agent \
  --region=us-central1 \
  --to-revisions=<last-known-good-revision>=100
```

If the domain mapping or DNS cutover is bad:

1. Restore only the web records from the Route 53 export.
2. Keep mail and verification records unchanged.
3. Keep the legacy site online until public DNS and SSL are verified.
4. Rerun public smoke checks against both the official domain and `https://tho.sapphirealpha.xyz`.

## Client Approval Checklist

Before telling the provider to take the old site down, confirm:

- THO leadership approves the new site.
- Inventory shows current homes plus orderable floorplans.
- Document Center generates packets with complete data.
- Incomplete customer/deal records produce clear missing-field guidance.
- Documents identify the seller as **Texas Home Outlet, Inc.**
- Staff guide has been shared with the team.
- DNS export and rollback plan are saved.
- Provider handoff items are either complete or explicitly accepted as post-launch follow-ups.
