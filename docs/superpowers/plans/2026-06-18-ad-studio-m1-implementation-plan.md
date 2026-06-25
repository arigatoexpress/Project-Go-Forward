# Ad Studio M1 — Instagram ROI Loop: Implementation Plan

- **Date:** 2026-06-18
- **Status:** Plan for review (TDD, surgical PRs). PLAN ONLY — no code written yet.
- **Owner:** Ari (THO / Project-Go-Forward)
- **Spec:** `docs/superpowers/specs/2026-06-16-ad-studio-instagram-roi-loop-design.md`
- **Branching:** all PRs branch from `origin/main`. NOTE current local branch is
  `feat/real-analytics-dashboard` (2 commits ahead, the spec/docs commits). Rebase those or
  cut M1 branches from `origin/main` so each PR is a single clean concern.

## 0. What already exists (verified against the code)

The loop's rails are mostly built. M1 closes four gaps. Verified call-sites:

| Concern | Where it lives | State |
|---|---|---|
| Fail-closed publish + draft fallback | `tools/social_publishers.py` `prepare_or_publish_social_post` | Built. Double-gated by `THO_SOCIAL_PUBLISH_ENABLED` + tokens. |
| IG Reels adapter | `tools/social_publishers.py` `_publish_instagram_reel` (lines 187-223) | Built but **does NOT poll** the media container before `media_publish` — Gap #4. |
| Readiness reporting | `social_readiness()` (lines 99-126) | Built; reports `instagram_reels` required_env. |
| UTM CTA link builder | `_utm_cta_link` (lines 63-85) | Built; opt-in via `THO_UTM_CTA_ENABLED`. |
| Attribution categorizer | `main.py` `_categorize_lead_source` (lines 1461-1493) | Built; already reads `utm_source`/`referrer` via `getattr` (defensive — Lead has no such fields yet) — Gap #1. |
| Attribution report | `main.py` `GET /api/admin/crm/lead-sources` (line 1496) | Built; buckets by category + matches deals on email/phone for revenue. |
| Lead intake (contact) | `main.py` `POST /api/contact` `submit_contact_form` (lines 4619-4737) | Built; reads `name/phone/email/source` from body — does NOT read UTM — Gap #2 (backend). |
| Lead intake (appointment) | `main.py` `POST /api/appointments` Lead block (lines 4854-4866) | Built; same — no UTM. |
| Lead model | `lead_management.py` `Lead` dataclass (lines 33-109) | Built; **no UTM fields** — Gap #1. `from_dict` drops unknown keys (line 89-91) so the change is backfill-safe. |
| Generated video URL | `tools/video_generator.py` returns `download_url = /api/marketing/videos/{filename}` (lines 131, 334) | Built — but that route is **admin-gated** (`main.py:4202`), so Meta CANNOT fetch it — Gap #3. |
| Schedule wrapper | `tools/marketing_tools.py` `schedule_social_post` (line 1781) → `prepare_or_publish_social_post` | Built. |
| Schedule endpoint | `main.py` `POST /api/marketing/schedule` (line 4010) | Built. **No `POST /api/marketing/publish`** — Gap #5. |
| Frontend submit | `Contact.jsx` (POST /api/contact, line 26), `Appointments.jsx` (POST /api/appointments, line 352) | Built; no UTM capture — Gap #2 (frontend). |
| Ad Studio publish UI | `AdStudio.jsx` `handleSchedule` (line 410), sends `video_url: ...download_url` | Built; calls `/api/marketing/schedule`. Needs one-tap "Approve & Publish" → Gap #5. |

### Test harness (must conform)

- `tests/test_social_publishers.py` — autouse fixture clears every social env var and swaps
  `social_publishers.requests` for a `_NoHTTP()` guard that raises on any real HTTP call. New
  poll tests MUST use a fake `requests` that returns canned `status_code` JSON; the existing
  `_NoHTTP` guard is the model for "no test ever hits the network."
- `tests/test_lead_attribution.py` — uses `SimpleNamespace` fakes + the real `Lead` dataclass;
  pulls `_categorize_lead_source` via the `categorize` fixture (which calls
  `test_api_v1.create_client` to stub eager Firestore imports).
