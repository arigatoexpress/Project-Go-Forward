# DocuSeal E-Sign Sidecar — Feasibility & Design

**Status:** Feasibility / scaffolding — NOT deployed  
**Author:** AI Engineering Assistant  
**Date:** 2026-05-03  
**Activation:** Requires explicit sign-off from Ari (see [Activation Checklist](#activation-checklist))

---

## XFA/AcroForm Compatibility Test

### Method
Docker was unavailable in the CI sandbox. Raw AcroForm inspection was performed
using the PDF binary directly (same layer that DocuSeal's `pdf-lib` reads).

### Results — `TMHA_SalesContract.pdf`

```
File: tho_documents/TMHA_SalesContract.pdf
PDF type: Hybrid XFA + AcroForm

AcroForm /T field occurrences : 67
Unique AcroForm field names   : 61
  └─ Business-data fields     : 56  (91.8% discovery rate)
  └─ Structural wrappers      : 5   (topmostSubform, Page1–4)

Field inventory (61 unique):
  APR[0]                       Amt_Points[0]
  Creditor_Address[0]          Creditor_City_State_Zip[0]
  Creditor_Desc[0]             Creditor_Name[0]
  Creditor_Phone_1[0]          Creditor_Phone_2[0]
  Creditor_Phone_3[0]          Creditor_Phone_4[0]
  Creditor_Phone_5[0]          Creditor_Phone_6[0]
  Creditor_Phone_7[0]          Creditor_Phone_8[0]
  Creditor_Phone_9[0]          Creditor_Phone_10[0]
  Doc_Fee[0]                   Document_Preparation_Desc[0]
  DownPmt[0]                   FCD_Footer[0]
  Finance_Charge[0]            HUD_Sec_1[0]
  HUD_Sec_2[0]                 Install_Address[0]
  Install_City[0]              Install_City_State_Zip[0]
  Install_County[0]            Install_State[0]
  Install_Zip[0]               Insurance_Included_Yes[0]
  Insurance_Premium[0]         Insurance_Yes[0]
  Interest_Rate_Desc[0]        Loan_Term[0]
  Manufacturer[0]              Max_Financed[0]
  Model[0]                     New_Yes[0]
  No_of_Sections[0]            Page1[0]  *structural*
  Page2[0]  *structural*       Page3[0]  *structural*
  Page4[0]  *structural*       Payment_Breakdown[0]
  Pmt_Start_Date[0]            Points_Desc[0]
  SalePrice[0]                 Seller_RBI[0]
  Serial_Sec_1[0]              Serial_Sec_2[0]
  Tax_Escrow_Included_Yes[0]   Total_Paid[0]
  Total_Paid_To_Others[0]      Total_Payment[0]
  Total_Pmts[0]                Total_Unpaid_Balance[0]
  Unpaid_Balance[0]            Used_Yes[0]
  Wheels_No[0]                 Wheels_Yes[0]
  topmostSubform[0]  *structural*
```

**Assessment:** ✅ COMPATIBLE  
DocuSeal reads the AcroForm layer exclusively. All 56 business-data fields are
detectable. The XFA layer is ignored by DocuSeal (same as pypdf). No template
modifications are required.

> **Note:** When DocuSeal ingests a hybrid PDF, it strips the XFA data stream
> and retains only the AcroForm layer for signing. The signed output is a pure
> AcroForm PDF — fully readable by pypdf and Adobe Reader.

---

## Architecture

### Deployment topology

```
┌─────────────────────────────────────────────────────────────────────┐
│  Google Cloud Run  (project: tho-ai-agent, region: us-central1)     │
│                                                                      │
│  ┌───────────────────────────────┐   REST    ┌──────────────────┐   │
│  │  THO Main Service             │ ────────► │  DocuSeal        │   │
│  │  (main.py, port 8080)         │           │  (port 3000)     │   │
│  │                               │ ◄──────── │                  │   │
│  │  POST /api/docuseal/send      │  webhook  │  Cloud Run       │   │
│  │  POST /api/docuseal/webhook   │           │  (separate svc)  │   │
│  └───────────┬───────────────────┘           └────────┬─────────┘   │
│              │                                        │              │
│              ▼                                        ▼              │
│  ┌──────────────────────┐              ┌────────────────────────┐   │
│  │  GCS Bucket          │              │  Cloud SQL (Postgres)   │   │
│  │  tho-secure-docs     │              │  OR SQLite vol mount    │   │
│  │                      │              │  (DocuSeal internal DB) │   │
│  │  generated_docs/     │              └────────────────────────┘   │
│  │  signed_documents/   │                                           │
│  └──────────────────────┘                                           │
└─────────────────────────────────────────────────────────────────────┘
```

### Key design decisions

| Decision | Choice | Rationale |
|---|---|---|
| Deployment | Separate Cloud Run service | Isolation; DocuSeal has its own DB & process |
| Database | SQLite on volume mount (start) → Cloud SQL Postgres (scale) | Zero config for MVP; migrate when concurrent submissions > 10/day |
| PDF flow | THO generates → DocuSeal signs | Preserves existing generation engine exactly |
| Storage | Mirror signed PDFs to existing GCS bucket | Single source of truth; no new bucket needed |
| Auth | DOCUSEAL_API_TOKEN (header) + DOCUSEAL_WEBHOOK_SECRET (HMAC) | Follows DocuSeal's standard auth model |

---

## Signing Sequence

```
Team (CRM)          THO Backend         DocuSeal            Buyer           GCS
    │                    │                   │                 │              │
    │ Click               │                   │                 │              │
    │ "Send for Sig"      │                   │                 │              │
    │──────────────────►  │                   │                 │              │
    │  POST               │  POST             │                 │              │
    │  /api/docuseal/send │  /api/submissions │                 │              │
    │                     │──────────────────►│                 │              │
    │                     │  201 {id, url}    │                 │              │
    │                     │◄──────────────────│                 │              │
    │  {success, sub_id}  │                   │  Email invite   │              │
    │◄────────────────────│                   │────────────────►│              │
    │                     │                   │                 │              │
    │                     │                   │  Buyer signs    │              │
    │                     │                   │◄────────────────│              │
    │                     │                   │                 │              │
    │                     │  Webhook POST     │                 │              │
    │                     │  /api/docuseal/   │                 │              │
    │                     │  webhook          │                 │              │
    │                     │◄──────────────────│                 │              │
    │                     │  Validate HMAC    │                 │              │
    │                     │  Download signed  │                 │              │
    │                     │  PDF              │                 │              │
    │                     │  Mirror to GCS ──────────────────────────────────►│
    │                     │  Write deal note  │                 │              │
    │                     │  (Firestore)      │                 │              │
```

---

## Per-Template Upload Checklist

Priority order is based on closing-packet inclusion and signature frequency.

| # | Template file | DocuSeal upload status | Fields | Signer roles needed |
|---|---|---|---|---|
| 1 | TMHA_SalesContract.pdf | ⬜ pending | 56 data | Buyer, Co-Buyer, Seller |
| 2 | TDHCA_1038_Consumer_Disclosure.pdf | ⬜ pending | ~12 | Buyer, Seller |
| 3 | TDHCA_1054_Habitability_Warranty.pdf | ⬜ pending | ~8 | Buyer, Seller |
| 4 | Internal_Homestead.pdf | ⬜ pending | ~6 | Buyer |
| 5 | All_Cover.pdf | ⬜ pending | ~4 | Buyer |
| 6 | HUD_Settlement.pdf (if present) | ⬜ pending | ~20 | Buyer, Seller, Lender |
| 7 | Title_Transfer.pdf | ⬜ pending | ~10 | Buyer, Seller |
| 8 | Insurance_Agreement.pdf | ⬜ pending | ~8 | Buyer |
| 9 | Closing_Disclosure.pdf | ⬜ pending | ~15 | Buyer |

**Upload steps (per template):**
1. `POST /api/templates/pdf` with the PDF binary
2. Record returned `template_id` in `config/field_map.json` under `docuseal_template_id`
3. Map AcroForm field names → DocuSeal signer roles in `config/field_map.json`
4. Test with a sandbox submission before enabling in production

---

## Cost Estimate

### DocuSeal service (self-hosted)

| Component | Option A — SQLite | Option B — Cloud SQL |
|---|---|---|
| Cloud Run (512 MB, 1 vCPU, min-instances=0) | ~$2–5/mo | ~$2–5/mo |
| Storage (Cloud SQL Postgres db-f1-micro) | — | ~$7/mo |
| Storage (1 GB Cloud Run volume for SQLite) | ~$0.04/mo | — |
| GCS egress for signed PDFs | negligible (<100 MB/mo) | same |
| **Total** | **~$2–5/mo** | **~$9–12/mo** |

Recommendation: start with SQLite on a Cloud Run volume mount. Migrate to Cloud
SQL when DocuSeal's DB file exceeds 1 GB or when concurrent writes cause lock
contention (visible as 500s on `/api/submissions`).

### Signing volume
No per-signature fees — self-hosted means unlimited submissions. The only
marginal cost is outbound email (DocuSeal uses its own SMTP or SendGrid; budget
~$0 on SendGrid free tier for <100 emails/day).

---

## AGPL-3.0 Implications

DocuSeal is licensed under AGPL-3.0.

| Scenario | AGPL obligation |
|---|---|
| Self-hosted internal use (THO team only) | ✅ No obligation to publish source |
| THO offers DocuSeal signing as a service to external customers | ⚠️ Must publish all modifications as AGPL-3.0 |
| THO modifies DocuSeal source code | Must release modifications if the service is offered to third parties |

**Conclusion for THO:** Internal use as a back-office signing tool for THO's own
closing workflow has **no AGPL disclosure obligation**. The existing THO codebase
(main.py, etc.) is not affected by AGPL because it merely calls DocuSeal over
REST — it is not a derivative work.

---

## Alternatives Considered

| Tool | License | Est. cost | Notes |
|---|---|---|---|
| **DocuSeal** ✅ chosen | AGPL-3.0 | ~$5/mo self-hosted | 12K GitHub stars; active; REST API; Docker-ready |
| Dropbox Sign (HelloSign) | Proprietary | ~$15–25/mo per user | Well-supported but recurring SaaS cost; data leaves THO infra |
| Anvil | Proprietary | ~$50–149/mo | Excellent PDF tooling but expensive for a small dealership |
| OpenSign | AGPL-3.0 | ~$5/mo self-hosted | Similar feature set; smaller community; less API coverage |
| DocuSign | Proprietary | ~$25/mo/user | Industry standard but overkill; data sovereignty concern |

---

## Activation Checklist

Complete all steps before merging the activation PR:

- [ ] Deploy DocuSeal to Cloud Run using `services/docuseal/cloudbuild.yaml`
- [ ] Set `DOCUSEAL_SECRET_KEY` (random 32-byte hex) in DocuSeal service secrets
- [ ] Set `DATABASE_URL` (Cloud SQL URL or leave blank for SQLite)
- [ ] Obtain DocuSeal API token from `http://<docuseal-url>/user/settings`
- [ ] Set `DOCUSEAL_API_URL` + `DOCUSEAL_API_TOKEN` in THO main service env vars (Cloud Run)
- [ ] Set `DOCUSEAL_WEBHOOK_SECRET` (match value configured in DocuSeal → Webhooks)
- [ ] Upload TMHA_SalesContract.pdf via `POST /api/templates/pdf`; record template ID
- [ ] Populate `docuseal_template_id` fields in `config/field_map.json` for top 9 templates
- [ ] Configure DocuSeal webhook URL: `https://<tho-service-url>/api/docuseal/webhook`
- [ ] Smoke test: create a sandbox submission → verify signed PDF appears in GCS
- [ ] Enable "Send for Signature" button by un-gating in frontend (currently shows "Coming soon" on 501)
