# Launch Readiness — Texas Home Outlet Go-Live

Status ledger for the pre-launch punch list. GO requires every row ✅ and
operator sign-off on this file's PR trail.

Last updated: 2026-07-25 (merge-train cycle: **PR #298** staging-gauntlet e2e tests codified for partner-API auth + DocuSeal e-sign, **PR #299** API reference docs, **PR #300** user-safe error messages — all merged; code side of IMPROVEMENTS.md fully closed except user guides). Previously: 2026-07-25 (Firestore RPC-timeout hardening **merged via PR #296** — risk #2 code is on `main`; clears fully on the next production traffic promotion, which remains an operator canary cutover). Previously: 2026-07-24 (code-complete on branch `agent/firestore-timeouts`); 2026-06-10 (Phase 0 + Phase 1 code-side complete).

## Punch list

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | Branch protection on `main` | ✅ DONE | Ruleset active (require PR, required check `test`, block force-push, no bypass). Verified 2026-06-09: direct push **rejected** (403); PR #136 with green `test` **merged**. |
| 2 | HEAD handler for `/` | ✅ DONE | PR #135. Prod verified: `HEAD / → 200`; regression test `test_head_requests_supported_for_uptime_monitors`. |
| 3 | `/api/v1/inventory` 503 | ⚠️ DIAGNOSED — operator action | Intentional fail-closed when no partner key configured (`require_partner_api_key`). **Action: one click — run the "Ops bootstrap" workflow** (Actions tab), which creates the `tho-api-key` secret (never printed; retrieve via `gcloud secrets versions access latest --secret=tho-api-key`) and attaches it as `THO_API_KEY`. Manual fallback: `docs/RUNBOOK.md` §3.4. |
| 4 | Secrets posture | ⚠️ PARTIAL | gitleaks v8.24.3 over full history (271 commits): **no credentials** (only public captcha sitekeys in archived HTML + baseline self-matches). CI `ADMIN_PIN_HASH` is a test fixture (OK). **Action: verify prod PIN strength** per `docs/PRODUCTION_READINESS.md`. |
| 5 | Machine-specific path purge | ✅ DONE | PR #135; `docs/LOCAL_DATA.md` added; repo-wide grep for `/Users/` clean. |
| 6 | DocuSeal e-sign deployed + E2E | ❌ NOT DEPLOYED | `deploy-docuseal.yml` has **zero workflow runs** — the e-sign server has never been stood up. Code is merged (#127–129) but inert. **Action: one click — run the "Deploy DocuSeal (e-sign server)" workflow** (Actions tab, defaults are correct; creates Cloud SQL db-g1-small + a 1-instance Cloud Run service, ~$40-80/mo). Then the ~5-min manual steps printed at the end of the run (admin account, API token, app secrets, webhook), then E2E test incl. ESIGN consent — `docs/DOCUSEAL_DEPLOY_RUNBOOK.md`. |
| 7 | PR #118 (llms.txt) | ✅ DONE | Merged 2026-06-09; prod serves `/llms.txt`. |
| 8 | Operational readiness | ⚠️ PARTIAL | `docs/RUNBOOK.md` written (rollback, triage tree, secret rotation). **Action: one click — run the "Ops bootstrap" workflow** (Actions tab): uptime check on /healthz/ + email alerting, 5xx-burst alert, daily Firestore backups (7-day retention), $200/mo budget alarm (best-effort), and a no-traffic `staging` tag for the load test. Idempotent; any step that 403s names the missing WIF role. Backup **restore drill** remains manual after the schedule exists. |
| 9 | Pre-launch gauntlet | ⚠️ PARTIAL | See below. |
| 10 | SEO cutover surface (client request 2026-06-10) | ✅ CODE DONE — operator steps remain | `seo_routes.py`: all 279 legacy texashomeoutlet.com detail URLs kept alive at 200 with per-home meta/JSON-LD/crawlable HTML; 279 quote-URL 301s; sitemap.xml; robots.txt; real 404s; noindex on admin routes; llms.txt rewrite. 13 new tests. **Operator: cutover checklist in `docs/SEO_MIGRATION.md`** (GSC domain property, DNS TTL, sitemap submission, GBP check, `CANONICAL_PUBLIC_URL` env). |

## Gauntlet evidence (item 9)

| Check | Status | Evidence (2026-06-10) |
|---|---|---|
| Full test suite | ✅ | `pytest tests/` → **628 passed, 29 skipped** (incl. after dependency upgrades) |
| Frontend build | ✅ | `npm ci && npm run build` clean (Vite + PWA, 20 precache entries) |
| Frontend lint | ✅ | `eslint .` clean |
| ruff | ✅ | PR #138: repo-wide `ruff check .` clean (379 findings fixed); CI lint step widened to the full repo. |
| Live frontend smoke | ✅ | Headless Chromium against prod: home + inventory render live data (273 houses hero, property cards, images), Document Center correctly gated by admin modal, Contact renders. Only console error = sandbox TLS-proxy artifact. |
| Backend dependency audit | ✅ | pip-audit clean of runtime findings after google-adk 2.5.0 + starlette 1.3.1 + pypdf 6.14.2 (PR #297). Only remainder: setuptools 80.9.0 PYSEC-2026-3447 (build tool, not a runtime pin). |
| Frontend dependency audit | ✅ | `npm audit`: 1 moderate (dev-chain `brace-expansion`), no prod-impacting findings |
| E2E vs staging revision | ❌ TODO | Needs a staging revision + operator-run pass: inventory browse, lead submission, admin login (PIN + passkey), packet generation + download, e-sign flow, partner API auth. **PR #298 (merged 2026-07-25):** `tests/e2e/test_staging_gauntlet.py` codifies the two previously untestable flows (partner-API authorized access, DocuSeal e-sign) — 7 tests, skip gracefully until the operator provisions the `tho-api-key` secret and the e-sign server |
| Load sanity (20 rps × 5 min, p95 < 1s, zero 5xx) | ❌ TODO | Run against a staging revision, not prod |

## Known accepted risks (pre-launch)

1. ~~starlette PYSEC-2026-161~~ — **CLEARED by PR #297**: google-adk 2.5.0 permits starlette 1.3.1 (also clears PYSEC-2026-248/249/2280/2281); pypdf 6.14.2 clears CVE-2026-59935/36/37/38. pip-audit remainder: setuptools 80.9.0 PYSEC-2026-3447 (build tool, not a runtime pin).
2. **Event-loop wedge under Firestore hang** — a hanging Firestore call can stall an instance (see `docs/RUNBOOK.md` §3.2). Cloud Run probes recycle wedged instances. **CODE FIX MERGED via PR #296 (2026-07-25):** all request-path Firestore RPCs now pass a bounded `timeout` (shared `database/rpc_timeout.py`, default 10s, env `FIRESTORE_RPC_TIMEOUT_SECONDS`); the 3 coverage-audit gaps (chat lead persist in `tools/crm_tools.py`, transactional read in `lead_management.transition_lead_status`, transaction Begin/Commit RPCs wall-clock bounded via `asyncio.wait_for` + `FIRESTORE_TRANSACTION_TIMEOUT`) are closed; +4 regression tests; full suite 1791 passed. **Remaining:** the fix reaches production on the next traffic promotion (operator canary cutover) — until then the old revision still carries the risk.
3. **Two empty `protection test` commits** in `main` history — no-op artifacts of the 2026-06-09 branch-protection verification.

## GO decision

**Current answer: NO.** Blocking items: 3 (partner key), 4 (PIN verify), 6
(DocuSeal deploy + E2E), 8 (monitoring/alerts/backup), 9 (staging E2E + load).
Operator sign-off recorded by approving/merging the PR that flips this section
to GO.
