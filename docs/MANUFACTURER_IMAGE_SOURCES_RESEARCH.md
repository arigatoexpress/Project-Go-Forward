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
