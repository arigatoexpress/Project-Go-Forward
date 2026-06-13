# Evening DNS Cutover Checklist — `texashomeoutlet.com`

> **Status:** DRAFT — human-gated DNS flip. Ari gives explicit go-ahead before Step 2.  
> **Rollback IP:** `52.10.0.211`  
> **TTL:** 300 seconds (≤ 5 min propagation)

---

## 0. Preconditions (all must pass before cutover)

- [ ] `go-live-hardening-20260612` PR merged to `main` and deployed to Cloud Run
- [ ] `https://tho.sapphirealpha.xyz/` returns 200 with valid TLS
- [ ] Cloud Run domain mappings created for both domains:
  ```bash
  gcloud beta run domain-mappings describe --domain=texashomeoutlet.com --region=us-central1 --format="yaml(status)"
  gcloud beta run domain-mappings describe --domain=www.texashomeoutlet.com --region=us-central1 --format="yaml(status)"
  ```
  Expected: `CertificateProvisioned = True` for both.
- [ ] Legacy 301 redirects, quote URLs, and vendor redirects tested
- [ ] SEO smoke tests pass: `python scripts/production_smoke.py`
- [ ] Ari has given explicit go-ahead in writing/voice

---

## 1. Pre-cutover verification

| Check | Command | Expected |
|-------|---------|----------|
| Domain mapping active | `gcloud beta run domain-mappings list --region=us-central1` | Both domains show `Active` |
| SSL provisioned | `gcloud beta run domain-mappings describe --domain=www.texashomeoutlet.com --region=us-central1 --format="yaml(status)"` | `CertificateProvisioned = True` |
| Current apex DNS | `dig texashomeoutlet.com A +short` | `52.10.0.211` |
| Current www DNS | `dig www.texashomeoutlet.com A +short` | `52.10.0.211` or existing value |

---

## 2. DNS changes in Turbify / Route 53 (Mark executes after Ari says go)

**Apex domain**

- Record: `texashomeoutlet.com` (`@`)
- Type: `A`
- TTL: `300`
- Action: Replace single existing A value with **four** Google Cloud Run IPs:
  - `216.239.32.21`
  - `216.239.34.21`
  - `216.239.36.21`
  - `216.239.38.21`
- Rollback value: `52.10.0.211`

**WWW subdomain**

- Record: `www.texashomeoutlet.com`
- Type: `CNAME` (convert from A if necessary)
- TTL: `300`
- Value: `ghs.googlehosted.com`
- Rollback value: A record `52.10.0.211`

**Leave unchanged:** NS records, MX records (Yahoo/Turbify inbound mail).

---

## 3. Post-change verification (run every 60–90 seconds until stable)

```bash
# Apex should resolve to Google IPs, not 52.10.0.211
dig texashomeoutlet.com A +short

# WWW should resolve to ghs.googlehosted.com
dig www.texashomeoutlet.com A +short
dig www.texashomeoutlet.com CNAME +short

# Health and status checks
curl -s -o /dev/null -w "apex status: %{http_code}\n" https://texashomeoutlet.com/
curl -s -o /dev/null -w "www status: %{http_code}\n" https://www.texashomeoutlet.com/
curl -s -o /dev/null -w "healthz status: %{http_code}\n" https://www.texashomeoutlet.com/healthz/

# TLS certificate validity
openssl s_client -connect www.texashomeoutlet.com:443 -servername www.texashomeoutlet.com < /dev/null | openssl x509 -noout -dates -subject

# Sitemap
curl -s -o /dev/null -w "sitemap status: %{http_code}\n" https://www.texashomeoutlet.com/sitemap.xml
```

**Manual spot-checks**

- [ ] 5 legacy detail URLs → 200, correct `<title>`, meta description, JSON-LD
- [ ] 1 vendor redirect → 301 to correct target
- [ ] Contact / checkout pages load with no mixed-content warnings

**Success criteria**

- Apex resolves to the 4 Google IPs.
- WWW resolves to `ghs.googlehosted.com`.
- Both apex and www return 200 (or valid 301/302 to canonical).
- TLS certificate covers `texashomeoutlet.com` / `www.texashomeoutlet.com`.

---

## 4. Rollback (instant, if anything breaks)

| Record | Action | Rollback Value |
|--------|--------|----------------|
| `texashomeoutlet.com` (`@`) | Replace 4 Google A records with single A | `52.10.0.211` |
| `www.texashomeoutlet.com` | Replace CNAME with A | `52.10.0.211` |

After reverting, wait ~5 minutes and re-run verification commands. Old site remains up.

---

## 5. Post-cutover steps (after DNS is stable for 15+ minutes)

### 5.1 Resend email setup

- [ ] Add domain in Resend dashboard: `texashomeoutlet.com` (or `mail.texashomeoutlet.com`)
- [ ] Copy exact DKIM TXT and SPF record from Resend
- [ ] Mark adds records to Route 53 (TTL 300), preserving existing Yahoo MX
- [ ] Verify domain in Resend
- [ ] Deploy secrets and env vars to Cloud Run:
  ```bash
  gcloud run deploy project-go-forward \
    --region=us-central1 \
    --update-secrets RESEND_API_KEY=resend-api-key:latest \
    --update-env-vars RESEND_FROM=...,REPLY_TO=...,NOTIFICATION_EMAIL=...,PUBLIC_SITE_URL=https://www.texashomeoutlet.com
  ```
  ⚠️ Use `--update-*`, never `--set-*`.
- [ ] Send test email and confirm delivery via Resend dashboard

### 5.2 Search Console & SEO

- [ ] Submit `https://www.texashomeoutlet.com/sitemap.xml` in Google Search Console
- [ ] Submit same sitemap to [Bing Webmaster Tools](https://www.bing.com/webmasters)
- [ ] Run Rich Results Test on homepage and one detail URL
- [ ] Confirm Google Business Profile URL = `https://www.texashomeoutlet.com`

### 5.3 Monitoring (first 48 hours)

- [ ] Run `python scripts/production_smoke.py` every 2 hours
- [ ] Watch Cloud Run logs: `gcloud logging read "resource.type=cloud_run_revision" --limit=50 --format=json`
- [ ] Check Search Console Coverage for 5xx / redirect errors
- [ ] Monitor Resend delivery and bounce rates
- [ ] Keep 301 redirects active for ≥ 1 year

---

## One-line summary

Flip apex A records to the four Google IPs, flip `www` to `ghs.googlehosted.com` CNAME, verify with `dig`/`curl`/browser, then configure Resend and submit the sitemap — with instant rollback to `52.10.0.211` if anything fails.