- `tests/test_contact_lead_capture.py` — uses `create_client(monkeypatch)` from
  `tests/test_api_v1.py`; `FakeLeadManager.create_lead` appends to `.leads`, so assertions read
  `main.lead_manager.leads[-1]`.
- `tests/test_api_v1.py` defines a **hand-maintained `FakeLead` dataclass** (lines 67-112) with
  its own explicit `to_dict()`. **Any new `Lead` field that intake endpoints set must be mirrored
  here**, or contact/appointment tests that read `created.utm_source` will fail. This is the most
  easily missed coupling in M1.
- `tests/test_lead_management.py` — round-trips the real `Lead` (`to_dict`/`from_dict`).

### Guardrails baked into every item

- **CRITICAL RULE 1 (outward = gated):** publishing to Instagram is double-gated
  (`THO_SOCIAL_PUBLISH_ENABLED` env + per-post human "Approve & Publish"). Claude never posts;
  no scheduler, no auto-trigger. The new endpoint is admin-auth + human-initiated only.
- **Honesty (`docs/HOW_LEADS_WORK.md`):** the site captures a lead only when a visitor *reaches
  out*. UTM is first-party link tagging carried on the lead the visitor chose to submit — NOT
  passive/anonymous visitor tracking, no pixel, no third-party tracker. The plan adds UTM to the
  three reach-out paths only; it does NOT add any always-on beacon. PR 2 includes a docs update
  to `HOW_LEADS_WORK.md` moving "knowing where a lead came from" from "not live" to "live, honest".
- **No PII:** UTM values are marketing tokens, never customer PII; captions are about homes, not
  customers. Existing `pii_guard` conventions unchanged. UTM strings are length-capped on intake.
- **Fail-closed:** missing token/gate/URL → reviewed `draft_ready`, never a silent fake "posted".

---

## 1. Dependency graph & PR sequence

```
PR 1  Lead UTM fields (model)          ── independent ─────────────┐
PR 2  Backend UTM intake + honesty doc ── depends on PR 1 ─────────┤
PR 3  Frontend UTM capture             ── depends on PR 2 contract ┤  (attribution track)
                                                                    │
PR 4  Meta async-ingestion poll        ── independent ─────────────┐
PR 5  Public asset URL for creatives   ── independent ─────────────┤  (publish track)
PR 6  One-tap publish endpoint + UI    ── depends on PR 4 + PR 5 ──┘
```

- **Parallelizable now:** PR 1, PR 4, PR 5 are mutually independent — three agents can run at once.
- **Sequential:** PR 2 after PR 1; PR 3 after PR 2 (contract); PR 6 after PR 4 + PR 5.
- The **attribution track (1→2→3)** and the **publish track (4,5→6)** are independent of each
  other and can proceed on parallel branches.

Each PR: write the failing test(s) first, run `python -m pytest tests/<file> -q` to see red, make
the minimal change, see green, then run the full suite (`python -m pytest tests/ -q`) before
opening the PR. Frontend changes also run `cd frontend && npm run build`.

---

## PR 1 — UTM fields on the Lead model (Gap #1, data layer)

**Concern:** add nullable UTM columns to `Lead` so attribution has something to read. No behavior
change to any endpoint yet.

**TDD — failing test first.** Extend `tests/test_lead_management.py`:
- `test_lead_accepts_and_roundtrips_utm_fields`: construct
  `Lead(lead_id=..., user_id=..., session_id=..., utm_source="instagram", utm_medium="social",
  utm_campaign="spring-sale", utm_content="reel-a", utm_term=None, referrer="https://t.co/x")`;
  assert `to_dict()` contains all six keys; assert `Lead.from_dict(lead.to_dict())` round-trips
  them.
- `test_lead_from_dict_defaults_utm_to_none`: `Lead.from_dict({lead_id, user_id, session_id})`
  → all UTM fields are `None` (backfill-safe: existing Firestore docs lack these keys).
- `test_lead_from_dict_still_drops_unknown_keys`: pass a dict with `utm_source` + a junk key;
  junk dropped, `utm_source` kept (guards the existing line 89-91 contract).

