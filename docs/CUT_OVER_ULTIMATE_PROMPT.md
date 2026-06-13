# Ultimate Handoff Prompt — Complete `texashomeoutlet.com` Cutover & Integrations

> **For:** Claude Opus 4.8 (1M context)  
> **Project:** Texas Home Outlet (THO) — `arigatoexpress/Project-Go-Forward`  
> **Date:** 2026-06-13  
> **Status:** Domain verified, Cloud Run domain mappings created, DNS cutover pending Ari’s evening go-ahead.

You are taking over a live business system cutover. Ari S. has given you explicit authority to operate autonomously on his machine and accounts. All credentials are in Brave or already authenticated in the shell (gcloud, gh). **Do not push directly to `main`** unless Ari explicitly overrides the repo guardrails in this prompt.

---

## 1. PROJECT SNAPSHOT

### What the app is
FastAPI + React single-container app running on Cloud Run. It serves:
- Public storefront + inventory
- Internal CRM (customers, deals, leads, appointments, service requests)
- Document generation center (63 regulatory PDF templates)
- Marketing Ad Studio (image/video/script generation)
- Conversational AI agent using Google ADK (`root_agent.py` → `sales_agent` + `service_agent`)

### Current live state
| Item | Value |
|------|-------|
| Cloud Run service | `project-go-forward` in `us-central1`, project `tho-ai-agent` |
| Current production URL | `https://tho.sapphirealpha.xyz` |
| Raw Cloud Run URL | `https://project-go-forward-trgi34bxuq-uc.a.run.app` |
| Domain mappings created | `texashomeoutlet.com`, `www.texashomeoutlet.com` (status: `CertificatePending`, waiting for DNS) |
| Verified domains | `texashomeoutlet.com`, `sapphirealpha.xyz`, `goforwardapp.app` |
| Current apex A | `20.121.124.106` (Turbify default after NS reset) |
| Current nameservers | `ns1.turbify.com`, `ns2.turbify.com` |
| Current MX | Yahoo/Turbify (`mx-biz.mail.am0.yahoodns.net`) — **preserve** |
| GitHub remote | `git@github.com:arigatoexpress/Project-Go-Forward.git` |
| Working directory | `/Users/aribs/Code/Project-Go-Forward` |

### Files you must read first
1. `docs/EVENING_CUTOVER_CHECKLIST.md` — exact DNS cutover checklist.
2. `docs/DNS_CUTOVER_RUNBOOK.md` — phase-by-phase runbook.
3. `docs/ARCHITECTURE.md` — architecture + env vars.
4. `docs/PRODUCTION_READINESS.md` — Resend, admin PIN, smoke tests.
5. `docs/INTEGRATION_NOTION.md` — Notion/Etai integration contract.
6. `.env.example` — env var template.
7. `AGENTS.md` — agent-specific safety boundaries.

---

## 2. PRIMARY MISSION

Complete the DNS cutover to Cloud Run, provision SSL, validate the site, configure transactional email (Resend), wire/verify all integrations (agent, CRM, Notion, n8n, partner webhooks), run production smoke tests, and produce a clean handoff report.

---

## 3. PHASE 1 — EVENING DNS CUTOVER (HUMAN-GATED)

**Do not change DNS until Ari explicitly says “go” in chat/voice.** Until then, prepare everything.

### DNS records to set in Turbify Domain Control Panel
Ari has already reset nameservers to Turbify defaults, so the DCP is authoritative.

| Record | Type | TTL | Value | Rollback |
|--------|------|-----|-------|----------|
| `texashomeoutlet.com` (`@`) | A | 300 | `216.239.32.21`  
`216.239.34.21`  
`216.239.36.21`  
`216.239.38.21` | `20.121.124.106` (current Turbify IP) or `52.10.0.211` (old) |
| `www.texashomeoutlet.com` | CNAME | 300 | `ghs.googlehosted.com` | A record to `20.121.124.106` or `52.10.0.211` |

