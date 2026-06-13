# Claude Final Push Prompt — Texas Home Outlet Cutover & Business Upgrade

> **For:** Claude Opus 4.8 (1M context)  
> **Project:** `arigatoexpress/Project-Go-Forward`  
> **Working directory:** `/Users/aribs/Code/Project-Go-Forward`  
> **Date:** 2026-06-13  
> **Audience:** Ari S. and the THO team (assume non-technical; explain every step).

You are the final push agent for a live business cutover. **Ari has full authority** over this repo, his Google Cloud project, his Brave browser sessions, and his DNS. Move carefully, explain what you are doing in plain English, and **never execute a destructive step without first stating it and getting Ari’s OK** unless the action is explicitly labeled as safe/non-destructive below.

---

## 0. WHAT HAS ALREADY HAPPENED (read this first)

The previous agent completed these steps on 2026-06-13:

1. ✅ Reset domain nameservers to Turbify defaults so the Domain Control Panel is authoritative.
2. ✅ Verified `texashomeoutlet.com` in Google Search Console.
3. ✅ Created Cloud Run domain mappings for `texashomeoutlet.com` and `www.texashomeoutlet.com`.
4. ✅ Updated Cloud Run env vars:
   - `CANONICAL_PUBLIC_URL=https://www.texashomeoutlet.com`
   - `PUBLIC_SITE_URL=https://www.texashomeoutlet.com`
   - `RESEND_FROM="Texas Home Outlet <noreply@texashomeoutlet.com>"`
   - `REPLY_TO=support@texashomeoutlet.com`
   - `NOTIFICATION_EMAIL=aristotlespec@gmail.com`
5. ✅ Bound `RESEND_API_KEY=resend-api-key:latest` from Secret Manager to Cloud Run.
6. ✅ Updated `llms.txt` Site line to `https://www.texashomeoutlet.com/`.
7. ✅ Created `frontend/public/robots.txt` and `frontend/public/llms.txt` as build-time fallbacks.
8. ✅ Started DNS propagation and SSL provisioning monitoring.

**Current status at handoff:**
- DNS records for `www.texashomeoutlet.com` point to `ghs.googlehosted.com` on Turbify’s nameservers.
- DNS records for `texashomeoutlet.com` point to one Google IP (`216.239.32.21`).
- Cloud Run SSL certificates are **provisioning** (status: `CertificatePending`).
- The site is **not yet live** on the new domain until SSL finishes and Ari confirms.

---

## 1. YOUR PRIMARY MISSION (plain English)

Finish the cutover to `https://www.texashomeoutlet.com`, make sure the site works, and then complete the marketing/analytics/email/agent/CRM/Notion integrations that turn this from a domain flip into a real business upgrade.

---

## 2. CREDENTIALS & TOOLS YOU HAVE

| Tool | How to use it | Status |
|------|---------------|--------|
| **gcloud CLI** | Terminal commands | Authenticated as `aristotlespec@gmail.com`, project `tho-ai-agent` |
| **gh CLI** | Terminal commands | Authenticated as `arigatoexpress` |
| **Brave browser** | AppleScript automation or ask Ari to click | Google/Turbify/Resend sessions active |
| **Git** | `git` in `/Users/aribs/Code/Project-Go-Forward` | SSH key `~/.ssh/id_ed25519_new` |
| **Cloud Run** | `gcloud run services update/describe` | Ready |
| **Secret Manager** | `gcloud secrets versions add` | Ready |

---

## 3. SAFETY RULES (non-negotiable)

1. **No direct push to `main`.** Always open a PR and wait for Ari/Mark approval.
2. **Use `--update-*`, never `--set-*`, on Cloud Run env/secrets.** `--set-*` wipes other variables.
3. **Do not remove Yahoo MX records** when adding Resend DNS records.
4. **Do not modify files in `tho_documents/`** — regulatory originals.
5. **Do not send raw customer PII to any LLM or log it.** Use `tools/pii_guard.py`.
6. **Do not change DNS unless Ari explicitly says “go.”** DNS changes are human-gated.
7. **Keep rollback commands ready** at every step.

---

## 4. PHASE 1 — WAIT FOR SSL, THEN VERIFY THE SITE (safe to poll)

### 4.1 Check if SSL is ready
Run this every few minutes until it succeeds:

```bash
gcloud beta run domain-mappings describe --domain=www.texashomeoutlet.com --region=us-central1 --format="yaml(status.conditions)"
gcloud beta run domain-mappings describe --domain=texashomeoutlet.com --region=us-central1 --format="yaml(status.conditions)"
```

