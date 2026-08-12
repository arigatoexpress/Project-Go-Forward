# THO Inventory And Floorplan Catalog Operations

This app now treats public homes as three explicit groups:

- `available_now`: live/current inventory listings.
- `pre_owned`: live/current pre-owned listings.
- `orderable_floorplan`: manufacturer floorplans customers can ask Texas Home Outlet to order or customize.

## What Clients See

The public Inventory page shows all three groups together, with filters for
Available Now, Orderable, and Pre-Owned. Orderable floorplans are labeled
`Orderable`, not `Available`, so customers see the broad manufacturer offering
without confusing catalog plans for homes already on the lot.

## Source Order

1. Archived current-listing snapshot in `data/legacy_site/legacy_inventory_context.json`.
2. Archived orderable catalog snapshot from the legacy `/floor-plans/` page.
3. Approved local manufacturer asset catalog in `tools/asset_scraper.py` as an
   offline/development fallback.
4. Firestore/seed fallback only when the archived/current listing path is unavailable.

The live legacy crawl remains available as a manual refresh path, but the public
site should not depend on ManufacturedHomes.com staying online after cutover.

The archived orderable catalog lives in
`data/legacy_site/legacy_floorplan_catalog_context.json`. It was generated from
`https://www.texashomeoutlet.com/floor-plans/` on May 20, 2026 and captured
271 orderable floorplans before provider takedown. The public API de-duplicates
those against archived current on-lot homes and currently exposes 19 current listings
plus 260 non-duplicate orderable floorplans when the live inventory crawl is
available or the current-listing archive is loaded.

The May 6 Drive package, `THO Inventory Media Drive Package 2026-05-06
Post-Enrichment`, remains the safe reference package for reconciliation. Its
summary says it contains 44 production inventory rows, 42 rows with floorplan
URLs, 24 Matterport tours, and a redacted House Orders sheet. Do not import raw
House Orders data directly into the public site.

## Freshness And Source Truth

`GET /api/marketing/inventory-context` reports a PII-free `source_status`:

- `requested`: configured `INVENTORY_SOURCE` (`legacy`, `firestore`, or `auto`)
- `selected_path`: the path that actually served the response
- `reported_source`: the provenance supplied by that path
- `freshness`: `fresh`, `stale`, or `unknown`
- `retrieved_at`, `age_days`, and `stale_after_days`

A usable payload can still be stale. The endpoint keeps serving the known
catalog to avoid a blank storefront, adds `inventory_source_stale` or
`inventory_source_freshness_unknown` to `warnings`, and exposes the same signal
as the soft `inventory` check in `/readyz`.

`INVENTORY_SOURCE=auto` is intentionally fail-closed. It switches only when a
result has all three pieces of evidence: `source=firestore_inventory`, a fresh
source timestamp, and at least `INVENTORY_FIRESTORE_MIN_HOMES` homes. The
current marketing loader can fall back from Firestore to local JSON or sample
data, so it reports `inventory_fallback_chain` and is not eligible for an
automatic switch merely because it returned homes.

### Dated production evidence — 2026-08-12

The deployed legacy snapshot reports `retrieved_at=2026-05-11T23:49:10Z`, 19
current homes, and 260 de-duplicated orderable floorplans (279 total). A
read-only Firestore projection found 19 `AVAILABLE` documents, all with
`source=texashomeoutlet.com`, no freshness timestamps, and exactly the same 19
identifiers and model names as the May 11 snapshot. Therefore changing to
Firestore would change storage paths, not establish fresher inventory.

Do not set `INVENTORY_SOURCE=firestore` or promote `auto` on this evidence. A
future activation must first provide:

1. an operator-approved current inventory export;
2. a dry-run reconciliation with explicit serial allow-list and no PII/cost;
3. a strict Firestore-only public loader with no JSON/sample fallback;
4. an approved-sync timestamp exposed as source provenance; and
5. candidate evidence showing `reported_source=firestore_inventory`,
   `freshness=fresh`, expected current-home counts, and media parity.

Read the live signal without dumping individual listings:

```bash
curl -fsS https://www.texashomeoutlet.com/api/marketing/inventory-context \
  | jq '{source, source_status, warnings, current_inventory_count, orderable_floorplans, total_inventory}'
curl -fsS https://www.texashomeoutlet.com/readyz \
  | jq '{ready, inventory: .checks.inventory}'
```

## Adding Or Editing Inventory

Use this path for current homes:

- Add or update the home in the live inventory source or admin inventory feed.
- Include manufacturer, model name, bedrooms, bathrooms, square feet, dimensions,
  price when approved, status, floorplan URL, and Matterport ID when available.
- Add dealer photos first when they exist. Manufacturer/media-kit photos are
  acceptable for orderable floorplans, but actual lot photos are preferred for
  available-now homes.
- Run the focused inventory tests before publishing or merging.

Use this path for orderable manufacturer floorplans:

- If the legacy provider site is still online, refresh the snapshot with:
  `python3 tools/legacy_site_crawler.py --floorplans --max-pages 35 --limit 500 --output-dir data/legacy_site`.
- If the legacy provider site is offline, add the floorplan to
  `tools/asset_scraper.py` with `is_new: True`.
- Include `manufacturer`, `manufacturer_id` or `legacy_plan_id`, `plan_id`,
  `beds`, `baths`, `sqft`, `dims`, `floor_plan`, approved `images`, and
  `matterport_id` if available.
- Do not add customer names, addresses, finance notes, SSNs, raw contracts, or
  private Drive links.

## Verification

Run these before marking an inventory/catalog change ready:

```bash
python -m pytest tests/test_catalog_floorplans.py tests/test_api_v1.py::test_marketing_inventory_context_appends_orderable_catalog_to_live_inventory tests/test_frontend_readability.py
cd frontend && npm run build
```

After deployment, read back:

```bash
curl -fsS https://tho.sapphirealpha.xyz/api/marketing/inventory-context
```

Confirm `orderable_floorplans` is greater than zero and that homes with
`inventory_kind: orderable_floorplan` show `status: Orderable`.
