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

## 2) Dependency vulnerabilities in frontend transitive tree (OPEN)
**Impact:** High. Current scan reports high-severity advisories in shipped build dependencies.

**Observed (`npm audit`):**
- High: `undici` (multiple advisories, including TLS/connect option handling + DoS vectors)
- High: `source-map-js`
- High: `brace-expansion`
- Moderate: `fast-uri`

**Why not fixed here:** Requires coordinated lockfile/package updates and retest of build/runtime behavior. This should be done in a dedicated dependency-refresh PR to keep review scope safe.

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
- **#333 — REBASE:** Docs-only operational-truth changes are old (last updated 2026-08-29) and should be rebased/revalidated against current runbook/docs before merge.
- **#335 — CLOSE:** The `RESEND_API_KEY` Secret Manager binding this PR adds is already present on `main` in `.github/workflows/deploy.yml`, so this branch is effectively superseded.
- **#336 — CLOSE:** Core inventory hero/fallback behavior from this branch (lazy-watchdog + fallback handling) is already represented in `main` (`InventoryBrowse.jsx` + hero fallback/watchdog tests), so the PR appears superseded.
- **#337 — REBASE:** It is stacked on `fix/hero-photo-ranking` (not `main`) and introduces a different floorplan-classification path; rebase to `main` and re-justify against current `tools/photo_classifier.py` before any merge decision.

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

## Recommended follow-up PR sequence
1. Frontend dependency patch PR (`npm audit fix` plus explicit package/override updates, full frontend + smoke validation).
2. Python dependency patch PR (upgrade vulnerable pins with compatibility matrix and targeted runtime tests).
3. Rate-limit parity PR for additional public write endpoints (including cancellation).
4. Frontend performance PR (route-level code splitting + bundle budget checks).
