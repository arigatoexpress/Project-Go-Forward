# Live Monitoring Check

A read-only CLI smoke test for the THO Mira bridge, DNS/MX cutover status, and
ops signals.

## Files

- `scripts/live_monitoring_check.py` — monitoring script
- `tests/test_live_monitoring_check.py` — unit tests

## What it checks

| Category | Endpoints |
|---|---|
| Public health | `/healthz/`, `/api/v1/mira/health` |
| Mira bridge (protected) | `/api/v1/mira/system`, `/api/v1/mira/metrics`, `/api/v1/mira/leads/*`, `/api/v1/mira/appointments/summary`, `/api/v1/mira/installations/*`, `/api/v1/mira/feedback/*`, `/api/v1/mira/firestore/collections`, `/api/v1/mira/chat/summary` |
| Ops signals | `/api/v1/github/mira/status`, `/api/v1/cutover/dns-status`, `/api/v1/cutover/mx-status` |

It also warns when:

- The count of leads in `new` status exceeds the configured threshold.
- DNS or MX cutover endpoints report `overall_ready: false`.

## Usage

```bash
# Public endpoints only
python scripts/live_monitoring_check.py

# Full check (requires THO_API_KEY or THO_API_KEY_MIRA)
python scripts/live_monitoring_check.py --base-url https://www.texashomeoutlet.com --api-key "$THO_API_KEY"
```

Environment variables:

- `THO_BASE_URL` — default base URL
- `THO_API_KEY` — partner API key for protected endpoints

Exit codes:

- `0` — all probes passed
- `1` — one or more probes failed
