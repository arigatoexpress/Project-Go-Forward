# Incident Runbook — Project Go Forward (Texas Home Outlet)

Canonical production: `https://www.texashomeoutlet.com` · diagnostic alias:
`https://tho.sapphirealpha.xyz` · Cloud Run service `project-go-forward`, project
`tho-ai-agent`, region `us-central1`. Every push to `main` builds, deploys, and
smokes a zero-traffic `candidate` revision. Production traffic promotion is a
separate gated operator action.

## 1. Quick health checks

```bash
curl -s https://www.texashomeoutlet.com/healthz/      # {"status":"ok","version":"<git sha>"}
curl -sI https://www.texashomeoutlet.com/ | head -1   # HTTP 200 (HEAD supported)
```

`version` tells you exactly which commit is serving. Compare with `git log origin/main -1`.

## 2. Rollback (the most important section)

Rolling back traffic is faster and safer than hot-fixing. Do this FIRST when a
deploy goes bad; diagnose afterwards.

```bash
# 1. List revisions, newest first; pick the last known-good one
gcloud run revisions list --service project-go-forward \
  --region us-central1 --project tho-ai-agent

# 2. Point 100% of traffic at it
gcloud run services update-traffic project-go-forward \
  --region us-central1 --project tho-ai-agent \
  --to-revisions <GOOD_REVISION>=100

# 3. Verify
curl -s https://tho.sapphirealpha.xyz/healthz/   # version should be the old SHA
```

To re-enable normal "latest revision serves" behavior after the bad commit is
reverted/fixed on `main`:

```bash
gcloud run services update-traffic project-go-forward \
  --region us-central1 --project tho-ai-agent --to-latest
```

Never bypass branch protection to hot-push a fix to `main`. Roll back traffic,
then fix forward through a PR.

## 3. "Site down" triage tree

1. **`/healthz/` times out or 5xx**
   - `gcloud run services describe project-go-forward --region us-central1 --project tho-ai-agent`
     → check `Ready` condition and the serving revision.
   - `gcloud run revisions logs read <REVISION> --project tho-ai-agent` (or Cloud
     Console → Cloud Run → Logs) → look for startup tracebacks.
   - Startup crash after a deploy → **rollback (section 2)**.
2. **`/healthz/` OK but pages hang or load forever**
   - Known failure mode: a hanging Firestore/gRPC call inside an async endpoint
     can wedge an instance's event loop (observed 2026-06-10 in local testing —
     `/api/appointments/slots` with an unreachable Firestore stalled ALL
     subsequent requests on that instance). Cloud Run health probes will recycle
     wedged instances, but sustained Firestore degradation = sustained outage.
   - Check Firestore status: https://status.cloud.google.com/ and the service
     logs for `DeadlineExceeded` / `UNAVAILABLE`.
   - Mitigation: increase min instances temporarily; the static SPA + healthz
     remain serveable; Firestore-backed features (CRM, appointments, chat
     memory) are degraded until Firestore recovers.
3. **Pages load but inventory/data missing**
   - Inventory falls back: Firestore → JSON files → sample data. Check logs for
     which tier is active.
4. **Partner API returns 503 "API key auth not configured"**
   - Intentional fail-closed: the running revision has no `THO_API_KEY` /
     `THO_API_KEY_*` env var. Re-attach the secret:
     ```bash
     gcloud run services update project-go-forward --region us-central1 \
       --project tho-ai-agent --update-secrets=THO_API_KEY=tho-api-key:latest
     ```
5. **Admin login broken (PIN rejected for everyone)**
   - `ADMIN_PIN_HASH` secret missing/rotated incorrectly. See
     `docs/PIN_ROTATION_RUNBOOK.md` (includes rollback to the previous secret
     version). Note: rotating the PIN invalidates all admin sessions by design.
6. **Deploy pipeline broken (merges don't produce a candidate)**
   - Check Actions: the `test` job gates `build-and-deploy`. A red `test` on
     `main` means the merge commit is bad — revert it via PR.
   - A successful run intentionally leaves the candidate at zero traffic. Do
     not diagnose unchanged production traffic as a failed deploy.
   - `workflow_dispatch` on `deploy.yml` can re-run a deploy without a new commit.

## 4. Secret rotation

| Secret | Where | Procedure |
|---|---|---|
| `ADMIN_PIN_HASH` | Secret Manager `admin-pin-hash` | `docs/PIN_ROTATION_RUNBOOK.md` |
| `ADMIN_SESSION_SECRET` | Secret Manager | add independent ≥32-byte version (not PIN hash/legacy derivation), redeploy (invalidates sessions) |
| `THO_API_KEY` / `THO_API_KEY_<PARTNER>` | Secret Manager | add new version, `--update-secrets`, notify partner; per-partner vars allow revoking one partner without rotating the rest |
| Resend / DocuSeal tokens | Secret Manager | rotate at provider, add new secret version, redeploy |

After any rotation: `curl /healthz/` for liveness, then exercise the affected
flow (admin login, partner API call, email send) before closing the incident.

## 5. Escalation / ownership

- Operator: Ari (`arigatoexpress`) — repo admin, GCP owner.
- Client-facing: treat any customer-visible outage during business hours
  (Mon–Fri 9–6, Sat 9–5 CT; closed Sunday) as P1; the storefront is the business.
- Post-incident: file an issue with timeline + root cause; if the incident
  required a rollback, the bad commit must be reverted on `main` via PR before
  `--to-latest` is restored.
