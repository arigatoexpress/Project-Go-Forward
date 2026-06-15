# Service Level Objectives — Texas Home Outlet

**Scope:** public customer-facing app served by Cloud Run service `project-go-forward`.  
**Companion docs:** `docs/ON_CALL.md`, `docs/RUNBOOK.md`, `docs/FIRESTORE_RESTORE_RUNBOOK.md`, `docs/READ_TIMEOUTS.md`.

These SLOs are **operator targets**, not external SLAs. They tell us when to stop feature work and prioritize reliability.

---

## 1. SLO summary

| Objective | Target | Measurement window | Why |
|---|---|---|---|
| Availability | 99.9% | 30-day rolling | A dealer storefront that is down loses leads and appointments |
| Latency (p95) | < 1 s for public pages | 30-day rolling | Buyers bounce on slow pages |
| Latency (p99) | < 2 s for public pages | 30-day rolling | Headroom for slow 3G and cache misses |
| Error rate | < 0.1% 5xx on customer paths | 30-day rolling | Every 5xx is a potentially lost customer |
| Lead capture durability | 99.99% | 30-day rolling | A lost lead is lost revenue; `lead_storage_failed` events count as failures |
| Email deliverability | > 95% accepted by provider | 30-day rolling | Appointment confirmations and lead alerts must reach the team |

**Customer paths** = `/`, `/inventory`, `/inventory/*`, `/contact`, `/api/contact`, `/api/appointments/*`, `/api/feedback`.

---

## 2. Error budget

- **Availability budget:** 0.1% of 30 days ≈ 43 minutes downtime.
- **Latency budget:** 5% of requests may exceed p95 target.
- **Error budget:** 0.1% of customer-path requests may 5xx.

When a 30-day budget is **>50% consumed**, the team reviews recent deploys and risky changes.  
When a 30-day budget is **>75% consumed**, freeze non-critical deploys until the burn rate drops.  
When a 30-day budget is **>100% consumed**, declare a reliability sprint and hold deploys except hot-fixes.

---

## 3. Measuring the SLOs

### 3.1 Cloud Monitoring (recommended)

Create a custom Cloud Monitoring dashboard with these widgets:

```text
Availability:
  filter: metric.type="run.googleapis.com/request_count"
          resource.type="cloud_run_revision"
          resource.label.service_name="project-go-forward"
  good:   response_code_class != "5xx"
  total:  all request_count

Latency p95:
  filter: metric.type="run.googleapis.com/request_latencies"
          resource.type="cloud_run_revision"
          resource.label.service_name="project-go-forward"
  aligner: ALIGN_PERCENTILE_95

Error rate:
  filter: metric.type="run.googleapis.com/request_count"
          resource.labels.response_code_class="5xx"
```

### 3.2 Local smoke / load tests

Use the repo harnesses:

```bash
# Read-only public smoke
.venv/bin/python scripts/production_smoke.py --base-url https://www.texashomeoutlet.com

# Load test: 20 rps × 5 min, p95 < 1 s, zero 5xx
.venv/bin/python scripts/load_test.py --base-url https://www.texashomeoutlet.com --rps 20 --duration 300

# Staging E2E (requires STAGING_URL and ADMIN_PIN)
STAGING_URL=https://project-go-forward-trgi34bxuq-uc.a.run.app ADMIN_PIN=xxxx .venv/bin/python -m pytest tests/e2e/test_staging_smoke.py -q
```

---

## 4. SLO restore checklist

Use this after an incident that consumed error budget.

### 4.1 Immediate (during the incident)

- [ ] Roll back traffic to the last known-good revision (`docs/RUNBOOK.md` §2).
- [ ] Confirm `/healthz/` is green from two networks (home, mobile hotspot).
- [ ] Stop the customer bleed; data integrity comes next.

### 4.2 Short-term (within 24 hours)

- [ ] Write an incident timeline in a GitHub issue.
- [ ] Verify no customer data was lost: compare Firestore document counts / key lead IDs against the most recent daily backup.
- [ ] If any `lead_storage_failed` events fired, re-ingest or manually reconcile those leads.
- [ ] Re-run smoke and load tests against the restored revision.

### 4.3 Medium-term (within 1 week)

- [ ] Update this SLO doc with the measured impact (minutes down, requests failed, budget consumed).
- [ ] If a 30-day error budget was >50% consumed, schedule a reliability review.
- [ ] Add or tighten an alert that would have caught the incident faster.
- [ ] Merge a regression test or monitoring fix that prevents recurrence.

### 4.4 Before declaring "all clear"

- [ ] Customer-path smoke passes.
- [ ] Load test passes (20 rps, p95 < 1 s, zero 5xx).
- [ ] No `lead_storage_failed` events in the last hour.
- [ ] On-call handoff notes written.

---

## 5. SLO exclusions

Planned maintenance, Google Cloud outages outside our control, and DNS propagation windows are **not** counted against the error budget if they are documented in the incident issue. Agent-caused corruption from ungated edits **is** counted.

---

## 6. Revision history

| Date | Change | Author |
|---|---|---|
| 2026-06-15 | Initial SLO + restore checklist | runbooks-oncall-slo-restore |
