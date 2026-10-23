# Release checklist — from a green Deploy to the live site

This checklist is for the owner or a named operator with gcloud, curl and
Cloud Run IAM. Owner approval is required before the promote command, and
the Google Cloud login must be able to change Cloud Run traffic on project
`tho-ai-agent`.

A green **Deploy to Cloud Run** check means the new code is built and checked
as a **candidate** with **0% of customer traffic**. Customers on
https://www.texashomeoutlet.com keep seeing the previous version until the
promote step in this list.

Operator record: on 2026-10-15 at about 7:04 PM MT, after a read-only
candidate check, Grok Bot ran:

```text
gcloud run services update-traffic project-go-forward --project tho-ai-agent --region us-central1 --to-revisions=project-go-forward-00531-lor=100
```

That command named revision `project-go-forward-00531-lor` (commit `4511d3f`,
pull request #401). This document does not redeploy that revision and does
not change traffic. The promote procedure for a new release is step 5.

## Words used below

| Word | Meaning |
|---|---|
| Candidate | The new Cloud Run revision. Its tag is `candidate`. It receives 0% of visits until you promote it. Its address starts with `https://candidate---`. |
| Live site | https://www.texashomeoutlet.com. This is what customers and staff use. |
| Promote | The separate command that sends 100% of visits to one named revision. |
| Revision name | The Cloud Run name, shaped like `project-go-forward-00531-lor`. |
| Commit | The git SHA. `/healthz/` reports it in the `version` field. |

Service facts, used in every command below:

- Google Cloud project: `tho-ai-agent`
- Cloud Run service: `project-go-forward`
- Region: `us-central1`

Always pass `--project tho-ai-agent`. A laptop's default project may be a
different one.

## Checklist

### 1. Use the green Deploy run for the commit you mean to ship

Open GitHub → Actions → **Deploy to Cloud Run**.

- The run is green.
- The run's commit is the one you intend to publish.
- The run finished. A newer merge can cancel an older run. A canceled run is
  not a release.

Green means the tests passed and the candidate was checked. The public site
is unchanged at this point.

### 2. Write down what is live now

This is the undo button. Save it before you change anything.

```bash
gcloud run services describe project-go-forward \
  --project tho-ai-agent \
  --region us-central1 \
  --format='yaml(status.traffic)'
```

Copy the revision whose `percent` is `100`. That is the rollback revision.

```bash
curl -fsS https://www.texashomeoutlet.com/healthz/
```

Copy the `version` value. That is the commit customers see right now.

### 3. Confirm the candidate is that same commit

In the green Deploy log, copy the line `Candidate URL:`. It looks like
`https://candidate---project-go-forward-....run.app`.

Check these three addresses on that host. They stay on the candidate:

```bash
CANDIDATE_URL="https://candidate---...."   # paste the real URL

curl -fsS "$CANDIDATE_URL/healthz/"
curl -fsS "$CANDIDATE_URL/readyz"
curl -fsS -o /dev/null -w 'inventory-context %{http_code}\n' \
  "$CANDIDATE_URL/api/marketing/inventory-context"
```

Use the commands as written (GET). `curl -I` is HEAD. On `/api/...` a HEAD
request can return 404 when the same GET returns 200.

Pass looks like this:

- `/healthz/` has `"status": "ok"` and `version` equal to the full commit SHA
  from the green Deploy run.
- `/readyz` has `"ready": true`.
- inventory-context prints `inventory-context 200`.

Then match the `candidate` tag to a revision name:

```bash
gcloud run services describe project-go-forward \
  --project tho-ai-agent \
  --region us-central1 \
  --format='yaml(status.traffic)'
```

Find the entry with `tag: candidate` and copy its `revisionName`. If a later
Deploy has already moved that tag, stop. Promote only the revision whose
`/healthz/` version you just checked.

Page checks on the candidate host follow a redirect to the live site. Skip
them until step 6. Details are in
[Candidate page links follow the live site](#candidate-page-links-follow-the-live-site).

### 4. Get owner approval for this exact revision

The approval names all of these:

- the revision name from step 3
- the commit SHA
- the rollback revision from step 2
- what counts as a failed publish, so rollback can proceed from
  [RUNBOOK.md](RUNBOOK.md) without a second meeting

### 5. Promote that revision to 100%

Send all customer traffic to the revision name you wrote down:

```bash
gcloud run services update-traffic project-go-forward \
  --project tho-ai-agent \
  --region us-central1 \
  --to-revisions=REVISION_NAME=100
```

Replace `REVISION_NAME` with the `revisionName` you copied in step 3.

When you have just confirmed the `candidate` tag still points at that same
revision, this command does the same publish:

```bash
gcloud run services update-traffic project-go-forward \
  --project tho-ai-agent \
  --region us-central1 \
  --to-tags=candidate=100
```

Use a revision name when any time has passed since step 3. Another Deploy can
move the `candidate` tag onto a newer revision.

Leave `--to-latest` unused. It can select a revision nobody reviewed.

There is no Promote button in GitHub Actions. This repository has no GitHub
Environment with a required reviewer. A workflow job that called
`update-traffic` would create an unprotected environment the first time it
ran, and a manual run could move the live site without a reviewer. Promotion
stays the command above, after the approval in step 4.

### 6. Check the live site after promotion

Run these on https://www.texashomeoutlet.com.

```bash
curl -fsS https://www.texashomeoutlet.com/healthz/
curl -fsS https://www.texashomeoutlet.com/readyz
```

- `/healthz/` shows `"status": "ok"` and `version` equal to the commit you
  promoted. Use the trailing slash. External checks are verified on
  `/healthz/`.
- `/readyz` shows `"ready": true`.

Key pages should return the site itself (HTTP 200):

```bash
for path in / /inventory /contact /appointments /studio /ad-studio; do
  code=$(curl -s -o /dev/null -w '%{http_code}' "https://www.texashomeoutlet.com${path}")
  echo "$code $path"
done
```

Each line should start with `200`. The command is a GET. `curl -I` is HEAD
and can print a different code than the page a browser loads.

`/ad-studio` is the Ad Studio page (Google Ads for signed-in staff). Signed-out
visitors get the staff sign-in shell. The page should load. A "not found" page
or a blank error means the check failed.

From a copy of this repository, the fuller read-only check is:

```bash
python3 scripts/production_smoke.py \
  --base-url https://www.texashomeoutlet.com \
  --expect-commit FULL_COMMIT_SHA
```

`FULL_COMMIT_SHA` is the 40-character commit from the Deploy run. The script
prints `"ok": true` when the public checks pass.

### 7. If the live site is wrong

Point traffic back at the revision you saved in step 2. The commands are in
[RUNBOOK.md](RUNBOOK.md), section "Rollback". Publish the fix later as a new
candidate, then repeat this list.

## Candidate page links follow the live site

The candidate address (`https://candidate---....run.app`) answers health and
API checks itself. For most website pages it answers with **HTTP 308** and
sends the browser to the same path on https://www.texashomeoutlet.com.

Home, inventory, contact, appointments, and `/ad-studio` all do this. A
browser follows the redirect. So does the Deploy smoke script
(`scripts/production_smoke.py` uses a client that follows redirects). The page
you then see is the **current live site**, which is still the previous
version until step 5.

A green HTML result on `candidate---....run.app` can be a result from the site
you have not changed yet. Treat those page results as unreliable.

These candidate paths stay on the candidate. Use them in step 3:

- `/health` and `/healthz/`
- `/readyz`
- paths that start with `/api/`
- `/robots.txt`, `/sitemap.xml`, and `/llms.txt`
- `/staff` (the sign-in page stays on the candidate host)

The Deploy workflow requests three of these paths on the tag address before
smoke: `/healthz/`, `/api/marketing/inventory-context`, and `/staff`. It does
not follow redirects. A 308 does not count as warm. `GET /` is not in that
list, because the tag host sends `/` to the live site.

Check website pages, including the `/ad-studio` shell, on
https://www.texashomeoutlet.com after promotion (step 6).

## What this list does not change

Reading this file does not deploy, merge, or move traffic. It does not change
DNS, passwords, secrets, or who can access the Google Cloud project.

Related: [RUNBOOK.md](RUNBOOK.md) (rollback and incidents),
[PRODUCTION_READINESS.md](PRODUCTION_READINESS.md) (read-only smoke details),
[ON_CALL.md](ON_CALL.md).
