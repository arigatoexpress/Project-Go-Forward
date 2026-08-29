# Texas Home Outlet — Site Walkthrough & Handoff Guide

**Audience:** THO staff (day-to-day users) and any developer taking the project
forward.
**Live site:** https://www.texashomeoutlet.com
**Diagnostic alias:** https://tho.sapphirealpha.xyz
**Status as of 2026-08-29:** Deployed and healthy on Cloud Run. Production runs
the last promoted revision; the latest `main` is a healthy zero-traffic
candidate because merges do not automatically promote traffic. Read-only
production smoke passed 33/33 probes; latest CI passed 2,122 backend tests and
260 frontend tests.

This guide has two parts:

1. **Part A — Using the site** (for THO staff: Ben, Lee, Celeste, Mark).
2. **Part B — Technical handoff** (for a developer continuing the work).

---

## Part A — Using the Site

### 1. Logging in (staff)

Public visitors see the customer site (home browsing, AI chat, contact, visit
booking) with no login. **Staff tools** — Documents, CRM, Inventory, Photos,
Ad Studio, Analytics, System Hub, Health, Chat History, Ops Copilot, and the
in-app Guide — are behind staff sign-in.

There are three ways in, all from the lock icon in the top nav (or the
**Admin** link in the footer):

1. **Admin PIN** — the shared staff PIN. Enter it and you're signed in on
   that browser. (The PIN is shared by phone / password manager, never by
   email — see the PIN Rotation Runbook for how to change it.)
2. **Passkey** — the fastest option once set up. Sign in with the PIN once,
   click the key icon in the top nav, and register the device using an
   approved owner email or a `@texashomeoutlet.com` staff email. After that,
   "Sign in with Passkey" (fingerprint / face / device PIN) unlocks the staff
   tools with no PIN. Lost or replaced devices can be revoked from
   **System Hub → Passkey Recovery**.
3. **Email sign-in code** — the fallback if the PIN expired or you're on a
   new device. Click **Email me a sign-in code**, enter your authorized staff
   email, and type the 6-digit code we send.

Sessions expire after a while; if you see "Session expired," just sign in
again — your work in the Document Center is saved in the browser.

### 2. The AI Assistant (customer-facing)

The chat box ("Tex") is a 24/7 assistant that answers customer questions about
homes, pricing, hours, financing, and warranty, can capture leads, and helps
book appointments. It pulls live inventory and routes between a Sales agent
and a Service agent. No staff action needed — it runs itself and emails the
team when a new lead or appointment comes in. You can review what customers
asked in **Chat History** (see §6).

### 3. Browsing Inventory

The **Inventory** page lists every home with photos, specs (beds, baths,
sq ft), pricing, and 3D tours where available. This is the same data the AI
assistant and the Document Center pull from, so a home you pick for a contract
is the real listing.

Staff also get two inventory tools (top nav once signed in):

- **Inventory Manager** — add, edit, or retire homes (statuses: Available,
  Pending, Reserved, Sold, Retired). Dealer cost fields are never shown or
  exposed on the public site.
- **Photos** — pick a home and drop in photos; they appear on the public
  Inventory page. Homes that still need pictures are flagged.

### 4. Document Center — the FastContracts replacement

This replaces FastContracts. It generates **filled, ready-to-sign Texas
manufactured-home documents** — 63 templates total (TMHA, TDHCA, State, and
internal disclosures) and 5 prebuilt packets. The top of the page is a
"Production Document Desk" showing template/packet counts, recently generated
PDFs (re-download any of them), and a readiness badge.

It's a 4-step wizard:

1. **Customer Info** — start a new deal by typing the customer's name, **load
   an existing deal**, or **load from customer records** (FastContract
   migration). Buyer (and co-buyer) name, contact, SSN/DOB, address,
   employment, references, marital status. Drafts auto-save in your browser,
   and the wizard warns you if a duplicate deal already exists.
2. **Choose Home** — pick from live inventory (auto-fills manufacturer, model,
   serial/label numbers, sections, dimensions, wind zone, weights), or enter
   home details manually. New vs. used is a toggle and changes which packet
   applies. Texas Home Outlet is the default installer; switch when a deal
   uses a different licensed installer. For pre-owned deals, the **trade-in
   calculator** values the trade by condition and applies it to the deal.
3. **Pick Documents** — choose individual forms (grouped by TMHA / TDHCA /
   State / Internal) or a whole packet:
   - **Standard Closing Packet (New Homes)** — 8 docs
   - **Used Home Closing Packet** — 10 docs
   - **Full New Home Closing** — 45 docs
   - **Full Used Home Closing** — 47 docs
   - **Credit Application Package** — 3 docs

   The Consumer Disclosure is available in English (MHD 1038) or Spanish
   (MHD 1040) — pick the language per deal. Two legacy Manufactured Home
   Note/Security Agreement templates are deliberately blocked as "not
   production-ready yet" and can't be generated.
