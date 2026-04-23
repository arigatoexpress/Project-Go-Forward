# Project-Go-Forward — Shared Agent Guide

This repo is the Texas Home Outlet app (`arigatoexpress/Project-Go-Forward`): a FastAPI + React application that powers the live THO site, CRM flows, document generation, partner API endpoints, and regulatory PDF RAG search. It auto-deploys to Cloud Run on every push to `main`, so agent work here is always production-adjacent.

## Production State

- Production URL: `https://project-go-forward-trgi34bxuq-uc.a.run.app`
- Health check: `curl -fsS https://project-go-forward-trgi34bxuq-uc.a.run.app/health`
- Logs live in Google Cloud Logging for Cloud Run service `project-go-forward` in project `tho-ai-agent`
- CLI log check:
  - `gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="project-go-forward"' --project tho-ai-agent --limit 50`
- Rollback:
  - `gcloud run revisions list --service project-go-forward --project tho-ai-agent --region us-central1`
  - `gcloud run services update-traffic project-go-forward --project tho-ai-agent --region us-central1 --to-revisions <REVISION>=100`
- Claude-specific build/deploy detail stays in [CLAUDE.md](CLAUDE.md)

## Workflow For Any AI Agent

- Always branch from the real remote main, never from local `main`:
  - `git fetch origin && git switch -c <branch> origin/main`
- Branch naming:
  - `feat/*`, `fix/*`, `chore/*`, `docs/*`, `test/*`
- Commit style:
  - imperative, scoped when useful, matching existing history like `feat(rag): ...`, `fix(ci): ...`, `chore: ...`
- Open a draft PR by default unless the branch is clearly throwaway or exploratory
- Never merge your own PR without explicit human approval
- This repo is production-adjacent, so even trivial-looking changes still require a human before merge
- Run pre-commit before push:
  - `pre-commit run --files <changed-files>`
  - Use `pre-commit run --all-files` only for explicit repo-wide hygiene branches

## Division Of Labor

- Claude Code: ongoing review, architecture, deployment-sensitive coordination, large cross-cutting changes
- Codex: well-scoped refactors, repo hygiene, test-writing, toolchain and automation cleanup
- Kimi Code: TODO document the best-fit tasks after more direct usage data from this repo

## Conflict Avoidance

- Before starting, check both:
  - `gh pr list --limit 20`
  - `git worktree list`
- If another branch already touches the same area, prefer stacking on that branch or rebasing onto it instead of parallel edits
- If multiple agents are active, call out intended file ownership in the PR description or handoff note

## File-Level Ownership Hints

- `main.py` is large and frequently edited by multiple agents; if two branches touch it, the later branch rebases
- `frontend/src/pages/CRM.jsx`, `frontend/src/App.jsx`, and `database/models.py` also see frequent overlap during feature work
- `tho_documents/` contains regulatory PDFs; never modify those files in agent work

## Local Environment

- Canonical Python target: `3.11` via [`.python-version`](.python-version)
- Install dev tooling with [docs/DEV_SETUP.md](docs/DEV_SETUP.md)
- Keep local `main` disposable; if it drifts, back it up and recreate it from `origin/main` rather than branching from stale history
