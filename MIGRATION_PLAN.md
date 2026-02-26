# Inventory Migration Plan: Legacy → New GCP Site

## The Challenge
When you cut over from your third-party provider (texashomeoutlet.com) to your new GCP-built site, how do we keep inventory flowing to the AI platform?

## The Solution: Shared Firestore Database

Since **both your new site AND the AI platform** are in the same GCP project (`sapphire-479610`), the simplest solution is to **share the Firestore database**.

```
┌─────────────────────────────────────────────────────────────┐
│                    GCP Project: sapphire-479610              │
│                                                              │
│  ┌──────────────┐        ┌──────────────┐                   │
│  │  Your New    │        │  AI Platform │                   │
│  │  Website     │◄──────►│  (tho-agent) │                   │
│  │  (Cloud Run) │        │  (Cloud Run) │                   │
│  └──────┬───────┘        └──────┬───────┘                   │
│         │                       │                           │
│         └──────────┬────────────┘                           │
│                    │                                        │
│              ┌─────▼──────┐                                │
│              │ Firestore  │                                │
│              │ inventory  │                                │
│              │ collection │                                │
│              └────────────┘                                │
└─────────────────────────────────────────────────────────────┘
```

---

## Implementation Options

### Option 1: Shared Database (RECOMMENDED)

**How it works:**
- Your new website writes directly to Firestore `inventory` collection
- AI platform reads from same Firestore collection
- No APIs, no syncing, no delay

**Pros:**
- ✅ Real-time updates
- ✅ Zero latency
- ✅ No integration code needed
- ✅ No network egress costs (same project)

**Cons:**
- ❌ Your website must use Firestore (not MySQL/Postgres)
- ❌ Tight coupling between site and AI

**Code Example:**
```javascript
// From your new website's admin panel
const { Firestore } = require('@google-cloud/firestore');
const db = new Firestore({ projectId: 'sapphire-479610' });

// When admin saves a listing
async function saveListing(listing) {
  // Save to Firestore (AI sees it immediately)
  await db.collection('inventory').doc(listing.id).set({
    model_name: listing.title,
    manufacturer: listing.brand,
    bedrooms: listing.beds,
    bathrooms: listing.baths,
    sqft: listing.sqft,
    price: listing.price,
    photos: listing.images,  // Array of image URLs
    is_new: listing.condition === 'new',
    status: listing.available ? 'AVAILABLE' : 'SOLD',
    last_updated: new Date().toISOString()
  });
}
```

---

### Option 2: REST API Bridge (If you use SQL database)

**How it works:**
- Your website keeps its existing database (MySQL/Postgres)
- We create an API endpoint that syncs to Firestore

**Architecture:**
```
Your Website → Your Database → Sync API → Firestore → AI Platform
```

**Implementation:**
```javascript
// Cloud Function: sync-inventory
const { Firestore } = require('@google-cloud/firestore');
const db = new Firestore();

exports.syncInventory = async (req, res) => {
  // Your website calls this when inventory changes
  const { listingId, data } = req.body;
  
  await db.collection('inventory').doc(listingId).set({
    ...data,
    synced_at: new Date().toISOString()
  }, { merge: true });
  
  res.json({ success: true });
};
```

**When to call:**
- When admin creates/edits/deletes a listing
- Nightly batch sync as backup

---

### Option 3: Scheduled Batch Sync (Simplest for migration)

**How it works:**
- Cloud Scheduler runs every hour/day
- Pulls all inventory from your website
- Updates Firestore in bulk

**Good for:**
- Initial migration
- If you can't modify website code
- Temporary solution during transition

```python
# Cloud Function triggered by Cloud Scheduler
def scheduled_sync(event, context):
    # 1. Fetch from your website's API
    inventory = requests.get('https://yoursite.com/api/inventory').json()
    
    # 2. Update Firestore
    db = firestore.Client()
    for item in inventory:
        db.collection('inventory').document(item['id']).set(item, merge=True)
```

---

## Picture Handling

### Where do images live?

**Option A: Keep using CloudFront CDN** (Recommended)
- Your new site uploads to same CDN: `d132mt2yijm03y.cloudfront.net`
- Just update the URLs in Firestore
- Zero changes needed on AI side

**Option B: Use Google Cloud Storage**
- Upload images to GCS bucket
- Store GCS URLs in Firestore
- Update AI image base URL config

**Option C: Hotlink from your site**
- Store URLs pointing to yoursite.com/images/...
- AI fetches directly from your site
- Simplest but couples image serving to your site

---

## Migration Checklist

### Before Cutover
- [ ] Decide on Option 1, 2, or 3
- [ ] Build inventory sync mechanism
- [ ] Test with 5-10 sample listings
- [ ] Verify images load correctly in AI chat

### During Cutover
- [ ] Freeze inventory updates on old site
- [ ] Run final sync to Firestore
- [ ] Point domain to new site
- [ ] Test AI recommendations immediately

### After Cutover
- [ ] Monitor for missing inventory
- [ ] Set up automated sync (if using Option 2 or 3)
- [ ] Train staff on new admin workflow

---

## My Recommendation

**Use Option 1 (Shared Firestore) if:**
- You're building the new site from scratch
- You can choose Firestore as your database

**Use Option 2 (API Bridge) if:**
- You already committed to MySQL/Postgres
- You want clean separation between site and AI

**Use Option 3 (Batch Sync) temporarily if:**
- You need to migrate quickly
- You'll implement real-time sync later

---

## Questions for You

1. **Database:** Is your new site using Firestore, or something else (MySQL, Postgres)?

2. **Images:** Where will photos be stored? Same CDN or new system?

3. **Timeline:** When is the cutover planned?

4. **Control:** Do you want real-time sync, or is hourly/daily acceptable?

Once you answer these, I can build the exact integration code you need.