You want to see:
- `CertificateProvisioned: True`
- `Ready: True`

If this takes longer than 90 minutes, tell Ari.

### 4.2 Once SSL is ready, verify the new domain

```bash
# Should return 200 from the THO app (not nginx/WordPress)
curl -s -o /dev/null -w "www status: %{http_code}\n" https://www.texashomeoutlet.com/
curl -s -o /dev/null -w "apex status: %{http_code}\n" https://texashomeoutlet.com/
curl -s -o /dev/null -w "healthz: %{http_code}\n" https://www.texashomeoutlet.com/healthz/

# TLS certificate check
echo | openssl s_client -connect www.texashomeoutlet.com:443 -servername www.texashomeoutlet.com 2>/dev/null | openssl x509 -noout -dates -subject
```

If you still see `server: nginx/1.18.0 (Ubuntu)` or `PHPSESSID`, the local resolver is caching the old site. Wait 5 minutes or ask Ari to flush his DNS.

### 4.3 Run production smoke tests

```bash
python3 scripts/production_smoke.py --base-url https://www.texashomeoutlet.com
```

If the script does not exist, create a minimal one that checks `/healthz/`, `/inventory`, `/contact`, `/sitemap.xml`, and an admin-auth rejection.

---

## 5. PHASE 2 — RESEND EMAIL (mostly human-gated)

### 5.1 Confirm the API key
The Cloud Run service is already bound to Secret Manager secret `resend-api-key`.

Ask Ari:
> “Is the latest version of the `resend-api-key` secret in Google Cloud the current live Resend API key (starts with `re_`)?”

If **yes**, skip to 5.2.  
If **no**, replace it:

```bash
read -rsp "Resend API key: " RESEND_API_KEY; echo
printf '%s' "$RESEND_API_KEY" | gcloud secrets versions add resend-api-key \
  --project=tho-ai-agent --data-file=-
unset RESEND_API_KEY
```

### 5.2 Add Resend DNS records (ask Ari/Mark to do this)

Resend will ask you to add DKIM/SPF records. **Do not delete the Yahoo MX records.** Current MX:

```text
20 mx-biz.mail.am0.yahoodns.net.
30 mx-biz.mail.am0.yahoodns.net.
```

Steps:
1. Open `https://resend.com/domains` in Brave.
2. Add `texashomeoutlet.com`.
3. Copy the DNS records Resend gives you.
4. In `https://dcp.turbify.com/dcp/texashomeoutlet.com/dns`, add those records alongside the existing MX.
5. If an SPF record already exists, merge it. Example:
   ```text
   v=spf1 include:send.resend.com include:_spf.mail.yahoo.com ~all
   ```
6. (Recommended) Add DMARC monitoring:
   ```text
   _dmarc.texashomeoutlet.com.  TXT  "v=DMARC1; p=none; rua=mailto:dmarc@texashomeoutlet.com"
   ```

### 5.3 Verify email is configured

Get an admin token (Ari must provide the PIN):

```bash
read -rsp "THO admin PIN: " PIN; echo
TOKEN=$(curl -fsS -X POST https://www.texashomeoutlet.com/api/admin/verify \
  -H "Content-Type: application/json" \
  -d "{\"pin\":\"$PIN\"}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
unset PIN
curl -fsS https://www.texashomeoutlet.com/healthz/detailed \
  -H "X-Admin-Token: $TOKEN" | python3 -m json.tool
```

Expected: `dependencies.email: configured` and no warnings.

### 5.4 Send a test email

Use the contact form or this curl:

```bash
curl -fsS -X POST https://www.texashomeoutlet.com/api/leads \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Resend Test",
    "email": "your-test-email@example.com",
    "phone": "(555) 555-5555",
    "message": "Testing transactional email setup."
  }'
```

Check the Resend dashboard for a delivered log.

---

## 6. PHASE 3 — ANALYTICS & MARKETING (biggest business value)

Read `/tmp/marketing_analytics_audit_20260613.md` first.

### 6.1 Add Google Analytics 4 / Google Tag Manager

Ask Ari for the GA4 measurement ID (looks like `G-XXXXXXXXXX`) or GTM container ID (`GTM-XXXXXX`).

Then add the snippet to `frontend/index.html` **or** inject it via `seo_routes.py` if CSP rules make inline scripts hard.

Rebuild and deploy via PR:

```bash
npm --prefix frontend install
npm --prefix frontend run build
git checkout -b feat/ga4-tagging
git add frontend/
git commit -m "feat(marketing): add GA4/GTM for texashomeoutlet.com"
git push -u origin feat/ga4-tagging
gh pr create --title "feat(marketing): add GA4/GTM" --body "Adds GA4/GTM snippet for new domain."
```

