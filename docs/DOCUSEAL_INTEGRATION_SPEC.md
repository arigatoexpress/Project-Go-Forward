# DocuSeal E-Signature Integration — Spec

**Prepared for:** Texas Home Outlet (THO) — Project Go Forward
**Date:** 2026-06-04
**Companion doc:** `docs/TX_MH_COMPLIANCE_RESEARCH.md` (legal basis — e-sign is valid for these documents)

> **Decision this enables:** capture buyer/seller signatures on the closing packet (TMHA sales contract, deposit agreement, warranties, disclosures, and the TDHCA 1023) electronically instead of printing/wet-signing, then store the signed PDFs durably and file the SOO.

---

## 1. Why DocuSeal fits THO

- THO **already generates filled PDF/AcroForm documents** via `tools/document_engine_v2.py` + `config/field_map.json`. DocuSeal is built around **PDF templates with positioned fields** — so the existing generated PDFs become DocuSeal templates directly. No re-authoring of documents.
- **Legally sufficient:** DocuSeal e-signatures are compliant with **ESIGN, UETA, eIDAS**, which matches our compliance finding that these TX manufactured-home documents may be e-signed (no "in ink" requirement; Form 1023 says "do not notarize"). ([DocuSeal](https://www.docuseal.com/), [GitHub](https://github.com/docusealco/docuseal))
- **Open source + self-hostable**, with cloud storage (GCS) support — fits our Cloud Run + GCS stack and our 6-year retention obligation (10 TAC §80.30).
- **REST API + webhooks** → clean automation from the existing generate flow. ([Signing API](https://www.docuseal.com/signing-api))

## 2. Hosting & cost recommendation

**Recommendation: self-host DocuSeal as a separate Cloud Run service** (same project `tho-ai-agent`, region us-central1), backed by Postgres (Cloud SQL) and GCS.

| Option | Cost | Notes |
|---|---|---|
| **Self-host, free tier** | $0 | Unlimited documents + all core signing (PDF builder, multi-signer, signature verification, webhooks, GCS storage). Good for a pilot. |
| **Self-host, Pro** | **$240/yr/user + $0.20/signed doc** | Adds SMS identity verification for signers, signing reminders, SAML SSO, email branding, and **API/embedding for production use**. |
| **Cloud (SaaS)** | **$200/yr** (Pro) | No infra to run; data leaves our environment. |

([Pricing](https://www.docuseal.com/pricing), [On-premises](https://www.docuseal.com/on-premises))

**Why self-host:** keeps PII (buyer SSN-adjacent data, financials) inside THO's GCP project, integrates with existing GCS retention, and the per-document API fee is avoidable on the free tier for a pilot. Start free self-host; upgrade to Pro if **SMS signer identity verification** or **embedded signing for production** is needed.

**⚠️ AGPLv3 note (accurate scope):** DocuSeal is AGPLv3. Running it **unmodified** as a standalone service that THO's app calls over its API does **not** make THO's application a derivative work and does **not** require publishing THO's source. AGPL §13 only requires publishing **modifications to DocuSeal itself** if you change its code and expose it to network users. **Plan: deploy the official image unmodified; do all customization on THO's side via the API/branding settings.** If a fork ever becomes necessary, budget for publishing those DocuSeal-specific changes. ([Signbee comparison](https://signb.ee/blog/open-source-e-signature-api-comparison))

## 3. How it plugs into the existing flow

```
Deal (CRM)  ──►  generate-batch (existing)  ──►  filled PDFs in GCS
                                                      │
                                                      ▼
                              POST /api/signatures/send  (NEW)
                                 → create DocuSeal submission from the
                                   merged packet, with submitters =
                                   buyer(s) + THO rep, fields pre-filled
                                                      │
                                                      ▼
                              DocuSeal hosts the signing ceremony
                              (email link or embedded iframe in CRM)
                                                      │
                       signer completes  ──►  DocuSeal webhook  ──►
                              POST /api/signatures/webhook (NEW)
                                 → download signed PDF → store in GCS
                                   (6-yr retention) → update deal status
                                                      │
                                                      ▼
                              THO (licensed retailer) files the SOO with
                              TDHCA (online retailer system / mail) within
                              60 days — signatures already captured.
```

**Key design choices:**
- **Reuse generated PDFs.** The merged packet (or per-document PDFs) from `generate_batch` are uploaded to DocuSeal as the submission's documents. Signature/initial/date fields are positioned per template (one-time setup per template, stored as DocuSeal template IDs mapped alongside `field_map.json`).
- **Pre-fill, don't re-collect.** DocuSeal "fields" for data we already have (names, addresses, price) are pre-populated from deal data; the signer only adds signatures/initials/dates.
- **Async via webhook** — never block the request waiting for a human to sign.

## 4. Data model changes (`database/models.py`)

Add to `Deal` (and Firestore):
```
signature_status: "not_sent" | "sent" | "viewed" | "partially_signed" | "completed" | "declined" | "expired"
docuseal_submission_id: str | None
docuseal_submitters: [{role, email, status, signed_at}]
signed_document_urls: [gcs_url]          # final signed PDFs
signature_audit_url: gcs_url | None      # DocuSeal completion certificate / audit log
signature_sent_at / signature_completed_at: timestamps
```

## 5. New API endpoints (`main.py`, above the SPA catch-all)

| Endpoint | Purpose |
|---|---|
| `POST /api/signatures/send` | Create a DocuSeal submission from a deal's generated packet; returns signing URL(s). Admin-protected. |
| `GET /api/signatures/{deal_id}/status` | Current signing status for the CRM UI. |
| `POST /api/signatures/webhook` | DocuSeal webhook receiver (HMAC-verified): on `submission.completed`, fetch signed PDF + audit cert, store in GCS, update deal. **No auth cookie — verify webhook signature instead.** |
| `POST /api/signatures/{deal_id}/resend` | Re-send/remind. |
| `POST /api/signatures/{deal_id}/void` | Cancel an outstanding request. |

New module: `signature_service.py` (DocuSeal API client, mirrors the pattern of `email_service.py`). Config (base URL, API token, webhook secret) via env/secrets, never hardcoded.

## 6. The ESIGN consent step (compliance-required)

Before the consumer signs disclosures, present the **ESIGN §7001(c) consumer consent**: affirmative consent to do business electronically, right to a paper copy, right to withdraw, and hardware/software requirements. Implement as the **first document/step in the DocuSeal submission** (a short consent page the signer accepts) so consent is captured in the same audit trail. This is a hard requirement for consumer-facing disclosures — see compliance doc §4.

## 7. The SOO / "retailer files it" nuance

DocuSeal captures **buyer + seller signatures** on the 1023 and the sale packet. **THO (the licensed retailer) still files the SOO** with TDHCA — the online SOO system is retailer-only and the 60-day clock applies. The signed 1023 PDF (with the retailer's portal submission) satisfies the signature capture; **no notarization** is needed for the application itself. A **lien-release affidavit (Form B)** may need notarization → use **Texas Remote Online Notarization** (separate flow, out of DocuSeal scope or via a RON-capable add-on).

## 8. Security, PII, retention

- **PII:** strip/avoid sending SSN/financial-account fields to DocuSeal unless a document requires them; reuse `tools/pii_guard.py` patterns. Self-hosting keeps data in-project.
- **Webhook auth:** verify DocuSeal's signature/HMAC on every webhook; do not trust payloads blindly.
- **Retention:** store signed PDFs + the DocuSeal **completion certificate / audit trail** in GCS under the deal, with the **6-year lifecycle** policy (10 TAC §80.30). The audit certificate is the evidence of a valid e-signature.
- **Access:** signing links are capability URLs — set expirations and use per-submitter links.

## 9. Rollout phases

1. **Pilot (free self-host):** deploy DocuSeal service; manually template the 3–4 highest-volume documents (sales contract, deposit agreement, 1023, consumer disclosure/Form 1038); send via API; webhook → GCS. Validate one real deal end-to-end.
2. **CRM integration:** "Send for signature" button on the deal; status badges; embedded signing option.
3. **Templatize the full packet** + ESIGN consent step; map DocuSeal template IDs alongside `field_map.json`.
4. **Decide Pro** if SMS signer ID-verification or production embedding is required.

## 10. Open decisions for THO

- **Self-host vs SaaS** (recommend self-host free tier to pilot).
- **Signer identity verification** level — email link only, or SMS/KBA (drives Pro upgrade).
- **Email vs embedded** signing in the CRM.
- Whether to bring **lien releases** into scope (adds RON/notary complexity).

---

### Sources
- DocuSeal — https://www.docuseal.com/ · Pricing — https://www.docuseal.com/pricing · On-premises — https://www.docuseal.com/on-premises · Signing API — https://www.docuseal.com/signing-api
- GitHub (docusealco/docuseal) — https://github.com/docusealco/docuseal
- Open-source e-sign API comparison — https://signb.ee/blog/open-source-e-signature-api-comparison
- Legal basis: `docs/TX_MH_COMPLIANCE_RESEARCH.md` (ESIGN/UETA validity; no "in ink"/notary barrier for the 1023)

*DocuSeal pricing/licensing facts as of June 2026 web check; confirm current pricing at signup.*
