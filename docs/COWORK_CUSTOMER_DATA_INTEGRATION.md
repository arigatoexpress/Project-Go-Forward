# Cowork Prompt — THO Customer Data & Floorplan Integration

**Purpose**: Self-contained prompt for a Cowork agent to (1) triage the
aribspector@gmail.com inbox in Brave for THO-relevant context, (2) coordinate
with Mark Willcott on admin access, and (3) wire internal data — manufacturer
floorplans, full ~270-house inventory, customer feedback — into Project Go
Forward.

Pass the **Prompt** section verbatim. It is written so a fresh agent with no
prior conversation context can act.

---

## Operating context the agent needs

- **Repo**: `arigatoexpress/Project-Go-Forward` (cloned at `~/Code/Project-Go-Forward`).
  Production: Cloud Run `project-go-forward` in `tho-ai-agent` / `us-central1`.
  Customer URL: <https://tho.sapphirealpha.xyz>. Raw Cloud Run URLs are
  diagnostic-only and should not be sent to the client.
- **Mark Willcott**: THO operations / sales lead. Phone 713.412.2200,
  email via Yahoo. He owns the keys to the manufacturer dealer portals and
  the pre-prod admin PIN. He sent the Joe Blo bug report on 2026-04-27.
- **Existing inventory pipeline**: `tools/sync_inventory_from_website.py`,
  `tools/asset_scraper.py`, `tools/inventory_sync.py`. CDN base
  `https://d132mt2yijm03y.cloudfront.net/`, dealer ID `3522`.
- **Existing manufacturer research**: `docs/MANUFACTURER_IMAGE_SOURCES_RESEARCH.md`,
  `docs/MANUFACTURER_OUTREACH_TEMPLATES.md`, `docs/INVENTORY_SYNC_ACTION_PLAN.md`.
- **Document templates**: `tho_documents/` (63 PDFs). Generation engine:
  `tools/document_engine.py`. The Joe Blo unpopulated-fields bug was just
  fixed on `main` (commits `447d1f9` + `6b3b301`) — **don't rework that fix**.
- **Auth model**: HMAC JWT issued from a 4-digit PIN (current PIN is in
  the `ADMIN_PIN_HASH` Cloud Run env var; Mark holds it). Endpoints under
  `Depends(require_admin)` need a session token from `POST /api/admin/verify`.

## Available MCPs / tools the agent should reach for

- **Brave** is Chromium-based. Try `mcp__claude-in-chrome__*` first
  (will work if the Chrome extension is loaded for Brave). If that's not
  connected, fall back to `mcp__computer-use__*` at "read" tier — Brave
  will appear in screenshots but clicks are blocked, so use Chrome MCP for
  navigation and only use computer-use for OCR-style reading.
- **Gmail MCP** (`mcp__b21dd510-...__search_threads/get_thread`) is
  authenticated to **aristotlespec@gmail.com**, NOT aribspector. Don't
  rely on it for the aribspector inbox; use the Brave session.
- **Google Calendar MCP** (`mcp__dba4566e-...`) is available if scheduling
  with Mark is needed.
- **Read iMessages** (`mcp__Read_and_Send_iMessages__*`) is available if
  Mark texts faster than he emails.

## Hard guardrails

- **Never click web links from Mail / Messages with computer-use.** Pass
  any link to the Chrome MCP for inspection instead.
- **Never type a customer SSN/DOB into a screenshot or screen-readable
  surface.** PII guards in `tools/pii_guard.py` apply on the backend; the
  agent should not paste raw PII into prompts or commits.
- **Never modify** `tho_documents/*.pdf` — those are regulatory originals.
- **Don't change the document fill code** unless a *new* customer issue is
  observed; the AcroForm + XFA dual-fill is intentional (Joe Blo fix).
- **No financial actions.** Don't initiate transfers, place orders, or
  modify Cloud Run billing. Reading dashboards is fine.
- **Confirm before pushing** to `main` if changes touch
  `main.py`, `requirements.txt`, the `Dockerfile`, or anything under
  `database/` — those routes auto-deploy via `.github/workflows/deploy.yml`.

---

## Prompt

