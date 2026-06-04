# Texas Manufactured-Home Document Compliance — Research & Recommendations

**Prepared for:** Texas Home Outlet (THO) — Project Go Forward AI document system
**Date:** 2026-06-04
**Scope:** TDHCA + federal HUD compliance for the documents the system generates, record retention, e-signature validity (DocuSeal decision), and 2023–2026 legal changes.

> **Not legal advice.** This is a research synthesis from primary sources (Texas Occupations Code Ch. 1201, 10 TAC Ch. 80, Texas Bus. & Com. Code Ch. 322, 24 CFR Part 3280, ESIGN Act) to guide product decisions. Have THO's counsel confirm before relying on it for filings.

---

## Executive summary (the 6 things that matter)

1. **E-signatures are legally clean for these documents — adopt DocuSeal.** ESIGN + Texas UETA make e-signatures valid; UETA's exclusions (§322.003) do **not** touch manufactured-home sales, titling, or Ch. 9 liens; §1201.205 has **no "in ink" requirement** (verified against the statute); and TDHCA's own Form 1023 instructions say **"Do not notarize the application."** Two operational caveats below.
2. **The HUD label number is a *legally required* identifier on the Statement of Ownership** (§1201.205(4)). This **validates the "soft warning, don't hard-block" design we shipped** — keep nudging staff to enter it, because the *filed* SOO needs it.
3. **The 60-day SOO filing deadline has teeth right now.** Ownership doesn't legally vest until the SOO is filed, the late fee is up to $100, and **TDHCA began issuing Notices of Violation in May 2025 with a timely-filing enforcement initiative starting June 1, 2026.** This is a strong case for a deadline tracker in the CRM.
4. **The "Important Health Notice" (formaldehyde) is now a *Texas* requirement, not federal** — HUD removed the federal posting rule in 2020. Texas satisfies it via **Form 1038**, which also carries the consumer disclosure and Wind Zone I notice. Audit THO's standalone health-notice template against current Form 1038.
5. **Records: keep everything 6 years (10 TAC §80.30); electronic storage is explicitly allowed** and (as of SB 1341, 9/1/2025) no longer has to be physically in Texas. THO's cloud storage qualifies — just set a 6-year retention policy and include the Retail Monitoring Checklist.
6. **Two 2025 laws change the paperwork:** **SB 1341** (relaxed disclosure timing + simplified rescission, eff. 9/1/2025) and **SB 1940** (new transfer-on-death beneficiary designation that TDHCA must add to the Form 1023). Don't enforce the old 24-hour disclosure rule; watch for the revised 1023.

---

## 1. Statement of Ownership (TDHCA Form 1023) — the core filing

| Item | Requirement | Source |
|---|---|---|
| **Filing deadline** | **60 days** from date of sale. First retail sale → **retailer's** duty; subsequent sale → seller/transferor; relocation → owner. | Occ. Code **§1201.206(b),(c),(f)** ✅ verified |
| **"Date of sale" trigger** | Latest of: transfer/ownership-change date, date full price paid/funded, or final installation/delivery date (Form 1023 Block 4(d)). Interpretive TDHCA clarification — **not** a statutory change. | TDHCA MHD announcement; TMHA |
| **Late fee** | **At least $100** against the seller after the 61st day (mandated legend reads "up to $100"). | §1201.206(h) ✅ verified |
| **Ownership vesting** | **Ownership does not pass or vest until the SOO application is filed.** | §1201.206(e) ✅ verified |
| **Required identifiers** | "Identification number for each section/module" = **HUD label/Texas Seal number(s) + serial number(s).** | §1201.205(4); 10 TAC §80.2; TDHCA Form 1127 |
| **No HUD label** (pre-6/15/1976 or lost) | TDHCA issues a **Texas Seal**, **$35/section**. | TDHCA Forms 1127 / 1022 |
| **Issuance fee** | **$55** per Statement of Ownership (any issuing transaction). | TDHCA Form 1022 |
| **Other fees** | Notice of Installation **$75** first section / **$25** each additional; Texas Seal **$35**/section. | TDHCA Form 1022 |
| **Processing time** | TDHCA processes a complete application within **15 working days**. | §1201.207 |
| **Filing channels** | Paper (mail/fax/email) for everyone; **online SOO system is licensed-retailers-only, new homes only.** | TDHCA OS User Guide |
| **No notarization** | Form 1023 instructions: **"Do not notarize the application."** | TDHCA Form 1037 |
| **Signature execution** | **No "in ink" / wet-signature requirement in the statute.** | §1201.205 ✅ verified (no signature clause) |
| **Consequences of late/non-filing** | Buyer's ownership unperfected; **up to $100** fee; **repeated** late filing is a licensee disciplinary ground; salvage-label violations are a **Class B misdemeanor** (§1201.461). | §§1201.206, 1201.461 |

