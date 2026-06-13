# Texas Home Outlet Cutover — Status Report

**Date:** 2026-06-13  
**Branch:** `cutover/20260613-dns-prep`  
**Pull Request:** https://github.com/arigatoexpress/Project-Go-Forward/pull/156  
**Current production URL:** `https://tho.sapphirealpha.xyz`  
**Target URL:** `https://www.texashomeoutlet.com`

---

## ✅ What is Done

| Step | Status | Notes |
|------|--------|-------|
| Search Console verification | ✅ | `texashomeoutlet.com` verified; TXT record live on Turbify NS |
| Reset nameservers to Turbify defaults | ✅ | Parent `.com` delegation now `ns1.turbify.com` / `ns2.turbify.com` |
| Cloud Run domain mappings | ✅ | Created for `texashomeoutlet.com` and `www.texashomeoutlet.com` |
| Apex A record | ✅ | Points to `216.239.32.21` in Turbify DCP |
| www CNAME | ✅ | Points to `ghs.googlehosted.com` |
| Apex SSL certificate | ✅ | `CertificateProvisioned = True`, `Ready = True` |
| Cloud Run env update | ✅ | `CANONICAL_PUBLIC_URL`, `PUBLIC_SITE_URL`, `RESEND_*`, partner secrets bound |
| `llms.txt` update | ✅ | Repo-root and `frontend/public/llms.txt` point to new domain |
| `robots.txt` fallback | ✅ | Created at `frontend/public/robots.txt` |
| Telegram integration | ✅ | `services/telegram/` + tests merged into branch |
| PR opened | ✅ | PR #156 ready for review |
| Claude handoff prompt | ✅ | `docs/CLAUDE_FINAL_PUSH_PROMPT.md` |

---

## ⏳ What is In Progress / Pending

| Step | Status | Notes |
|------|--------|-------|
| www SSL certificate | ⏳ | `CertificateProvisioned = Unknown / CertificatePending`. Google is retrying every ~5 min. Should finish within 15–60 min of DNS stabilizing. |
| Local DNS cache | ⏳ | Local resolver still caches old Route 53 records / old site IP. Will clear with TTL or a DNS flush on Ari’s machine. |
| Production smoke tests | ⏳ | Waiting for www SSL + DNS cache clear before running on `https://www.texashomeoutlet.com` |
| Resend DNS records | ⏳ | API key bound; DKIM/SPF/MX records must be added to Turbify DNS and domain verified in Resend dashboard |
| Analytics pixels (GA4/Meta/TikTok) | ⏳ | Code ready; IDs not provided |
| Sentry DSN | ⏳ | Not provided |
| Social publishing tokens (TikTok/Meta) | ⏳ | Not provided |
| Notion/Etai webhook URL | ⏳ | Not provided |

---

## 🔧 Commands for the Next Agent

### Check SSL status
```bash
gcloud beta run domain-mappings describe --domain=www.texashomeoutlet.com --region=us-central1 --format="yaml(status.conditions)"
gcloud beta run domain-mappings describe --domain=texashomeoutlet.com --region=us-central1 --format="yaml(status.conditions)"
```

### Verify live site once SSL is ready
```bash
curl -s -o /dev/null -w "www: %{http_code}\n" https://www.texashomeoutlet.com/
curl -s -o /dev/null -w "apex: %{http_code}\n" https://texashomeoutlet.com/
curl -s -o /dev/null -w "healthz: %{http_code}\n" https://www.texashomeoutlet.com/healthz/
```

### Run smoke tests
```bash
python3 scripts/production_smoke.py --base-url https://www.texashomeoutlet.com
```

### Detailed health (needs admin PIN)
```bash
ADMIN_TOKEN=$(curl -fsS -X POST https://www.texashomeoutlet.com/api/admin/verify \
  -H "Content-Type: application/json" \
  -d '{"pin":"PIN"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
curl -fsS https://www.texashomeoutlet.com/healthz/detailed \
  -H "X-Admin-Token: $ADMIN_TOKEN" | python3 -m json.tool
```

---

## 📝 Key Files Created/Modified

- `docs/CLAUDE_FINAL_PUSH_PROMPT.md` — primary handoff for Claude Opus
- `docs/CUT_OVER_ULTIMATE_PROMPT.md` — earlier detailed cutover prompt
- `docs/EVENING_CUTOVER_CHECKLIST.md` — evening cutover checklist
- `docs/CUTOVER_STATUS_REPORT_20260613.md` — this file
- `frontend/public/robots.txt`
- `frontend/public/llms.txt`
- `llms.txt`
- `services/telegram/` — Telegram/Mira integration
- `tests/telegram/` — Telegram tests
- `.env.example`
- `main.py`

---

## 🚨 Rollback (keep ready)

In Turbify DCP (`https://dcp.turbify.com/dcp/texashomeoutlet.com/dns`):

| Record | Type | Value |
|--------|------|-------|
| `texashomeoutlet.com` (`@`) | A | `20.121.124.106` or `52.10.0.211` |
| `www.texashomeoutlet.com` | A | `20.121.124.106` or `52.10.0.211` |

Remove the `www` CNAME if it was added.

---

## 🎯 Next Actions for Claude

1. Wait for `www.texashomeoutlet.com` SSL to show `Ready = True`.
2. Run production smoke tests on the new domain.
3. Walk Ari through Resend domain setup and DNS record addition.
4. Add analytics/pixels once IDs are provided.
5. Wire social publishing tokens once provided.
6. Complete Notion/Etai integration with the real webhook URL.
7. Write `docs/CUTOVER_COMPLETION_REPORT_20260613.md`.

---

*Generated for handoff to Claude Code final push.*
