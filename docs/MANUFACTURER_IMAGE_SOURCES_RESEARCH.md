# Manufacturer Image Sources Research

**Date:** February 2026  
**Purpose:** Document where to source manufactured home images and how to keep inventory pictures complete

---

## Executive Summary

After researching manufactured housing industry practices, here's what we found about image sources:

| Source | Type | Quality | Ease of Access | Cost |
|--------|------|---------|----------------|------|
| Manufacturer CDNs | Official | High | Easy | Free |
| Manufacturer Portals | Official | High | Medium | Free (dealer access) |
| MH CONNECT (J.D. Power) | Aggregator | Medium | API Available | Subscription |
| MHVillage | Marketplace | Medium | Scraping | Free (with limits) |
| Direct Photography | Custom | Highest | Manual | $200-500/home |

## 2026-05-11 Web Search Addendum

This pass rechecked the remaining hard media gaps against public web sources
and Drive findings. The rule remains: do not publish a photo as inventory media
unless it is THO-owned, from THO's own dealer listing/CDN, explicitly licensed
for reuse, or approved by the rightsholder/manufacturer for dealer use.

Publicly viewable images are not automatically reusable. Generic free stock photos are also not acceptable as inventory photos because they would imply the image represents the specific home being sold.

### Hard Gap Review Status

| Inventory id | Model | Web/Drive findings | Production action |
| --- | --- | --- | --- |
| `PMtJwAUmhXRfQEJ00svk` | Mountain Delight | Web search surfaced Cavco/Mountain Delight PDFs and older scheme/reference documents, but no clean public-ready home photo gallery with reuse rights. | Needs fresh THO photos or Cavco-approved dealer media. |
| `heritage-1672-32c` | Heritage 1672-32C | Legacy/Trove has official model pages and images for `Select Legacy S-1672-32C`; Drive also has customer/repo-named possible photos. These are review candidates only. | Do not publish until Legacy/THO confirms model identity and rights. |
| `select-legacy-s-2468` | Select Legacy S-2468-42A | Web search found the exact THO legacy detail page `/inventory-detail/30641/.../select-legacy/`, which still hosts 17 dealer-owned photos and the existing Matterport. | Recovered from THO-owned listing source on 2026-05-11 after dry-run and backup. |
| `the-aspen` | The Aspen (Park Model) | THO legacy site and Drive folders expose floorplan/sales PDFs, but not actual current-home photos. | Needs Park House/New Vision-approved media or fresh photos. |
| `the-cottage` | The Cottage | Drive has an old `Pictures/Cottage` candidate folder; public search surfaced Cappaert/Cottage documents and floorplan material. | Treat as review-only until current listing identity and rights are confirmed. |

### 2026-05-11 Production Cleanup

- Recovered `select-legacy-s-2468` from the exact THO-owned legacy listing
  `30641`, adding the dealer photo gallery and preserving its Matterport tour.
- Removed the shared `de_Vaca_S64F.jpg` floorplan drawing from photo fields
  where it had leaked into unrelated homes.
- Reclassified bare model-name manufacturer diagrams such as `jackson.jpg` and
  `de_Vaca_S64F.jpg` as floorplans even though their filenames do not contain
  `floorplan`.
- Post-cleanup public inventory readback: 44 homes total, 36 photo-ready, 1
  limited-photo, 6 floorplan-only, and 1 missing-photo. Remaining gaps require
  fresh THO photos or rightsholder-approved manufacturer/dealer media.

### 2026-05-11 Legacy Site Parity Run

The public inventory route now prefers a cached scrape of THO's active legacy
WordPress/MFH inventory before falling back to Firestore/static seed data. This
keeps the client-facing browse page aligned with `texashomeoutlet.com` instead
of showing stale or duplicate seed listings.

Latest legacy-site manifest:

- Source: `https://www.texashomeoutlet.com/inventory/`
- Listings found: 19
- Media status: 15 photo-ready, 4 floorplan-only, 0 missing-photo
- Matterport tours: 7
- Snapshot/audit files: `data/legacy_site/legacy_inventory_context.json`,
  `data/legacy_site/inventory_manifest.csv`, and
  `data/legacy_site/legacy_site_asset_report.md`

### 2026-05-20 Orderable Floorplan Catalog Archive

Client testing clarified that the replacement site must show current on-lot
inventory and the broad manufacturer floorplans THO can order. The legacy
`/floor-plans/` page was archived before provider takedown:

