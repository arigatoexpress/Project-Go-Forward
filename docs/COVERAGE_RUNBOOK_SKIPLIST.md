# Coverage Campaign — Runbook Skip List & Measurement Notes

Status after swarm cycles c7–c13 (PRs #414–#421). Every production request-path
module is at ~99–100% honest coverage (Python 3.11 measurement venv, targeted
`--cov=<module>` runs). What remains uncovered is listed here deliberately, so
future coverage work does not re-investigate it.

## 1. Intentionally uncovered: standalone operator runbook scripts

These modules have **zero production importers** (verified by grep across
`main.py`, `mira_routes.py`, `obsidian_routes.py`, `root_agent.py`, `tools/`,
`jobs/`, `services/`). They are one-off operational tooling run by hand with
`--dry-run` first; unit-testing their argparse plumbing has near-zero launch
value.

| Module | Missing lines | Purpose |
|---|---|---|
| tools/inventory_picture_audit.py | 246 | one-off picture audit |
| tools/sync_inventory_from_website.py | 183 | one-off website→inventory sync |
| tools/migrate_fastcontract.py | 174 | one-off FastContract migration |
| tools/scraper.py | 113 | standalone scraper utility |
| tools/validate_migration.py | 109 | one-off migration validation |
| tools/data_audit.py | 106 | one-off data audit |
| tools/social_live_publishers.py | 74 | standalone social publisher |
| tools/merge_inventory.py | 74 | one-off inventory merge |
| tools/social_publish_assets.py | 56 | standalone social asset publisher |
| tools/firebase_import.py | 55 | one-off Firebase import |
| tools/service_tools.py | 47 | standalone service-tool script |

If any of these gains a production importer, it leaves this list and enters the
normal coverage mandate.

## 2. Documented dead / defensive lines (pinned by structural tests, not forced)

Each was proven unreachable by campaign agents and pinned with a named test.
They are kept deliberately (defense-in-value / fail-closed guards); removal is
a separate, human-reviewed decision.

| Location | Why unreachable |
|---|---|
| main.py 2658–2660 | `get_chat_analytics` except — try body only returns a literal dict |
| database/google_ads_authority.py 402 | model validator enforces the identity coupling this double-checks |
| database/models.py 633 | `Field(ge=0)` fires before the field validator's custom raise |
| tools/inventory_sync.py 523–524 | `replace("the ", "")` can never match keys that all contain "the " |
| tools/image_storage.py 397 | durable-storage exception handler above already raises |
| tools/listing_urls.py 64–65 | exhaustive 192-combination probe: every orderable classification is already rejected upstream |
| tools/document_engine_v2.py 357–358 | before-validator coerces every falsy loan_term before the guard |
| tools/document_engine_v2.py 480–481 | try/except around `str.replace` with str args, which cannot raise |
| tools/drive_floorplan_sync.py 72 | guarded libs installed → ImportError branch cannot fire |
| google_ads_admin/approval_routes.py 259 | proof/contract/request digest binding makes drift impossible |
| google_ads_admin/status.py 34 | `validate_draft()` pins the deployment key before this guard |
| auth/routes.py 71–72 + sentry block (~285–337) | webauthn/Sentry import fallbacks + subprocess-only init (covered by `tests/test_sentry_init_smoke.py` and import-fallback tests) |
| tools/house_orders_sanitizer.py 39 | sys.path guard never fires when CWD is repo root |

## 3. Measurement-venv accounting

The periodic full-repo measurement uses a partial Python-3.12 venv
(`/tmp/sysmon-venv312`) without heavy optional deps (webauthn, google.adk,
googleapiclient, pandas, google.cloud.storage). Consequences, all expected:

- 55 tests fail there (18 long-standing + 37 added by c10–c12 campaign files
  that hard-require those deps). They pass in the main venv and CI.
- `auth/routes.py`, `auth/google_ads_step_up*.py`, `root_agent.py` show large
  "missing" numbers in that venv's JSON — a webauthn/adk-absent artifact; the
  honest main-venv numbers are 99–100%.
- Ranking for campaign purposes should consult the per-module main-venv
  targeted runs, not the 3.12 JSON, for those modules.

## 4. Suite count history (gauntlet evidence)

628 (2026-06-10) → 1791 → 2609 (2026-10-12) → 3344 (PR #409) → 3596 (PR #413)
→ 3599 (PR #414) → 3709 (PR #415) → 3894 (PR #417) → 3959 (PR #418)
→ 4152 (PR #419) → 4402 (PR #420) → current (PR #421, c13: +74 tests).