### 6.2 Add Meta Pixel and TikTok Pixel

Same flow as GA4. Ask Ari for:
- Meta Pixel ID
- TikTok Pixel ID

Add the base scripts to `frontend/index.html`, rebuild, PR, and merge.

### 6.3 Add UTM tracking to Ad Studio CTAs

In `tools/marketing_tools.py` or wherever flyer/social CTAs are generated, append UTM params:

```text
?utm_source=adstudio&utm_medium=social&utm_campaign=<home-name-or-slug>
```

Keep the change minimal and test one generated flyer.

### 6.4 Configure Sentry (optional but strongly recommended)

Ask Ari for the Sentry DSN.

Cloud Run runtime:
```bash
gcloud run services update project-go-forward \
  --project=tho-ai-agent --region=us-central1 \
  --update-env-vars=SENTRY_DSN=https://...@....ingest.sentry.io/...
```

Frontend build-time (requires GitHub Actions or local build with env):
```bash
VITE_SENTRY_DSN=https://...@....ingest.sentry.io/... npm --prefix frontend run build
```

---

## 7. PHASE 4 — SOCIAL PUBLISHING

The Ad Studio can generate content but cannot publish because social tokens are missing.

### 7.1 TikTok

Needs:
- `TIKTOK_ACCESS_TOKEN`
- `TIKTOK_ADVERTISER_ID`
- `TIKTOK_PRIVACY_LEVEL`
- `THO_SOCIAL_PUBLISH_ENABLED=1`

Ask Ari to create a TikTok for Business app and authorize the account.

### 7.2 Instagram / Meta

Needs:
- `META_ACCESS_TOKEN`
- `INSTAGRAM_BUSINESS_ACCOUNT_ID`
- `META_GRAPH_VERSION=v24.0`
- `THO_SOCIAL_PUBLISH_ENABLED=1`

Ask Ari to create a Meta app, link the Instagram Business account, and generate a token with `instagram_content_publish` and `pages_read_engagement` scopes.

### 7.3 Store tokens safely

Add each token to Secret Manager, then bind them to Cloud Run:

```bash
read -rsp "Meta token: " META; echo
printf '%s' "$META" | gcloud secrets versions add meta-access-token --project=tho-ai-agent --data-file=-
unset META

gcloud run services update project-go-forward \
  --project=tho-ai-agent --region=us-central1 \
  --update-secrets=META_ACCESS_TOKEN=meta-access-token:latest \
  --update-env-vars=META_GRAPH_VERSION=v24.0,INSTAGRAM_BUSINESS_ACCOUNT_ID=...,THO_SOCIAL_PUBLISH_ENABLED=1
```

Repeat for TikTok.

---

## 8. PHASE 5 — NOTION & INTEGRATION PARTNERS

Read `docs/INTEGRATION_NOTION.md` first.

### 8.1 Merge API v1 integration if a PR exists

Check:
```bash
gh pr list --state=open --search "api v1 OR notion OR integration"
git branch -a | grep -iE 'api-v1|notion|integration'
```

If there is an open PR, review it, run tests, and help Ari merge it.

### 8.2 Rotate partner API keys

Move `THO_API_KEY` and `N8N_API_TOKEN` to Secret Manager if they are still plaintext.

```bash
gcloud secrets list --project=tho-ai-agent --filter="name:(tho-api-key OR n8n)"
```

If missing, create them and bind to Cloud Run.

### 8.3 Register partner webhooks

For Etai/Notion:

```bash
gcloud run services update project-go-forward \
  --project=tho-ai-agent --region=us-central1 \
  --update-env-vars=PARTNER_WEBHOOK_URL_ETAI=https://notion.example.com/hooks/deals
```

Replace the URL with the real one Ari provides.

### 8.4 Test a `deal.funded` webhook

Create or update a test deal to `funded` status and verify the webhook fires and is signed with `X-THO-Signature`.

---

## 9. PHASE 6 — FINAL SEO & SEARCH CONSOLE

Most SEO work is already done in `seo_routes.py`. Finish these:

1. Submit `https://www.texashomeoutlet.com/sitemap.xml` in Google Search Console.
2. Submit the same sitemap to Bing Webmaster Tools.
3. Run the [Rich Results Test](https://search.google.com/test/rich-results) on `/` and one inventory detail page.
4. Confirm Google Business Profile website URL = `https://www.texashomeoutlet.com`.
5. Ensure `frontend/public/robots.txt` and `frontend/public/llms.txt` are committed and deployed.

---

## 10. PHASE 7 — TESTING & ACCEPTANCE

Before declaring done, verify every item:

- [ ] `https://www.texashomeoutlet.com/` returns 200 with valid TLS.
- [ ] `https://texashomeoutlet.com/` redirects to `www` or serves directly with valid TLS.
- [ ] `/healthz/` returns 200.
- [ ] `/sitemap.xml` returns 200.
- [ ] `/robots.txt` and `/llms.txt` return 200.
- [ ] Admin routes reject unauthenticated traffic.
- [ ] CRM loads at `/crm` after PIN login.
- [ ] Chat agent responds at `/api/chat`.
- [ ] Resend `dependencies.email` is `configured` on `/healthz/detailed`.
- [ ] A test email delivers.
- [ ] GA4/GTM/Meta/TikTok tags are present on the live site (if Ari provided IDs).
- [ ] Sentry is initialized (if DSN provided).
- [ ] 5 legacy detail URLs return 200 with correct title and JSON-LD.
- [ ] 1 vendor redirect returns 301 to the correct target.
- [ ] No 5xx spikes in Cloud Run logs for 30 minutes.

---

## 11. ROLLBACK (keep this ready)

If anything breaks during or after the cutover, revert DNS in Turbify DCP:

| Record | Type | Value |
|--------|------|-------|
| `texashomeoutlet.com` (`@`) | A | `20.121.124.106` or `52.10.0.211` |
| `www.texashomeoutlet.com` | A | `20.121.124.106` or `52.10.0.211` |

Remove the `www` CNAME if you added it, and restore the A record.

Wait ~5 minutes and verify the old site returns.

---

## 12. FINAL DELIVERABLE

Create `docs/CUTOVER_COMPLETION_REPORT_20260613.md` with:

1. Exact time of DNS cutover and who authorized it.
2. DNS records before and after.
3. SSL provisioning timeline.
4. Smoke test results.
5. Resend configuration status.
6. Analytics/pixel configuration status.
7. Social publishing token status.
8. Notion/integration status.
9. Security hardening completed.
10. Open follow-ups with owners.
11. Rollback commands.

Update `docs/EVENING_CUTOVER_CHECKLIST.md` to mark completed items.

Then tell Ari:
> “Cutover is complete. The site is live on `https://www.texashomeoutlet.com`. SSL, smoke tests, email, analytics, and integrations are [status]. The completion report is at `docs/CUTOVER_COMPLETION_REPORT_20260613.md`.”

---

## 13. OPEN QUESTIONS ONLY ARI CAN ANSWER

Keep this list handy. Ask these one at a time when they block progress:

1. What is the current admin PIN for `/api/admin/verify`?
2. Is the latest `resend-api-key` secret the live Resend API key?
3. What is the GA4 measurement ID (G-XXXXXXXXXX) or GTM container ID?
4. What is the Meta Pixel ID?
5. What is the TikTok Pixel ID?
6. Do you have a Meta app + Instagram Business account token for social publishing?
7. Do you have a TikTok for Business app token for social publishing?
8. What is Etai’s Notion webhook URL?
9. Is there an open API v1 / Notion integration PR to merge?
10. What is the Sentry DSN (frontend + backend)?

---

## 14. QUICK REFERENCE COMMANDS

```bash
# Domain mapping status
gcloud beta run domain-mappings list --project=tho-ai-agent --region=us-central1

# Cloud Run service env/secrets
gcloud run services describe project-go-forward --project=tho-ai-agent --region=us-central1 --format=json

# Cloud Run logs
gcloud logging read "resource.type=cloud_run_revision" --limit=50 --format=json

# DNS checks
dig texashomeoutlet.com A +short
dig www.texashomeoutlet.com CNAME +short
dig www.texashomeoutlet.com A +short

# TLS check
echo | openssl s_client -connect www.texashomeoutlet.com:443 -servername www.texashomeoutlet.com 2>/dev/null | openssl x509 -noout -dates -subject

# Health checks
curl -fsS https://www.texashomeoutlet.com/healthz/
curl -fsS https://tho.sapphirealpha.xyz/healthz/
```

---

## 15. BOOMER-PROOF SUMMARY FOR ARI

> “We moved the domain to Google, got SSL, tested the site, turned on customer emails, added Google/Meta/TikTok tracking, connected the AI agent and CRM, and hooked up Notion. Everything is documented. If anything breaks, we can roll back the DNS in two clicks.”

Good luck. Move slow, document everything, and ask Ari when you need a token, PIN, or human click.
