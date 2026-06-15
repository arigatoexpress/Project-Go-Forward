# Notion → Mira Bridge

**Workstream:** `notion-bridge`
**Status:** Implemented / ready for env wiring

## What it does

Lets the Mira bridge endpoints source from Notion instead of Firestore, without
changing their PII-redacted response shape:

| Endpoint | Notion source (when configured) | Firestore source (default) |
|----------|---------------------------------|----------------------------|
| `/api/v1/mira/installations/summary` | Delivery Tracker DB | `service_requests` |
| `/api/v1/mira/installations/recent`  | Delivery Tracker DB | `service_requests` |
| `/api/v1/mira/feedback/summary`      | CS survey DB        | `feedback`         |
| `/api/v1/mira/feedback/recent`       | CS survey DB        | `feedback`         |

Each response carries a `"source": "notion"` or `"source": "firestore"` field so
the operator can confirm which backend served it. Aggregation is identical
either way, so the schema is stable regardless of source.

## How the fallback works

Notion is the **preferred** source, activated per-domain only when both the
token AND that domain's DB id are set:

- Installations use Notion when `NOTION_TOKEN` **and**
  `NOTION_DELIVERY_TRACKER_DB_ID` are set.
- Feedback uses Notion when `NOTION_TOKEN` **and** `NOTION_CS_SURVEY_DB_ID` are
  set.

If either var for a domain is missing, that endpoint keeps reading Firestore.
Any Notion HTTP/parse error is logged and degrades to an empty result set — the
bridge never returns a 500.

## Get an internal integration token

1. Go to <https://www.notion.so/my-integrations> → **New integration**.
2. Name it (e.g. `THO Mira Bridge`), pick the workspace, **Read content** only.
3. Copy the **Internal Integration Secret** (`secret_…`) → `NOTION_TOKEN`.

## Share the two databases with the integration

For **each** database (Delivery Tracker and CS survey): open it, click the `•••`
menu → **Connections** → **Connect to** → select your integration. The
integration can only read databases that have been explicitly shared with it.

## Find the database IDs

Open a database as a full page; the URL is
`https://www.notion.so/<workspace>/<DB_ID>?v=<view_id>`. The 32-character hex
string before `?v=` is the database id (hyphenated or unhyphenated both work).

- Delivery Tracker id → `NOTION_DELIVERY_TRACKER_DB_ID`
- CS survey id → `NOTION_CS_SURVEY_DB_ID`

## Set the env vars

```bash
NOTION_TOKEN=secret_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
NOTION_DELIVERY_TRACKER_DB_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
NOTION_CS_SURVEY_DB_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

In production, store these in Secret Manager and mount them as env vars on the
Cloud Run service.

## Expected Notion columns (PII-redacted by contract)

Only non-PII columns are read; customer name/email/phone columns are ignored.
Column names are matched case-insensitively with a few aliases.

- **Delivery Tracker:** `Status`, `Issue Type`, `Warranty Claim` (checkbox),
  `Warranty Status`, `Assigned Contractor`, `Created At` (date), `Deal ID`.
- **CS survey:** `Rating` (number), `Sentiment`, `Source`, `Created At` (date),
  `Deal ID`.

## Testing

```bash
.venv/bin/python -m pytest tests/test_notion_client.py tests/test_mira_routes.py -q
```
