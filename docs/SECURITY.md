# THO App — Security & Principle of Least Privilege

## 1. Auth model

### Admin session (internal users)

- **Mechanism**: stateless HMAC-SHA256 JWT ([main.py:217](../main.py)–254).
- **Token**: 24 bytes = 8-byte big-endian uint64 expiry + 16-byte HMAC tag. Base64 → ~32-char string.
- **Secret derivation**: `SHA256(f"sapphire-jwt-{ADMIN_PIN_HASH[:16]}")` — tied to the admin PIN hash.
- **TTL**: default 2 hours, tunable via `ADMIN_TOKEN_TTL`.
- **Header**: `X-Admin-Token`. `require_admin()` dependency on protected routes.
- **Revocation**: none (stateless). To invalidate all tokens, rotate `ADMIN_PIN_HASH`.

### External API (partners, integrations)

Scaffolding exists in worktree branches but is **not on `main`**. See [INTEGRATION_NOTION.md](INTEGRATION_NOTION.md) §4.

Two patterns drafted:

| Pattern | Env var | Header | Scope |
|-----|-----|-----|-----|
| `/api/v1/*` Bearer | `THO_API_KEY` | `Authorization: Bearer <key>` or `X-API-Key` | CRUD on customers/inventory/leads + webhooks + stats |
| `/api/v1/*` n8n-style | `N8N_API_TOKEN` | `Authorization: Bearer <token>` | automation workflows |

## 2. Secret hygiene — current state and plan

### Current (Cloud Run env)

| Secret | Storage | Status |
|-----|-----|-----|
| `PII_ENCRYPTION_KEY` | Secret Manager (`secretKeyRef`) | ✓ correct |
| `ADMIN_PIN_HASH` | Secret Manager (`admin-pin-hash`) | ✓ correct |
| `RESEND_API_KEY` | Secret Manager (`resend-api-key`) | ✓ bound in `deploy.yml` as of 2026-08-31. Prerequisite: the `resend-api-key` secret must exist, or the deploy step fails. Rotate with `gcloud secrets versions add resend-api-key --data-file=-`; the service picks up `:latest` on the next revision. |
| `N8N_API_TOKEN` | plaintext env | ⚠️ move to Secret Manager + rotate (exposed in prior tooling output) |
| `THO_API_KEY` | plaintext env | ⚠️ move to Secret Manager + rotate (exposed in prior tooling output) |
| `GOOGLE_APPLICATION_CREDENTIALS` | not used in Cloud Run (uses metadata server) | ✓ correct |

### Plan

1. Rotate `THO_API_KEY` and `N8N_API_TOKEN` — both have appeared in tool output / transcripts.
2. Create or update the Secret Manager records named `resend-api-key`, `n8n-api-token`, and `tho-api-key`.
3. Update Cloud Run service with `secretKeyRef` bindings.
4. Remove plaintext env vars.
5. Grant Cloud Run's runtime service account `roles/secretmanager.secretAccessor` per secret (not project-wide).

Done via:

```bash
# Example for one secret
echo -n "<new-key>" | gcloud secrets create tho-api-key --data-file=- --project=tho-ai-agent
gcloud run services update project-go-forward \
  --region=us-central1 --project=tho-ai-agent \
  --update-secrets=THO_API_KEY=tho-api-key:latest \
  --remove-env-vars=THO_API_KEY
```

## 3. PII handling

### Fields classified as PII

- Customer: `full_name`, `phone`, `email`
- Deal: `buyer_ssn`, `co_buyer_ssn`, `buyer_phone`, `co_buyer_phone`, `buyer_email`, `co_buyer_email`, `reference1_phone`, `reference2_phone`
- Any DOB or financial account numbers if added

### Guardrails in code

| Guardrail | Location | What it does |
|-----|-----|-----|
| `pii_guard.py` | `tools/pii_guard.py` | Strip PII from log statements and LLM prompts |
| `_strip_pii_from_deal` | `main.py` | Redact SSNs from Deal responses when not authorized |
| `_strip_ssn_from_customer` | `main.py` | Redact SSN from Customer responses |
| `form_extraction.py` filter | `tools/form_extraction.py` | Never ship SSN/income/DOB to Gemini |

### Field-level encryption

`PII_ENCRYPTION_KEY` (Secret Manager) powers symmetric encryption for the most sensitive fields at rest. Coverage of that key across the full Deal/Customer model should be audited — grep for usages.

## 4. Transport security

- **HTTPS-only**: Cloud Run enforces TLS; HTTP redirects to HTTPS (301).
- **HSTS**: 1-year enforcement header.
- **CSP**: self + inline styles (Tailwind) + Matterport 3D iframe + CDN image hosts.
- **X-Frame-Options**: `DENY` (no embedding).
- **X-Content-Type-Options**: `nosniff`.

## 5. Rate limiting

- Per-IP in-memory counter, default 60 RPM (`RATE_LIMIT_RPM`).
- `/health` and `/healthz` exempt.
- 429 on exceed.
- Max request body: 1 MB (`MAX_REQUEST_BODY_BYTES`).

## 6. Principle of least privilege — access matrix

Who gets what, by role. Apply when provisioning any new access.

| Actor | GCP | Firestore | GitHub repo | Admin UI | Integration API | Secrets |
|-----|-----|-----|-----|-----|-----|-----|
| **Ari (owner)** | Owner on both projects | full | admin | full | full | read |
| **Celeste (founder)** | Project Viewer on `tho-ai-agent` | read via UI only | read (optional) | full (has PIN) | n/a | none |
| **Mark / Ben (partners)** | Project Viewer on `tho-ai-agent` | read via UI only | none | full (has PIN) | n/a | none |
| **Etai (Notion contractor)** | **none** | **none** | **none** | **none** | Bearer `THO_API_KEY` scoped to `/api/v1/*` | **none directly** — only the integration key |
| **Cloud Run runtime SA** | `tho-ai-agent` workload identity | read/write via metadata server | n/a | n/a | n/a | `secretAccessor` per secret |
| **GitHub Actions SA** | WIF principal | n/a (tests run against JSON fixtures) | n/a | n/a | n/a | no human-readable access |