4. **Review & Generate** — the wizard flags any missing fields before you
   generate, then produces the PDFs (or one merged packet) to download. The
   seller is filled as the registered legal entity **Prosperity Acquisitions,
   Inc. dba Texas Home Outlet** with RBI license 35248, so documents are
   accepted as filed.

**E-signatures (unavailable):** the DocuSeal e-sign integration is built into
the app, but five deployment attempts failed and the service has no ready
revision. Until a compatible pinned image is verified, deployed, and exercised
end to end, **Send for Signature** remains unavailable. Keep downloading and
wet-signing for now.

### 5. CRM — Leads, Pipeline, Tasks, Email

The CRM is the team's home base, organized into tabs:

- **Leads** — everything that comes in from the website/chat and the contact
  form; the whole team is emailed automatically. View, update status (with
  first-response-time tracking), email a lead directly, and export to CSV.
- **Pipeline (Deals)** — track a sale end to end (pending → approved →
  contract → funded → complete). A deal stores all the buyer/home/financial
  data once, and you can **generate the Sales Contract, Consumer Disclosure,
  Warranty, Homestead, or a full closing packet straight from the deal** — no
  re-typing. **Send for Signature** lives here too (e-sign unavailable, see
  §4). Customers get a secure document link for their deal that verifies them
  with the phone number on their application before showing any paperwork.
- **Tasks** — to-dos for follow-ups, with pending/done tracking.
- **Appointments** — everything booked through the site, in one list.
- **Email Log** — every email the system has sent.
- **Reply Drafts** — AI-drafted replies to inbound customer emails, ready for
  a human to review and send.
- **Customers** — the customer records behind deals and documents.
- **Reviews** — review-request workflow (visible when the review link is
  configured).

**Email templates:** when emailing a lead you can start from a ready-made
template (Welcome, Follow-up, Appointment Follow-up, Price & Availability) and
edit before sending. Charts for the lead funnel, lead sources, and customer
analytics are built into the dashboard.

### 6. Chat History

**Chat History** lets staff browse and search every customer conversation with
Tex — useful for seeing what customers ask about, picking up a lead's context
before calling them back, and checking what the AI told someone.

### 7. Appointments (booking page)

Customers book showroom visits from the public **Book Visit** page (date →
time → their info → confirm). Confirmation emails go out when email is
configured (see Part B). Staff see and manage the bookings in the CRM's
Appointments tab — there's no separate staff scheduling tool to learn.

### 8. Ad Studio (marketing)

AI-assisted marketing content built around real inventory:

- **Create Ad** — generate video ad scripts for TikTok, Instagram Reels, and
  Facebook, with content types (Home Tour, Myth Busting,
  Financing Tips, Clearance Alert, Behind the Scenes, Customer Story,
  Comparison, FAQ), Tex avatar styles, English or Spanish, and image styles.
- **Content Ideas** — trending ideas to keep the posting calendar full.
- **Drafts** — review drafts prepared while the Ad Studio screen is open; they are not persisted or posted to a social platform.
- **Analytics** — local creative and inventory readiness; it does not claim live social-platform metrics.

Because it reads live inventory, ads reference homes actually on the lot. You
can also jump here straight from a home on the Inventory page to create an ad
for that home.

### 9. Ops Copilot (staff AI helper)

**Ops Copilot** is an in-app assistant for staff only. Ask it questions about
live business data — "How many new leads do we have?", "What's on the
appointment schedule?" — or how to use the platform ("How do I upload photos
for a home?"). It's read-only by design: it looks things up, it never changes
anything.

### 10. Analytics, System Hub, and Health Dashboard

- **Analytics** — lead stats over time, lead status and engagement, site
  events, document generation activity (totals, by type, recent), and
  inventory analytics (including most-viewed homes).
- **System Hub** — the operator's map of the whole platform: quick links to
  every admin surface, an architecture diagram, security posture checklist,
  keyboard shortcuts, and **Passkey Recovery** (revoke lost/deprecated
  passkeys).
- **Health Dashboard** — live service metrics from `/api/metrics`: total
  requests, p95 latency, error rate, uptime, plus recent staff user activity.

### 11. Getting Started (in-app Guide)

The **Guide** page walks a new staff member through the standard workflow:
start with the customer, choose the exact home, confirm installer and site,
generate the packet. If you only read one page on your first day, read that
one.

### 12. A note on error messages