> You are picking up an open thread for Texas Home Outlet (THO). The team
> just shipped a bugfix for empty PDF fields in customer closing packets
> (commit `6b3b301` on `arigatoexpress/Project-Go-Forward` `main`). Now we
> need to widen our internal data so the AI agent (`project-go-forward` on
> Cloud Run) can answer customer questions with complete inventory and
> floorplan context.
>
> Mark Willcott (phone 713.412.2200, sells from the Huffman lot) emailed
> on 2026-04-27 with two items: a packet bug (now fixed) and a question
> about whether we list only on-lot homes or every home our manufacturers
> can build. The team's working answer is "list everything we can get,
> ~270 homes" — your job is to operationalize that.
>
> ### Step 1 — Brave inbox sweep (aribspector@gmail.com)
>
> The browser session for `aribspector@gmail.com` is already open in
> Brave. Brave is Chromium so try the `mcp__claude-in-chrome__*` tools
> first; if the extension isn't connected ask the user to install it
> rather than dropping to pixel-clicking with computer-use.
>
> Search the inbox for:
> - `from:mwillcott OR from:Willcott` — Mark's recent threads, especially
>   any with attachments (PDFs, spreadsheets, screenshots).
> - `from:newvisionmfg.com OR from:jessuphousing.com OR from:claytonhomes.com OR from:championhomesinc.com OR from:legacyhousing.com`
>   — manufacturer correspondence; harvest dealer-portal URLs, login IDs
>   (do NOT extract passwords; ask Mark for those out-of-band), and any
>   floorplan PDFs.
> - `subject:floorplan OR subject:"floor plan" OR subject:inventory`
> - `has:attachment filename:pdf` from the last 90 days, scoped to
>   "Texas Home Outlet" / "THO" / "Huffman".
> - `from:tdhca.texas.gov` — regulatory updates that may affect
>   `tho_documents/` templates.
>
> For each interesting thread: capture sender, subject, date, a 1-line
> summary, and the URL of any attachment. Write this to a single markdown
> report at `~/Code/Project-Go-Forward/docs/INBOX_SWEEP_2026-04-27.md`
> (gitignored if it contains PII — check; if any address, phone, SSN,
> bank routing, or DOB shows up, redact before saving and add the file
> to `.gitignore` if it isn't already).
>
> ### Step 2 — Coordinate with Mark for admin access
>
> Compose (don't send yet) a short email to Mark — surface as a draft in
> Gmail or a markdown file at `~/Code/Project-Go-Forward/docs/MARK_ADMIN_REQUEST_DRAFT.md`
> — that asks for:
> 1. Manufacturer dealer-portal logins he uses (vendor name + portal URL
>    + the username; passwords go through 1Password / phone, never email).
> 2. The current production admin PIN (so we can validate `/api/admin/verify`
>    against staging without redeploying).
> 3. Any spreadsheet of "all available floorplans we can order" — Mark
>    referenced ~270 homes; we want the source-of-truth list.
> 4. Confirmation of which manufacturers are currently active partners
>    (the existing list in `docs/MANUFACTURER_IMAGE_SOURCES_RESEARCH.md`
>    may be stale).
>
> If Mark replies via iMessage faster than email, fold those answers
> into the same draft for follow-up.
>
> ### Step 3 — Wire floorplans / inventory into the system
>
> Once you have access:
> - Run `python3 tools/sync_inventory_from_website.py --dry-run` first;
>   read `data/inventory_sync_DRY_RUN_*.txt` and show me what would
>   change before applying. **Do not** run without `--dry-run` until I
>   approve.
> - For each manufacturer Mark confirms, walk
>   `docs/MANUFACTURER_IMAGE_SOURCES_RESEARCH.md` — does the CDN ID list
>   need updating? Add new manufacturers in a small PR.
> - For Mark's "all 270 homes" list, draft a new sync source in
>   `tools/inventory_sync.py` (or a sibling module) that ingests it. If
>   it's a spreadsheet, prefer `pandas.read_excel` over CSV-by-hand;
>   `pandas` is already in `requirements.txt`. Validate every row against
>   `database/models.py::Inventory` (PR #20 enforces this at write time).
> - For floorplan PDFs from manufacturers, save to
>   `data/floorplans/<manufacturer>/<plan_id>.pdf` (create the dir if
>   missing), and update `tools/asset_scraper.py::PROPERTY_ASSETS` so
>   `get_assets_for_home()` returns them. Don't bake the PDFs into the
>   container image — upload to GCS bucket `tho-secure-documents` under
>   `floorplans/` and serve via signed URL behind `require_admin`.
>
> ### Step 4 — Verify, PR, hand back
>
> - Run `python3 -m pytest tests/ -q --ignore=tests/test_api_v1.py --ignore=tests/test_crm.py --ignore=tests/test_healthz.py`
>   (the three excluded files need fastapi locally; CI runs them). All
>   green is required before opening a PR.
> - Open a PR titled `feat(inventory): full manufacturer floorplan
>   integration` — keep the diff scoped to inventory + asset_scraper +
>   docs; do NOT include the Brave session sweep markdown if it has any
>   PII. Reference the Joe Blo fix commits in the PR body.
> - When the PR is open, ping back here with the PR URL, the
>   to-do list of items still blocked on Mark, and any data-quality
>   concerns you found in the 270-home list.
>
> Report progress as you go — short status updates, one per step. Don't
> mass-email manufacturers without explicit approval; the templates in
> `docs/MANUFACTURER_OUTREACH_TEMPLATES.md` are drafts, not pre-approved.

---

## How to invoke

- **Inline / quick**: copy the **Prompt** block above and paste it into
  a `/cowork` (or `Agent` / spawned-task) call.
- **From this repo**: `claude /cowork "$(cat docs/COWORK_CUSTOMER_DATA_INTEGRATION.md | sed -n '/^## Prompt/,/^## How to invoke/p' | sed '1d;$d')"`
  (extracts just the Prompt section).
- **Schedule**: if you want a weekly inbox sweep, wrap Step 1 only and
  hand it to `/schedule` with a Mon 8am cadence.
