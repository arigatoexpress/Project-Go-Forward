# Texas Home Outlet — System Status

**Last updated:** 2026-10-07

This file is a pointer, not a live status board. It replaces a 2026-02-25
snapshot that named a Cloud Run service URL
(`tho-agent-s77j6bxyra-uc.a.run.app`) that now returns 404. Check the sources
below for current status instead of trusting any dated document.

## What was observed on 2026-10-07

Read-only checks, recorded for orientation only:

| Check | Result |
|---|---|
| `GET https://www.texashomeoutlet.com/healthz/` | `{"status":"ok","version":"bd4fd22…"}` |
| `GET https://project-go-forward-trgi34bxuq-uc.a.run.app/healthz/` | same version `bd4fd22…` |
| Latest `main` build ([Deploy to Cloud Run run](https://github.com/arigatoexpress/Project-Go-Forward/actions/runs/37501824168), commit `bd4fd22`, 2026-10-06) | success; 2,217 backend tests passed (24 skipped), 291 frontend tests passed |

`bd4fd22` is "Hide passkey on the staff sign-in page"
([#370](https://github.com/arigatoexpress/Project-Go-Forward/pull/370)).

## Where current status lives

| Question | Source |
|---|---|
| Is the site up, and which commit is serving? | `curl https://www.texashomeoutlet.com/healthz/` (liveness + version). Readiness: `/health`. |
| Full read-only smoke | `python3 scripts/production_smoke.py --base-url https://www.texashomeoutlet.com` ([PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md)) |
| Did the latest merge build a candidate? | GitHub Actions, [Deploy to Cloud Run](https://github.com/arigatoexpress/Project-Go-Forward/actions/workflows/deploy.yml). Merges build a `--no-traffic --tag=candidate` revision; they do not move production traffic. |
| Which revision is serving traffic? | `gcloud run services describe project-go-forward --project tho-ai-agent --region us-central1 --format='value(status.traffic)'` (operator access required) |
| Go-live checklist | [LAUNCH_READINESS.md](LAUNCH_READINESS.md) |
| Incidents and rollback | [docs/RUNBOOK.md](docs/RUNBOOK.md) |

## Monitoring and backups (verified in GCP on 2026-10-07, after 10:06 MT)

- **Backups:** a daily Firestore backup schedule on the `(default)` database,
  active since 2026-06-20. Retention changed from 7 days to 14 days
  (`1209600s`) at 10:05 MT (16:05 UTC) on 2026-10-07. Backups taken before that
  change still expire after 7 days. Check with
  `gcloud firestore backups schedules list --database="(default)" --project=tho-ai-agent`
  ([restore runbook](docs/FIRESTORE_RESTORE_RUNBOOK.md)).
- **Uptime checks:** three checks, every 5 minutes, all with SSL validation:
  - `/healthz/`
  - `/` (response must contain "Texas Home Outlet")
  - `/staff`
- **Alert policies (email to the owner):**
  - `/healthz/` uptime failure and 5xx burst alerts (created by
    [`.github/workflows/ops-bootstrap.yml`](.github/workflows/ops-bootstrap.yml)),
    unchanged.
  - "THO home/staff down + SSL expiring": fires when the `/` or `/staff` check
    fails from 2 or more regions for 5 minutes, or when the SSL certificate has
    under 21 days left.

## Known config drift (follow-ups, not yet fixed)

- `tools/health_check.py` hardcodes Firestore project `sapphire-479610`.
  Production Firestore is in `tho-ai-agent`.
- `firebase.json` rewrites to Cloud Run service `tho-agent`. The live service is
  `project-go-forward`.

## Domains

- Canonical storefront: https://www.texashomeoutlet.com
- Staff sign-in: https://www.texashomeoutlet.com/staff
- Legacy origin: https://tho.sapphirealpha.xyz (not the customer URL)
