"""
Merge scraped_inventory.json with inventory.json

Strategy:
- For homes that exist in both: keep curated metadata from inventory.json,
  replace image_url/gallery_images with verified CDN URLs from scraper.
- For homes only in scraped: add them with auto-detected classification.
- For homes only in inventory.json: keep as-is (may be older/removed from site).
"""

import json
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

def classify_home(specs):
    """Auto-detect classification from dimensions."""
    width = specs.get("width", 0)
    if width >= 28:
        return "Double Wide"
    elif width >= 20:
        return "Double Wide"
    else:
        return "Single Wide"

def merge():
    # Load both files
    with open(os.path.join(DATA_DIR, "inventory.json"), "r") as f:
        existing = json.load(f)
    
    with open(os.path.join(DATA_DIR, "scraped_inventory.json"), "r") as f:
        scraped = json.load(f)
    
    # Index existing by ID
    existing_by_id = {h["id"]: h for h in existing}
    scraped_by_id = {h["id"]: h for h in scraped}
    
    merged = []
    updated_count = 0
    added_count = 0
    
    # Process existing entries - update images from scraper
    for home in existing:
        hid = home["id"]
        if hid in scraped_by_id:
            s = scraped_by_id[hid]
            
            # Update image with verified CDN URL from scraper
            if s.get("image_url"):
                home["image_url"] = s["image_url"]
            
            # Build gallery from scraper + existing 
            gallery = []
            if s.get("image_url"):
                gallery.append(s["image_url"])
            if s.get("floorplan_url"):
                gallery.append(s["floorplan_url"])
            # Add existing gallery images that aren't duplicates
            for img in home.get("gallery_images", []):
                if img not in gallery:
                    gallery.append(img)
            home["gallery_images"] = gallery
            
            # Add floorplan_url if available
            if s.get("floorplan_url"):
                home["floorplan_url"] = s["floorplan_url"]
            
            # Add detail_url from scraper
            if s.get("detail_url"):
                home["detail_url"] = s["detail_url"]
            
            updated_count += 1
        
        merged.append(home)
    
    # Add scraped-only entries (not in existing)
    for hid, s in scraped_by_id.items():
        if hid not in existing_by_id:
            # Parse model_name from scraped format "Series / Model"
            parts = s["model_name"].split(" / ", 1)
            series_name = parts[0].strip() if len(parts) > 1 else ""
            model_name = parts[1].strip() if len(parts) > 1 else s["model_name"]
            
            # Determine classification
            classification = classify_home(s.get("specs", {}))
            
            # Determine if pre-owned
            is_preowned = "PRE-OWNED" in s["model_name"].upper()
            
            new_entry = {
                "id": hid,
                "website_id": hid,
                "serial_number": hid,
                "manufacturer": s.get("manufacturer", "Unknown"),
                "model_name": model_name,
                "classification": classification,
                "status": "Available",
                "specs": s.get("specs", {}),
                "pricing": s.get("pricing", {"price_value": 0, "display_price": "Call for Price"}),
                "features": ["Pre-Owned"] if is_preowned else [series_name] if series_name else [],
                "marketing_tags": ["pre-owned", "budget"] if is_preowned else [],
                "image_url": s.get("image_url"),
                "gallery_images": [],
                "detail_url": s.get("detail_url"),
                "floorplan_url": s.get("floorplan_url")
            }
            
            # Build gallery
            gallery = []
            if s.get("image_url"):
                gallery.append(s["image_url"])
            if s.get("floorplan_url"):
                gallery.append(s["floorplan_url"])
            new_entry["gallery_images"] = gallery
            
            # Add price_value = 0 if missing
            if "price_value" not in new_entry["pricing"]:
                new_entry["pricing"]["price_value"] = 0
                new_entry["pricing"]["price_tier"] = "Pre-Owned" if is_preowned else "Call for Price"
            
            merged.append(new_entry)
            added_count += 1
    
    # Save merged
    output_path = os.path.join(DATA_DIR, "inventory.json")
    with open(output_path, "w") as f:
        json.dump(merged, f, indent=2)
    
    print("Merge complete!")
    print(f"  Updated: {updated_count} existing entries with scraper images")
    print(f"  Added: {added_count} new entries from scraper")
    print(f"  Total: {len(merged)} entries in inventory.json")
    
    # Verify images
    no_image = [h for h in merged if not h.get("image_url")]
    print(f"  Entries missing image_url: {len(no_image)}")
    if no_image:
        for h in no_image:
            print(f"    - {h['id']}: {h['model_name']}")

if __name__ == "__main__":
    merge()