If something goes wrong, the site now shows plain-English messages ("We
couldn't reach the server…", "Your session has expired…") instead of raw
technical errors. If you ever *do* see something that looks like programmer
gibberish, that's a bug — report it.

---

## Part B — Technical Handoff (for a developer)

The authoritative technical docs live in `docs/` — this section is a map, not
a duplicate. Read `docs/ARCHITECTURE.md` for system design,
`docs/API_REFERENCE.md` for every endpoint (auto-generated from the app's own
OpenAPI schema by `scripts/generate_api_reference.py`), and `docs/RUNBOOK.md`
for incident response and rollback.

### Stack at a glance

| Layer | Technology |
|-------|------------|
| Backend | FastAPI (Python 3.11), Google ADK, Gemini 2.5 Flash via Vertex AI |
| Frontend | React 19 + Vite + Tailwind, built to `frontend/dist/` |
| Data | Google Firestore (primary), JSON fallback, sample data last resort |
| PDFs | `pypdf` form-fill driven by `config/field_map.json` |
| E-sign | DocuSeal (integration built; returns 501 until env-configured — see `docs/DOCUSEAL_DEPLOY_RUNBOOK.md`) |
| Email | Resend (optional, env-gated) |
| Deploy | Single Docker container on Cloud Run (project `tho-ai-agent`, region `us-central1`) |
| Hosting | Canonical URL `www.texashomeoutlet.com`; `main` deploys a zero-traffic candidate |

### Repository map

See `CLAUDE.md` (root) for the authoritative directory layout and conventions.
The most important pieces:

- `main.py` — FastAPI app and the bulk of the API endpoints, auth, and
  middleware. Additional routers are mounted near the bottom (passkeys, SEO
  routes, partner integrations such as `mira_routes.py` /
  `obsidian_routes.py`).
- `config.yaml` — single source of truth for business config.
- `config/field_map.json` — **the** registry mapping every PDF template and
  packet to its fields. Never hardcode PDF field names in Python.
- `tools/document_engine.py` / `tools/document_tools.py` — the PDF fill engine.
- `frontend/src/pages/DocumentCenter.jsx` — the contract wizard UI.
- `database/firestore_client.py` — Firestore CRUD (all request-path RPCs run
  under shared timeouts, `database/rpc_timeout.py`).
- `docuseal_service.py` — e-signature orchestration (env-gated).
- `email_service.py` — transactional email via Resend;
  `email_reply_drafts.py` — AI-drafted replies for the CRM.

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

See `docs/DEV_SETUP.md` for the fuller local setup.

### Deploy

Feature branches open a PR; direct pushes to `main` are forbidden. After all
applicable checks are explicitly green, a THO PR may merge without a separate
human approval. The merge deploys and smokes a zero-traffic candidate; it does
not promote production traffic. Direct deployment and production traffic
promotion remain operator-gated actions. Manual command for the operator
runbook, if ever needed:

```bash
gcloud run deploy project-go-forward --source . --region us-central1
```

### Health & smoke (read-only, safe to run anytime)

```bash
curl -fsS https://www.texashomeoutlet.com/healthz/      # liveness + deployed commit
python3 scripts/production_smoke.py --base-url https://www.texashomeoutlet.com
```

### Secrets / environment (Cloud Run, via Secret Manager)

| Variable | Purpose | Required? |
|----------|---------|-----------|
| `ADMIN_PIN_HASH` | SHA-256 of the staff admin PIN | **Yes** |
| `ADMIN_SESSION_SECRET` | Independent session signing secret (Cloud Run requires ≥32 UTF-8 bytes; never PIN-derived) | Required |
| `GOOGLE_GENAI_USE_VERTEXAI=TRUE` | Use Vertex AI for Gemini | Yes |
| `RESEND_API_KEY` | Transactional email (lead/appointment/deal emails, staff sign-in codes) | For email |
| `NOTIFICATION_EMAIL` | Comma-separated staff alert recipients | For email |
| `DOCUSEAL_API_URL` / `DOCUSEAL_API_TOKEN` / `DOCUSEAL_WEBHOOK_SECRET` | E-signature | For e-sign (not yet set — see deploy runbook) |

Passkeys are stored in Firestore and gated to the approved owner email and
`@texashomeoutlet.com` staff accounts; no extra env vars are needed beyond the
admin session secret.

Runbooks live in `docs/PRODUCTION_READINESS.md` (deploy/email/PIN),
`docs/PIN_ROTATION_RUNBOOK.md`, and `docs/DOCUSEAL_DEPLOY_RUNBOOK.md`
(e-sign rollout).

### Guardrails (do not violate)

- **Never modify the PDF templates** in `tho_documents/` — they're regulatory
  originals.
- **Never log or send PII** (SSN, financial account numbers) to the LLM — use
  `tools/pii_guard.py`.
- Keep the legacy `/api/documents/sales-contract` endpoint working.
- Add new endpoints in `main.py` **above** the SPA catch-all route at the
  bottom.
- Route every user-visible error through `frontend/src/utils/apiError.js` —
  raw technical strings must never reach the UI.

### Known limitations (from `SYSTEM_STATUS.md`)

- Cold-start latency of ~5–10s on first hit (Cloud Run scale-to-zero).
- Chat history persists per browser session; a visitor on a new browser or
  cleared storage starts a fresh session.
- Inventory sync is run periodically via the scraper tool, not real-time.

---

*Maintained alongside `docs/PRODUCTION_READINESS.md`. Last updated 2026-07-25.*
