# Texas Home Outlet — Go-Live Readiness & Action Plan

**Status: ~64% positioned — soft-launch ready, not yet fully positioned.**
The storefront captures + routes leads today. What's left is closing two ops gates,
a thin trust/discoverability layer, and merging the staged improvement PRs.

This is the command center: do the **gated actions** (only you can), and Claude
executes the **autonomous queue** on the clean base.

---

## 📊 Readiness scorecard

| Dimension | Score | Verdict |
|---|---|---|
| Email & Communications | 72 | Strongest pillar — live + verified; harden DMARC, set `PUBLIC_SITE_URL`, add reminders |
| Discoverability & Local SEO | 68 | Technical SEO is exemplary; **invisible in the local pack** until GBP + geo + city pages land |
| Conversion Funnel | 68 | Strong capture; behavioral events now have a sink (PR #164) — wire the dashboard to real data |
| Lead Operations | 62 | Leads land + alert the team; no follow-up cadence, dedup, or routing yet |
| Go-Live Completeness | 62 | Foundation shipped; gated on legal pages, ops-bootstrap, DocuSeal, GBP, env confirms |
| Monitoring / Ops | 58 | Great infra + runbooks; Sentry, ops-bootstrap, partner key, DocuSeal unprovisioned |
| **Trust & Content** | **55** | **The weak link** — no About / testimonials / financing / FAQ; buyers lack proof to commit |

**Overall: ~64%.** Two days of gated setup + the autonomous queue closes most of it.

---

## ✅ Step 1 — Merge the staged PR queue (you; ~15 min)

All green, all file-disjoint. **Merge #165 first** (restores the green deploy signal),
then the rest one at a time, letting each deploy land. The service is on `--to-latest`,
so each auto-serves on merge.

| Order | PR | What it ships |
|---|---|---|
| 1 | **#165** | smoke fix — restores green deploys (do first) |
| 2 | **#164** | `/api/analytics` sink + lead/appointment failure alerting |
| 3 | **#166** | recharts out of PWA precache (−40% first install) |
| 4 | **#167** | LocalBusiness → Organization entity signals |
| 5 | **#168** | form a11y (`role=alert` + autocomplete) |
| 6 | **#169** | **Mira/Telegram bridge** (rebased onto main, hardened; fail-closed until tokens set) |

> After the queue merges, Claude's autonomous build items (city pages, trust pages,
> real analytics dashboard, lead-ops) unblock — they currently overlap these PRs' files.

---

## 🔒 Step 2 — Gated actions (only you can do these)

### Tier 1 — Critical launch blockers (run first)
1. **Run the "Ops bootstrap" workflow** (GitHub → Actions → run workflow). One idempotent
   dispatch creates: email alert channel, `/healthz/` uptime check, 5xx-burst alert, daily
   Firestore backup, the **`THO_API_KEY`** secret (clears a live `/api/v1/inventory` 503),
   and the **staging tag** (unblocks E2E + load tests). *Highest single-action leverage.*
2. **Run the "Deploy DocuSeal" workflow** + the 5-min post-deploy (admin account, API token,
   app secrets, webhook), then E2E the e-sign flow. *No closing-packet signatures without it.*

### Tier 2 — Discoverability / revenue (one-time, ~1–2 h)
3. **Create the Google Business Profile** at business.google.com → verify the Huffman
   location → fill phone / website / hours / categories / photos. Then extract the lat/lng
   and hand them to Claude (or set `config.yaml` `business.geo.latitude/longitude` +
   `business.same_as[]` = [GBP, Facebook, Instagram]). **Single biggest local-SEO unlock** —
   pairs with Claude's city-pages build.
4. **Confirm/set env vars** (Cloud Run, ~15 min):
   - `CANONICAL_PUBLIC_URL=https://www.texashomeoutlet.com`
   - `PUBLIC_SITE_URL=https://www.texashomeoutlet.com` (fixes broken email download links)
   - `OG_IMAGE_URL=https://www.texashomeoutlet.com/og-card.png` (the card already ships —
     this turns on social-share previews)
5. **Provide a GA4 Measurement ID** (`G-XXXXXXXXXX`) (+ optional GTM container) → set via env.
   Without it, Search Console shows impressions but zero conversion-intent data.

### Tier 3 — Quick verifies / decisions (~30 min)
6. **Bind `SENTRY_DSN`** (create Sentry project → secret → `--update-secrets`). Error
   tracking is dark until this lands.
