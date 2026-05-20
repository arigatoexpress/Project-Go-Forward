# THO Enterprise Readiness Audit

Date: 2026-05-20

Production surface: `https://tho.sapphirealpha.xyz`

This audit records the verified state after reconciling the client-reported Document Center issues and the Gemini handoff claims against the live repo and production app.

## Status

GO after external handoff only.

The application-side Document Center and catalog paths are verified for continued client testing and final approval. Official cutover still depends on provider/DNS/social/billing handoff items and THO go-live approval.

## Client Issues Checked

| Issue | Verified Outcome |
| --- | --- |
| Documents must say Texas Home Outlet, Inc. | Covered by local document quality tests and deployed Document Center defaults. |
| Incomplete customer/deal data generated poor packets | Backend now rejects incomplete generation with `missing_required_fields`; frontend shows missing-field guidance before generation. |
| Boss got stuck on Phase 3 without feedback | Production browser test shows Phase 3 displays `Deal data needs attention`, lists the missing field, and disables generation. |
| Salespeople need to know what to enter manually | Staff guide now explains missing-field workflow and manual entry fields. |
| Public site must show current inventory plus orderable floorplans | Production inventory context and browser UI show available/pre-owned/orderable categories. |
| Client needs editing/new inventory guidance | Staff guide documents current inventory/photo/floorplan operating process without claiming nonexistent UI fields. |

## Verified Production Evidence

Production health:

- `/health` returned `status=ok`.
- `/healthz/` returned deployed version `a51a6e3b9a6c2606047e6ee0f8a2e79439d1b8e6`.

Production smoke:

- `python3 scripts/production_smoke.py` returned `ok=true` with 32/32 probes.
- Authenticated empty-document rejection returned `ok=true` with 33/33 probes.
- Empty document generation returned status `400`, error `missing_required_fields`, and no download URL.

Document Center production write smoke:

- Admin auth succeeded.
- Document readiness reported ready.
- Templates: 63.
- Packets: 5.
- Generated document count visible in readiness/history.
- Single-document generation succeeded.
- Packet generation succeeded with 8 included documents and 16 pages.
- Downloaded PDFs were nonzero and started with `%PDF-`.
- Customer save/search worked without raw SSN exposure.

Production frontend browser test:

- Inventory labels include Available Now, Orderable, and Pre-Owned.
- Inventory filters opened successfully.
- Desktop inventory had no horizontal overflow.
- Visible inventory images were not broken.
- Admin auth established a frontend session.
- Partial deal at Phase 3 showed `Deal data needs attention`.
- Partial deal listed `Sales price is required and must be greater than $0 for TMHA Sales Contract`.
- Partial deal kept Generate disabled.
- Completed synthetic deal showed `Deal data ready for selected documents`.
- Completed synthetic deal enabled generation.
- Browser-generated packet succeeded with 8 of 8 documents and 16 pages.
- Browser-downloaded merged packet was a valid PDF, 830,460 bytes.
- Mobile inventory had no horizontal overflow.
- No severe console/page errors occurred during the browser flow.

Browser artifacts were written under `/tmp/tho-frontend-e2e/` during verification.

## Local Verification Evidence

Focused Document Center regression suite:

- `73 passed, 203 warnings`

Full backend suite in the repo Python 3.11 dependency environment:

- `593 passed, 3 skipped, 384 warnings`

Frontend:

- `npm --prefix frontend run build` completed successfully.
- `npm --prefix frontend run lint -- --quiet src/pages/DocumentCenter.jsx` completed successfully.

Pre-commit on new docs:

- `check for added large files` passed.
- `Detect secrets` passed.

## Corrections Made To Gemini Artifacts

The handoff docs created by Gemini were not accepted as-is. They were corrected because they contained unsafe or inaccurate claims:

- Removed embedded admin PIN examples.
- Removed the unverified `admin-token` Secret Manager assumption.
- Removed Namecheap/registrar assumptions and replaced them with the current Route 53 export requirement.
- Removed unverified AES-256/customer-storage claims.
- Removed unverified Inventory Editor/Matterport field claims.
- Reframed Cloud Storage/download wording to match the verified app behavior.
- Added explicit external blocker separation.

## Remaining Blockers

Provider:

- Lead export pending.
- Final billing statement pending.
- Instagram `@txhomeoutlet` credential/reset handoff pending.
- Facebook admin transfer to Celeste pending.
- Old site takedown should wait for THO approval.

DNS:

- Full AWS Route 53 hosted-zone export is still required before official cutover.
- Mail and verification records must be preserved.

Client approval:

- THO leadership still needs to approve the final go-live window.

## Operator Notes

Do not use fake placeholder values to force packets. If the app flags missing data, enter the real value or pause the packet.

Use `docs/THO_STAFF_LAUNCH_GUIDE.md` for staff guidance and `docs/LAUNCH_RUNBOOK.md` for cutover/rollback.
