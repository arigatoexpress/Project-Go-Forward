# Texas Home Outlet — Site Walkthrough & Handoff Guide

**Audience:** THO staff (day-to-day users) and any developer taking the project
forward.
**Live site:** https://tho.sapphirealpha.xyz
**Status as of 2026-06-04:** Deployed and healthy on Cloud Run, running the
latest `main`. Production smoke checks pass; the contract/Document Center test
suite passes (41 focused tests green).

This guide has two parts:

1. **Part A — Using the site** (for THO staff: Ben, Lee, Celeste, Mark).
2. **Part B — Technical handoff** (for a developer continuing the work).

---

## Part A — Using the Site

### 1. Logging in

Public visitors see the customer site (AI chat + home browsing) with no login.
**Staff tools** (Document Center, CRM, Analytics, Marketing) are behind an
**admin PIN**.

1. Go to https://tho.sapphirealpha.xyz.
2. Open any staff page (e.g. **Documents** or **CRM**). You'll be prompted for
   the admin PIN.
3. Enter the PIN. You get a session token that keeps you logged in on that
   browser. (The PIN is shared by phone / password manager, never by email —
   see the PIN Rotation Runbook for how to change it.)

### 2. The AI Assistant (customer-facing)

The chat box on the home page is a 24/7 assistant that answers customer
questions about homes, pricing, hours, and can capture leads and help book
appointments. It pulls live inventory and routes between a Sales agent and a
Service agent. No staff action needed — it runs itself and emails the team when
a new lead or appointment comes in.

### 3. Browsing Inventory

The **Inventory** page lists every home with photos and specs (beds, baths,
sq ft). This is the same data the AI assistant and the Document Center pull
from, so a home you pick for a contract is the real listing.

### 4. Document Center — the FastContracts replacement

This is the part that replaces FastContracts. It generates **filled, ready-to-
sign Texas manufactured-home documents** — 63 templates total (TMHA, TDHCA,
State, and internal disclosures) and 5 prebuilt packets.

It's a 4-step wizard:

1. **Customer Info** — buyer (and co-buyer) name, contact, SSN/DOB, address,
   employment, marital status. Drafts auto-save in your browser, so you won't
   lose work if you step away.
2. **Choose Home** — pick from live inventory, or enter home details manually
   (manufacturer, model, year, serial/label numbers, sections, dimensions,
   wind zone, weights). New vs. used is a toggle and changes which packet
   applies.
3. **Pick Documents** — choose individual forms or a whole packet:
   - **Standard Closing (New)** — 9 docs
   - **Used Home Closing** — 11 docs
   - **Full New Home Closing** — 54 docs
   - **Full Used Home Closing** — 56 docs
   - **Credit Application Package** — 4 docs
4. **Review & Generate** — generates the PDFs (or one merged packet) and lets
   you download them. The seller is filled as the registered legal entity
   **Prosperity Acquisitions, Inc. dba Texas Home Outlet** with RBI license
   35248, so documents are accepted as filed.

There's also a **trade-in calculator** for pre-owned deals that values the
trade by condition and applies it to the down payment.

### 5. CRM — Leads, Deals, Appointments

- **Leads** come in from the website/chat and the contact form; the whole team
  is emailed automatically. You can view, update, and export leads to CSV.
- **Deals** track a sale end to end. A deal stores all the buyer/home/financial
  data once, then you can **generate any document or full packet straight from
  the deal** — no re-typing.
- **Appointments** can be booked and confirmed; confirmation emails go out when
  email is configured (see Part B).

### 6. Marketing / Ad Studio

AI-assisted marketing content: ad scripts, trending ideas, scheduling, and
performance analytics, with inventory context baked in so ads reference real
homes.

### 7. Analytics

Lead stats, conversion tracking, document activity, and inventory analytics for
the team.

---

## Part B — Technical Handoff (for a developer)

### Stack at a glance

