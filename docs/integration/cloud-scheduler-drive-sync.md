# Scheduled Drive Floorplan Sync — Cloud Run Job + Cloud Scheduler

This guide turns `tools/drive_floorplan_sync.py` into a recurring Cloud Run
Job. The Job walks Mark's shared "THO" Google Drive folder, pulls
manufacturer floorplans into `data/floorplans/<manufacturer>/`, and uploads
them to the `tho-secure-documents` GCS bucket under `floorplans/`. Re-runs
are idempotent — files whose md5 already matches the GCS blob are skipped.

> **Status**: Code shipped in `feat/scheduled-drive-sync`. Activation requires
> the operator checklist below to complete first.

## Why Cloud Run Job (not inline FastAPI background task)

- Clean separation of concerns — sync is not request-scoped.
- Cloud Run web requests time out at 60 minutes; Jobs can run up to 24 hours.
- Job retries are independent from web traffic; no risk of starving the API.
- Cloud Logging gets a dedicated stream we can alert on.

## Prerequisites

| Item | Value |
| --- | --- |
| GCP project | `tho-ai-agent` |
| Region | `us-central1` |
| GCS bucket | `tho-secure-documents` |
| Service account | `691674245427-compute@developer.gserviceaccount.com` (current Cloud Run runtime SA) |
| Drive folder | `THO` (shared by Mark Willcott) |

> **Recommendation**: create a dedicated service account
> `tho-drive-sync@tho-ai-agent.iam.gserviceaccount.com` so the Drive read
> grant is scoped to *only* the sync job, not the entire web service. The
> manifest below uses the runtime SA for parity with the existing service;
> swap in the dedicated SA once Mark has shared the folder with it.

## Required IAM bindings

On the runtime service account (or the dedicated `tho-drive-sync` SA):

```bash
SA="691674245427-compute@developer.gserviceaccount.com"
PROJECT="tho-ai-agent"

# Object create on the documents bucket — only what we need.
gsutil iam ch \
  "serviceAccount:${SA}:objectCreator,objectViewer" \
  gs://tho-secure-documents

# Cloud Run Job invoker (lets Cloud Scheduler trigger the Job).
gcloud run jobs add-iam-policy-binding tho-drive-sync \
  --region=us-central1 \
  --project="${PROJECT}" \
  --member="serviceAccount:${SA}" \
  --role="roles/run.invoker"
```

**Drive Reader access is granted by Mark** — see operator checklist below.
There is no IAM API for "share a Drive folder"; it must happen in the
Google Drive UI under Mark's account.

## Cloud Run Job manifest

Save as `infra/cloudrun/tho-drive-sync.job.yaml` *(not committed in this PR — apply once after the operator checklist is green)*:

```yaml
apiVersion: run.googleapis.com/v1
kind: Job
metadata:
  name: tho-drive-sync
  labels:
    cloud.googleapis.com/location: us-central1
spec:
  template:
    spec:
      parallelism: 1
      taskCount: 1
      template:
        spec:
          serviceAccountName: 691674245427-compute@developer.gserviceaccount.com
          containers:
            - image: us-central1-docker.pkg.dev/tho-ai-agent/cloud-run-source-deploy/project-go-forward:latest
              command: ["python", "-c"]
              args:
                - "from tools.drive_floorplan_sync import run_scheduled; import json, sys; stats = run_scheduled(); print(json.dumps(stats)); sys.exit(0 if not stats['errors'] else 1)"
              env:
                - name: DRIVE_FOLDER_ID
                  value: "REPLACE_WITH_THO_FOLDER_ID"
                - name: GCS_DOCUMENTS_BUCKET
                  value: tho-secure-documents
                - name: DRIVE_SYNC_DRY_RUN
                  value: "0"
              resources:
                limits:
                  cpu: "1"
                  memory: 1Gi
          timeoutSeconds: 1800
          maxRetries: 1
```

Apply with:

```bash
gcloud run jobs replace infra/cloudrun/tho-drive-sync.job.yaml \
  --region=us-central1 --project=tho-ai-agent
```

Smoke-test once before wiring the scheduler:

```bash
gcloud run jobs execute tho-drive-sync \
  --region=us-central1 --project=tho-ai-agent --wait
```

## Cloud Scheduler trigger

Daily at **03:07 America/Chicago** (off-the-minute jitter — round minutes
fight other fleet jobs that also fire at `:00`):

```bash
gcloud scheduler jobs create http tho-drive-sync-daily \
  --project=tho-ai-agent \
  --location=us-central1 \
  --schedule="7 3 * * *" \
  --time-zone="America/Chicago" \
  --http-method=POST \
  --uri="https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/tho-ai-agent/jobs/tho-drive-sync:run" \
  --oauth-service-account-email=691674245427-compute@developer.gserviceaccount.com \
  --description="Nightly Drive→GCS floorplan sync (Mark's THO folder)"
```

> If you need an exactly-on-the-hour schedule for compliance or audit
> reasons, swap to `--schedule="0 3 * * *"`. Off-the-minute is preferred to
> avoid contention with other 03:00 jobs in the same project.

## Operator checklist for Mark / Celeste

> Hand this to Mark verbatim. Each step blocks the next.

- [ ] **Mark shares the THO Drive folder** with
      `691674245427-compute@developer.gserviceaccount.com` at *Viewer* level
      (right-click folder → Share → paste the email → Viewer → Send).
      Do **not** use "Anyone with the link"; this is a service account grant.
- [ ] Mark sends Ari the THO folder ID — it is the chunk after `/folders/` in
      the Drive URL, e.g. `0BwMsFgQWT3QvWWoxbWMtQXJfR0U`.
- [ ] Ari fills in `DRIVE_FOLDER_ID` in the Job manifest and applies it.
- [ ] Ari runs `gcloud run jobs execute tho-drive-sync ... --wait` and pastes
      the resulting JSON stats into the THO ops thread.
- [ ] If `files_seen > 0` and `errors == []`, Ari creates the Cloud Scheduler
      trigger.
- [ ] First scheduled run fires the next morning at 03:07 CT. Ari spot-checks
      Cloud Logging for the job and confirms `files_skipped_md5` grows on
      subsequent runs (idempotency proof).

## Failure modes and triage

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Job fails with `DRIVE_FOLDER_ID required` | Env var unset on the Job | Re-apply manifest with the real ID. |
| `files_seen == 0` consistently | Drive folder not shared with SA | Mark re-shares the folder; verify SA email. |
| `errors[*].stage == "upload"` | GCS bucket IAM missing | Re-grant `objectCreator` on the bucket. |
| `files_skipped_md5 == 0` always | md5 precheck failing silently | Check Cloud Logging for `md5 precheck failed` traces. |
| Sentry not capturing errors | `sentry_sdk` not installed in image | Optional dep — add to `requirements.txt` if needed. |

## Rollback

```bash
gcloud scheduler jobs delete tho-drive-sync-daily --location=us-central1 --project=tho-ai-agent
gcloud run jobs delete tho-drive-sync --region=us-central1 --project=tho-ai-agent
```

The manual CLI path (`python3 tools/drive_floorplan_sync.py --apply ...`)
continues to work; teardown of the Job does not affect ad-hoc runs.
