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
   Editor/Owner/Secret Accessor. The same-named Cloud Run Job must be attached
   to it, use the exact `python scripts/google_ads_access_evidence_job.py`
   command, and bind only the named Ads secrets from Secret Manager. The
   service account must have accessor rights on each bound secret itself.
2. Compatibility-only: all three legacy user-OAuth secrets exist. The audit
   reports this path, but it does not satisfy GCP-native strict readiness.

The preferred path needs only the developer-token and customer-ID Secret
Manager entries. The optional login-customer-ID entry is needed when the target
account is accessed through a Google Ads manager account. Legacy user-OAuth
secrets must be absent, and the public storefront must have no Ads credential
bindings. Exactly one measurement path (GA4 or GTM) must be configured.
Search Console and Business Profile API status remains advisory and does not
block Ads presence readiness. A green presence audit does **not** prove the
identity has Google Ads account access.

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

## Checked-in zero-spend launch contract

The initial Search package lives at
`config/google_ads_launch_draft.json`. Validate it offline with:

```bash
python3 scripts/google_ads_launch_draft.py
```

The validator has no Google client dependency and makes no network request. It
fails if the campaign, ad groups, or ads stop being `PAUSED`; if the mode stops
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

Create a user-managed service account named `google-growth-control`. Do not
grant it project-wide Editor, do not attach it to the public storefront, and do
not create a key. The intended execution surface is a dedicated Cloud Run Job
with:

- `google-growth-control` as its attached service identity;
- the `adwords` OAuth scope requested through ADC by the probe/client;
- Secret Manager access scoped only to the Ads developer-token and account-ID
  secrets;
- the exact command `python scripts/google_ads_access_evidence_job.py`, split
  as command `python` and the single argument
  `scripts/google_ads_access_evidence_job.py`; the source image revision is
  pinned in `APP_VERSION`, and command, credential, deployment, or executable
  override arguments/environment values are forbidden;
- no public endpoint, campaign activation command, or spend authority.

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