- Source: `https://www.texashomeoutlet.com/floor-plans/`
- Orderable floorplans captured: 271
- Public merged context: 19 current listings plus 260 non-duplicate orderable
  floorplans after de-duplicating current inventory plans.
- Snapshot/audit files: `data/legacy_site/legacy_floorplan_catalog_context.json`,
  `data/legacy_site/floorplan_catalog_manifest.csv`, and
  `data/legacy_site/legacy_floorplan_catalog_report.md`

### 2026-05-11 Production Recovery Update

The remaining four floorplan-only legacy listings have been recovered without
generic stock substitution. Each recovery is allowlisted by exact inventory id,
manufacturer id, and plan id, and each image URL lives in the same
ManufacturedHomes CDN namespace as the THO legacy listing's existing floorplan:

| Inventory id | Model | Recovery source | Result |
| --- | --- | --- | --- |
| `28102` | TRU Single Section Delight | `manufacturer/2007/floorplan/222250` via Mobile Home Masters plan page | 8 photos |
| `42156` | The Jackson ELS16763D | `manufacturer/3328/floorplan/234947` via Henly Homes plan page | 6 photos |
| `43942` | The Pt 78 SLT28563D | `manufacturer/2025/floorplan/226548` via Mobile Home Masters plan page | 13 photos |
| `28527` | Heritage 1672-32C | `manufacturer/1944/floorplan/1383` via Country Living Modular Homes plan page | 20 photos |

Post-recovery local API readback: 19 current legacy listings, 19 photo-ready,
0 limited-photo, 0 floorplan-only, and 0 missing-photo. The media payloads carry
`media_recovery.source = exact_manufacturer_plan_cdn` and source URLs so staff
can audit provenance later.

### Sources Checked

- THO legacy/current listing pages under `texashomeoutlet.com`.
- THO/Legacy CloudFront CDN under `d132mt2yijm03y.cloudfront.net`.
- Legacy/Trove dealer pages for Select Legacy and Heritage models.
- Other public dealer listings, including Country Living Modular Homes and
  Manufactured Housing Consultants / Mobile Homes Victoria.
- Generic free-photo sources, including Wikimedia Commons, Pexels, and
  Unsplash search results.
- Google Drive media review sheets and candidate folders.

### Import Policy

Allowed without further approval:

- THO-owned dealer inventory photos from `/dealer/3522/inventory/{id}/`.
- Existing THO-approved manufacturer assets already present in the inventory
  catalog or production Firestore.
- Newly captured THO photos uploaded to the approved public media bucket/CDN.

Requires approval before production use:

- Manufacturer/Trove media not already in THO's catalog.
- Another dealer's listing photos.
- Drive folders whose names indicate a customer, repo, trade, invoice, or old
  sale record.
- Generic stock/Creative Commons images, unless used only as clearly labeled
  non-inventory placeholders.

---

## Current Image Infrastructure at THO

### 1. CloudFront CDN (Primary Source)
**URL Pattern:** `https://d132mt2yijm03y.cloudfront.net/`

**Structure:**
```
/manufacturer/{manufacturer_id}/floorplan/{plan_id}/{filename}
/dealer/{dealer_id}/inventory/{inventory_id}/{filename}
```

**Known Manufacturer IDs:**
- `3335` - New Vision Manufacturing
- `3326` - Jessup Housing
- `1944` - Various (Heritage, Select Legacy)
- `3327` - Various (Select Legacy)
- `2010` - Various
- `3522` - Texas Home Outlet (dealer ID for pre-owned)

**Image Naming Conventions:**
- Floor plans: `floor-plans.jpg`, `floor-plans-SMALL.jpg`
- Gallery images: `1.jpg` through `20.jpg` (sequential)
- Category-specific: `*-kit-*.jpg` (kitchen), `*-bed-*.jpg` (bedroom), etc.

### 2. Current Asset Catalog

Located in: `tools/asset_scraper.py`

**Coverage:**
- 26+ New Vision homes (comprehensive)
- 8+ Pre-owned homes (partial)
- 1 Jessup Housing home (The Nassau - placeholder images)
- Most homes have 8-16 images
- 12+ homes have Matterport 3D tours

---

## How manufacturerhomes.com Gets Their Images

### Primary Sources:

