# Inventory Picture Audit Report

**Generated:** 2026-02-25T09:15:27.441395

## Summary

| Metric | Count | Percentage |
|--------|-------|------------|
| Total homes on website | 20 | 100% |
| In database | 0 | 0.0% |
| In asset catalog | 0 | 0.0% |
| With 5+ photos | 0 | 0.0% |
| With Matterport | 0 | 0.0% |

## Action Items

- [ ] Add 20 homes to Firestore database
- [ ] Add 20 homes to asset_scraper.py
- [ ] Source additional photos for 20 homes

## Homes Missing from Database (20

- NEW YEAR CLEARANCE SALE / TRU Single Section Delight
- NEW YEAR CLEARANCE SALE / TRU Single Section Glory
- The Promotional Series / The Nassau FAC28483A
- The Elite Series / The Jackson ELS16763D
- Independent / Anatolia XL SLT28683AH
- Epic Experience / The Mariner CEE16763EH
- Premier / Creole 3256H32447
- Premier / Bourbon 3276H42398
- Alpine Series / El Capitan 32SAP32563AH
- TRU Multi Section / Triumph TRU28765AH
- TRU Multi Section / Satisfaction TRU28483RH
- Solution / The Pt 78 SLT28563D
- PRE-OWNED / Big Blue
- The Promotional Series / The Rosewood FAC28644A
- PRE-OWNED / Heritage 1672-32C
- PRE-OWNED / Select Legacy S-1672-32B
- PRE-OWNED / Select Legacy S-2468-42A
- PRE-OWNED / Select S-1272-32A
- PRE-OWNED / Select S-1256-21A
- PRE-OWNED / Heritage 1684-32A

## Homes Missing from Asset Catalog (20

- NEW YEAR CLEARANCE SALE / TRU Single Section Delight
- NEW YEAR CLEARANCE SALE / TRU Single Section Glory
- The Promotional Series / The Nassau FAC28483A
- The Elite Series / The Jackson ELS16763D
- Independent / Anatolia XL SLT28683AH
- Epic Experience / The Mariner CEE16763EH
- Premier / Creole 3256H32447
- Premier / Bourbon 3276H42398
- Alpine Series / El Capitan 32SAP32563AH
- TRU Multi Section / Triumph TRU28765AH
- TRU Multi Section / Satisfaction TRU28483RH
- Solution / The Pt 78 SLT28563D
- PRE-OWNED / Big Blue
- The Promotional Series / The Rosewood FAC28644A
- PRE-OWNED / Heritage 1672-32C
- PRE-OWNED / Select Legacy S-1672-32B
- PRE-OWNED / Select Legacy S-2468-42A
- PRE-OWNED / Select S-1272-32A
- PRE-OWNED / Select S-1256-21A
- PRE-OWNED / Heritage 1684-32A

## Homes Needing More Photos (20

- NEW YEAR CLEARANCE SALE / TRU Single Section Delight (currently has 0 photos)
- NEW YEAR CLEARANCE SALE / TRU Single Section Glory (currently has 0 photos)
- The Promotional Series / The Nassau FAC28483A (currently has 0 photos)
- The Elite Series / The Jackson ELS16763D (currently has 0 photos)
- Independent / Anatolia XL SLT28683AH (currently has 0 photos)
- Epic Experience / The Mariner CEE16763EH (currently has 0 photos)
- Premier / Creole 3256H32447 (currently has 0 photos)
- Premier / Bourbon 3276H42398 (currently has 0 photos)
- Alpine Series / El Capitan 32SAP32563AH (currently has 0 photos)
- TRU Multi Section / Triumph TRU28765AH (currently has 0 photos)
- TRU Multi Section / Satisfaction TRU28483RH (currently has 0 photos)
- Solution / The Pt 78 SLT28563D (currently has 0 photos)
- PRE-OWNED / Big Blue (currently has 0 photos)
- The Promotional Series / The Rosewood FAC28644A (currently has 0 photos)
- PRE-OWNED / Heritage 1672-32C (currently has 0 photos)
- PRE-OWNED / Select Legacy S-1672-32B (currently has 0 photos)
- PRE-OWNED / Select Legacy S-2468-42A (currently has 0 photos)
- PRE-OWNED / Select S-1272-32A (currently has 0 photos)
- PRE-OWNED / Select S-1256-21A (currently has 0 photos)
- PRE-OWNED / Heritage 1684-32A (currently has 0 photos)

## Image Sources

### Current Sources
1. **texashomeoutlet.com** - Primary website with all listings
2. **CloudFront CDN** (d132mt2yijm03y.cloudfront.net) - Manufacturer images
3. **Matterport** - 3D tours for select homes

### Manufacturer Image URLs
- New Vision Manufacturing: `https://d132mt2yijm03y.cloudfront.net/manufacturer/3335/floorplan/{plan_id}/{filename}`
- Pre-owned homes: `https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/{id}/{filename}`

### Known Manufacturers at THO
- New Vision Manufacturing (ID: 3335)
- Jessup Housing (ID: 3326)
- Park House (ID: varies)
- Various pre-owned manufacturers

## Recommendations

1. **Immediate Actions:**
   - Run scraper weekly to catch new inventory
   - Sync database with website inventory weekly
   - Add all missing homes to asset_scraper.py

2. **Photo Sourcing Strategy:**
   - Contact manufacturers directly for high-res image packs
   - Use manufacturer portals/APIs if available
   - Schedule professional photography for pre-owned homes without images

3. **Long-term:**
   - Negotiate direct API access with major manufacturers
   - Set up automated image sync from manufacturer feeds
   - Create standardized image requirements document

