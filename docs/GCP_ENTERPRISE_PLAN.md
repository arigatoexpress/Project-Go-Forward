# GCP Enterprise Integration Plan

Targets chosen by the operator (2026-06-11): **Identity (IAP/SSO)**,
**BigQuery analytics**, **Vertex AI enterprise**. This is the prep/decision
document — implementation is phased post-launch per the Track A freeze, except
where marked "pre-launch safe".

Current state that makes this easy: single Cloud Run service (stateless, env
config, JSON logs), Firestore primary store, config.yaml as the single source
of business truth, model selection already config-driven, prompts now
externalized in `prompts/` (this PR).

## 1. Identity — IAP / Workspace SSO for staff surfaces

**Goal:** replace "shared PIN + passkey" with per-person Google identities for
`/crm`, `/documents`, `/studio`, `/analytics`, `/system` and `/api/admin/*`.

**Recommended shape:**
- Put **Identity-Aware Proxy** in front of a second Cloud Run *ingress* path:
  keep the public site on the existing service; route staff paths through an
  IAP-protected load balancer (or a separate `tho-admin` service) so customer
  traffic never pays the IAP hop.
- Authorize a Google Group (`staff@texashomeoutlet.com` Workspace group) so
  onboarding/offboarding is group membership, not code.
- The app already gates these routes server-side (`require_admin`); IAP adds
  identity on top. Keep `require_admin` as defense-in-depth (verify the IAP
  JWT assertion header `x-goog-iap-jwt-assertion` instead of the PIN once
  IAP is live; keep passkey as break-glass).
- **Prereqs:** Workspace accounts for staff (they exist — @texashomeoutlet.com
  addresses), an HTTPS LB in front of Cloud Run, OAuth consent config.
- **Effort:** ~1-2 days infra + 1 day app change (JWT verification). Phase:
  T+2 weeks post-launch.

## 2. BigQuery analytics

**Goal:** real reporting on leads, deals, appointments, and chat/agent events;
Looker Studio dashboards for the client.

**Recommended shape (no app changes to start):**
- Enable the **"Stream Firestore to BigQuery"** Firebase extension (or a
  scheduled `firestore export` → BQ load job) for collections: `leads`,
  `customers`, `deals`, `appointments`, `analytics_events`.
- Pre-launch safe step (already true): `analytics_service.py` writes
  structured events; once mirrored to BQ, dashboards are pure SQL/Looker
  work, zero app risk.
- PII rule: exports inherit Firestore data; restrict the BQ dataset to the
  same staff group as IAP, and exclude SSN-bearing fields at the extension
  config level (the sanitizer already keeps SSNs out of these collections —
  verify with a dataset audit before the first dashboard ships).
- **Effort:** half a day per collection incl. dashboard. Phase: T+2-4 weeks.

## 3. Vertex AI enterprise

**Goal:** enterprise controls and flexibility for the agent stack without a
provider rewrite (the multi-provider abstraction arrives with the platform
library post-launch).

**Recommended steps, in order:**
1. **Grounding:** move the document/RAG answers from the local
   `data/rag_index/` to **Vertex AI Search** (managed RAG over
   `tho_documents/` + inventory), giving citations and freshness without
   maintaining the index build. The agent keeps its current shape — the RAG
   tool's backend changes.
2. **Model flexibility inside Vertex:** model IDs are config/env-driven
   (enforced by `tests/test_agnosticism_gates.py` as of this PR), so Model
   Garden swaps (new Gemini versions, or partner models) are a config.yaml
   change + the eval pass in `tests/test_document_quality.py`/agent smoke.
3. **Capacity & data controls when volume justifies:** Provisioned
   Throughput for the chat agent; data residency pinned to `us-central1`;
   CMEK on Firestore/GCS if the client's compliance posture ever requires it.
- **Effort:** step 1 ~2-3 days; steps 2-3 are config/purchasing decisions.
  Phase: step 1 at T+4 weeks, after the monolith decomposition unblocks a
  clean tool seam.

## Sequencing summary

| When | What |
|---|---|
| Pre-launch (done in this PR) | Prompts externalized; model-name gates in CI; UI 404 + title sync |
| T+2 weeks | IAP/SSO for staff surfaces |
| T+2-4 weeks | Firestore→BigQuery streaming + Looker dashboards |
| T+4 weeks | Vertex AI Search grounding; provider abstraction lands with the platform library (Track B `ari-llm`) and is adopted here behind the same config seam |
