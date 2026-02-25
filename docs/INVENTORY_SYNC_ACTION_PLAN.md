# Inventory Sync Action Plan

**Objective:** Complete inventory picture sync using manufacturer and free options only.

---

## Phase 1: Run Sync Tools (Today)

### Step 1: Preview Website Inventory
```bash
cd ~/Project-Go-Forward
python3 tools/sync_inventory_from_website.py --dry-run
```

This will show you what would be synced without making changes.

### Step 2: Review the Report
Check `data/inventory_sync_DRY_RUN_*.txt` for:
- How many homes would be added
- Which manufacturers are represented
- Any potential issues

### Step 3: Sync to Firestore (When Ready)
```bash
python3 tools/sync_inventory_from_website.py --force
```

**⚠️ WARNING:** This will write to your live database. Review the dry run first!

---

## Phase 2: Manufacturer Outreach (This Week)

### Day 1: Make Phone Calls
- [ ] **New Vision Manufacturing:** (580) 795-0123
  - Ask for dealer portal access
  - Request media kit for all 20 models
  - Get direct contact email
  
- [ ] **Jessup Housing:** (903) 595-2131
  - Request images for The Nassau, The Jackson
  - Ask about dealer portal

### Day 2-3: Send Emails
Use templates in `docs/MANUFACTURER_OUTREACH_TEMPLATES.md`

- [ ] Email New Vision (follow-up from call)
- [ ] Email Jessup Housing (follow-up from call)
- [ ] Research contacts for TRU, Premier, Epic, Independent, Alpine

### Day 4-7: Follow Up
- [ ] Call back manufacturers who haven't responded
- [ ] Send follow-up emails
- [ ] Document who you've spoken with

---

## Phase 3: Process Received Assets (As They Arrive)

### For Each Manufacturer Response:

1. **Download and Organize**
   ```
   data/manufacturer_assets/
   ├── new_vision/
   │   ├── big_steve/
   │   │   ├── kitchen_01.jpg
   │   │   ├── bedroom_01.jpg
   │   │   └── floor_plan.jpg
   │   └── ...
   ├── jessup/
   └── ...
   ```

2. **Optimize Images**
   - Resize to max 1920px width
   - Compress for web (aim for <200KB each)
   - Convert to JPG if needed

3. **Upload to Storage**
   Options (free/cheap):
   - **Cloudflare Images** - $5/month, unlimited
   - **AWS S3** - Pay per GB (~$0.023/GB)
   - **Keep using existing CDN** (if accessible)

4. **Update asset_scraper.py**
   Add new image URLs to `PROPERTY_ASSETS` dictionary

5. **Test**
   - Check images load on AI platform
   - Verify in Document Center
   - Test in chat responses

---

## Phase 4: Handle Missing Images (Free Options)

### Option A: Scrape from Manufacturer Websites

For manufacturers without APIs, scrape their public websites:

```python
# Example for New Vision
import requests
from bs4 import BeautifulSoup

def scrape_new_vision_floorplan(plan_id):
    url = f"https://www.newvisionmfg.com/floor-plan/{plan_id}"
    soup = BeautifulSoup(requests.get(url).text)
    images = soup.select('.gallery img')
    return [img['src'] for img in images]
```

### Option B: Use Existing CDN Discovery

Try to find images on the CloudFront CDN:

```bash
# Test if images exist at common URLs
curl -I https://d132mt2yijm03y.cloudfront.net/manufacturer/3326/floorplan/224001/1.jpg
```

### Option C: Screenshot from Website

For critical missing images:
1. Go to texashomeoutlet.com
2. Find the home's detail page
3. Take high-res screenshots
4. Use as temporary placeholders

### Option D: Generic Placeholders by Category

Create/use generic images:
- Generic kitchen for homes without kitchen photos
- Generic bedroom for homes without bedroom photos
- Clearly mark as "Representative Image"

---

## Phase 5: Verify and Monitor

### Weekly Checks

- [ ] Run `python3 tools/inventory_picture_audit.py`
- [ ] Check for broken image links
- [ ] Verify new homes from website are in database
- [ ] Count homes with < 5 photos

### Monthly Reviews