### Etai's access in detail

What he **gets**:

1. A scoped `THO_API_KEY` (rotated, unique to him) — grants read+write on `/api/v1/customers`, `/api/v1/inventory`, `/api/v1/leads`, `/api/v1/stats`, `/api/v1/webhooks/notify`.
2. This documentation package (`docs/`), which tells him field names, entity shapes, ID conventions, integration points.
3. An invite to the shared Notion workspace (Celeste provides).
4. Google Drive access to a specific per-deal parent folder, if Mark approves — **not** the entire Drive.

What he **does not get**:

- Direct Firestore access (not even read).
- The Cloud Run console.
- GitHub repo access.
- The admin PIN.
- GCS bucket credentials.
- Any production secrets beyond his scoped API key.
- Customer PII in bulk — API responses will be redacted unless explicitly whitelisted for a given integration flow.

### Key rotation cadence

| Secret | Rotation | Trigger |
|-----|-----|-----|
| `ADMIN_PIN_HASH` | Every 90 days or on partner change | Scheduled + reactive |
| `THO_API_KEY` (Etai) | End of Etai's engagement (within 7 days of final payment) | Reactive |
| `THO_API_KEY` (general) | 180 days | Scheduled |
| `RESEND_API_KEY` | On suspected compromise | Reactive |
| `PII_ENCRYPTION_KEY` | Never rotate without migration plan (re-encrypts stored ciphertext) | With migration |

## 7. Delete-protection & blast-radius

### Immediate safety gaps to close

1. **Firestore `(default)` in `tho-ai-agent` has `DELETE_PROTECTION_DISABLED`.** Enable:

    ```bash
    gcloud firestore databases update --database="(default)" \
      --delete-protection --project=tho-ai-agent
    ```

2. **GCS bucket `tho-secure-documents` should have object versioning + lifecycle retention.** Audit and enable:

    ```bash
    gcloud storage buckets update gs://tho-secure-documents \
      --versioning --project=tho-ai-agent
    ```

3. **`sapphire-479610` is load-bearing for DNS.** Add a note in its description so nobody deletes it:

    ```bash
    gcloud projects update sapphire-479610 \
      --update-labels=do-not-delete=true,role=dns-host \
      --account=aristotlespec@gmail.com
    ```

4. **Cloud Run revision traffic**: keep at least the last 3 revisions for fast rollback. Use `gcloud run services describe` to confirm.

### Backup posture

- **Firestore**: no scheduled export currently. Add a Cloud Scheduler job that runs `gcloud firestore export gs://<backup-bucket>/<ts>/` weekly.
- **GCS generated PDFs**: retention via versioning (recommended above).
- **Code**: GitHub remote (`arigatoexpress/Project-Go-Forward`) is the source of truth.

## 8. Audit

### Structured audit trail (`audit_log.py`)

Sensitive admin and partner actions write a structured record to the Firestore
`audit_log` collection via `log_admin_action(...)` (mutations only — reads are
not logged so the signal isn't drowned out). Each entry carries:

| Field | Source | Notes |
|-----|-----|-----|
| `timestamp` | server UTC ISO8601 | — |
| `actor` | `_audit_actor(request)` | SHA256-prefixed admin-token id (`admin:<12hex>`) or `partner:<8hex>` key fingerprint — never the raw token/key |
| `action` | call site | from `ALLOWED_ACTIONS`; unknown values warn (drift detector) |
| `target_type` / `target_id` | call site | entity kind + id (deal/customer/inventory/lead/crm_task/document/email/session) |
| `ip` | `X-Forwarded-For` first hop | Cloud Run aware |
| `user_agent` | request header | capped to 300 chars |
| `details` | call site | IDs / field-name deltas / counts only — `_sanitize_details` strips any PII-shaped key |

Read the trail at `GET /api/admin/audit-log` (admin-gated, filterable by
actor/action/target/since).

### Instrumented actions

- **Auth**: admin PIN verify (`admin.login`).
- **Deals**: create / update / status transition (`status_from`→`status_to`) / generate-document / generate-packet.
- **Customers**: create / update (admin) and create via partner API (`partner:<fp>` actor).
- **Inventory**: create / update / delete-or-retire / bulk-import / photo upload / reorder / delete.
- **Documents**: generate / generate-batch / sales-contract (legacy) / e-sign send (`document.esign_send`) / e-sign complete / share.
- **Leads & CRM**: lead update (field names only) / CRM task create + update.
- **Email**: custom send — recipient is recorded as a non-reversible SHA256 fingerprint, never the address; subject/body recorded only as lengths.
- **Integration-key usage**: every `/api/v1/*` request is logged (`_log_partner_api_request`) with the key fingerprint (not the key), endpoint, method, caller IP, and auth result.

### What we don't log

- Any PII field value (see §3) — `details` carries field *names*, IDs, counts, and fingerprints only.
- Full request/response bodies.
- Secrets, raw tokens, raw API keys, raw PINs, raw SSNs, or email/phone addresses.

### Future enhancements

- Per-key partner dashboards over the `/api/v1/*` request logs to drive rotation decisions.
- Mirror state-change audit entries into the `activities/` Firestore collection used by PM entities for a unified activity feed.
