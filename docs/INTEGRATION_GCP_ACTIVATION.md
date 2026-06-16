# THO Integration — GCP Activation Runbook

Staged, audited commands to take the inventory + Notion-bridge integration live.
Every step here is a **gated production action** (it deploys/configures the live
client app), so it is yours to run — the code is built, tested, and reversible;
this just makes the activation push-button.

> **Audited prod state (2026-06-16):** service `project-go-forward` @ rev `00279`,
> project `tho-ai-agent`, region `us-central1`, run-as SA
> `691674245427-compute@developer.gserviceaccount.com`, Firestore `(default)`
> NATIVE us-central1. **All integration env vars are currently unset**
> (`INVENTORY_SOURCE`, `NOTION_TOKEN`, `NOTION_COMMAND_CENTER`, …) → the feature
> is fully dark; nothing changes until you run the steps below.

> ⚠️ **Golden rule for Cloud Run config:** always use `--update-env-vars` /
> `--update-secrets` (additive). **Never** `--set-env-vars` / `--set-secrets` —
> that would wipe the 16 existing env vars + 7 secret mounts (WEBAUTHN_*, RESEND,
> admin PIN/session, partner keys) and break prod.

---

## Part A — Inventory unfreeze  (branch `feat/in-app-inventory`)

1. **Rebase + merge.** That branch was cut from main `6098a91` (#192); main is now
   ≥ #198. Rebase onto current main (changes are additive — new files + targeted
   edits, should be clean), then merge the PR. Default `INVENTORY_SOURCE=legacy`
   → **the merge deploys with zero behavior change.**

2. **Seed Firestore from the real source.** Put `House Orders.xlsx` in `data/`,
   preview, then write (uses Application Default Credentials with Firestore access
   to `tho-ai-agent`):
   ```bash
   python -m tools.house_orders_sync                 # dry-run: eyeball the homes + AVAILABLE/SOLD split
   python -m tools.house_orders_sync --apply         # upsert into Firestore `inventory` (idempotent)
   ```
   (Fallback if you don't have the sheet handy: `python -m tools.inventory_seed --apply`
   seeds the 279-home May-11 snapshot instead.)

3. **Flip the public read onto the in-app store** (gated deploy → new revision):
   ```bash
   gcloud run services update project-go-forward --region=us-central1 --project=tho-ai-agent \
     --update-env-vars INVENTORY_SOURCE=firestore
   ```
   The site now serves Mark's current stock. **Instant revert** (no redeploy delay,
   just a new revision):
   ```bash
   gcloud run services update project-go-forward --region=us-central1 --project=tho-ai-agent \
     --update-env-vars INVENTORY_SOURCE=legacy
   ```

---

## Part B — Notion bridge / Ops Copilot  (branch `feat/notion-ops-bridge`)

The 7 ops-DB ids are already in `config.yaml`; the only secret is the token.

1. **Token secret — ✅ DONE.** Stored as Secret Manager secret **`notion-id`**
   (version 1 enabled). To rotate later:
   `printf '%s' 'ntn_NEW' | gcloud secrets versions add notion-id --data-file=- --project=tho-ai-agent`.

2. **SA read access — ✅ DONE.** The run-as SA
   `691674245427-compute@developer.gserviceaccount.com` was granted
   `roles/secretmanager.secretAccessor` on `notion-id` (so the deploy below can bind it).

3. **Share the 7 databases with the integration** (Notion UI, once each): on the
   hub page (or each DB) → `•••` → **Connections** → add your integration. The
   ids the app uses: Delivery Tracker, Customer-satisfaction surveys, Service &
   Warranty, Title processing, Collections, Insurance/KIP, Lead Pipeline.

4. **Merge `feat/notion-ops-bridge`**, then wire + flip on (gated deploy):
   ```bash
   gcloud run services update project-go-forward --region=us-central1 --project=tho-ai-agent \
     --update-secrets NOTION_TOKEN=notion-id:latest \
     --update-env-vars NOTION_COMMAND_CENTER=on
   ```

5. **Verify:** ask the Ops Copilot *"how many titles are pending?"* or *"how many
   homes are in trim-out?"* — it answers from the staff's live Notion, **count-by-
   status only, no customer PII**. **Instant revert:** `--update-env-vars NOTION_COMMAND_CENTER=off`
   (the snapshot silently returns to Firestore counts).

---

## Part C — deferred GCP automation (built later, gated)

- **Daily House Orders sync** — Cloud Scheduler → an admin sync endpoint →
  `house_orders_sync` → Firestore, so the site refreshes without a manual run.
  (Needs a sheet→GCS drop or Drive-API access for the run-as SA.)
- **Mira-secret cleanup** — after the Ops Copilot bridge proves parity in prod,
  retire `tho-api-key-mira` + `telegram-asfao-token` and delete the Mira routers
  (`mira_routes.py`, `mira_notify.py`, `github_mira_trigger.py`).

## Security notes
- `NOTION_TOKEN` lives only in Secret Manager; DB ids are config (not secrets).
- The bridge is **PII-free by construction** — count-by-status only; no customer
  identity, dollar figure, or free text ever enters the snapshot.
- Every activation is a single env var with an instant, no-code revert.
- The two existing scheduler loops (`brain-ooda-loop`, `asfao-decide-loop`) were
  audited: both write analysis/**proposed** decisions to BigQuery — neither
  executes trades. The no-auto-execution boundary holds.