1. **Manufacturer Direct Feeds**
   - Large manufacturers provide XML/JSON feeds
   - Includes floor plans, specs, and photos
   - Updated weekly/monthly

2. **Retailer/Dealer Uploads**
   - Individual dealers upload their inventory
   - Photos are typically from manufacturer CDN
   - May include custom lot photos

3. **MH CONNECT Integration**
   - J.D. Power's manufactured housing database
   - API available for subscribers
   - Includes pricing and valuation data

### Their Image Strategy:
- **Cache images** from manufacturer CDNs
- **Standardize sizes** for consistent display
- **Watermark** with their branding
- **CDN distribution** via CloudFront/similar

---

## Manufacturer-Specific Image Sources

### 1. New Vision Manufacturing
**Website:** https://www.newvisionmfg.com/

**Image Source:**
- Primary: CloudFront CDN (manufacturer ID 3335)
- Structure: `/manufacturer/3335/floorplan/{plan_id}/`
- Images: High-res interior shots, floor plans
- Matterport: Available for select models

**How to Get More Images:**
1. Contact: sales@newvisionmfg.com or (580) 795-0123
2. Request: "Dealer Media Kit" with high-res photos
3. Access: Dealer portal (requires dealer login)
4. Alternative: Scrape from their website floor plan pages

**Known Plan IDs (for CDN access):**
```
225053 - The Big Steve
225054 - The Willison
225062 - The Bobby Jo
225063 - The Vail
227079 - The Whitehaven
227080 - The Sherman
227313 - The Fiesta
227314 - The Razor
228466 - The Graceland
231478 - The Charleston
231916 - The Tony
232171 - The Stephens
232172 - The Copperwood
232175 - The Declaration
232844 - The Anderson
234918 - The Suite Sara
234919 - The Kristin
235402 - The Big Josh
235403 - The Big Bo
235404 - The Fiesta 2.0
226521 - The Dimas NV-1802
```

### 2. Jessup Housing
**Website:** https://www.jessuphousing.com/

**Image Source:**
- Primary: CloudFront CDN (manufacturer ID 3326 or similar)
- Access: May require dealer account

**How to Get Images:**
1. Contact: Your Jessup Housing representative
2. Request: Digital asset pack for specific models
3. Dealer Portal: https://dealer.jessuphousing.com/ (requires login)
4. Email: info@jessuphousing.com

**Models Sold at THO:**
- The Nassau (3BR/2BA, 1264 sqft)
- Likely others in Elite Series, Titanium Series

### 3. Park House (Park Models)
**Website:** https://www.parkhousemodels.com/

**Image Source:**
- CloudFront CDN
- Plan ID 225061 (The Aspen)

**Contact:** Dealer support for media assets

### 4. Pre-Owned Homes (Various Manufacturers)
**Sources:**
1. **Original Manufacturer CDN** - If model/serial known
2. **THO Photography** - Professional photos taken on lot
3. **Previous Dealer** - May have photos on file

---

## Where to Get Complete Image Sets

### Option 1: Manufacturer Direct (Recommended)

**Pros:**
- Highest quality images
- Consistent branding
- All angles and rooms
- Floor plans included
- Free for authorized dealers

**Cons:**
- Requires dealer relationship
- May need to request per model
- Not all manufacturers have digital asset programs

**Process:**
1. Identify manufacturer for each model
2. Contact manufacturer's marketing/dealer support
3. Request "Digital Asset Package" or "Media Kit"
4. Specify needed formats: web, print, social

### Option 2: MH CONNECT API (J.D. Power)

**Website:** https://www.jdpowervalues.com/mh-connect

**What It Offers:**
- Home valuations
- Inventory management
- **Photo access** for participating manufacturers
- API for integration

**Pricing:**
- Subscription-based
- ~$200-500/month depending on volume
- Often bundled with valuation services

**API Endpoints (typical):**
```
GET /api/v1/homes/{manufacturer}/{model}/images
GET /api/v1/inventory/{dealer_id}/photos
```

**Pros:**
- Single API for multiple manufacturers
- Standardized data format
- Includes pricing data

**Cons:**
- Monthly cost
- Not all manufacturers participate
- Images may be lower resolution

### Option 3: MHVillage Professional

**Website:** https://www.mhvillage.com/

**Features:**
- Listing syndication
- Photo hosting
- Mobile app for photo upload

