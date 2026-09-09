# Incident Runbook — Project Go Forward (Texas Home Outlet)

Production: `https://www.texashomeoutlet.com` · Cloud Run service `project-go-forward`,
project `tho-ai-agent`, region `us-central1`. The legacy `tho.sapphirealpha.xyz`
origin is not the canonical storefront.

## Release and approval flow

1. Use a branch and PR; observe every applicable check finish green before merging.
   PR merges are authorized under the standing operator policy; direct pushes to
   `main` are not.
2. The `main` workflow builds and verifies a candidate using
   `--no-traffic --tag=candidate`. A successful workflow does not move production
   traffic. Wait for that exact main run to finish before another merge: newer
   main runs can cancel an in-flight candidate pipeline.
3. Before promotion, resolve the candidate tag URL and exact revision, verify its
   `/healthz/` version against the intended commit, and review its smoke results.
   Record the currently serving revision and traffic allocation as rollback
   evidence. Obtain explicit operator approval for the exact traffic change,
   then verify the canonical storefront's serving commit and affected behavior.

Direct production deploys, traffic changes (including rollback), DNS changes,
secret rotations, and outward messages require explicit operator approval.
Read-only inspection and preparation may proceed before that approval.

## 1. Quick health checks

```bash
curl -s https://www.texashomeoutlet.com/healthz/        # {"status":"ok","version":"<git sha>"}
curl -sI https://www.texashomeoutlet.com/ | head -1     # HTTP 200 (HEAD supported)
```

`version` identifies the serving commit. Compare it with the approved serving
revision, not automatically with the newest `origin/main`: main may contain a
verified candidate that has not been promoted. Probe the candidate tag URL
separately when checking candidate code.

## 2. Rollback (the most important section)

When a promoted revision causes an outage, prepare a rollback to the recorded
last known-good revision and obtain explicit operator approval before changing
traffic. The commands below are an operator runbook, not unattended instructions.

```bash
# 1. List revisions, newest first; pick the last known-good one
gcloud run revisions list --service project-go-forward \
  --region us-central1 --project tho-ai-agent

# 2. After approval, point traffic at the exact last known-good revision
gcloud run services update-traffic project-go-forward \
  --region us-central1 --project tho-ai-agent \
  --to-revisions <GOOD_REVISION>=100

# 3. Verify
curl -s https://www.texashomeoutlet.com/healthz/   # version should be the approved rollback SHA
```

Keep traffic pinned to that explicit revision. Fix forward through a verified PR,
then review and approve the new candidate's exact revision before promotion.
Do not use `--to-latest`, which can select an unreviewed candidate. Never bypass
branch protection to hot-push a fix to `main`.

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
6. **Candidate pipeline failed, or merged changes are not visible in production**
   - Check the exact main Actions run: the `test` job gates `build-and-deploy`.
     Diagnose whether a failure is in source, infrastructure, or credentials;
     a failed check alone does not establish a bad commit. Fix or revert source
     regressions through a PR.
   - If the candidate passes but the storefront still serves the previous
     approved commit, check the traffic allocation. This is expected until an
     approved promotion, not evidence that the deployment failed.
   - `workflow_dispatch` on `deploy.yml` can rebuild a candidate without a new
     commit; it does not promote that candidate.

## 4. Secret rotation

| Secret | Where | Procedure |
|---|---|---|
| `ADMIN_PIN_HASH` | Secret Manager `admin-pin-hash` | `docs/PIN_ROTATION_RUNBOOK.md` |
| `ADMIN_SESSION_SECRET` | Secret Manager | add independent ≥32-byte version (not PIN hash/legacy derivation), redeploy (invalidates sessions) |
| `THO_API_KEY` / `THO_API_KEY_<PARTNER>` | Secret Manager | add new version, `--update-secrets`, notify partner; per-partner vars allow revoking one partner without rotating the rest |
| Resend / DocuSeal tokens | Secret Manager | rotate at provider, add new secret version, redeploy |

After an approved rotation, check `/healthz/` for liveness and verify the affected
flow. Email delivery requires a separately approved test send; a secret binding
or liveness response alone does not prove delivery.

## 5. Escalation / ownership

- Operator: Ari (`arigatoexpress`) — repo admin, GCP owner.
- Client-facing: treat any customer-visible outage during business hours
  (Mon–Fri 9–6, Sat 9–5 CT; closed Sunday) as P1; the storefront is the business.
- Post-incident: record the timeline, root cause, serving revision, and rollback
  evidence. Fix or revert the source through a PR; keep production traffic pinned
  until the replacement candidate is verified and its promotion approved.
