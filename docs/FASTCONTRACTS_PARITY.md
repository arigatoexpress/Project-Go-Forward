# FastContracts → THO Document Center: Capability Parity

**Purpose:** Confirm the THO Document Center does everything the client relies
on FastContracts for, so the FastContracts subscription can be cancelled with
confidence.

**Verified:** 2026-06-04. Contract engine tests pass (41 focused tests green);
production Document Center is live behind the admin login at
https://tho.sapphirealpha.xyz.

## What FastContracts does (and how THO covers it)

| FastContracts capability | THO Document Center | Status |
|--------------------------|---------------------|--------|
| Texas manufactured-home contract forms (TMHA/TDHCA) | 63 mapped templates incl. TMHA Sales Contract, all core TDHCA 10xx forms, State + internal disclosures | ✅ Covered |
| Pre-built closing packets | 5 packets: Standard New (9), Used (11), Full New (54), Full Used (56), Credit App (4) | ✅ Covered |
| Auto-fill buyer/home/financial data | 4-step wizard + deal-based generation; data entered once, reused across every form | ✅ Covered |
| Correct seller/legal entity on every doc | Hardcoded to **Prosperity Acquisitions, Inc. dba Texas Home Outlet**, RBI 35248 | ✅ Covered |
| Merge into one closing packet PDF | `/api/documents/generate-packet` + per-deal packet; tested for no blank/partial pages | ✅ Covered |
| Pull home details from inventory | Live Firestore inventory feeds the wizard | ✅ Covered (FastContracts can't do this) |
| Generate straight from a CRM deal | `/api/deals/{id}/generate-document` and `/generate-packet` | ✅ Covered (FastContracts can't do this) |
| Trade-in valuation for used homes | Built-in trade-in calculator applied to down payment | ✅ Covered (extra) |
| E-signature / send for signing | DocuSeal integration (`/api/docuseal/send` + webhook) | ⚙️ Built, **needs operator config** (see below) |

## The one thing to confirm before cancelling: e-signature

This is the only FastContracts feature that isn't necessarily "on" yet. The
**DocuSeal e-signature integration is built**, but it's gated behind three
environment variables that an operator must set in Cloud Run Secret Manager:

- `DOCUSEAL_API_URL`
- `DOCUSEAL_API_TOKEN`
- `DOCUSEAL_WEBHOOK_SECRET`

Plus a DocuSeal template mapping at `config/docuseal_templates.json` (generated
by `tools/docuseal_template_uploader.py`).

**Decision point for the client:**

- If FastContracts was used **only to generate/fill the contract PDFs**, the THO
  Document Center is a complete drop-in replacement **today** — generate and
  download, then sign in person or via whatever signing tool THO already uses.
- If FastContracts was also used to **send documents out for e-signature**, wire
  up DocuSeal (a few hours of setup + a DocuSeal account) before cancelling, so
  there's no gap in the e-sign workflow.

## How to verify it yourself (read-only)

```bash
# Live health + deployed commit
curl -fsS https://tho.sapphirealpha.xyz/healthz/

# Full production smoke (public routes + auth gating)
python3 scripts/production_smoke.py --base-url https://tho.sapphirealpha.xyz

# Contract engine tests
python -m pytest tests/test_packet_no_blank_pages.py \
                 tests/test_doccenter_autofill.py \
                 tests/test_pdf_packet_qa.py -q
```

Then, logged in as admin on the live site: **Documents → run the 4-step wizard →
generate the Full New Home Closing packet → download** and eyeball the filled
PDF against a known-good FastContracts output.

## Bottom line

For document generation and packets, THO is at parity with — and beyond —
FastContracts (it also pulls live inventory and generates from CRM deals). The
only open item is turning on DocuSeal e-signature if the client needs send-for-
signature in the app. Everything else is ready to take over from FastContracts
now.

---

*Last updated 2026-06-04.*