**Product implication:** The earlier decision to make HUD Label #1 a **soft, non-blocking warning** is the right call — it unblocks staff during generation, while the label remains legally required on the *filed* SOO. Recommendation: add a **pre-submission checklist** that flags missing HUD label/serial before a SOO is sent to TDHCA (distinct from the generation step).

---

## 2. Required warranties & disclosures (what each THO template must reflect)

| Document | Legal requirement | Period / timing | Source |
|---|---|---|---|
| **Manufacturer warranty** | Home/appliances free of defects (non-cosmetic); meets HUD standard | **1 year** from later of install or closing | §1201.351 |
| **Retailer warranty** (new home) | Proper installation + appliance install | **1 year**; warranty copies delivered **before contract**; installer-warranty copy **within 30 days of install** | §1201.352 |
| **Installation warranty** | Install meets all TDHCA standards; retailer responsible for new homes (joint & several with licensed installer) | **2 years** from later of purchase/install | §1201.361 |
| **Used-home habitability warranty** | Home habitable; written condition disclosure (≤2 pp.) | Habitable to **60th day**; consumer must give written notice by **65th day** | §1201.455 |
| **Consumer Disclosure Statement** | Board form, ≥12-pt type; ownership costs, financing, rescission right | **Before credit application** or before a non-financed agreement *(SB 1341 removed the old ">1 day before" rule, eff. 9/1/2025)* | §1201.162; **Form 1038** |
| **Formaldehyde / "Important Health Notice"** | **Texas** requirement (federal posting repealed 2020) | **Before** binding sales/retail-installment contract | §§1201.153–.154; **Form 1038** |
| **Wind Zone I notice** | Warn home not built for Wind Zone II/III; can't be placed in a TX Wind Zone II county | **Before** binding agreement | §1201.256 |
| **Right of rescission** | Conspicuous disclosure | **3 days** after signing *(SB 1341 simplified the mechanics, 9/1/2025)* | §1201.1521 |

**Product implications:**
- **Consolidation opportunity:** TDHCA **Form 1038** already bundles the Consumer Disclosure + Formaldehyde Notice + Wind Zone flag. THO's separate `State_ImportantHealth` template may be redundant with / should be reconciled against current Form 1038 (rev. 10/27/2025). Avoid outdated **federal** formaldehyde citations (former 24 CFR 3280.309 now governs *siding*).
- **Don't enforce the old 24-hour disclosure timing** anywhere in the workflow (SB 1341).
- Verify the warranty templates state the correct **1 / 1 / 2-year** and **60/65-day** periods.

---

## 3. Record retention

- **Retain all sale-file records for ≥ 6 years** — 10 TAC **§80.30**.
- **Electronic storage explicitly allowed** if producible to TDHCA on request (§80.30); SB 1341 (9/1/2025) removed the "must be in Texas" restriction → **THO's Firestore/GCS storage qualifies.**
- The file must include a completed **Retail Monitoring Checklist** plus all executed contracts, disclosures, warranties, financing/deposit docs, and SOO paperwork — 10 TAC **§80.32**.
- TDHCA may **inspect without advance notice** and subpoena records (§1201.608); noncompliance is **up to $10,000/violation** (§1201.605).

**Product implication:** Set an explicit **6-year retention policy** on generated PDFs in GCS (lifecycle rule), and make sure the Retail Monitoring Checklist is part of the stored deal file.

---

## 4. E-signatures & DocuSeal — recommendation: **GREEN LIGHT, with 3 guardrails**

**Why it's legally clean:**
- **Federal ESIGN** (15 U.S.C. §7001(a)) and **Texas UETA** (Bus. & Com. Code §322.007) make e-signatures/records valid and equivalent to wet ink.
- **UETA §322.003 exclusions** cover only wills/codicils/testamentary trusts and parts of the UCC (Ch. 2/2A) — **not** manufactured-home sales, titling, or **Ch. 9 liens.** Nothing excludes these documents.
- **§1201.205 has no "in ink" requirement** (verified); Form 1023 instructions say **"Do not notarize."**
- TDHCA itself accepts electronic submission via its online SOO system.