- [ ] Update asset_scraper.py with new manufacturer images
- [ ] Re-contact manufacturers who haven't responded
- [ ] Review website vs database sync status
- [ ] Check for new models from manufacturers

---

## Quick Wins (Do These First)

### 1. Fix The Nassau (Jessup Housing)
**Status:** Currently using placeholder images
**Action:** Contact Jessup immediately
**Impact:** High (popular model)

### 2. Add TRU Homes Images
**Status:** New models on website, no images in system
**Action:** Research TRU Homes contact or scrape their site
**Impact:** High (clearance sale models)

### 3. Verify Pre-Owned Images
**Status:** 6 pre-owned homes need verification
**Action:** Check if images are on CDN or need photography
**Impact:** Medium

### 4. Sync Database
**Status:** 20 homes on website, need to verify in database
**Action:** Run sync script
**Impact:** Critical

---

## Budget-Free Resources

### Image Storage
- **Current:** CloudFront CDN (already working)
- **Alternative:** GitHub + jsDelivr CDN (free, 100GB limit)
- **Alternative:** Cloudflare Pages (free, unlimited)

### Image Processing
- **TinyPNG.com** - Free compression (20 images/batch)
- **Squoosh.app** - Free Google tool
- **ImageMagick** - Free CLI tool (already installed)

### Contact Research
- **LinkedIn** - Find manufacturer marketing contacts
- **Manufacturer websites** - Dealer/Partner sections
- **Industry directories:**
  - https://www.manufacturedhomes.com/manufacturers/
  - https://www.mhi.org/

---

## Success Metrics

Track these numbers weekly:

| Metric | Current | Target | Status |
|--------|---------|--------|--------|
| Homes in database | 0* | 20 | ⬜ |
| Homes with 5+ photos | 0* | 20 | ⬜ |
| Homes with floor plans | ? | 20 | ⬜ |
| Homes with Matterport | ? | 10 | ⬜ |
| Manufacturers contacted | 0 | 7 | ⬜ |
| Manufacturer responses | 0 | 3+ | ⬜ |

*Based on audit showing 0% coverage - need to verify actual state

---

## Troubleshooting

### Problem: Manufacturer won't respond
**Solution:** 
- Try LinkedIn to find marketing manager
- Call instead of email
- Mention you're a paying dealer
- Set deadline: "Need by Friday for launch"

### Problem: Images are too large
**Solution:**
```bash
# Resize with ImageMagick
mogrify -resize 1920x1080> *.jpg
# Compress
mogrify -quality 85 *.jpg
```

### Problem: Can't find manufacturer contact
**Solution:**
- Check your purchase invoices for rep contacts
- Ask other dealers in your network
- Check MH CONNECT directory
- Search "[Manufacturer] dealer portal"

### Problem: CDN images are 404
**Solution:**
- Check if URL format changed
- Try alternate naming (1.jpg vs 01.jpg)
- Contact manufacturer to confirm URLs
- Use Wayback Machine to find old images

---

## Timeline

| Week | Actions |
|------|---------|
| **Week 1** | Run sync, contact New Vision & Jessup, fix The Nassau |
| **Week 2** | Contact remaining manufacturers, process received assets |
| **Week 3** | Follow-ups, scrape manufacturer sites for missing images |
| **Week 4** | Verify all homes have images, document gaps |

---

## Documentation to Maintain

1. **Manufacturer Contact Log** - Who you spoke with, when, what they promised
2. **Asset Inventory** - What images you have for each home
3. **CDN URL Mapping** - Document working image URLs
4. **Sync Reports** - Keep weekly audit results

---

## Emergency Fallback

If manufacturers don't respond:

1. **Use texashomeoutlet.com images** (already being scraped)
2. **Create simple text-based cards** instead of photo galleries
3. **Focus on getting floor plans** (most important for sales)
4. **Schedule professional photography** for top 5 models

---

## Next Steps Right Now

1. ⬜ Run: `python3 tools/sync_inventory_from_website.py --dry-run`
2. ⬜ Call New Vision: (580) 795-0123
3. ⬜ Call Jessup: (903) 595-2131
4. ⬜ Review this plan with team
5. ⬜ Assign someone to follow up weekly

---

*Last Updated: February 2026*
*Next Review: Weekly until complete*
