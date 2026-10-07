# Shippable Audit — 2026-10-07

Scope: Texas Home Outlet storefront + API in this repository (`www.texashomeoutlet.com` on Cloud Run).

Out of scope by request:
- Staff email sign-in flow (recent PRs #369 and #370), except incidental bug protection.
- Privacy and terms pages (separate workstream / PR #368).
- Any deploy/traffic/DNS/secret/Firestore schema changes.

## What was run

### Backend
- `python3 -m ruff check .` ✅ pass
- `python3 -m pytest` ✅ pass
  - Result: `2212 passed, 50 skipped, 361 warnings`

### Frontend
- `npm --prefix frontend run lint` ✅ pass
- `npm --prefix frontend run test` ✅ pass
  - Result: `46 files, 291 tests passed`

### Dependency security
- `npm --prefix frontend audit --json` ❌ 4 vulnerabilities (3 high, 1 moderate)
- `python3 -m pip_audit` ❌ 71 findings across 11 packages

## Dependabot alert triage addendum (18 open alerts, requested)

Notes:
- GitHub Dependabot REST alert listing is not accessible from this cloud run context (`403 Resource not accessible by integration`), so this triage uses the owner-provided open-alert count plus reproducible local evidence (`npm audit`, `pip-audit`, and dependency graph inspection).
- Safe/non-breaking fixes were applied in this PR (lockfile updates + patch/minor pin updates only).

### Safe fixes folded into this draft PR
- `frontend/package-lock.json`: applied `npm audit fix` (resolved prior `undici`, `source-map-js`, `brace-expansion`, `fast-uri` advisories in the frontend tree).
- `frontend/package.json`: tightened override to `undici: ^7.30.0` (non-breaking within major 7).
- `requirements.txt`: bumped `pypdf` from `6.18.0` to `6.19.0` (patch/minor security uplift).

### Severity + reachability triage
| Package / alert cluster | Severity band | Reachability from storefront runtime | Action in this PR |
|---|---|---|---|
| `undici`, `source-map-js`, `brace-expansion`, `fast-uri` (frontend tree) | High/Moderate | Low direct runtime reachability (build/dev toolchain), but still supply-chain risk | **Fixed** via lockfile refresh and override floor |
| `pypdf` | High | **Directly reachable** via document-generation paths | **Fixed** (`pypdf==6.19.0`) |
| `urllib3` | High/Moderate cluster | Indirect runtime reachability via `requests` / `sentry-sdk` | Deferred (transitive, not directly pinned here) |
| `httplib2` | High/Moderate cluster | Limited reachability (mainly Drive/admin integrations, not public hot path) | Deferred (transitive via `google-api-python-client`) |
| `pyjwt`, `oauthlib` | High/Moderate cluster | Currently low observed reachability in this runtime (`Required-by` empty in env scan) | Deferred (dependency provenance to pin explicitly before changing) |
| `ansible`, `ansible-core`, `pip`, `setuptools`, `wheel` | Mixed (tooling CVEs) | Not part of request-serving app path; environment/toolchain scope | Deferred to base-image/toolchain hardening track |

### Alerts that require breaking (major) upgrades
- `oauthlib` → `4.0.0` (major).
- `ansible` → `12.2.0` (major).
- `pip` → `26.x` (major train relative to currently provisioned tooling).

These are intentionally **not** auto-applied in this draft because they can alter tooling behavior and need dedicated validation/release notes.

## Ranked findings (highest user/business impact first)

## 1) Public contact intake accepted malformed payloads and weak optional field validation (FIXED)
**Impact:** High. Contact leads are revenue-critical. Weak server-side validation increases spam/abuse surface and can let malformed records through to CRM pipelines.

**Observed:**
- `/api/contact` accepted non-object JSON payloads.
- Optional email had no server-side format validation.
- Message length bounds were not enforced server-side.
- Invalid input often responded with HTTP 200 instead of clear 4xx classification.

**Fix shipped in this PR:**
- Added strict payload-shape and field validation in `main.py`:
  - name required, trimmed, max length
  - phone required, normalized digits, 10-digit or leading-1 11-digit only
  - optional email format + max length validation
  - message max length validation
- Invalid requests now return HTTP 400 with safe user-facing errors.
- Added regression tests for each validation path and status code.

## 2) Dependency vulnerabilities in frontend transitive tree (FIXED IN THIS PR)
**Impact:** High before fix. Advisories were present in frontend dependency tree.

**Observed (`npm audit`):**
- High: `undici` (multiple advisories, including TLS/connect option handling + DoS vectors)
- High: `source-map-js`
- High: `brace-expansion`
- Moderate: `fast-uri`

**Fix shipped in this PR:** Applied lockfile-safe upgrades (`npm audit fix`) and raised the `undici` override floor to a non-vulnerable 7.x patch line; post-fix `npm audit` reports 0 vulnerabilities.

## 3) Dependency vulnerabilities in Python environment (OPEN)
**Impact:** High. `pip-audit` reports many known vulnerabilities in installed ecosystem packages, including auth/token and parsing libraries.

**Observed examples (`pip-audit`):**
- `pyjwt 2.7.0`
- `jinja2 3.1.2`
- `pypdf 6.18.0`
- `urllib3 2.6.3`
- `ansible-core 2.16.3`

**Why not fixed here:** Broad upgrades may have compatibility impact across tooling/runtime. Needs staged dependency remediation with pinned update plan and CI soak.

## 4) Appointment cancellation endpoint has weaker abuse protection than create/contact paths (OPEN)
**Impact:** Medium. Endpoint requires phone match, but currently lacks explicit route-level limiter unlike major public POST forms.

**Recommendation:** Add route-specific limiter + tests for burst attempts and 429 behavior.

## 5) Main frontend routes are largely eager-loaded (OPEN, performance)
**Impact:** Medium. Initial JS payload likely larger than necessary for first-page interactive speed, especially on mobile or weak networks.

**Observed:** App/page imports appear largely static; route-level code-splitting opportunities remain.

**Recommendation:** Introduce incremental lazy-loading for heavy admin/document/analytics views, verify no UX regressions, and monitor bundle + Web Vitals deltas.

## Additional observations (lower immediate risk)
- Pytest warnings include deprecated `datetime.utcnow()` usage at several call sites. Not breaking now, but should be migrated to timezone-aware `datetime.now(datetime.UTC)`.
- OpenAPI warning about duplicate operation IDs for `readyz` variants appears in test logs; low severity but worth cleanup.

## Stale open PR triage (requested addendum)

### Supersession checks requested (#363/#364/#365)
Diff basis used: `git diff main...pr-<id>` file-level deltas plus targeted content diffs on overlapping files.

| Claim | Verdict | Diff-based note |
|---|---|---|
| `#363` replaces `#341` | **Confirmed (substantive)** | The auth/passkey origin hardening files overlap almost exactly; `#341` additionally includes `AGENTS.md` + `docs/CLIENT_WALKTHROUGH.md` copy updates that `#363` intentionally leaves out. |
| `#364` replaces `#335` | **Confirmed** | Both carry the Resend secret binding intent in deploy/docs; the remaining `#335` `main.py` delta is a cosmetic heading-width comment change, not runtime behavior. |
| `#365` replaces `#336`, `#337`, `#338` | **Confirmed (substantive)** | `#365` includes the same inventory/media hardening surfaces (`InventoryBrowse`, classifier/scan, smoke probes, related tests) on current `main`; old branches retain a few docs/readability-only hunks not carried forward. |

### Keep / Rebase / Close recommendations
| PR | Recommendation | One-line reason |
|---|---|---|
| **#333** | **REBASE** | Docs-only operational-truth edits are stale (last update 2026-08-29) and should be reconciled with current runbooks before merge. |
| **#335** | **CLOSE** | Superseded by `#364` for Resend secret binding and prerequisite docs, with no remaining runtime-meaningful delta. |
| **#336** | **CLOSE** | Superseded substantively by `#365` on current `main` for hero fallback/ranking behavior and tests. |
| **#337** | **CLOSE** | Superseded substantively by `#365`; original branch is also stacked/non-main and no longer the clean landing path. |
| **#338** | **CLOSE** | Superseded substantively by `#365`, which carries forward and extends the production reality/smoke probe hardening. |
| **#341** | **CLOSE** | Superseded substantively by `#363` for canonical WebAuthn origin hardening on a current `main` base. |

## Changes implemented in this PR

- `main.py`
  - Added `_validate_public_contact_payload(...)` helper.
  - Hardened `/api/contact` validation and request parsing.
  - Standardized validation failures to HTTP 400 JSON responses.
- `tests/test_contact_lead_capture.py`
  - Added coverage for invalid optional email, non-object payload rejection, oversized message rejection.
  - Updated existing contact validation assertions to enforce HTTP 400 on invalid inputs.
- `frontend/src/pages/InventoryBrowse.jsx`
  - Updated stale inline comment to match current backend behavior.

## Verification after fixes
- `python3 -m pytest tests/test_contact_lead_capture.py -q` ✅
- `python3 -m pytest -q` ✅
- `npm --prefix frontend run test -- --run ContactDeliveryFailure.test.jsx` ✅
- `npm --prefix frontend run lint` ✅
- `npm --prefix frontend audit --json` ✅ (`0` vulnerabilities)
- `python3 -m pip_audit` ⚠️ residual environment/toolchain findings remain (`65` vulnerabilities across `10` packages)

## Recommended follow-up PR sequence
1. Frontend dependency patch PR (`npm audit fix` plus explicit package/override updates, full frontend + smoke validation).
2. Python dependency patch PR (upgrade vulnerable pins with compatibility matrix and targeted runtime tests).
3. Rate-limit parity PR for additional public write endpoints (including cancellation).
4. Frontend performance PR (route-level code splitting + bundle budget checks).