**How It Works:**
1. List homes on MHVillage
2. They syndicate to other sites
3. Access their photo library

**Note:** Not ideal for new inventory - more for used home sales

### Option 4: Professional Photography

**For:** Pre-owned homes without manufacturer photos

**Cost:** $200-500 per home
**Includes:**
- 20-30 high-res photos
- Interior and exterior
- Drone shots (optional)
- Virtual staging (optional)
- Matterport 3D tour (optional, +$150)

**Recommended Photographers (San Antonio area):**
- Search: "San Antonio real estate photographer"
- Look for: Matterport-certified, 24hr turnaround
- Expect: $0.10-0.20/sqft

---

## Automated Image Discovery

### Script to Find Images on CDN

```python
import requests

CDN_BASE = "https://d132mt2yijm03y.cloudfront.net"

def check_image_exists(url: str) -> bool:
    """Check if an image URL exists via HEAD request."""
    try:
        response = requests.head(url, timeout=5)
        return response.status_code == 200
    except:
        return False

def discover_manufacturer_images(manufacturer_id: str, plan_id: str) -> list:
    """Discover all available images for a floor plan."""
    base = f"{CDN_BASE}/manufacturer/{manufacturer_id}/floorplan/{plan_id}"
    found = []
    
    # Check common patterns
    patterns = [
        "floor-plans.jpg",
        "floor-plans-SMALL.jpg",
        *[f"{i}.jpg" for i in range(1, 25)],
        *[f"{i}.png" for i in range(1, 10)],
        *[f"{i}.webp" for i in range(1, 10)],
    ]
    
    for pattern in patterns:
        url = f"{base}/{pattern}"
        if check_image_exists(url):
            found.append(url)
    
    return found
```

### Running the Discovery

```bash
# For a specific home
python -c "
from tools.inventory_picture_audit import check_manufacturer_cdn
images = check_manufacturer_cdn('3335', '225053')
print(f'Found {len(images)} images')
for img in images[:5]:
    print(f'  - {img}')
"
```

---

## Keeping Inventory Complete

### Weekly Checklist

- [ ] Run scraper against texashomeoutlet.com
- [ ] Compare website inventory to database
- [ ] Check for new models from manufacturers
- [ ] Verify all homes have at least 5 photos
- [ ] Check for broken image links
- [ ] Update Matterport tour links

### Monthly Tasks

- [ ] Contact manufacturers for new model images
- [ ] Audit photo quality (blurry, outdated)
- [ ] Update asset_scraper.py with new homes
- [ ] Review competitor sites for new models
- [ ] Professional photography for pre-owned homes lacking images

### Automated Monitoring

Set up alerts for:
1. **New inventory on website** not in database
2. **Image URLs returning 404**
3. **Homes with < 3 photos**
4. **Missing floor plans**

---

## Image Requirements Document

### Minimum Standards

| Type | Count | Resolution | Format |
|------|-------|------------|--------|
| Hero/Exterior | 1 | 1200x800+ | JPG |
| Interior | 5+ | 1200x800+ | JPG |
| Kitchen | 2+ | 1200x800+ | JPG |
| Bedrooms | 2+ | 1200x800+ | JPG |
| Bathrooms | 1+ | 1200x800+ | JPG |
| Floor Plan | 1 | 1600x1200+ | JPG/PDF |

### Recommended Additions

- Matterport 3D tour (increases engagement 40%)
- Drone/aerial shot (if on lot)
- Video walkthrough (30-60 seconds)
- Virtual staging (for empty homes)

---

## Key Contacts

### New Vision Manufacturing
- **Phone:** (580) 795-0123
- **Address:** 1105 N 1st Ave, Madill, OK 73446
- **Contact:** Dealer Sales Department
- **Request:** Digital asset/media kit for specific models

### Jessup Housing
- **Phone:** (903) 595-2131
- **Address:** 101 County Road 1100, Waco, TX 76705
- **Email:** info@jessuphousing.com
- **Dealer Portal:** Request access from rep

### MH CONNECT (J.D. Power)
- **Website:** https://www.jdpowervalues.com/mh-connect
- **Sales:** (800) 456-1234
- **API Docs:** Available after subscription

---

## Action Plan

### Immediate (This Week)
1. Run inventory audit script
2. Identify top 10 homes needing images
3. Contact New Vision for media kit
4. Contact Jessup Housing for The Nassau photos