**Leave unchanged:** NS, MX, TXT (including the Search Console verification TXT).

### How to apply in Turbify DCP
1. Open `https://dcp.turbify.com/dcp/texashomeoutlet.com/dns` in Brave.
2. Edit the apex A record: replace the single Turbify IP with the four Google IPs.
3. Edit `www`: change from A to CNAME `ghs.googlehosted.com`.
4. Save changes.

### Verification loop (run every 60–90 seconds for 10 minutes)
```bash
# Apex must return only Google IPs
dig texashomeoutlet.com A +short

# www must resolve to ghs.googlehosted.com
dig www.texashomeoutlet.com CNAME +short
dig www.texashomeoutlet.com A +short

# HTTP checks
curl -s -o /dev/null -w "apex: %{http_code}\n" https://texashomeoutlet.com/
curl -s -o /dev/null -w "www: %{http_code}\n" https://www.texashomeoutlet.com/
curl -s -o /dev/null -w "healthz: %{http_code}\n" https://www.texashomeoutlet.com/healthz/

# TLS certificate
echo | openssl s_client -connect www.texashomeoutlet.com:443 -servername www.texashomeoutlet.com 2>/dev/null | openssl x509 -noout -dates -subject
```

### Rollback (instant if anything breaks)
Revert the two records to the Turbify IP (`20.121.124.106` or `52.10.0.211`). Wait ~5 minutes and re-run verification.

---

## 4. PHASE 2 — SSL PROVISIONING MONITORING

After DNS points to Google, Cloud Run will provision managed certificates (15–60 minutes).

```bash
watch -n 60 'gcloud beta run domain-mappings describe --domain=texashomeoutlet.com --region=us-central1 --format="yaml(status.conditions)"'
```

Success criteria:
- `CertificateProvisioned` condition status = `True`
- `Ready` condition status = `True`
- `https://www.texashomeoutlet.com/` returns 200 with a cert covering `www.texashomeoutlet.com` and ideally the apex.

---

## 5. PHASE 3 — POST-CUTOVER VALIDATION

Run the production smoke script:
```bash
python3 scripts/production_smoke.py --base-url https://www.texashomeoutlet.com
```

If `scripts/production_smoke.py` does not exist, create a minimal one that checks:
- `/health` and `/healthz/`
- Public SPA routes
- `/api/marketing/inventory-context`
- `/sitemap.xml`

Spot-check:
- 5 legacy detail URLs → 200, correct title, meta description, JSON-LD.
- 1 vendor redirect → 301 to correct target.
- Contact/checkout pages load with no mixed-content warnings.

---

## 6. PHASE 4 — RESEND EMAIL SETUP

### 4.1 Configure Resend domain
1. Open `https://resend.com/domains` in Brave.
2. Add domain `texashomeoutlet.com` (or `www.texashomeoutlet.com` if using subdomain).
3. Resend will issue DNS records (DKIM TXT, SPF TXT, MX). Add them in Turbify DCP **without removing existing Yahoo MX**.
4. Verify the domain in Resend.

### 4.2 Store API key in Secret Manager
```bash
read -rsp "Resend API key: " RESEND_API_KEY; echo
printf '%s' "$RESEND_API_KEY" | gcloud secrets versions add resend-api-key \
  --project=tho-ai-agent --data-file=-
unset RESEND_API_KEY
```

If the secret does not exist:
```bash
read -rsp "Resend API key: " RESEND_API_KEY; echo
printf '%s' "$RESEND_API_KEY" | gcloud secrets create resend-api-key \
  --project=tho-ai-agent --replication-policy=automatic --data-file=-
unset RESEND_API_KEY
```

### 4.3 Bind secret and env vars to Cloud Run
```bash
gcloud run services update project-go-forward \
  --project=tho-ai-agent \
  --region=us-central1 \
  --update-secrets=RESEND_API_KEY=resend-api-key:latest \
  --update-env-vars=\
RESEND_FROM="Texas Home Outlet <noreply@texashomeoutlet.com>",\
REPLY_TO="support@texashomeoutlet.com",\
NOTIFICATION_EMAIL="aristotlespec@gmail.com",\
PUBLIC_SITE_URL="https://www.texashomeoutlet.com"
```

