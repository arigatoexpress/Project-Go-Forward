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
