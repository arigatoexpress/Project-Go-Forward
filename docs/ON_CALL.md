# On-Call Guide — Texas Home Outlet

**Scope:** live production service `project-go-forward` (Cloud Run, `tho-ai-agent`, `us-central1`).  
**Canonical production URL:** `https://www.texashomeoutlet.com`  
**Cloud Run direct URL:** `https://project-go-forward-trgi34bxuq-uc.a.run.app`  
**Companion docs:**
- `docs/RUNBOOK.md` — rollback, incident triage, secret rotation
- `docs/SLO.md` — targets, error budgets, restore checklists
- `docs/FIRESTORE_RESTORE_RUNBOOK.md` — database recovery
- `docs/READ_TIMEOUTS.md` — timeout values and why they matter

This guide assumes the **Ops bootstrap workflow** (`.github/workflows/ops-bootstrap.yml`) has run and created the uptime check, 5xx alert, Firestore backup schedule, and `THO_API_KEY` secret.

---

## 1. Severity definitions

| Severity | Meaning | Response | Examples |
|---|---|---|---|
| **P1 — Critical** | Customer-facing revenue stop or data-loss risk | Page/phone; drop what you are doing | Site down (`/healthz/` failing), checkout/contact broken during business hours, Firestore data corruption, uncontrolled spend spike |
| **P2 — High** | Major feature degraded; workaround exists | Respond within 30 min | Admin CRM unavailable, inventory API slow (>2 s p95), email alerts silent, lead capture failing silently |
| **P3 — Normal** | Non-urgent bug, observability gap, cleanup | Next business day | Analytics dashboard shows mock data, a 404 on a secondary page, missing docs, certificate expiring in >14 days |

**Business hours** (from `config.yaml`):
- Mon–Fri 9:00 AM – 6:00 PM CT
- Sat 9:00 AM – 5:00 PM CT
- Sun Closed

A P1 outside business hours still pages because the storefront is 24/7 online revenue.

---

## 2. On-call checklist (first 5 minutes)

1. **Acknowledge the page** in your incident channel.
2. **Confirm the symptom** from two vantage points:
   ```bash
   curl -fsS https://www.texashomeoutlet.com/healthz/ | python3 -m json.tool
   curl -fsS https://project-go-forward-trgi34bxuq-uc.a.run.app/healthz/ | python3 -m json.tool
   ```
3. **Check the version** returned by `/healthz/` against the last known good commit.
4. **Look at recent deploys:** GitHub → Actions → `deploy.yml`.
5. **Open Cloud Run logs** for the serving revision.

If `/healthz/` is failing or the bad commit is obvious → **rollback first** per `docs/RUNBOOK.md` §2, then investigate.

---

## 3. Alert-to-runbook mapping

| Alert you might see | Likely cause | First action | Full runbook |
|---|---|---|---|
| `THO uptime failure` | Site unreachable | Rollback if a deploy just landed | `docs/RUNBOOK.md` §2, §3 |
| `THO 5xx burst` | Bad deploy or dependency failure | Rollback; check logs | `docs/RUNBOOK.md` §2, §3 |
| `lead_storage_failed` (logs-based, set up post-#164) | Firestore write rejected | Check Firestore quota/indexes; verify fallback capture | `docs/FIRESTORE_RESTORE_RUNBOOK.md` |
| Admin PIN rejected for everyone | `ADMIN_PIN_HASH` missing/rotated | Verify secret binding | `docs/PIN_ROTATION_RUNBOOK.md` |
| `/api/v1/inventory` returns 503 "API key auth not configured" | `THO_API_KEY` not attached | Re-attach secret | `docs/RUNBOOK.md` §3.4 |
| Certificate expiry warning | Domain mapping cert aging | Verify mapping; Google-managed certs auto-renew unless DNS is broken | `docs/DNS_CUTOVER_RUNBOOK.md` |

---

## 4. Escalation

| Step | Who | When |
|---|---|---|
| 1. On-call engineer | Ari / repo owner | Immediate |
| 2. GCP project owner | Ari (`arigatoexpress`) | P1 >15 min unresolved |
| 3. Vendor support | Google Cloud Support (if subscribed) | P1 infra issue >30 min |

Never escalate by posting secrets, PINs, or tokens.

---

## 5. Incident communication

- **Internal:** update the incident thread with: time detected, symptom, version SHA, suspected cause, rollback status.
- **Customer-facing:** if the site is hard-down >15 min, consider a brief status message. Do not publish root cause until confirmed.
- **Post-incident:** open a GitHub issue with timeline, root cause, and follow-up items. If a rollback happened, the bad commit must be reverted or fixed via PR before restoring `--to-latest`.

---

## 6. Shift handoff template

```text
THO On-call handoff — YYYY-MM-DD HH:MM CT
- Open incidents: (none / #xxx)
- Deploys since last shift: (SHA list)
- Alerts fired: (list)
- Outstanding P3s: (list)
- Action required: (none / see items)
```

*Keep handoffs in the same incident channel; do not put secrets in them.*