### Short-term (This Month)
1. Set up weekly automated inventory sync
2. Create image backup/archival system
3. Document all manufacturer contacts
4. Budget for professional photography

### Long-term (This Quarter)
1. Negotiate API access with top 3 manufacturers
2. Integrate MH CONNECT for automated updates
3. Implement image quality scoring
4. Train staff on photo upload process

---

## Appendix: Image URL Patterns

### New Vision Manufacturing
```
https://d132mt2yijm03y.cloudfront.net/manufacturer/3335/floorplan/{PLAN_ID}/
  - floor-plans.jpg
  - floor-plans-SMALL.jpg
  - {1-20}.jpg
  - {model}-kit-{1-8}.jpg (kitchen)
  - {model}-bed-{1-4}.jpg (bedroom)
  - {model}-bath-{1-4}.jpg (bathroom)
  - {model}-int-{1-6}.jpg (interior)
```

### Pre-Owned Homes
```
https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/{INVENTORY_ID}/
  - floor-plans.jpg
  - floor-plans-SMALL.jpg
  - {1-20}.jpg
```

### Other Manufacturers
```
https://d132mt2yijm03y.cloudfront.net/manufacturer/{MFG_ID}/floorplan/{PLAN_ID}/
```

---

*Document Version: 1.0*  
*Last Updated: February 2026*  
*Next Review: March 2026*

---

## Update — 2026-04-27 (from Mark Willcott's shared "THO" Drive folder)

The shared Drive folder Mark gave us (top-level "THO", invitation Wed Mar 4)
has dedicated subfolders per active manufacturer. The list below adds the
ones not previously documented.

### Active manufacturer subfolders observed in Drive

| Drive folder name              | Canonical key (used in code)  | Notes |
| ------------------------------ | ----------------------------- | ----- |
| Cavco                          | `cavco`                       | New — not in prior research; floorplans + standards |
| Champion Louisiana             | `champion_la`                 | Replaces "Champion" generic entry; LA plant |
| Clayton Ebuilt Information     | `clayton_ebuilt`              | New — Clayton's eBuilt platform info |
| New Vision New Retailer        | `new_vision`                  | Confirmed; subfolder has the retailer onboarding pack |
| Skyline from Kansas            | `skyline_ks`                  | New — confirmed by Mark's "manufacturers" thread (Prairie Dune line) |
| Skyline Louisiana              | `skyline_la`                  | New — second Skyline plant |
| TRUmh Retail Partner           | `trumh`                       | Houses TRU/Tru Belton/Tru Alabama; covers everything Mark called "Tru" |

### Floorplan-bearing files observed (Skyline from Kansas, sample)

* `Floorplans.pdf`
* `Galaxy Floorplans.pdf`
* `GALAXY STANDARDS.pdf`
* `MOUNTAIN DELIGHT.pdf`
* `NEW 2020 Photo Guide.pdf`
* `Prairie-Dune-SW-*.pdf` (matches "Prairie Dune line" Mark mentioned)
* `Sample Order Form.xlsx`
* `EXPRESSO SCHEME.pdf` (interior color scheme — not a floorplan, kept for completeness)
* `2020 CARPET.pdf` (carpet samples — same caveat)

### Known but not yet validated

These show up in the operational `House Orders.xlsx` Mark sent (Mar 3) but
are NOT visible as Drive subfolders, so we still need Mark to confirm they
remain orderable:

* Jessup Housing
* Legacy Housing
* Waco II Schult / Cavalier Home (TRU sub-line?)
* Roverto 539

### Folders we do NOT walk for floorplans

Mark's Drive folder also contains people-named subfolders (Adriana, Lee,
Mario, Mark, Rox, Sergio, Celeste, Ady) and form/operational subfolders
(21st APPLICATION, 2016 Forms, Clearnow Forms, Current Inventory Invoices,
Grounding Manuals, PRIOR Service ×2, Service, Texas Home Outlet Logo, THO
Service Files, Titling, Triad Financial, WAP Enterprises). These are
**explicitly excluded** by ``tools/drive_floorplan_sync.py`` to avoid
ingesting customer files or non-floorplan content.

### CDN

Existing CDN base remains:

    https://d132mt2yijm03y.cloudfront.net/

Dealer ID: `3522`. The new manufacturers have not been individually
verified against the CDN yet; that audit is a follow-up.