**NEVER use `--set-env-vars` or `--set-secrets`** — they wipe other env vars.

### 4.4 Verify email readiness
```bash
ADMIN_TOKEN="<get from /api/admin/verify>"
curl -fsS "https://www.texashomeoutlet.com/healthz/detailed" \
  -H "X-Admin-Token: $ADMIN_TOKEN" | python3 -m json.tool
```

`dependencies.email` should be `configured`. Send a test email via the contact form or API and confirm delivery in the Resend dashboard.

---

## 7. PHASE 5 — AGENT, CRM & INTEGRATIONS

### 7.1 Agent (Google ADK / root_agent.py)
- Confirm `root_agent.py` loads and the sales/service agents render correctly.
- Test the chat endpoint on the new domain:
  ```bash
  curl -s -X POST https://www.texashomeoutlet.com/api/chat \
    -H "Content-Type: application/json" \
    -d '{"message":"What manufactured homes do you have in Huffman?"}' | head -c 500
  ```
- Check `/healthz/detailed` for agent dependency status.
- Verify `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, and Vertex AI env vars are correct.

### 7.2 CRM
- Log into the CRM at `https://www.texashomeoutlet.com/crm` using admin PIN.
- Verify customer/deal lists load.
- Create a test lead or deal and confirm it appears in Firestore.
- Check that `deal.status_changed` webhooks fire correctly (if a partner webhook is configured).

### 7.3 Notion Integration (Etai)
See `docs/INTEGRATION_NOTION.md`.

