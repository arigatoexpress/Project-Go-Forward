# Read Timeouts & Deadline Guide — Texas Home Outlet

**Scope:** all timeouts and deadlines used by the THO production app and its operators.  
**Companion docs:** `docs/RUNBOOK.md`, `docs/ON_CALL.md`, `docs/SLO.md`, `docs/FIRESTORE_RESTORE_RUNBOOK.md`.

Timeouts exist to fail fast, free resources, and keep the app responsive. A missing or too-long timeout lets a single slow dependency wedge the whole instance.

---

## 1. Timeout inventory

| Layer | Timeout | Where | Why |
|---|---|---|---|
| Cloud Run request | 300 s (platform max) | Cloud Run default | Hard ceiling; most endpoints should finish far sooner |
| Health probe | 10 s | Ops bootstrap uptime check | `/healthz/` must stay fast; a slow probe causes false alarms |
| Public API / httpx | 30 s | `tests/e2e/test_staging_smoke.py`, `scripts/load_test.py` | Tolerates cold starts without hanging forever |
| AI agent run | `AI_RUN_TIMEOUT` | `main.py` | Caps Gemini/ADK response generation |
| DocuSeal client | 15 s / 30 s / 60 s | `docuseal_service.py` | Short for status, longer for template upload |
| Partner webhooks | configurable, default 10 s | `tools/partner_webhooks.py` | Do not let a slow partner stall the caller |
| Redis connect | 2 s | `caching.py` | Fail fast to local-cache fallback |
| External media fetch | 15–30 s | `tools/inventory_*.py`, `tools/video_generator.py` | Image/video downloads should not block the request loop |
| Legacy site crawler | `REQUEST_TIMEOUT_SECONDS` | `tools/legacy_site_crawler.py` | One-off tool, not customer-facing |

---

## 2. Firestore / gRPC timeouts

`database/firestore_client.py` currently uses the default Firestore client timeouts. The observed failure mode is a **hanging Firestore/gRPC call inside an async endpoint** that wedges the instance's event loop (noted 2026-06-10 in local testing with `/api/appointments/slots`).

### Recommended guardrails

Set these env vars on Cloud Run if Firestore latency becomes problematic:

```bash
# Client-side deadline for Firestore RPCs (milliseconds)
FIRESTORE_TIMEOUT_MS=10000

# DeadlineExceeded / UNAVAILABLE retry behavior is handled by the client library
```

Apply them in `database/firestore_client.py`:

```python
from google.api_core import retry as google_retry
from google.api_core.exceptions import DeadlineExceeded, ServiceUnavailable

_FIRESTORE_TIMEOUT = float(os.environ.get("FIRESTORE_TIMEOUT_MS", "10000")) / 1000.0
_FIRESTORE_RETRY = google_retry.Retry(
    predicate=google_retry.if_exception_type(DeadlineExceeded, ServiceUnavailable),
    initial=1.0,
    maximum=10.0,
    multiplier=2.0,
    deadline=30.0,
)
```

Then wrap read/write calls with:

```python
doc_ref.get(timeout=_FIRESTORE_TIMEOUT, retry=_FIRESTORE_RETRY)
```

> **Note:** This is a documented recommendation, not a source change in this PR. The actual code change touches `database/firestore_client.py` and `database/models.py`, which are owned by another agent. Implement only after coordination.

---

## 3. Timeout triage during an incident

| Symptom | Likely timeout issue | Action |
|---|---|---|
| `/healthz/` slow but eventually 200 | Probe or dependency warmup | Check Cloud Run cold-start latency; raise min instances |
| `/api/appointments/slots` hangs then all requests on that instance hang | Firestore/gRPC deadlock | Increase min instances; set `FIRESTORE_TIMEOUT_MS`; recycle revision |
| 5xx burst after a deploy | New code missing a timeout | Rollback; add timeout in code |
| Load test p95 > 1 s | Downstream dependency slow | Identify slow endpoint from Cloud Run logs; add/adjust timeout or cache |

---

## 4. Load-test timeout alignment

`scripts/load_test.py` uses a 30 s request timeout and fails if **p95 > 1 s** or any 5xx occurs. This directly enforces the SLO latency target (`docs/SLO.md`).

Run it after any timeout change:

```bash
.venv/bin/python scripts/load_test.py \
  --base-url https://www.texashomeoutlet.com \
  --rps 20 --duration 300 --output /tmp/slo-load-test.json

cat /tmp/slo-load-test.json
```

---

## 5. Adding a new timeout

When you add a new external call, follow this order:

1. **Pick the smallest value that allows real work.** Start aggressive; loosen only with data.
2. **Make it env-configurable.** `TIMEOUT_SECONDS = int(os.environ.get("MY_TIMEOUT_SECONDS", "5"))`.
3. **Log the timeout breach.** Emit a structured log with `event="timeout_breach"`, `dependency`, and `timeout_seconds`.
4. **Test it.** Add a unit test that mocks the slow path and verifies the call fails fast.
5. **Document it here.** Update this table and the incident triage section.

---

## 6. Timeout values by operation

Use this quick reference during incidents:

```text
Health probe:            10 s
Public API client:       30 s
Admin API client:        30 s
DocuSeal status:         15 s
DocuSeal template:       60 s
Partner webhook:         10 s
Redis connect:           2 s
Media fetch:             15–30 s
Firestore RPC (target):  10 s
AI run:                  AI_RUN_TIMEOUT env
```