7. **Cookie-consent decision** (banner-gated pixels vs. explicit opt-in) — unblocks
   GA4/Meta/TikTok firing; needs the legal banner copy (Claude can draft it).
8. **`lead_storage_failed` Cloud Run alert** — PR #164 emits the structured log
   (`severity=ERROR jsonPayload.event="lead_storage_failed"`); wire a logs-based alert →
   `NOTIFICATION_EMAIL`. Lost-lead = lost-revenue should page someone.
9. **DMARC `p=none → p=quarantine`** in Resend (after ~1 week of clean live traffic).
10. **Mira/Telegram bridge tokens** (`MIRA_GROUP_ID` / `TELEGRAM_BOT_TOKEN` /
    `GITHUB_WEBHOOK_SECRET`) — optional; email alerts already suffice. The bridge stays
    fail-closed (dark) until set, so #169 is safe to merge without them.

---

## 🤖 Step 3 — Claude's autonomous queue (after the PR merge unblocks the files)

Ranked by business value. Claude builds these as tested PRs for your review.

1. **Real analytics dashboard** — PR #164 captures events to Firestore `analytics_events`;
   wire `analytics_service.py` + the Analytics page to read real data (today it renders
   mock random numbers). *You're flying blind until this — it informs every other decision.*
2. **City landing pages** — `/manufactured-homes-in-{city}-tx` for all 11 served cities,
   recovering the dead legacy-vendor 301s and ranking for high-intent "manufactured homes
   in {city}" queries. *Pairs with your GBP setup — the #1 customer-acquisition unlock.*
3. **Trust & content pages** — About, **Financing**, **FAQ** (+ visible content unlocks
   FAQPage schema), Warranty, Delivery/Setup. *The #1 conversion drag — buyers see homes
   priced "Call for Price" with no proof of who THO is or how financing works.* (Some
   THO-specific details — exact financing partners, warranty terms — need your input;
   Claude drafts industry-accurate scaffolds with marked placeholders.)
4. **Link the funnels + capture home context** — offer the appointment step after a contact
   submission; attach `home_id` to the lead so the CRM can answer "which models drive quotes."
5. **Lead-ops automation** — phone normalization (E.164), duplicate-lead dedup, a follow-up
   cadence (Cloud Scheduler "email if not contacted in 24 h"), lead scoring. *(Touches
   `lead_management.py` — sequence after #169 merges.)*
6. **Email hardening** — appointment reminder emails (lifts showroom show-rate),
   bounce/suppression handling, contact-form email validation.
7. **Ops docs Claude owns** — `ON_CALL.md` (P1/P2/P3), `SLO.md`, Firestore restore runbook,
   read-timeouts, E2E + load-test scripts.
8. **Legal page drafts** — Privacy, Terms, Cookie/Consent, Accessibility Statement (drafts
   for your + legal review; noindex). *Unblocks the analytics-pixel consent gate.*
9. **Fix the lone failing `test_crm.py`** — clean the suite for full-confidence deploys.

---

## 🎯 The three highest-leverage moves

1. **Overall — GBP + city pages.** THO has world-class technical SEO and is still invisible
   for every "manufactured homes near {city}" search — the exact high-intent local queries
   that convert for a single-location dealer. GBP is your 30-min task; city pages are Claude's.
   Do them together.
2. **Gated — the Ops-bootstrap workflow.** One dispatch clears the biggest cluster of
   monitoring/backup/partner-key/staging blockers at once.
3. **Autonomous — real analytics.** Today behavioral data is generated and thrown away and
   the dashboard shows mock numbers. Seeing real funnel behavior unblocks every optimization
   and ad-spend decision.

---

## Recommended sequence

1. Merge **#165 → #164 → #166 → #167 → #168 → #169** (verify each deploy).
2. Run **Ops-bootstrap** + **DocuSeal** workflows.
3. Create the **GBP**, hand Claude the geo + socials.
4. Set the **env vars** (`CANONICAL_PUBLIC_URL`, `PUBLIC_SITE_URL`, `OG_IMAGE_URL`, GA4).
5. Claude builds the **autonomous queue** (analytics → city pages → trust content → lead-ops).
6. Re-run the readiness assessment; iterate to ~90%+.

*Generated from the 7-dimension business-readiness assessment, 2026-06-14.*
