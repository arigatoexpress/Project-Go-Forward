# Google growth activation — GCP control plane, zero-spend first

This runbook turns THO's existing SEO and lead funnel into a measurable Google
growth system. Google Cloud is the credential, automation, and attribution
control plane. Google Ads remains the system of record for campaigns, billing,
and budgets.

## Hard gates

- Code, APIs, OAuth, dashboards, and campaigns in `PAUSED` state are reversible.
- No campaign may leave `PAUSED`, no budget may be attached to an enabled
  campaign, and no money may be spent without Ari approving the exact daily and
  monthly caps.
- No production traffic, DNS, or direct Cloud Run deployment change is part of
  this runbook.
- Never paste OAuth secrets, refresh tokens, customer IDs, or the developer
  token into chat, source control, logs, shell history, or unmanaged runtime
  configuration.
- The public storefront service is not the Ads operator. Do not grant Google
  Ads access to its broad default Compute service account. Ads automation must
  run as a dedicated identity in a dedicated Cloud Run Job.
- Do not create or download a persistent service-account JSON key. The preferred
  GCP runtime uses an attached user-managed service account and scoped
  Application Default Credentials (ADC).

## What the application provides

- Consent is first-party and defaults to denied. Third-party JavaScript is not
  downloaded before an explicit grant.
- GA4/GTM is runtime-gated by `GA4_MEASUREMENT_ID` or `GTM_CONTAINER_ID`.
- Public SPA navigation emits `page_view`; contact/quote/tour capture emits
  `generate_lead`; completed booking emits `schedule_appointment`.
- Analytics forwarding strips name, email, phone, address, message, notes, and
  lead/session identifiers. The first-party Firestore event sink remains the
  durable source even when analytics is declined or unavailable.
- `gclid`, `gbraid`, and `wbraid` use strict formats and are retained only when
  the visitor actively submits a lead. They remain in Firestore for later
  deterministic offline-conversion upload.

## Read-only audit

Run from an authenticated operator machine:

```bash
python3 scripts/google_growth_readiness.py --project tho-ai-agent
```

The report exposes presence booleans only. It never reads secret payloads or
prints measurement IDs, customer IDs, service-account emails, or token values.
It recognizes two authentication paths:

1. Preferred: the dedicated `google-growth-control` service account has no
   user-managed keys and has only project-level `roles/datastore.user`, never
   Editor/Owner/Secret Accessor. Two provider-facing Cloud Run Jobs must be
   attached to it: `google-growth-control` runs only the exact
   `python scripts/google_ads_access_evidence_job.py` command, while
   `google-growth-paused-create` runs only the exact
   `python scripts/google_ads_paused_worker_job.py` command. Each command is a
   two-element command array with an empty argument list and only the named Ads
   secrets from Secret Manager. The service account must have accessor rights
   on each bound secret itself. Job-resource IAM may grant only non-override
   execution/viewer roles; override-capable or custom roles fail readiness.
   A third `google-growth-paused-dispatch` job must use the separate keyless
   `google-growth-dispatcher` identity. Its template may contain only the fixed
   dispatcher command and revision-bound, non-secret approval configuration;
   its identity has only `roles/datastore.user` at project scope and exactly
   `roles/run.invoker` on `google-growth-paused-create`, with no binding on the
   access-evidence or dispatcher job and no Ads-secret or impersonation access.
2. Compatibility-only: all three legacy user-OAuth secrets exist. The audit
   reports this path, but it does not satisfy GCP-native strict readiness.

The preferred path needs only the developer-token and customer-ID Secret
Manager entries. The optional login-customer-ID entry is needed when the target
account is accessed through a Google Ads manager account. Legacy user-OAuth
secrets must be absent, and the public storefront must have no Ads credential
bindings, must use a distinct identity, and must have no project-level Cloud
Run Job invocation role, job-resource execution binding, direct Ads-secret
access, or ability to impersonate the Ads job identity. Exactly one measurement
path (GA4 or GTM) must be configured.
Search Console and Business Profile API status remains advisory and does not
block Ads presence readiness. A green presence audit does **not** prove the
identity has Google Ads account access. IAM checks cover direct project and
   job/secret/service-account resource bindings; folder/organization inheritance
and transitive group membership remain an explicit external operator review. The
audit also requires the storefront and all three jobs to report the same 40-hex
`APP_VERSION` and the same immutable `@sha256:` container-image digest. That is
not cryptographic digest-to-Git provenance: the go-live packet must separately
record the verified image-digest-to-candidate-SHA build mapping.

The account-access probe is offline by default:

```bash
python3 scripts/google_ads_access_probe.py
```

After a dedicated job identity and managed secret bindings exist, the job runs
the fixed `python scripts/google_ads_access_evidence_job.py` command with no
arguments or runtime overrides. It reads the immutable checked-in contract,
uses scoped ADC for only `SELECT customer.id FROM customer LIMIT 1`, and writes
a sanitized Firestore evidence record plus append-only event in one
version-checked transaction. The strict record contains only deployment ID,
the allowlisted access-check key/status, UTC observation/expiry, source
revision, and an evidence digest. It never stores credentials, customer/login
IDs, request IDs, resource names, raw responses, or provider errors. Evidence
expires after five minutes and does not authorize campaign creation or spend.

The paused-create job is a separate, fixed protocol. There is no storefront job
invocation route, and it accepts no deployment ID, request body, credential
selector, or command-line override. It can consume only an existing
`PAUSED_CREATE_APPROVED` authority record for the checked-in contract while the
same transaction observes its durable outbox in `DISPATCHING`; a direct or
scheduled worker invocation while `PENDING`, `FAILED`, or `DISPATCHED` is inert.
The v25
REST adapter first submits the exact atomic graph with `validateOnly=true` and
`partialFailure=false`, then submits the same operation graph with
`validateOnly=false`. Campaign, campaign criteria, ad groups, keywords, and ads
are all created `PAUSED`. A deterministic contract label is the idempotency
key. Creation is accepted only after a full provider readback matches the
reviewed statuses, $20 daily budget, $5 CPC ceiling, networks, geo criteria,
keywords, ad copy, URLs, and contract hash. Duplicate labels, pagination,
drift, and ambiguous provider outcomes fail fenced; raw account IDs, resource
names, responses, request IDs, credentials, and provider errors are never
written to Firestore or job output. No activation operation exists in this
slice.

The admin approval request also never invokes a job. After exact-owner WebAuthn
user verification, it consumes a signed five-minute proof reference and, in one
Firestore transaction, changes `SERVER_VALIDATED` to
`PAUSED_CREATE_APPROVED`, appends the authority event, and creates one sanitized
`PENDING` outbox row. Exact replay is idempotent; another proof, version,
contract, caps, or access-evidence digest conflicts. PIN, bearer, shared-admin,
and staff sessions cannot approve.

Outbox delivery belongs to a third, fixed zero-argument Cloud Run Job running
`python scripts/google_ads_paused_dispatcher_job.py`. It must use a separate
identity with only Firestore access and non-override invocation permission on
the single configured `google-growth-paused-create` job. It sends the official
Cloud Run v2 `:run` request with an empty JSON body: no request, environment,
argument, credential, or executable overrides are representable. Cloud Run's
acceptance leaves the row `DISPATCHING`; it is not provider success. Only the
worker's durable `PAUSED_CREATED` reconciliation settles `DISPATCHED`. A definite
4xx rejection or sanitized worker failure re-arms `PENDING`; an ambiguous timeout
or server failure stays leased as `DISPATCHING` so a possibly accepted worker can
finish, then becomes reclaimable after expiry. After three attempts the outbox
becomes `FAILED` and requires operator remediation. Duplicate dispatchers use a
lease and cannot submit concurrently. The dispatcher remains inert unless both
the approval and dispatch flags are explicitly true and the current app
revision matches the externally verified readiness revision.

## Checked-in zero-spend launch contract

The initial Search package lives at
`config/google_ads_launch_draft.json`. Validate it offline with:

```bash
python3 scripts/google_ads_launch_draft.py
```

The validator has no Google client dependency and makes no network request. It
fails if the campaign, campaign criteria, ad groups, keywords, or ads stop
being `PAUSED`; if the mode stops
being `VALIDATE_ONLY`; if the approval fields become non-null; or if the package
violates the dedicated-job/keyless-ADC identity contract or the initial
housing, attribution, budget, landing-page, or responsive search ad constraints.

The proposed test is one Search campaign with two high-intent ad groups:

| Ad group | Landing page | Positive match types |
|---|---|---|
| Local Inventory | `/inventory` | Exact + phrase only |
| Showroom Tours | `/appointments` | Exact + phrase only |

The proposal is **not approved**. Its average daily budget is $20, its explicit
single-day charging limit is $40, and its full-month charging limit is $608.
Those larger limits are recorded because Google says most campaigns may spend
up to 2× the average daily budget on a day and charge up to 30.4× it over a
month. The package also proposes a $5 maximum CPC ceiling and a stop-loss that
pauses the campaign on any configured failure threshold.

