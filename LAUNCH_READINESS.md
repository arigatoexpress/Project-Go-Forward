# Launch Readiness — Texas Home Outlet Go-Live

Status ledger for the pre-launch punch list. GO requires every row ✅ and
operator sign-off on this file's PR trail.

Last updated: 2026-07-25 (firestore-timeouts branch hardened by independent coverage audit — 3 remaining gaps closed; still pending PR merge). Previously: 2026-07-24 (Firestore RPC-timeout hardening code-complete on branch `agent/firestore-timeouts`); 2026-06-10 (Phase 0 + Phase 1 code-side complete).

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
| Backend dependency audit | ✅* | pip-audit: 33 findings → 1 after upgrades (pypdf 6.10.2, Pillow 12.2.0, google-adk 1.28.1, fastapi 0.136.3, pytest 9.0.3). *Remaining: starlette PYSEC-2026-161 — fix (1.0.1) blocked by `google-adk<2.0`; tracked for the google-adk 2.x upgrade branch. |
| Frontend dependency audit | ✅ | `npm audit`: 1 moderate (dev-chain `brace-expansion`), no prod-impacting findings |
| E2E vs staging revision | ❌ TODO | Needs a staging revision + operator-run pass: inventory browse, lead submission, admin login (PIN + passkey), packet generation + download, e-sign flow, partner API auth |
| Load sanity (20 rps × 5 min, p95 < 1s, zero 5xx) | ❌ TODO | Run against a staging revision, not prod |

## Known accepted risks (pre-launch)

1. **starlette PYSEC-2026-161** — unfixable until google-adk 2.x; revisit on the adk-2 branch.
2. **Event-loop wedge under Firestore hang** — a hanging Firestore call can stall an instance (see `docs/RUNBOOK.md` §3.2). Cloud Run probes recycle wedged instances. **CODE FIX on branch `agent/firestore-timeouts` (2026-07-24):** all request-path Firestore RPCs now pass a bounded `timeout` (shared `database/rpc_timeout.py`, default 10s, env `FIRESTORE_RPC_TIMEOUT_SECONDS`); suite green. **2026-07-25 follow-up:** independent coverage audit of the branch found and closed the last 3 gaps — chat lead persist in `tools/crm_tools.py`, the transactional read in `lead_management.transition_lead_status`, and the structurally un-timeout-able transaction Begin/Commit RPCs (now wall-clock bounded with `asyncio.wait_for` + `FIRESTORE_TRANSACTION_TIMEOUT` at both transaction call sites: lead transition + appointment booking). +4 regression tests; full suite 1791 passed. Risk clears once that branch merges and deploys; until then it stands.
3. **Two empty `protection test` commits** in `main` history — no-op artifacts of the 2026-06-09 branch-protection verification.

## GO decision

**Current answer: NO.** Blocking items: 3 (partner key), 4 (PIN verify), 6
(DocuSeal deploy + E2E), 8 (monitoring/alerts/backup), 9 (staging E2E + load).
Operator sign-off recorded by approving/merging the PR that flips this section
to GO.
