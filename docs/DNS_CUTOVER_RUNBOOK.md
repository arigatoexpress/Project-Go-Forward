# DNS Cutover & Post-Launch Runbook

> **Status:** DRAFT — awaiting human review and execution.  
> **Do NOT execute any steps marked 🔒 without explicit Ari approval.**  
> **Fenced actions:** DNS changes, secret deployment, Search Console verification, and email cutover are all human-gated.

---

## Phase 1 — Search Console Verification (Ari executes, Mark prepares DNS)

**Goal:** Establish Google Search Console domain property for `texashomeoutlet.com` before the DNS flip so post-launch data starts immediately.

| Step | Owner | Action | Notes |
|------|-------|--------|-------|
| 1.1 | Ari | Open [Google Search Console](https://search.google.com/search-console) → **Add property** → **Domain** → enter `texashomeoutlet.com` | Domain property (not URL prefix) covers all subdomains |
| 1.2 | Ari | Copy the `google-site-verification=…` TXT value | It looks like `google-site-verification=abc123…` |
| 1.3 | Ari | Hand the TXT value to Mark (email/Slack/text) | Mark will add it to Route 53 |
| 1.4 | Mark | In Route 53, add TXT record: `texashomeoutlet.com` (apex) → `"google-site-verification=<value>`" | TTL = 300 seconds. Do NOT change NS or MX. |
| 1.5 | Mark | Confirm the TXT is live: `dig TXT texashomeoutlet.com` | Should return the verification string within ~5 min |
| 1.6 | Ari | Click **Verify** in Search Console | Verification should succeed immediately once DNS propagates |

**Rollback:** If verification fails, remove the TXT record from Route 53. No other changes.

---

## Phase 2 — THE DNS FLIP (🔒 HUMAN-GATED — Ari says go)

**Goal:** Point `texashomeoutlet.com` and `www.texashomeoutlet.com` to the new Google Cloud Run site.

**Preconditions before flipping:**
- [ ] Search Console verification complete (Phase 1)
- [ ] PR #143 (`go-live-hardening-20260612`) is merged to `main` and deployed to Cloud Run
- [ ] `https://tho.sapphirealpha.xyz/` serves the new site with valid TLS certificate
- [ ] `https://www.texashomeoutlet.com` (future URL) — the Cloud Run domain mapping is ready and the certificate is provisioned
- [ ] All 301 redirects are tested (legacy detail URLs, vendor pages, quote URLs)
- [ ] SEO smoke tests pass (`scripts/production_smoke.py`)
- [ ] Ari has given explicit verbal/text go-ahead

**Route 53 changes (Mark executes, TTL = 300):**

| Record | Action | Old Value | New Value |
|--------|--------|-----------|-----------|
| `texashomeoutlet.com` (apex) | **EDIT** A | `52.10.0.211` | 4 Google IPs: `216.239.32.21`, `216.239.34.21`, `216.239.36.21`, `216.239.38.21` |
| `www.texashomeoutlet.com` | **EDIT** A → CNAME | `52.10.0.211` (A) | `ghs.googlehosted.com` (CNAME) |

**Leave unchanged:** NS records, MX records (Yahoo/Turbify email stays working).

**Verification after flip:**
- [ ] `dig texashomeoutlet.com` returns 4 Google IPs
- [ ] `dig www.texashomeoutlet.com` returns `ghs.googlehosted.com`
- [ ] `curl -I https://www.texashomeoutlet.com` returns 200 with TLS
- [ ] `curl -I https://texashomeoutlet.com` (apex) redirects to `www` or serves directly
- [ ] Spot-check 5 legacy detail URLs → 200 with correct meta + JSON-LD
- [ ] Spot-check 1 vendor redirect → 301 to correct target
- [ ] `curl -I https://www.texashomeoutlet.com/sitemap.xml` returns 200

**Rollback (instant):**
| Record | Action | Rollback Value |
|--------|--------|----------------|
| `texashomeoutlet.com` (apex) | **EDIT** A | `52.10.0.211` |
| `www.texashomeoutlet.com` | **EDIT** CNAME → A | `52.10.0.211` |

Rollback time: ≤ 5 minutes (TTL = 300). Old site stays up the whole time.

---

## Phase 3 — Resend Email Setup (🔒 HUMAN-GATED — Ari executes)

**Goal:** Configure Resend for transactional email from the new site.

**Preconditions:**
- [ ] DNS flip is complete and stable (Phase 2)
- [ ] Resend account created and API key generated

**Steps:**

| Step | Action | Detail |
|------|--------|--------|
| 3.1 | Create/locate Resend account | [resend.com](https://resend.com) — free tier: 3,000 emails/day |
| 3.2 | Generate API key | Copy the API key (starts with `re_`) |
| 3.3 | Add domain in Resend | Enter `texashomeoutlet.com` → Resend issues DKIM + SPF/MX records |
| 3.4 | Copy DKIM value from Resend | Looks like `resend._domainkey` TXT record |
| 3.5 | Hand DKIM + send-subdomain MX/SPF to Mark | Mark adds to Route 53 (TTL 300), **preserving existing Yahoo MX** |
| 3.6 | Verify domain in Resend | Resend checks DNS and confirms ownership |
| 3.7 | Set Cloud Run secrets | `gcloud run deploy project-go-forward --update-secrets RESEND_API_KEY=resend-api-key:latest` |
| 3.8 | Set Cloud Run env vars | `--update-env-vars RESEND_FROM=...`, `REPLY_TO=...`, `NOTIFICATION_EMAIL=...`, `PUBLIC_SITE_URL=https://www.texashomeoutlet.com` |
| 3.9 | **NEVER use `--set-env-vars` or `--set-secrets`** | These wipe out-of-band env vars like `WEBAUTHN_*` |
| 3.10 | Send test email | Use the contact form or API to trigger one email |
| 3.11 | Check Resend dashboard | Confirm delivery, no bounces |

**DMARC policy:** Start with `p=none` (monitoring), then tighten to `p=quarantine` after 2 weeks of clean delivery data.

---

## Phase 4 — Post-Cutover SEO & Monitoring (Ari executes)

**Search Console (after DNS flip):**
- [ ] Submit `https://www.texashomeoutlet.com/sitemap.xml` in Google Search Console
- [ ] Submit the same sitemap to [Bing Webmaster Tools](https://www.bing.com/webmasters)
- [ ] Run [Rich Results Test](https://search.google.com/test/rich-results) on `/` and one detail URL
- [ ] Confirm Google Business Profile site URL = `https://www.texashomeoutlet.com`
- [ ] Keep all 301 redirects active for ≥ 1 year (do not remove)

**Monitoring (first 48 hours):**
- [ ] Run `scripts/production_smoke.py` every 2 hours
- [ ] Watch Cloud Run logs for 5xx errors
- [ ] Check Search Console for crawl errors
- [ ] Verify email delivery via Resend dashboard
- [ ] Monitor Uptime Check alerts (if configured via ops-bootstrap workflow)

**Known accepted risks (pre-launch):**
1. `starlette` PYSEC-2026-161 — blocked by `google-adk<2.0`, tracked for adk-2 upgrade
2. Firestore event-loop wedge — Cloud Run probes recycle instances; full timeout fix is post-launch

---

## One-Click Checklist Summary

| Phase | Action | Owner | Gated? |
|-------|--------|-------|--------|
| 1 | Search Console verification | Ari + Mark | ❌ No (additive, zero risk) |
| 2 | DNS flip to new site | Mark | ✅ YES — Ari's explicit go |
| 3 | Resend email setup | Ari + Mark | ✅ YES — after DNS flip stable |
| 4 | Post-cutover SEO + monitoring | Ari | ❌ No (normal operations) |

**Emergency contact:** If anything breaks during the flip, Mark can rollback Phase 2 in ≤ 5 minutes by reverting the two DNS records to `52.10.0.211`.
