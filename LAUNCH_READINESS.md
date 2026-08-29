# Launch Readiness — Texas Home Outlet Go-Live

Status ledger for the pre-launch punch list. GO requires every row ✅ and
operator sign-off on this file's PR trail.

Last updated: 2026-08-29 (read-only operational audit reconciled GitHub, Cloud Run, DNS, and public health state). The code-side improvement backlog remains closed; the remaining work is inventory-source recovery, e-sign deployment/E2E, release-governance enforcement, and operator validation.

## Punch list

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | Branch protection on `main` | ⚠️ PARTIAL | Active ruleset requires a PR and blocks deletion/non-fast-forward with no bypass actors, but the 2026-08-29 GitHub API readback shows **no required status-check rule**. Until `test` is enforced by the ruleset, explicitly wait for all applicable checks to finish green before every merge. |
| 2 | HEAD handler for `/` | ✅ DONE | PR #135. Prod verified: `HEAD / → 200`; regression test `test_head_requests_supported_for_uptime_monitors`. |
| 3 | Partner API key | ✅ DONE | Ops bootstrap run `27723297970` succeeded on 2026-06-17 and the serving revision has `THO_API_KEY` attached from Secret Manager. Partner routes remain intentionally fail-closed if that binding is ever removed. |
| 4 | Secrets posture | ⚠️ PARTIAL | gitleaks v8.24.3 over full history (271 commits): **no credentials** (only public captcha sitekeys in archived HTML + baseline self-matches). CI `ADMIN_PIN_HASH` is a test fixture (OK). **Action: verify prod PIN strength** per `docs/PRODUCTION_READINESS.md`. |
| 5 | Machine-specific path purge | ✅ DONE | PR #135; `docs/LOCAL_DATA.md` added; repo-wide grep for `/Users/` clean. |
| 6 | DocuSeal e-sign deployed + E2E | ❌ UNAVAILABLE | Five workflow attempts on 2026-06-17 failed container startup. The `docuseal` Cloud Run service has no ready revision; `docuseal-db` exists but is stopped; only the infrastructure secrets exist. `main` references mutable `docuseal/docuseal:latest`. Re-evaluate and pin a current compatible image digest, add a boot/migration smoke, then complete the gated deployment and signer/webhook E2E. |
| 7 | PR #118 (llms.txt) | ✅ DONE | Merged 2026-06-09; prod serves `/llms.txt`. |
| 8 | Operational readiness | ⚠️ PARTIAL | Ops bootstrap run `27723297970` succeeded: uptime and 5xx alerts, daily Firestore backups, partner key, staging tag, and budget alarm were created. The backup restore drill remains unverified. |
| 9 | Pre-launch gauntlet | ⚠️ PARTIAL | See below. |
| 10 | DNS + SEO cutover surface | ✅ DNS LIVE / ⚠️ SEO OPS | Apex and `www.texashomeoutlet.com` both serve the app with valid TLS; `www` is the canonical origin in homepage, robots, and sitemap output. The code preserves the legacy detail/quote URL surface. Search Console sitemap acceptance and Business Profile link verification remain operator evidence items; future DNS changes remain gated. |
| 11 | Current-listing freshness | ❌ STALE | On 2026-08-29, production `/readyz/` reported the selected `legacy_site_snapshot` was retrieved 2026-05-11, age 109 days, with 19 current listings and `ok=false`; readiness remains soft-green so the known catalog stays visible. The public 279-home response includes those 19 current listings plus 260 orderable floorplans. Recover an operator-approved current source and prove candidate parity before changing `INVENTORY_SOURCE`. |

## Gauntlet evidence (item 9)

| Check | Status | Current evidence |
|---|---|---|
| Full test suite | ✅ | Latest `main` run `31784526533` (2026-08-14): **2,122 passed, 24 skipped**. |
| Frontend build/tests | ✅ | Build clean; **39 test files / 260 tests passed** in run `31784526533`. |
| Frontend lint | ✅ | `eslint .` clean |
| ruff | ✅ | PR #138: repo-wide `ruff check .` clean (379 findings fixed); CI lint step widened to the full repo. |
| Live public smoke | ✅ | Read-only 2026-08-29 production smoke: **33/33 probes passed**, including canonical authority, 279-home catalog response, media depth, admin protection, and appointment slots. This does not override the stale-current-inventory blocker above. |
| Backend dependency audit | ✅ | pip-audit clean of runtime findings after google-adk 2.5.0 + starlette 1.3.1 + pypdf 6.14.2 (PR #297). Only remainder: setuptools 80.9.0 PYSEC-2026-3447 (build tool, not a runtime pin). |
| Frontend dependency audit | ✅ | `npm audit`: 1 moderate (dev-chain `brace-expansion`), no prod-impacting findings |
| E2E vs staging revision | ❌ TODO | Needs a staging revision + operator-run pass: inventory browse, lead submission, admin login (PIN + passkey), packet generation + download, e-sign flow, partner API auth. **PR #298 (merged 2026-07-25):** `tests/e2e/test_staging_gauntlet.py` codifies the two previously untestable flows (partner-API authorized access, DocuSeal e-sign) — 7 tests, skip gracefully until the operator provisions the `tho-api-key` secret and the e-sign server |
| Load sanity (20 rps × 5 min, p95 < 1s, zero 5xx) | ❌ TODO | Run against a staging revision, not prod |

## Known accepted risks (pre-launch)

1. ~~starlette PYSEC-2026-161~~ — **CLEARED by PR #297**: google-adk 2.5.0 permits starlette 1.3.1 (also clears PYSEC-2026-248/249/2280/2281); pypdf 6.14.2 clears CVE-2026-59935/36/37/38. pip-audit remainder: setuptools 80.9.0 PYSEC-2026-3447 (build tool, not a runtime pin).
2. **Event-loop wedge under Firestore hang** — a hanging Firestore call can stall an instance (see `docs/RUNBOOK.md` §3.2). Cloud Run probes recycle wedged instances. **CODE FIX MERGED via PR #296 (2026-07-25):** all request-path Firestore RPCs now pass a bounded `timeout` (shared `database/rpc_timeout.py`, default 10s, env `FIRESTORE_RPC_TIMEOUT_SECONDS`); the 3 coverage-audit gaps (chat lead persist in `tools/crm_tools.py`, transactional read in `lead_management.transition_lead_status`, transaction Begin/Commit RPCs wall-clock bounded via `asyncio.wait_for` + `FIRESTORE_TRANSACTION_TIMEOUT`) are closed; +4 regression tests; full suite 1791 passed. **Remaining:** the fix reaches production on the next traffic promotion (operator canary cutover) — until then the old revision still carries the risk.
3. **Two empty `protection test` commits** in `main` history — no-op artifacts of the 2026-06-09 branch-protection verification.

## GO decision

**Current answer: NO.** Blocking items: 1 (required CI status enforcement), 4
(PIN verify), 6 (DocuSeal deploy + E2E), 8 (backup restore drill), 9 (staging
E2E + load), and 11 (current-listing freshness/source recovery).
Operator sign-off recorded by approving/merging the PR that flips this section
to GO.
