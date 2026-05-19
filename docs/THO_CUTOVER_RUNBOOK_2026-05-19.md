# THO texashomeoutlet.com Cutover Runbook

**Prepared:** 2026-05-19
**App production URL:** https://tho.sapphirealpha.xyz
**Legacy/current site:** https://www.texashomeoutlet.com
**Archive evidence:** `data/legacy_site/cutover_archive_20260519T211349Z/`

This runbook is intentionally production-safe. It does not authorize DNS,
email, registrar, provider-account, Firestore, or GCS changes by itself. Use it
to verify readiness, perform the approved website cutover, and roll back if the
client asks to pause.

## 1. Confirmed State

### Repo/App

- THO production app is live at `https://tho.sapphirealpha.xyz`.
- Latest read-only production liveness check returned:
  - `/health`: `{"status":"ok"}`
  - `/healthz/`: `{"status":"ok","version":"d60811d..."}`
- Document Center repo fixes in this branch:
  - seller legal name defaults to `Prosperity Acquisitions INC. dba Texas Home Outlet`;
  - legacy seller names are normalized before PDF generation;
  - Standard Closing and Used Home Closing packets include the cover page;
  - Standard Closing and Used Home Closing packets include `approval.pdf`;
  - Standard Closing and Used Home Closing packets include `Internal_Proof_of_Insurance.pdf`.
- Local packet smoke generated a merged Standard Closing packet with 11
  documents and 19 pages, including approval and proof-of-insurance text.

### Legacy Site Archive

- Archive directory: `data/legacy_site/cutover_archive_20260519T211349Z/`
- Public inventory scrape succeeded:
  - total listings: 19;
  - new homes: 13;
  - pre-owned homes: 6;
  - photo-ready listings: 19;
  - detail/quote URL manifest: 38 URLs.
- Raw public snapshots captured:
  - `homepage.html`
  - `inventory_page_1.html`
  - `inventory_page_2.html`
  - `robots.txt`
  - `sitemap_index.xml`
- One legacy detail page was slow during crawling; the final manifest still
  completed and includes the listing URL for later reconciliation.

### Current DNS/HTTP Observed

Read-only checks on 2026-05-19:

```bash
texashomeoutlet.com A      52.10.0.211
www.texashomeoutlet.com A  52.10.0.211
texashomeoutlet.com MX     mx-biz.mail.am0.yahoodns.net
texashomeoutlet.com NS     ns-1292.awsdns-33.org
texashomeoutlet.com NS     ns-551.awsdns-04.net
texashomeoutlet.com NS     ns-1824.awsdns-36.co.uk
texashomeoutlet.com NS     ns-26.awsdns-03.com
```

- `https://www.texashomeoutlet.com/` returns HTTP 200 from the current legacy
  WordPress/nginx site.
- `https://texashomeoutlet.com/` redirects to `https://www.texashomeoutlet.com/`.
- Preserve MX records during website cutover unless there is a separate written
  email migration approval.

### Provider/Client Handoff Still Needed

The provider has said they are ready to take the old site down when THO is
ready, but the following items are still outstanding or must be confirmed:

- lead export delivery from the legacy provider;
- final billing statement/closure confirmation;
- Instagram `@txhomeoutlet` credential handoff and post-login reset;
- Facebook admin transfer confirmation;
- THO final approval to point `texashomeoutlet.com` traffic at the new app;
- client confirmation that `approval.pdf` is the intended "21st" form, or the
  exact missing template name/file if it is not.

## 2. Pre-Cutover Verification

Run these before asking for DNS changes:

```bash
export THO_PROD_URL="https://tho.sapphirealpha.xyz"

curl -fsS "$THO_PROD_URL/health" | python3 -m json.tool
curl -fsS "$THO_PROD_URL/healthz/" | python3 -m json.tool
curl --max-time 45 -fsS "$THO_PROD_URL/api/marketing/inventory-context?limit=5" >/tmp/tho_inventory_context_5.json
python3 scripts/production_smoke.py --base-url "$THO_PROD_URL"
```

For authenticated Document Center readiness, verify through the admin UI after
deploying this branch:

1. Open `https://tho.sapphirealpha.xyz/documents`.
2. Authenticate with the current admin PIN.
3. Create/select a safe test deal.
4. Generate a Standard Closing packet.
5. Confirm the packet downloads and includes:
   - cover page;
   - TMHA Sales Contract;
   - TDHCA forms;
   - approval form;
   - proof-of-insurance form;
   - seller legal name: `Prosperity Acquisitions INC. dba Texas Home Outlet`.
6. Delete or clearly label any test-only generated artifacts according to the
   team's production-data policy.

## 3. DNS Cutover Plan

Do not guess final DNS records. Generate them from the approved Cloud Run custom
domain mapping or load balancer configuration at cutover time.

Website-only cutover rules:

- keep existing MX records unchanged;
- keep email provider/Turbify/Yahoo mail records unchanged;
- preserve the existing `52.10.0.211` records for rollback notes;
- lower TTL in advance if the DNS host allows it;
- add or verify a Cloud Run custom-domain mapping for `texashomeoutlet.com` and
  `www.texashomeoutlet.com`, or use the approved load balancer record set;
- apply only the Google-provided A/AAAA/CNAME records for web traffic;
- verify both apex and `www` after propagation.

Expected post-cutover behavior:

- `https://texashomeoutlet.com/` loads or redirects to the new THO app.
- `https://www.texashomeoutlet.com/` loads the new THO app.
- `https://texashomeoutlet.com/documents` reaches Document Center after admin
  auth.
- MX remains pointed at the current mail provider until a separate email
  migration is approved.

## 4. Post-Cutover Smoke

```bash
export THO_PUBLIC_APEX="https://texashomeoutlet.com"
export THO_PUBLIC_WWW="https://www.texashomeoutlet.com"

curl -I "$THO_PUBLIC_APEX/"
curl -I "$THO_PUBLIC_WWW/"
curl -fsS "$THO_PUBLIC_WWW/health" | python3 -m json.tool
curl -fsS "$THO_PUBLIC_WWW/healthz/" | python3 -m json.tool
curl --max-time 45 -fsS "$THO_PUBLIC_WWW/api/marketing/inventory-context?limit=5" >/tmp/tho_public_inventory_context_5.json
```

Then verify in a browser:

- homepage renders without legacy provider branding;
- inventory/search pages load homes and images;
- Document Center admin login works;
- safe packet generation/download works;
- contact/appointment forms show the expected THO branding and do not throw
  browser console errors.

## 5. Rollback

Rollback is web-only unless a separate email migration was approved.

If the client asks to pause or the new site fails after DNS cutover:

1. Restore the previous web records:
   - apex A: `52.10.0.211`
   - `www` A: `52.10.0.211`
2. Leave MX records unchanged.
3. Re-run:

```bash
dig +short texashomeoutlet.com A
dig +short www.texashomeoutlet.com A
curl -I https://www.texashomeoutlet.com/
```

4. Tell the provider not to take the old site down until the rollback is
   confirmed.

Cloud Run rollback for the app itself is documented in
`docs/PRODUCTION_READINESS.md`. Use Cloud Run traffic rollback only if the THO
app revision is the failure source.

## 6. Final Approval Message Checklist

Before the provider takes down the old site, collect written confirmation of:

- THO approval to proceed with website DNS cutover;
- no email/MX migration in this cutover;
- provider lead export delivered and stored;
- provider final billing statement delivered or explicitly pending;
- Instagram and Facebook transfers completed or assigned to a named owner;
- Document Center production packet smoke passed after this branch is deployed;
- archive path recorded in the launch ticket/handoff.
