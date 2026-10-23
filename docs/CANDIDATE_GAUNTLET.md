# Candidate gauntlet

A manual check for the Cloud Run revision tagged `candidate`. That revision
is deployed with no traffic. This check does not deploy, promote, change
traffic, change IAM, or read secrets.

Run it from GitHub → Actions → **Candidate gauntlet** → Run workflow. Leave
the URL blank to use the candidate tag host:

`https://candidate---project-go-forward-trgi34bxuq-uc.a.run.app`

The workflow is not part of **Deploy to Cloud Run**. A red gauntlet does not
block a deploy.

## What runs where

| Check | Where it runs | What it requests |
|---|---|---|
| Browser journeys | Playwright, candidate tag host | `/staff`, then in-app routes for home, inventory, a listing, contact, and appointments |
| Load sanity | `scripts/candidate_load_sanity.py` | GET `/healthz/`, `/staff`, `/sitemap.xml`, `/api/marketing/inventory-context`, `/api/appointments/slots` |

The candidate host answers `/`, `/inventory`, `/contact`, and `/appointments`
with HTTP 308 to the live site. The browser gauntlet does not follow those
redirects. It loads `/staff`, which the candidate serves, and changes pages
inside the app so the JavaScript and the API reads stay on the candidate.
The load script never requests the paths that redirect, and it never follows
a redirect.

Nothing in this check signs in, submits the contact form, books an
appointment, or sends email.

## Thresholds

| Limit | Value |
|---|---|
| p95 | under 1000 ms (1000 ms fails) |
| Unrecovered HTTP 5xx | 0 |
| Default duration | 30 seconds |
| Default pace | 2 workers, about 2 requests per second |
| Hard caps | 90 seconds, 4 workers, 4 requests per second |

p50 and p95 are from the timed window after one warmup GET per path. The
latency is the attempt that counted. A transport error, an HTTP 5xx, or the
stock Google frontend HTML 502 is retried up to three times (pauses of 0.5 s
and 1.5 s, the same budget as `scripts/production_smoke.py`). A recovered
response is logged as `recovered_gateway_html_count` or `recovered_5xx_count`
and is left out of `status_5xx_count` and out of the percentile sample. A
response that is still failing after those attempts fails the run.

The script refuses `www.texashomeoutlet.com`, `texashomeoutlet.com`, the
retired sapphire host, and the untagged Cloud Run service URL.

## Local run

```bash
cd e2e
npm ci
npx --no-install playwright install chromium
BASE_URL=https://candidate---project-go-forward-trgi34bxuq-uc.a.run.app \
  npx --no-install playwright test --config playwright.gauntlet.config.js

python3 scripts/candidate_load_sanity.py \
  --base-url https://candidate---project-go-forward-trgi34bxuq-uc.a.run.app \
  --output /tmp/gauntlet-load.json
```

Loopback is accepted for a local server. The live domain is not.