Mobile homes fall under Google's US housing-ad rules. The launch contract uses
a 50-mile radius, keeps every age/gender/parental-status group enabled, and
forbids ZIP-code, marital-status, and initial audience targeting. Search
Partners and Display expansion are also disabled for the first test.

Current primary references:

- [Google Ads API: create campaigns paused](https://developers.google.com/google-ads/api/docs/campaigns/create-campaigns)
- [Google Ads API: responsive search ad requirements](https://developers.google.com/google-ads/api/docs/responsive-search-ads/create-responsive-search-ads)
- [Google Ads: daily and monthly spending limits](https://support.google.com/google-ads/answer/10486637)
- [Google Ads: housing targeting restrictions](https://support.google.com/adspolicy/answer/16701755)
- [Google Ads: location-presence targeting](https://support.google.com/google-ads/answer/9376662)
- [Google Ads: negative keyword behavior](https://support.google.com/google-ads/answer/2453972)

## Phase A — account prerequisites

Record these in the password manager/operator checklist, not the repository:

1. Google Ads customer ID and, if used, manager customer ID.
2. Approved Google Ads API developer token.
3. GA4 property ID and web-stream measurement ID.
4. Search Console Domain-property ownership for the production domain.
5. Google Business Profile administrator access for the Huffman location.
6. An administrator who can add the dedicated service-account email as a Google
   Ads account user. Start with the least Ads access level that supports the
   read-only probe; only grant campaign-management access when paused campaign
   creation is approved.

User OAuth client ownership remains necessary only if the legacy Ads fallback
is chosen or a different Google product requires user consent. Do not assume
that Google Ads service-account access also grants Search Console or Business
Profile access; each product has its own property/account approval.

## Phase B — GCP APIs and secrets

Enable these APIs in `tho-ai-agent`:

- `googleads.googleapis.com`
- `searchconsole.googleapis.com`
- `analyticsadmin.googleapis.com`
- `analyticsdata.googleapis.com`
- `mybusinessbusinessinformation.googleapis.com`
- `businessprofileperformance.googleapis.com`

Store credentials under these names so the readiness audit can verify presence
without reading values:

- `google-ads-developer-token`
- `google-ads-customer-id`
- `google-ads-login-customer-id` (manager-account access only)

Create user-managed service accounts named `google-growth-control` and
`google-growth-dispatcher`. Do not grant either project-wide Editor, do not
attach either to the public storefront, and do not create keys. The first is
the Ads identity for the two provider-facing jobs; the second is the
secretless Firestore/outbox identity for the dispatcher job. The intended
execution surface has:

- `google-growth-control` as its attached service identity;
- no public-storefront binding on this identity's IAM policy and no storefront
  token-creator, service-account-user, or workload-identity-user grant;
- the `adwords` OAuth scope requested through ADC by the probe/client;
- Secret Manager access scoped only to the Ads developer-token and account-ID
  secrets;
- the exact access command `python scripts/google_ads_access_evidence_job.py`
  for `google-growth-control`, and the exact paused-create command
  `python scripts/google_ads_paused_worker_job.py` for
  `google-growth-paused-create`; each is split as the two-element command array
  `python`, script path, with an empty argument list; the source image revision
  is pinned in `APP_VERSION`, and command, credential, deployment, or
  executable override arguments/environment values are forbidden;
- job-resource IAM limited to `roles/run.invoker`,
  `roles/run.jobsExecutor`, or `roles/run.viewer`; none of these grants
  `run.jobs.runWithOverrides`. Custom and override-capable job roles fail the
  readiness audit. Privileged infrastructure administrators can replace the
  job itself and remain subject to the separate production-change gate;
- the dispatcher identity is the only execution-role member on the paused-create
  job; operator execution roles remain limited to the access-evidence and
  dispatcher jobs, never the provider target. Project-wide built-in Cloud Run
  execution/admin/developer roles fail readiness because they bypass that
  resource boundary;
- storefront, access-evidence, paused-create, and dispatcher runtimes use one
  immutable image digest and one exact `APP_VERSION`; separately verify and
  record the build provenance mapping from that digest to the candidate Git SHA;
- no public endpoint, campaign activation command, or spend authority.

The `google-growth-paused-dispatch` job must use the exact two-element command
array `python`, `scripts/google_ads_paused_dispatcher_job.py`, an empty argument
list, the exact revision-bound non-secret flags listed below, and no secret
binding. Give its `google-growth-dispatcher` identity only
`roles/datastore.user` at project scope and `roles/run.invoker` directly on
`google-growth-paused-create`; do not grant it any role on
`google-growth-control` or on itself. Storefront identity bindings on any of
the three jobs fail the readiness audit.

The storefront settings below default false/empty and must not be enabled by a
source merge alone:

- `THO_GOOGLE_ADS_PAUSED_CREATE_APPROVAL_ENABLED`
- `THO_GOOGLE_ADS_PAUSED_CREATE_CLOUD_READINESS_VERIFIED`
- `THO_GOOGLE_ADS_PAUSED_CREATE_IAM_VERIFIED`
- `THO_GOOGLE_ADS_PAUSED_CREATE_READINESS_REVISION` (must equal `APP_VERSION`)
- `THO_GOOGLE_ADS_PAUSED_CREATE_PROJECT`, `..._REGION`, and `..._JOB`
- `THO_GOOGLE_ADS_PAUSED_CREATE_DISPATCH_ENABLED` (separate execution gate)

Creating or changing the service accounts, jobs, IAM, API enablement, secret
bindings, Ads account invitation, or any live job execution is an external
operator gate. The checked-in code and injected-fake tests do not perform any
of those actions. A live access probe must be completed before a separately
approved paused-create execution; neither grants activation or spend authority.
Enabling approval/config and Ari's exact owner WebAuthn are one human gate. A
live dispatch that actually creates PAUSED provider resources is a later,
separate Ari gate. No activation/publish/spend state or control exists.

The Google Ads administrator must separately add the service-account email as
an account user. This is the step that grants Ads access; GCP IAM roles and API
enablement do not grant it.

The legacy user-OAuth fallback additionally needs:

- `google-ads-client-id`
- `google-ads-client-secret`
- `google-ads-refresh-token`

Business Profile API access must first be approved by Google, and its OAuth
user must have profile access. API enablement does not grant profile access by
itself.

## Phase C — measurement activation

1. Review the in-product privacy copy and the account's GA4 data-retention and
   ad-personalization settings.
2. Choose one primary loader: direct `GA4_MEASUREMENT_ID`, or a
   `GTM_CONTAINER_ID` whose published container owns GA4. Do not configure both
   to send the same property or page views will duplicate.
3. Deploy the ID to a zero-traffic candidate, accept analytics in a clean
   browser profile, and verify exactly one each of:
   - `page_view`
   - `generate_lead`
   - `schedule_appointment`
4. Decline analytics in another clean profile and verify no third-party script
   request is made while `/api/analytics` still records the first-party event.
5. Mark `generate_lead` and `schedule_appointment` as GA4 key events. Link GA4
   to Google Ads and import both conversion actions.

## Phase D — SEO properties

1. Verify the Search Console Domain property.
2. Submit the production `/sitemap.xml` and record the submission timestamp.
3. Confirm the Business Profile website and appointment link point to the
   canonical production domain.
4. Pull Search Console and Business Profile performance into BigQuery for the
   weekly revenue review; never treat API enablement as an SEO ranking switch.

## Phase E — paused campaigns

Create the initial Search campaigns through the Google Ads API with status
`PAUSED`. Use the validated checked-in launch contract as the source artifact;
the first API request must use the Ads API's validation-only mode before any
paused resource is created. Each ad group must map to a high-intent landing
surface and conversion:

| Intent | Landing surface | Primary conversion |
|---|---|---|
| manufactured homes near Huffman | `/inventory` | `generate_lead` |
| mobile homes with showroom visit | `/appointments` | `schedule_appointment` |
| specific available home/model | canonical detail URL | `generate_lead` |

Before requesting spend approval, attach a review artifact containing keyword
list, negatives, geo radius, ad copy, landing URL, conversion action, daily cap,
monthly maximum, and stop-loss rule. The checked-in launch contract contains
that artifact. Activation is a separate explicit gate.

## Definition of ready-to-spend

- Readiness audit is green.
- The sanitized live Ads access probe returns
  `account_access_validated: true` from the dedicated job identity.
- Consent allow/deny browser checks are green.
- Test lead and test appointment appear once in GA4 and once in the imported Ads
  conversion actions.
- Search Console sitemap is accepted; Business Profile link is verified.
- Campaigns remain `PAUSED` and the exact budget/stop-loss proposal is approved.

## Authentication references

- [Google Ads API service-account workflow](https://developers.google.com/google-ads/api/docs/oauth/service-accounts)
- [Google Ads API authorization and required headers](https://developers.google.com/google-ads/api/rest/auth)
- [Cloud Run service identity and keyless ADC](https://cloud.google.com/run/docs/securing/service-identity)
- [Application Default Credentials search order](https://cloud.google.com/docs/authentication/application-default-credentials)
