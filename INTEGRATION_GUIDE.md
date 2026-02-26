# Inventory Integration Guide

## For: New THO Web App (GCP Cloud Run)

Since your new site is in the same GCP project (`sapphire-479610`), you have **direct access** to the same Firestore database!

---

## 🎯 Easiest Option: Write Directly to Firestore

Your new web app can write directly to the `inventory` collection. No APIs needed!

### Firestore Collection: `inventory`

**Document ID:** Use your internal listing ID (e.g., `42155`)

**Required Fields:**
```javascript
{
  "model_name": "The Nassau FAC28483A",     // Display name
  "manufacturer": "Jessup Housing",         // Brand name
  "manufacturer_id": "3326",               // For image CDN
  "plan_id": "225060",                     // For image CDN
  "year": 2026,
  "is_new": true,                          // true=new, false=pre-owned
  "bedrooms": 3,
  "bathrooms": 2.0,
  "sqft": 1264,
  "msrp": 85000,                           // Base price
  "sale_price": 79900,                     // Current price
  "status": "AVAILABLE",                   // AVAILABLE, SOLD, PENDING
  "condition": "New",                      // New, Used, Excellent, etc.
  "image_url": "https://cdn.../1_card_lg.jpg",  // Hero image
  "hero_image": "https://cdn.../1_card_lg.jpg", // Same as above
  "photos": [                              // Array of all images
    "https://cdn.../1.jpg",
    "https://cdn.../2.jpg"
  ],
  "floorplan_url": "https://cdn.../floor-plans.jpg",
  "location": "San Antonio, TX",
  "date_added": "2026-02-26T12:00:00Z",
  "source_url": "https://yoursite.com/listing/42155"
}
```

### Example: Node.js (from your new web app)

```javascript
const { Firestore } = require('@google-cloud/firestore');

const db = new Firestore({
  projectId: 'sapphire-479610'
});

// Add/Update a home
async function syncHome(listingId, homeData) {
  await db.collection('inventory').doc(listingId).set({
    ...homeData,
    last_synced: new Date().toISOString()
  }, { merge: true });  // merge=true updates only changed fields
  
  console.log(`Synced home ${listingId}`);
}

// When user saves a listing in your admin:
await syncHome('42155', {
  model_name: 'The Nassau FAC28483A',
  manufacturer: 'Jessup Housing',
  bedrooms: 3,
  bathrooms: 2,
  // ... etc
});
```

### Example: Python

```python
from google.cloud import firestore

db = firestore.Client(project='sapphire-479610')

def sync_home(listing_id, home_data):
    db.collection('inventory').document(listing_id).set({
        **home_data,
        'last_synced': datetime.now().isoformat()
    }, merge=True)
```

---

## 🔄 When to Sync

### Option A: Real-time (Recommended)
Sync immediately when admin saves changes:
```javascript
// In your admin save handler
app.post('/admin/save-listing', async (req, res) => {
  const listing = req.body;
  
  // Save to your DB
  await saveToYourDatabase(listing);
  
  // ALSO sync to AI platform
  await syncHome(listing.id, listing);
  
  res.json({ success: true });
});
```

### Option B: Batch (Nightly)
Run a scheduled job to sync all inventory:
```javascript
// Cloud Scheduler → Cloud Function
exports.syncAllInventory = async () => {
  const allHomes = await getAllListingsFromYourDB();
  
  for (const home of allHomes) {
    await syncHome(home.id, home);
  }
  
  console.log(`Synced ${allHomes.length} homes`);
};
```

---

## 🖼️ Image CDN Pattern

Use the same CDN structure for consistency:

```
https://d132mt2yijm03y.cloudfront.net/manufacturer/{manufacturer_id}/floorplan/{plan_id}/{filename}

Examples:
- Hero: /1_card_lg.jpg
- Photos: /1.jpg, /2.jpg, /3.jpg...
- Floorplan: /floor-plans.jpg
```

**Manufacturers we use:**
- `3335` = New Vision Manufacturing
- `3326` = Jessup Housing
- `2007` = TRU Homes
- `1944` = Legacy Housing

---

## ✅ Quick Checklist

When you add/update a home in your new site:

- [ ] Write to your own database
- [ ] Also write to Firestore `inventory` collection
- [ ] Include all required fields (especially `is_new`, `status`, images)
- [ ] Use `merge: true` to avoid overwriting existing data

---

## 🧪 Test It

```javascript
// Quick test - add a fake home
await db.collection('inventory').doc('TEST001').set({
  model_name: 'TEST HOME - Delete Me',
  manufacturer: 'Test Manufacturer',
  year: 2026,
  is_new: true,
  bedrooms: 3,
  bathrooms: 2,
  sqft: 1200,
  status: 'AVAILABLE',
  date_added: new Date().toISOString()
});

// Then check if it appears in the AI chat!
```

---

## 📞 Need Help?

If you want me to:
- Create a Cloud Function for syncing
- Add a webhook endpoint you can call
- Set up Cloud Scheduler for nightly sync
- Review your integration code

Just let me know!

---

**Bottom line:** Since you're in the same GCP project, just write directly to Firestore. No APIs, no auth, no complexity!