**Minimal implementation.** In `lead_management.py` `Lead` dataclass, add after the Triage block
(line 68), all defaulting to `None`:
```python
# Marketing attribution (first-party UTM carried on a reached-out lead; NOT visitor tracking)
utm_source: str | None = None
utm_medium: str | None = None
utm_campaign: str | None = None
utm_content: str | None = None
utm_term: str | None = None
referrer: str | None = None
```
(Spec §5 lists source/medium/campaign/referrer; the task adds content + term. All six are
`str | None = None`. `to_dict` is `asdict` so it picks them up automatically.)

**Mirror in the test fake.** `tests/test_api_v1.py` `FakeLead` (lines 67-112): add the six fields
AND add them to its explicit `to_dict()` (the fake does not use `asdict`). Without this, PR 2's
contact/appointment assertions on `created.utm_source` fail. Optionally add to `to_csv_row` for
operator CSV visibility (nice-to-have, not required for M1 evals).

**Data shape / contract:** Lead doc gains 6 optional string fields. Firestore is schemaless;
existing docs need no migration; `from_dict` ignores their absence.

**Reversibility:** fully reversible — drop the fields. No migration, no data written until PR 2.

**Independent:** yes (PR 4, PR 5 unrelated).

---

## PR 2 — Backend UTM intake + honesty doc (Gap #2, backend half)

**Concern:** read UTM off the request body in the two reach-out endpoints and set them on the
`Lead`. (Chat-save path is out of scope for M1 thin loop — only Contact + Appointment carry a
landing URL; note as fast-follow.)

**TDD — failing tests first.** New file `tests/test_lead_utm_intake.py` (pattern: `create_client`
from `test_api_v1`):
- `test_contact_persists_utm_from_body`: POST `/api/contact` with
  `{name, phone:"2813243020", utm_source:"instagram", utm_medium:"social",
  utm_campaign:"spring-sale", utm_content:"reel-a", referrer:"https://t.co/x"}`;
  assert `main.lead_manager.leads[-1].utm_source == "instagram"` (and campaign/content/referrer).
- `test_contact_without_utm_leaves_fields_none`: POST with only name+phone → `utm_source is None`
  (proves no regression to the existing happy path / no fabricated values).
- `test_appointment_persists_utm_from_body`: POST `/api/appointments` with UTM in body → the
  CRM lead created (lines 4854-4866) carries them.
- `test_utm_values_are_length_capped`: POST a 500-char `utm_campaign` → stored value capped
  (e.g. `[:200]`) so a hostile/huge param can't bloat a Firestore doc.