Required follow-ups:
1. Merge any open API v1 integration PR (check `feat/api-v1-integration`, PR #4) into `main` via normal PR process.
2. Rotate `THO_API_KEY` and move it to Secret Manager (`tho-api-key`).
3. Generate a partner-scoped key for Etai (`tho-api-key-etai`) and deliver securely.
4. Register Etai’s webhook URL:
   ```bash
   gcloud run services update project-go-forward \
     --project=tho-ai-agent --region=us-central1 \
     --update-env-vars=PARTNER_WEBHOOK_URL_ETAI=https://notion.example.com/hooks/deals
   ```
5. Ensure `deal.funded`, `deal.complete`, and `deal.status_changed` events are dispatched.
6. Provision Google Drive folder structure per deal and grant Etai access.

### 7.4 n8n
- `N8N_API_TOKEN` is currently plaintext in Cloud Run env. Rotate and move to Secret Manager (`n8n-api-token`).
- Verify n8n-triggered workflows still authenticate.

### 7.5 Partner webhooks
- Confirm `X-THO-Signature` HMAC verification is implemented.
- Test a webhook delivery and check the Firestore `activities/` collection for the audit log.

---

## 8. PHASE 6 — SECURITY & PRODUCTION HARDENING

1. **Enable Firestore delete protection** on `tho-ai-agent`:
   ```bash
   gcloud alpha firestore databases update --database='(default)' \
     --delete-protection=enabled --project=tho-ai-agent
   ```
2. **Rotate secrets currently in plaintext env**:
   - `THO_API_KEY` → Secret Manager `tho-api-key`
   - `N8N_API_TOKEN` → Secret Manager `n8n-api-token`
3. **Verify `ADMIN_PIN_HASH`** is backed by Secret Manager `admin-pin-hash`.
4. **Review Cloud Run ingress** — should be `all` unless restricted.
5. **Run `pre-commit run --files <changed-files>`** on any code changes before PR.

---

## 9. PHASE 7 — SEARCH CONSOLE & SEO

After DNS is stable:
1. Submit sitemap: `https://www.texashomeoutlet.com/sitemap.xml`
2. Submit same sitemap to Bing Webmaster Tools.
3. Run Rich Results Test on homepage + one detail URL.
4. Confirm Google Business Profile URL = `https://www.texashomeoutlet.com`.
5. Keep all 301 redirects active for ≥ 1 year.

---

## 10. TESTING & ACCEPTANCE CRITERIA

Before declaring complete, all of these must pass:

- [ ] `https://www.texashomeoutlet.com/` returns 200 with valid TLS.
- [ ] `https://texashomeoutlet.com/` redirects to `www` or serves directly with valid TLS.
- [ ] Cloud Run mapping status: `CertificateProvisioned = True` and `Ready = True`.
- [ ] `python3 scripts/production_smoke.py --base-url https://www.texashomeoutlet.com` passes.
- [ ] Resend domain verified and test email delivered.
- [ ] Agent chat endpoint responds on new domain.
- [ ] CRM loads and a test deal persists.
- [ ] Notion webhook registered (or pending only on Etai’s side).
- [ ] No 5xx spikes in Cloud Run logs for 30 minutes post-cutover.
- [ ] Rollback commands documented and tested mentally.

---

## 11. TOOLING & CREDENTIALS

### Authenticated CLIs
- `gcloud` — active account `aristotlespec@gmail.com`, project `tho-ai-agent`.
- `gh` — GitHub CLI authenticated as `arigatoexpress`.
- `git` — SSH key `~/.ssh/id_ed255_new` for `git@github.com`.

### Key commands
```bash
# Domain mapping status
gcloud beta run domain-mappings list --project=tho-ai-agent --region=us-central1

# Service env/secrets
gcloud run services describe project-go-forward --project=tho-ai-agent --region=us-central1 --format=json

# Cloud Run logs
gcloud logging read "resource.type=cloud_run_revision" --limit=50 --format=json

# Admin token
THO_ADMIN_PIN="<PIN>" curl -fsS -X POST https://www.texashomeoutlet.com/api/admin/verify \
  -H "Content-Type: application/json" \
  --data-binary @- <<< "{\"pin\":\"$THO_ADMIN_PIN\"}"
```

### Browser automation
- Brave is the user’s default browser; Google/Turbify sessions are active.
- Use AppleScript + `System Events` clicks if JS clicks are blocked by `event.isTrusted`.
- Prefer exact coordinates computed from `getBoundingClientRect()` + window `screenY` + browser chrome offset.

---

## 12. GUARDRAILS (NON-NEGOTIABLE)

1. **No direct push to `main`.** Open PRs and get Ari/Mark approval before merge.
2. **Do not modify `tho_documents/*.pdf`** — regulatory originals.
3. **Do not send raw PII to LLMs or logs.** Use `tools/pii_guard.py`.
4. **Do not remove Yahoo MX records.** Resend and Yahoo mail must coexist.
5. **Do not change DNS until Ari says go.**
6. **Use `--update-*`, never `--set-*`, on Cloud Run env/secrets.**
7. **Keep rollback path live** at every step.

---

## 13. DELIVERABLES

Produce a final markdown report at `docs/CUTOVER_COMPLETION_REPORT_20260613.md` containing:

1. Exact time of DNS flip and who authorized it.
2. DNS records before/after.
3. SSL provisioning timestamps.
4. Smoke test results.
5. Resend configuration status.
6. Integration status (agent, CRM, Notion, n8n, webhooks).
7. Security hardening items completed.
8. Open follow-ups and owners.
9. Rollback confirmation commands.

Then update `docs/EVENING_CUTOVER_CHECKLIST.md` to mark all completed items.

---

## 14. OPEN QUESTIONS TO RESOLVE DURING EXECUTION

- What is the exact Resend API key? (Prompt Ari securely or retrieve from Resend dashboard.)
- What is Etai’s Notion webhook URL? (May be pending Etai setup.)
- Is there an open `go-live-hardening-20260612` PR that must merge first?
- What is the current admin PIN for `/api/admin/verify` testing?

If any of these block autonomous progress, ask Ari concisely and continue with the parts that are not blocked.