**Three guardrails to build into the signing flow:**
1. **ESIGN consumer e-consent step** (§7001(c)): before consumer-facing disclosures, capture affirmative consent + the paper-copy/withdrawal/hardware disclosures. DocuSeal should present this once at the start of the ceremony.
2. **Filing channel:** the online SOO system is **licensed-retailer-only** — so DocuSeal captures buyer/seller e-signatures on the sale docs + 1023, and **THO (the licensed retailer) files** the SOO. The 60-day clock still applies.
3. **Lien releases:** the core application needs no notary, but a **Form B lien-release affidavit may require notarization** (TDHCA sources conflict — confirm the specific pathway). Where notarization is needed, **Texas Remote Online Notarization** (Gov't Code Ch. 406) lets you do it electronically.

---

## 5. Recent changes to act on (2023–2026)

| Change | Effect on THO | Status / date |
|---|---|---|
| **SB 1341** (89th, 2025) | Disclosure due *before credit app / non-financed agreement* (old 24-hr rule gone); rescission simplified; **electronic + out-of-state recordkeeping allowed** | **Enacted, eff. 9/1/2025** |
| **SB 1940** (89th, 2025) | New optional **transfer-on-death beneficiary** on the SOO; **TDHCA must revise Form 1023** — beneficiary must file within 365 days of death | **Enacted, eff. 9/1/2025** — watch for updated 1023 |
| **HB 2706** (88th, 2023) | Retailer-license exemption for community bulk sales | Enacted, eff. 9/1/2023 |
| **10 TAC Ch. 80** updates | 7/14/2024 (exam admin + HB 2706 conformity); a **12/21/2025 package** (contents unverified — confirm) | Adopted |
| **Proposed §80.31/§80.32** (Oct 2025) | Clarifies no documents signed "in blank"; consumer copies | **Proposed** |

**Refuted / discard:** a supposed SB 1341 "public searchable website database" provision is **not** in the enrolled bill; the "in ink" SOO signature claim is **not** in the statute.

---

## Recommended next actions for THO (prioritized)

1. **Adopt DocuSeal** for e-signing sale packets + the 1023, with the ESIGN consent step and retailer-files-the-SOO flow. (Legally clear.)
2. **Build a 60-day SOO deadline tracker** in the CRM keyed to the Block 4(d) "date of sale," with filing-status + countdown — directly mitigates the active TDHCA enforcement initiative.
3. **Keep HUD label as a soft warning** (done) **and add a pre-filing checklist** that blocks *submission to TDHCA* (not generation) if HUD label/serial is missing.
4. **Reconcile `State_ImportantHealth`** against current **Form 1038**; remove outdated federal formaldehyde citations.
5. **Set a 6-year GCS retention policy** and include the Retail Monitoring Checklist in each deal file.
6. **Remove any old 24-hour disclosure-timing enforcement**; watch for TDHCA's **revised Form 1023** (SB 1940 beneficiary field) and the **12/21/2025** rule contents.
7. **Verify warranty periods** (1/1/2-yr; 60/65-day) in the warranty templates.

---

## Sources (primary-weighted)

**Statutes / rules:** Occ. Code Ch. 1201 (statutes.capitol.texas.gov/Docs/OC/htm/OC.1201.htm; §§1201.103, 1201.153–.154, 1201.162, 1201.205–.207, 1201.256, 1201.351–.352, 1201.361, 1201.451, 1201.455, 1201.461, 1201.605, 1201.608); 10 TAC Ch. 80 (§§80.30, 80.32; law.cornell.edu/regulations/texas); Bus. & Com. Code Ch. 322 (UETA; §§322.003, 322.007); ESIGN Act 15 U.S.C. §7001; 24 CFR 3280.305/.309.
**TDHCA:** Forms 1022 (fees), 1023 (SOO), 1037 (apply instructions), 1038 (Consumer Disclosure & Formaldehyde), 1127 (Label/Seal); Online SOO User Guide; SOO FAQ; MHD timely-filing enforcement announcement.
**Legislation:** SB 1341, SB 1940, SB 785 (capitol.texas.gov 89R billtext); HB 2706 (88R); Texas Register Ch. 80 adoptions (sos.state.tx.us/texreg).
**Industry corroboration:** Texas Manufactured Housing Association (texasmha.com) 89th-session summary; TRERC 88th-session report.

*Confidence: filing deadline, fee, vesting, retention period, e-sign validity, and warranty periods are High (verified against primary text). The 12/21/2025 rule contents and the exact Form-B notarization pathway are flagged for confirmation.*