- Attribution end-to-end (extend `tests/test_lead_attribution.py` is already covered for the
  categorizer; add) `test_lead_sources_buckets_utm_instagram`: not strictly new — the categorizer
  test already asserts `utm:instagram`. Add one integration-flavored test that a lead WITH
  `utm_source` set via the real `Lead` returns `utm:instagram` from `_categorize_lead_source`
  (closes the loop between PR 1's real field and the existing reader).

**Minimal implementation.** Add a tiny helper near the intake endpoints in `main.py`:
```python
def _extract_utm(data: dict) -> dict:
    """Pull first-party UTM/referrer from a lead-submit payload. Length-capped,
    never PII. Honest attribution: only set when the visitor reached out."""
    def clip(v): 
        return (str(v).strip()[:200] or None) if v else None
    return {
        "utm_source": clip(data.get("utm_source")),
        "utm_medium": clip(data.get("utm_medium")),
        "utm_campaign": clip(data.get("utm_campaign")),
        "utm_content": clip(data.get("utm_content")),
        "utm_term": clip(data.get("utm_term")),
        "referrer": clip(data.get("referrer")),
    }
```
Then in `submit_contact_form` (line 4655) and the appointment Lead block (line 4856), spread
`**_extract_utm(data)` into the `Lead(...)` constructor. (Appointment endpoint reads `data`
earlier in the handler — confirm the variable name in scope; the contact handler uses `data`.)

**Honesty doc.** Update `docs/HOW_LEADS_WORK.md`: move "Knowing where a lead came from" (lines
80-82) out of "not live yet" into the live section, framed honestly: *"When a customer clicks a
link we posted (e.g. an Instagram ad) and then reaches out, the lead is tagged with where it came
from. We still only capture people who contact us — we don't track anonymous browsers."* Keep the
"Tracking anonymous browsers — not live" bullet intact. This satisfies the client-comms honesty
guardrail in the same PR that makes the capability real.

**Data shape / contract:** request bodies MAY include `utm_source/utm_medium/utm_campaign/
utm_content/utm_term/referrer`; all optional; absent → `None`. No response shape change.

**Reversibility:** reversible — remove the helper + spreads; fields stay nullable. Already-tagged
leads keep their tags harmlessly.

**Depends on:** PR 1 (fields must exist). Independent of publish track.

---

## PR 3 — Frontend UTM capture (Gap #2, frontend half)

**Concern:** read `utm_*` from the landing URL once, persist for the session so they survive
navigation (landing → Contact/Appointments), and attach to the submit payloads. First-party only;
no third-party script, no beacon — honest with the guardrail.

**TDD — failing test first.** Frontend tests live under `frontend/src/__tests__` /
`frontend/src/test` (Vitest). New `frontend/src/utils/utm.test.js`:
- `captureUtmFromUrl` given `?utm_source=instagram&utm_campaign=spring-sale` writes a
  `{utm_source, utm_campaign, ...}` object to `sessionStorage` under a single key (e.g.
  `tho_utm`); first capture wins (don't overwrite on later navigations within the session).
- `getUtmParams` returns the stored object (empty `{}` when none) — what the forms spread into
  their payload.
- captures only the `utm_*` + `referrer` allowlist; ignores arbitrary query params.

**Minimal implementation.** New `frontend/src/utils/utm.js`:
```js
const KEY = 'tho_utm';
const FIELDS = ['utm_source','utm_medium','utm_campaign','utm_content','utm_term'];
export function captureUtmFromUrl(search = window.location.search) {
  if (sessionStorage.getItem(KEY)) return;            // first-touch wins
  const p = new URLSearchParams(search);
  const out = {};
  for (const f of FIELDS) if (p.get(f)) out[f] = p.get(f).slice(0, 200);
  if (document.referrer) out.referrer = document.referrer.slice(0, 200);
  if (Object.keys(out).length) sessionStorage.setItem(KEY, JSON.stringify(out));
}
export function getUtmParams() {
  try { return JSON.parse(sessionStorage.getItem(KEY) || '{}'); } catch { return {}; }
}
```
- Call `captureUtmFromUrl()` once on app mount in `frontend/src/App.jsx` (top-level `useEffect`).
- `Contact.jsx` (line 29): `body: JSON.stringify({ ...formData, ...getUtmParams() })`.
- `Appointments.jsx` (line 355): spread `...getUtmParams()` into the JSON body.

**Data shape / contract:** matches PR 2's `_extract_utm` keys exactly. Forms degrade cleanly when
no UTM present (empty spread).

**Reversibility:** reversible — delete `utm.js`, the mount call, and the two spreads. `sessionStorage`
self-clears at tab close; no persistent client state.

**Depends on:** PR 2 (backend must accept the keys, else they're silently dropped — harmless but
useless). Run `cd frontend && npm run build` + the Vitest file.

---

## PR 4 — Meta async-ingestion poll (Gap #4, publish adapter)

**Concern:** Reels media containers ingest asynchronously; `_publish_instagram_reel` currently
calls `media_publish` immediately after creating the container (lines 211-216), which fails for
non-trivial videos. Add a bounded poll of `GET /{creation_id}?fields=status_code` until `FINISHED`.

**TDD — failing tests first.** Extend `tests/test_social_publishers.py` (reuse the autouse env
fixture; replace the `_NoHTTP` guard per-test with a scripted fake `requests` returning canned JSON
— NEVER a real call):
- `test_instagram_poll_waits_for_finished_before_publish`: fake `requests.get` returns
  `status_code=IN_PROGRESS` twice then `FINISHED`; assert `media_publish` (the second `requests.post`)
  is only called AFTER a `FINISHED` poll; assert result `success` + `post_id`. Patch the sleep so the
  test is instant (`monkeypatch.setattr(social_publishers.time, "sleep", lambda *_: None)` — add a
  `time` import to the module, or inject a sleeper).
- `test_instagram_poll_times_out_returns_failure`: poll always `IN_PROGRESS`; after the bounded
  attempts the adapter returns `{"success": False, "error": ...}` WITHOUT calling `media_publish`
  (no silent publish of an unfinished container). `prepare_or_publish_social_post` then wraps it as
  a `draft_ready` with `success:False` (existing failure path, lines 301-313).
- `test_instagram_poll_errored_status_returns_failure`: poll returns `status_code=ERROR` →
  failure, no publish.
- Keep the existing gate/draft tests green (they never reach the adapter).

**Minimal implementation.** In `tools/social_publishers.py` `_publish_instagram_reel`, between
container creation (line 204) and `media_publish` (line 211), insert a poll loop:
```python
status_url = f"{META_GRAPH_BASE}/{version}/{creation_id}"
max_attempts = int(os.environ.get("META_REEL_POLL_ATTEMPTS", "20"))
interval = float(os.environ.get("META_REEL_POLL_INTERVAL_SECONDS", "3"))
for _ in range(max_attempts):
    poll = requests.get(status_url, params={"fields": "status_code", "access_token": token}, timeout=30)
    poll.raise_for_status()
    code = (poll.json() or {}).get("status_code")
    if code == "FINISHED":
        break
    if code == "ERROR":
        return {"success": False, "error": "Instagram media container ingestion failed (ERROR)."}
    time.sleep(interval)
else:
    return {"success": False, "error": "Instagram media container did not finish ingesting in time."}
```
Add `import time` to the module. Bounds are env-tunable so prod can widen for large clips without a
code change.

**Data shape / contract:** internal to the adapter; `_publish_instagram_reel` return shape
unchanged (`success`/`post_id`/`creation_id`/`api_response` on success, `success:False`/`error` on
failure). No API surface change.

**Reversibility:** reversible — remove the loop. Behavior reverts to immediate publish (current).

**Independent:** yes. **Safety:** still fully behind the publish gate — no test or prod path reaches
this without `THO_SOCIAL_PUBLISH_ENABLED` + tokens; the `_NoHTTP`/scripted-fake fixture guarantees
no real Meta call in CI.

---

## PR 5 — Public asset URL for approved creatives (Gap #3)

**Concern:** generated videos are served at `/api/marketing/videos/{filename}`, an admin-gated
route (`main.py:4202`) — Meta's servers get 401 and cannot fetch. The publish step needs a
public-readable (or short-TTL signed) HTTPS URL for the *specific* approved asset.

**Decision needed from Ari (see §Risks):** signed URL (preferred, least exposure) vs. public object
with a random name. Plan supports both behind one helper; default = **signed URL with bounded TTL**,
fall back to a public random-named object if Meta ingestion can outlive the TTL.

**TDD — failing tests first.** New `tests/test_marketing_publish_asset.py` (no real GCS — fake the
bucket/blob like `tests/test_image_storage.py` / `tests/test_document_gcs.py` do; respect
`THO_DISABLE_GCS_UPLOADS` for local/dev):
- `test_publish_asset_uploads_with_random_name`: uploading a local generated video yields a blob
  whose name is unguessable (random component), under a publish prefix.
- `test_publish_asset_returns_signed_url_when_signed_mode`: helper returns an `https://...` signed
  URL with an expiry; assert TTL bounded (e.g. ≤ configurable max).
- `test_publish_asset_public_fallback_returns_public_url`: in public mode, returns the public
  object URL (random name) and sets it readable.
- `test_publish_asset_local_fallback_when_gcs_disabled`: with GCS disabled, returns a clear
  `not_publicly_reachable` signal (NOT a fake URL) so `prepare_or_publish_social_post` stays a
  draft — fail-closed, never pretend.

**Minimal implementation.** Add `tools/marketing_assets.py` (small, single-responsibility; mirrors
`tools/image_storage.py` GCS pattern — lazy bucket, local fallback, `THO_DISABLE_GCS_UPLOADS`
honored):
```python
def publish_video_asset(local_path_or_filename: str) -> dict:
    """Make an approved generated creative fetchable by Meta. Returns
    {"success": True, "public_url": "https://..."} or {"success": False, "reason": ...}.
    Random object name (unguessable); signed URL with bounded TTL by default, public-object
    fallback when META_ASSET_PUBLIC=true."""
```
Use a dedicated bucket env `GCS_PUBLISH_ASSETS_BUCKET` (do NOT reuse the secure-documents bucket;
do NOT widen the listing-photos bucket). The signed-URL path uses the existing GCS client; TTL from
`META_ASSET_URL_TTL_SECONDS`.

**Reversibility:** reversible — new module + new bucket; nothing else changes until PR 6 wires it.
Objects are random-named and TTL-expiring (signed mode) so exposure is bounded and revocable.

**Independent:** yes. **Safety:** unguessable name; bounded TTL; no PII (marketing video only).

---

## PR 6 — One-tap publish endpoint + Approve & Publish UI (Gap #5)

**Concern:** a dedicated admin endpoint that a human triggers to publish *now* (distinct from the
deferred `/api/marketing/schedule`), wiring PR 5's public URL into PR 4's polling adapter. **Publish
stays gated** — admin-auth + the env gate + human click; Claude never calls it.

**TDD — failing tests first.** New `tests/test_marketing_publish_endpoint.py`
(`create_client` harness; `require_admin` is satisfied the same way existing admin-endpoint tests
do — see `tests/test_admin_auth.py` for the auth-cookie/PIN pattern):
- `test_publish_requires_admin`: POST `/api/marketing/publish` without admin auth → 401.
- `test_publish_draft_when_gate_off`: gate unset, tokens present → response
  `status == "draft_ready"`, `publish_attempted is False` (no Meta call; `_NoHTTP`-style guard).
- `test_publish_uploads_asset_then_calls_adapter_when_gate_on`: gate on + tokens + a generated
  video; monkeypatch `publish_video_asset` to return a fake public URL and the IG adapter to a
  scripted fake; assert the endpoint passes the **public** URL (not the admin `download_url`) into
  `prepare_or_publish_social_post(platform="instagram_reels", content_type="video", ...)` and returns
  `status == "published"`.
- `test_publish_fails_closed_when_asset_not_public`: `publish_video_asset` returns
  `success:False` → endpoint returns a draft/blocked response, never attempts publish.
- `test_publish_is_human_endpoint_only` (doc/assert): endpoint has no scheduler/cron wiring; it is
  only reachable via the admin route.

**Minimal implementation.** In `main.py`, **above the SPA catch-all** (project convention), near the
other marketing routes (~line 4053), add:
```python
@app.post("/api/marketing/publish", dependencies=[Depends(require_admin)])
async def api_marketing_publish(request: Request):
    """Human one-tap 'Approve & Publish' for a single approved creative. Outward action →
    admin-gated + THO_SOCIAL_PUBLISH_ENABLED + explicit click. Claude never calls this."""
    data = await request.json()
    # 1) resolve the approved local creative → public/signed URL (PR 5)
    asset = publish_video_asset(data.get("filename") or data.get("video_url"))
    if not asset.get("success"):
        return {"success": False, "status": "blocked", "reason": asset.get("reason", "asset_not_public")}
    # 2) hand the PUBLIC url to the gated, polling adapter (PR 4)
    return schedule_social_post(
        platform="instagram_reels", content_type="video",
        caption=data.get("caption"), hashtags=data.get("hashtags"),
        video_url=asset["public_url"], home_name=data.get("home_name"),
        campaign=data.get("campaign"),
    )
```
(Reuse `schedule_social_post` → `prepare_or_publish_social_post` so all the existing fail-closed gate
logic and the new poll apply unchanged. The only new behavior vs. `/schedule` is the public-URL
upload + "publish now" framing.)

**Frontend.** In `AdStudio.jsx`, add an "Approve & Publish" button (distinct from "Schedule",
line ~410) that calls `adminFetch('/api/marketing/publish', { method:'POST', body: JSON.stringify({
filename: generatedGenAIClip?.filename || generatedVideo?.filename, caption, hashtags, home_name,
campaign }) })`. Pass `filename` (not the admin `download_url`) so the backend resolves the public
URL itself. Gate the button's "live" affordance behind the existing `socialPublishReady`
(line 732); when not ready, the same click returns a `draft_ready` and the UI shows the missing
config (existing readiness panel). Confirm-before-publish dialog ("This posts to Instagram now")
reinforces the human gate.

**Data shape / contract:**
- Request: `{ filename, caption, hashtags?, home_name?, campaign? }`.
- Response: the `prepare_or_publish_social_post` shape — `draft_ready` (gate off / not ready) or
  `published` (gate on + finished poll) or `{success:False, status:"blocked", reason}` (asset not
  public).

**Reversibility:** reversible — remove the endpoint + button. No state migration. With the env gate
off (its default until Ari flips it per spec §6), the endpoint can ship to prod and only ever
produce drafts — safe to merge ahead of go-live.

**Depends on:** PR 4 (poll) + PR 5 (public URL).

---

## 2. Risks & decisions that need Ari's call

1. **Signed URL vs. public object (PR 5).** Spec §10 prefers a **signed URL with bounded TTL**
   (least exposure) but flags that if Meta's async ingestion outlives the TTL, publish fails →
   fall back to a **public random-named object**. *Decision:* default TTL value and whether to start
   in signed mode or public-random mode. Recommend: signed mode, TTL ≥ a few minutes (longer than
   the PR 4 poll budget = `attempts × interval`, default 60s), public-random as documented fallback.
2. **New GCS bucket (PR 5).** `GCS_PUBLISH_ASSETS_BUCKET` is a new bucket distinct from
   secure-documents and listing-photos (never widen those). *Decision:* bucket name + that creating
   it is an operator/outward step (gated; not done by Claude). Spec §6 already lists the env/secrets
   Ari sets.
2b. **Bucket creation is outward infra** — `gsutil mb` / IAM is an operator action on the THO
   project; gated, Ari runs it. Plan ships code that no-ops cleanly (local fallback / draft) until
   the bucket + secrets exist.
3. **Poll bounds (PR 4).** Defaults `META_REEL_POLL_ATTEMPTS=20`, `META_REEL_POLL_INTERVAL=3s`
   (~60s budget). Veo clips are short, but large/HD may need more. *Decision:* accept defaults or
   widen; they're env-tunable so this is reversible without code.
4. **Chat-save lead path excluded from M1.** Only Contact + Appointment carry a landing URL in M1.
   The in-chat lead-capture path (`main.py:1334`) is a documented fast-follow. *Decision:* confirm
   that scoping (matches spec §4 thin loop).
5. **`utm_content`/`utm_term` beyond spec §5.** Spec §5 names source/medium/campaign/referrer; this
   plan adds content + term (cheap, standard UTM, future-proofs ad-level attribution). *Decision:*
   keep all six or trim to the spec's four.
6. **Meta App Review / dev mode (spec §10).** Dev mode publishes only to app-role accounts — fine
   for THO's own IG. Not a code blocker for M1; flagged so scaling later isn't a surprise. No
   decision needed for M1; operator setup per spec §6.
7. **Go-live flip stays Ari's.** `THO_SOCIAL_PUBLISH_ENABLED=false` until a dev-mode test post is
   verified; **Ari flips it** (CRITICAL RULE 1/§6). All six PRs are safe to merge with the gate off
   — they only ever produce reviewed drafts until then.

## 3. Verification per PR

- Backend: `python -m pytest tests/<new_or_touched_file> -q` (red→green), then full
  `python -m pytest tests/ -q` (~1085 tests; must stay green — note the `FakeLead` mirror in PR 1
  is the likely break point if missed).
- Frontend (PR 3, PR 6): `cd frontend && npm run build` + the Vitest spec.
- No real network in any test: social tests use the `_NoHTTP`/scripted-fake `requests`; GCS tests
  fake the bucket or set `THO_DISABLE_GCS_UPLOADS`.

## 4. Out of scope (spec §9, restated)

Facebook Page-feed adapter; TikTok go-live; post scheduling/queue/automation; a new analytics
dashboard (existing lead-sources report covers M1); multi-account / production Meta App Review;
in-chat UTM capture (fast-follow).
