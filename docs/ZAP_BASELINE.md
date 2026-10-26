# OWASP ZAP baseline

Weekly passive scan of the public storefront, `https://www.texashomeoutlet.com`.
Workflow: `.github/workflows/zap-baseline.yml`. Rules: `.zap/rules.tsv`.
Tracked as COD-132 (security roadmap R16).

The job is report-only. It runs Monday mornings (08:41 MT) and from the Actions
tab (`workflow_dispatch`). It does not run on pull requests, so it cannot block
a merge. Findings do not fail the job. The scan does not log in and does not
use a repository secret.

## What the scan does

The pinned `zaproxy/action-baseline` image runs `zap-baseline.py`. That script
spiders the public origin for about a minute, then runs ZAP's stable passive
rules. It does not send attack payloads and it is not the active full scan.

`GET /healthz/` must return `"status": "ok"` before the spider starts. The
image is `ghcr.io/zaproxy/zaproxy:2.17.0`, pinned by digest in the workflow.

## How to read a report

1. Open the workflow run and download the `zap-baseline-report` artifact.
2. Open `report_html.html` in a browser. Alerts are grouped by risk
   (High, Medium, Low, Informational). This file is ZAP's own report, so it
   still lists alerts that `.zap/rules.tsv` marks `IGNORE` or `OUTOFSCOPE`.
3. Use `report_json.json` for the same alerts under `site[].alerts[]`.
   `riskcode` is `3` High, `2` Medium, `1` Low, `0` Informational. `pluginid`
   is the id to edit in the rules file. The artifact also includes
   `report_md.md`.
4. The job log prints the counts that the rules file actually applied:
   `FAIL-NEW`, `WARN-NEW`, `INFO`, `IGNORE`, and `PASS`. An `IGNORE` row keeps
   that plugin from counting as a warning. An `OUTOFSCOPE` row does the same
   only for URLs that match its regex. The comment above each of those rows
   says why it is suppressed.

A red run means the scan did not finish (the health check failed, or ZAP exited
with code 3). A green run can still contain High alerts. Read the artifact
either way.

## Tighten later: fail on High alerts

This stays report-only until an operator chooses to gate on it. `fail_action`
fails the job on any warning, not only High, so do not flip that switch by itself.

1. In a weekly `report_json.json`, copy the `pluginid` of each alert whose
   `riskcode` is `3`.
2. In `.zap/rules.tsv`, add those ids as `FAIL`, with a comment on the row.
   Leave accepted noise as `IGNORE` or `OUTOFSCOPE`. Unlisted rules stay `WARN`.
3. In `.github/workflows/zap-baseline.yml`, set `cmd_options: '-I'` so leftover
   warnings do not fail the container, and set `fail_action: true`. A `FAIL`
   rule then fails the job. Warnings do not.
4. Keep the workflow off `pull_request` until you intentionally want it as a
   merge check. A scheduled failure does not block other CI.

Do not switch this workflow to the full scan action, add `-j` (Ajax spider),
or add authentication.