| Layer | Technology |
|-------|------------|
| Backend | FastAPI (Python 3.11), Google ADK, Gemini 2.5 Flash via Vertex AI |
| Frontend | React 19 + Vite + Tailwind, built to `frontend/dist/` |
| Data | Google Firestore (primary), JSON fallback, sample data last resort |
| PDFs | `pypdf` form-fill driven by `config/field_map.json` |
| E-sign | DocuSeal (optional, env-gated) |
| Email | Resend (optional, env-gated) |
| Deploy | Single Docker container on Cloud Run (project `tho-ai-agent`, region `us-central1`) |
| Hosting | Canonical URL `tho.sapphirealpha.xyz`; auto-deploys from `main` |

### Repository map

See `CLAUDE.md` (root) for the authoritative directory layout and conventions.
The most important pieces:

- `main.py` — FastAPI app, **all** API endpoints, auth, middleware.
- `config.yaml` — single source of truth for business config.
- `config/field_map.json` — **the** registry mapping every PDF template and
  packet to its fields. Never hardcode PDF field names in Python.
- `tools/document_engine.py` / `tools/document_tools.py` — the PDF fill engine.
- `frontend/src/pages/DocumentCenter.jsx` — the contract wizard UI.
- `database/firestore_client.py` — Firestore CRUD.
- `docuseal_service.py` — e-signature orchestration.
- `email_service.py` — transactional email via Resend.

### Run it locally

```bash
# Frontend
cd frontend && npm install && npm run build

# Backend
pip install -r requirements.txt
export ADMIN_PIN_HASH="$(python3 -c 'import hashlib;print(hashlib.sha256(b"1234").hexdigest())')"  # local-only PIN
python main.py            # serves on :8080

# Tests (contract engine etc.)
python -m pytest tests/
```

### Deploy

The repo **auto-deploys from `main`**. Agent/feature branches should open a
draft PR and wait for a human merge unless a direct push is explicitly
authorized. Manual deploy if ever needed:

```bash
gcloud run deploy project-go-forward --source . --region us-central1
```

### Health & smoke (read-only, safe to run anytime)

```bash
curl -fsS https://tho.sapphirealpha.xyz/healthz/        # liveness + deployed commit
python3 scripts/production_smoke.py --base-url https://tho.sapphirealpha.xyz
```

### Secrets / environment (Cloud Run, via Secret Manager)

| Variable | Purpose | Required? |
|----------|---------|-----------|
| `ADMIN_PIN_HASH` | SHA-256 of the staff admin PIN | **Yes** |
| `ADMIN_SESSION_SECRET` | Session token signing (derived from PIN hash if unset) | Recommended |
| `GOOGLE_GENAI_USE_VERTEXAI=TRUE` | Use Vertex AI for Gemini | Yes |
| `RESEND_API_KEY` | Transactional email (lead/appointment/deal emails) | For email |
| `NOTIFICATION_EMAIL` | Comma-separated staff alert recipients | For email |
| `DOCUSEAL_API_URL` / `DOCUSEAL_API_TOKEN` / `DOCUSEAL_WEBHOOK_SECRET` | E-signature | For e-sign |

Runbooks live in `docs/PRODUCTION_READINESS.md` (deploy/email/PIN) and
`docs/PIN_ROTATION_RUNBOOK.md`.

### Guardrails (do not violate)

- **Never modify the PDF templates** in `tho_documents/` — they're regulatory
  originals.
- **Never log or send PII** (SSN, financial account numbers) to the LLM — use
  `tools/pii_guard.py`.
- Keep the legacy `/api/documents/sales-contract` endpoint working.
- Add new endpoints in `main.py` **above** the SPA catch-all route at the bottom.

### Known limitations (from `SYSTEM_STATUS.md`)

- Cold-start latency of ~5–10s on first hit (Cloud Run scale-to-zero).
- Chat history is session-only (no cross-refresh persistence).
- Inventory sync is run periodically via the scraper tool, not real-time.

---

*Maintained alongside `docs/PRODUCTION_READINESS.md`. Last updated 2026-06-04.*
