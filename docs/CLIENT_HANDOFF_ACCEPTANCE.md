# Texas Home Outlet — client handoff acceptance

The storefront is live. Operational handoff is complete only when the client can
access, operate, recover, and pay for the agreed system without relying on Ari's
personal accounts. This checklist records evidence, not a readiness percentage.
An unchecked item is unverified; it does not by itself prove the feature is broken.

## Verified baseline — September 9, 2026

Read-only checks at 23:42 UTC found the canonical storefront serving commit
`77b1c4f551812ca47bda8803af49f726dbab4083`. Cloud Run traffic was 100% on
`project-go-forward-00457-ciz`. `/readyz` reported required prompts, PDFs, and
Firestore healthy. The inventory soft check was **not healthy**: freshness was
unknown, with 46 current homes and 240 catalog floorplans (286 total). The
configured and selected path was Firestore, but provenance reported
`inventory_fallback_chain`; this is not proof of a fresh, Firestore-only catalog.

Passkey status reported persistent Firestore storage and support for both canonical
and legacy relying-party domains. That does not prove each staff member can log in.
No lead, email, appointment, document, credential, DNS, or production setting was
written during these checks. Earlier full release verification passed 24 GET
probes; the four endpoint checks above are the newer bounded observation.

A separate read-only backup check found a daily managed Firestore schedule with
seven-day retention and seven READY backups. The newest snapshot was September 9
at 22:16 UTC. Backup existence does not establish a successful restore rehearsal.

## Client acceptance checklist

Record an owner, date, and private evidence location for each item. Do not put
credentials, customer data, or signed documents in this repository.

| Acceptance item | Required evidence | Current status |
|---|---|---|
| Named business and technical owners | Client names a primary and backup operator, escalation contact, and scope of ongoing support | Unverified |
| Client-controlled accounts and billing | Client signs into domain/DNS, business email, Cloud project, repository, storage, and each enabled provider; confirms billing and recovery access | Unverified; personal project budget is separate |
| Staff login and recovery | Each intended staff member signs in at `https://www.texashomeoutlet.com`, registers a canonical-domain passkey, and confirms an approved recovery method | Persistent passkeys observed; per-person acceptance pending |
| Current inventory | Staff reconciles active homes, availability, serials, approved prices and photos against the lot; operator records the approved source timestamp and checks it is exposed correctly | 46 current entries; freshness unknown |
| Lead and appointment workflow | With approval, submit identified test records; staff finds them in CRM, assigns follow-up, and checks notification and confirmation receipt | Source and public routes exist; end-to-end acceptance pending |
| Email delivery | Verify sender/domain and allowed key scope; approved controlled message has provider acceptance and recipient receipt; record failures without customer content | Configuration alone does not prove delivery |
| Document workflow | Staff uses synthetic customer data to generate agreed new/used packets, reviews every field and page, downloads, and confirms the signing process | Required PDF files present; client packet acceptance pending |
| Backup and restore | Verify recent successful backups and demonstrate a restore into an isolated destination; document validation, recovery time and approved cutover procedure | Daily schedule and recent READY backups verified; restore rehearsal pending |
| Monitoring and support | Named operator can read errors, identify the serving SHA, receive approved alerts, and follow the incident runbook | Health and serving identity verified; client access/alert acceptance pending |
| Optional integrations | Explicitly accept or exclude e-sign, social publishing, inbound automation, ads, analytics, and third-party bridges | No new activation is authorized by this checklist |

Email verification must distinguish missing configuration, insufficient scope for
a diagnostic endpoint, provider throttling/outage, send acceptance, and recipient
delivery. A sending-only key may not read provider domain metadata; do not broaden
production key permissions merely to make a read probe pass. Never infer delivery
from an environment variable or a successful domain-list request.

Preserve existing legacy passkeys until affected staff have working canonical
credentials and recovery access. Do not revoke keys or retire old domains based
only on the public status count.

## Operating and release procedure

Staff starts with [the walkthrough](CLIENT_WALKTHROUGH.md). Check leads and
appointments in CRM while email acceptance is pending. Reconcile availability
before promising a home; do not use a green overall readiness result as proof that
inventory is current. Use downloaded, reviewed PDFs and the agreed signing process
until any e-sign rollout has its own acceptance evidence.

Engineering uses a branch, explicit-path staging, and a PR with all applicable
checks green. Main builds and verifies a candidate at zero traffic; it does not
make that revision serve customers. Follow [the runbook](RUNBOOK.md) to prepare an
exact commit/revision promotion, rollback target, and verification plan before
requesting approval. No direct production deploy, traffic change, DNS change,
credential rotation, paid activation, or outward message follows implicitly from
this document.

Read-only status checks:

```bash
curl -fsS https://www.texashomeoutlet.com/healthz/
python3 scripts/production_smoke.py --base-url https://www.texashomeoutlet.com
curl -fsS https://www.texashomeoutlet.com/readyz \
  | jq '{ready, inventory: .checks.inventory}'
gcloud run services describe project-go-forward --project=tho-ai-agent \
  --region=us-central1 --format='json(status.traffic,status.latestReadyRevisionName)'
```

The default smoke sends GETs only. Its optional POST flags need authorization for
their concrete test effects. Treat older go-live scorecards and stacked PR plans
as historical context, not instructions to activate services or merge a backlog.

## Sign-off record

Business owner: pending. Technical operator and backup: pending. Accepted feature
scope and exclusions: pending. Evidence location: pending. Handoff date and support
end date: pending. Record these after the client review; do not mark the entire
project handed off from automated tests alone.
