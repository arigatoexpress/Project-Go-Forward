# Launch Readiness — Texas Home Outlet Go-Live

Status ledger for the pre-launch punch list. GO requires every row ✅ and
operator sign-off on this file's PR trail.

Last updated: 2026-06-10 (session: Phase 0 + Phase 1 partial).

## Punch list

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | Branch protection on `main` | ✅ DONE | Ruleset active (require PR, required check `test`, block force-push, no bypass). Verified 2026-06-09: direct push **rejected** (403); PR #136 with green `test` **merged**. |
| 2 | HEAD handler for `/` | ✅ DONE | PR #135. Prod verified: `HEAD / → 200`; regression test `test_head_requests_supported_for_uptime_monitors`. |
| 3 | `/api/v1/inventory` 503 | ⚠️ DIAGNOSED — operator action | Intentional fail-closed when no partner key configured (`require_partner_api_key`). **Action: attach `THO_API_KEY` secret to the Cloud Run service** (command in `docs/RUNBOOK.md` §3.4). |
| 4 | Secrets posture | ⚠️ PARTIAL | gitleaks v8.24.3 over full history (271 commits): **no credentials** (only public captcha sitekeys in archived HTML + baseline self-matches). CI `ADMIN_PIN_HASH` is a test fixture (OK). **Action: verify prod PIN strength** per `docs/PRODUCTION_READINESS.md`. |
| 5 | Machine-specific path purge | ✅ DONE | PR #135; `docs/LOCAL_DATA.md` added; repo-wide grep for `/Users/` clean. |
| 6 | DocuSeal e-sign deployed + E2E | ❌ NOT DEPLOYED | `deploy-docuseal.yml` has **zero workflow runs** — the e-sign server has never been stood up. Code is merged (#127–129) but inert. **Action: run the workflow per `docs/DOCUSEAL_DEPLOY_RUNBOOK.md`, then E2E test incl. ESIGN consent.** |
| 7 | PR #118 (llms.txt) | ✅ DONE | Merged 2026-06-09; prod serves `/llms.txt`. |
| 8 | Operational readiness | ⚠️ PARTIAL | `docs/RUNBOOK.md` written (rollback, triage tree, secret rotation). **Still needed (GCP console): uptime check + alerting to phone/email, 5xx error-rate alert, budget alarm on `tho-ai-agent`, Firestore backup schedule + tested restore.** |
| 9 | Pre-launch gauntlet | ⚠️ PARTIAL | See below. |
| 10 | SEO cutover surface (client request 2026-06-10) | ✅ CODE DONE — operator steps remain | `seo_routes.py`: all 279 legacy texashomeoutlet.com detail URLs kept alive at 200 with per-home meta/JSON-LD/crawlable HTML; 279 quote-URL 301s; sitemap.xml; robots.txt; real 404s; noindex on admin routes; llms.txt rewrite. 13 new tests. **Operator: cutover checklist in `docs/SEO_MIGRATION.md`** (GSC domain property, DNS TTL, sitemap submission, GBP check, `CANONICAL_PUBLIC_URL` env). |

## Gauntlet evidence (item 9)

| Check | Status | Evidence (2026-06-10) |
|---|---|---|
| Full test suite | ✅ | `pytest tests/` → **628 passed, 29 skipped** (incl. after dependency upgrades) |
| Frontend build | ✅ | `npm ci && npm run build` clean (Vite + PWA, 20 precache entries) |
| Frontend lint | ✅ | `eslint .` clean |
| ruff | ⚠️ | CI scope (test_healthz) clean; **full-repo `ruff check .` has 379 latent findings** (328 auto-fixable). CI only lints one file. Recommend a mechanical autofix PR + widening CI scope. |
| Live frontend smoke | ✅ | Headless Chromium against prod: home + inventory render live data (273 houses hero, property cards, images), Document Center correctly gated by admin modal, Contact renders. Only console error = sandbox TLS-proxy artifact. |
| Backend dependency audit | ✅* | pip-audit: 33 findings → 1 after upgrades (pypdf 6.10.2, Pillow 12.2.0, google-adk 1.28.1, fastapi 0.136.3, pytest 9.0.3). *Remaining: starlette PYSEC-2026-161 — fix (1.0.1) blocked by `google-adk<2.0`; tracked for the google-adk 2.x upgrade branch. |
| Frontend dependency audit | ✅ | `npm audit`: 1 moderate (dev-chain `brace-expansion`), no prod-impacting findings |
| E2E vs staging revision | ❌ TODO | Needs a staging revision + operator-run pass: inventory browse, lead submission, admin login (PIN + passkey), packet generation + download, e-sign flow, partner API auth |
| Load sanity (20 rps × 5 min, p95 < 1s, zero 5xx) | ❌ TODO | Run against a staging revision, not prod |

## Known accepted risks (pre-launch)

1. **starlette PYSEC-2026-161** — unfixable until google-adk 2.x; revisit on the adk-2 branch.
2. **Event-loop wedge under Firestore hang** — a hanging Firestore call can stall an instance (see `docs/RUNBOOK.md` §3.2). Cloud Run probes recycle wedged instances; full fix (timeouts on Firestore calls) is post-launch hardening.
3. **Two empty `protection test` commits** in `main` history — no-op artifacts of the 2026-06-09 branch-protection verification.

## GO decision

**Current answer: NO.** Blocking items: 3 (partner key), 4 (PIN verify), 6
(DocuSeal deploy + E2E), 8 (monitoring/alerts/backup), 9 (staging E2E + load).
Operator sign-off recorded by approving/merging the PR that flips this section
to GO.
