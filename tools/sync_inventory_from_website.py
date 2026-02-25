"""
Inventory Sync Tool - Pulls from texashomeoutlet.com to Firestore

Usage:
    python tools/sync_inventory_from_website.py [--dry-run]
"""

import json
import argparse
import logging
from datetime import datetime
from typing import Dict, List, Optional

from scraper import scrape_inventory, get_soup
from bs4 import BeautifulSoup
import re

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def extract_detailed_home_info(detail_url: str) -> Optional[Dict]:
    """Scrape detailed info from a home's detail page."""
    try:
        soup = get_soup(detail_url)
        if not soup:
            return None
        
        info = {
            "serial_number": None,
            "label_number": None,
            "price": None,
            "description": None,
            "features": [],
            "gallery_images": [],
        }
        
        # Try to find serial/label numbers
        info_table = soup.select_one('.home-info-table, .specs-table')
        if info_table:
            rows = info_table.select('tr')
            for row in rows:
                cells = row.select('td, th')
                if len(cells) >= 2:
                    label = cells[0].get_text(strip=True).lower()
                    value = cells[1].get_text(strip=True)
                    if 'serial' in label:
                        info["serial_number"] = value
                    elif 'label' in label or 'vin' in label:
                        info["label_number"] = value
                    elif 'price' in label and value:
                        # Extract numeric price
                        price_match = re.search(r'[\d,]+', value)
                        if price_match:
                            info["price"] = int(price_match.group().replace(',', ''))
        
        # Description
        desc = soup.select_one('.home-description, .description, [class*="description"]')
        if desc:
            info["description"] = desc.get_text(strip=True)
        
        # Features
        features_list = soup.select('.feature-list li, .features li, .highlights li')
        info["features"] = [f.get_text(strip=True) for f in features_list if f.get_text(strip=True)]
        
        # Gallery images
        gallery = soup.select('.gallery-image, .fp-gallery img, .photo-gallery img')
        for img in gallery:
            src = img.get('src') or img.get('data-src')
            if src:
                if src.startswith('/'):
                    src = f"https://www.texashomeoutlet.com{src}"
                info["gallery_images"].append(src)
        
        return info
        
    except Exception as e:
        logger.error(f"Error extracting details from {detail_url}: {e}")
        return None


def transform_to_firestore_format(website_home: Dict) -> Dict:
    """Transform website data to Firestore inventory format."""
    specs = website_home.get("specs", {})
    
    # Parse model name - remove series prefix
    full_name = website_home.get("model_name", "")
    
    # Extract series and model
    series = ""
    model = full_name
    if " / " in full_name:
        parts = full_name.split(" / ", 1)
        series = parts[0].strip()
        model = parts[1].strip()
    
    # Determine status
    status = "AVAILABLE"
    is_new = True
    if "PRE-OWNED" in full_name.upper() or "pre-owned" in full_name.lower():
        status = "PRE-OWNED"
        is_new = False
    
    # Determine manufacturer from model info
    manufacturer = website_home.get("manufacturer", "Unknown")
    
    # Map common manufacturers
    manufacturer_mapping = {
        "TRU": "TRU Homes",
        "TRU Homes": "TRU Homes",
        "TRU Multi Section": "TRU Homes",
        "TRU Single Section": "TRU Homes",
        "Premier": "Premier Homes",
        "Epic Experience": "Epic Experience",
        "Independent": "Independent Homes",
        "Alpine Series": "Alpine Homes",
        "Jessup": "Jessup Housing",
        "The Elite Series": "Jessup Housing",
        "The Promotional Series": "Jessup Housing",
        "Solution": "Solution Homes",
    }
    
    for key, value in manufacturer_mapping.items():
        if key in series or key in model:
            manufacturer = value
            break
    
    # Build Firestore document
    firestore_doc = {
        "model_name": model,
        "manufacturer": manufacturer,
        "series": series,
        "year": datetime.now().year if is_new else None,
        "is_new": is_new,
        "status": status,
        "classification": "Double Wide" if specs.get("width", 0) >= 24 else "Single Wide",
        
        # Specs
        "bedrooms": specs.get("beds", 0),
        "bathrooms": specs.get("baths", 0),
        "sqft": specs.get("sq_ft", 0),
        "width": specs.get("width", 0),
        "length": specs.get("length", 0),
        "sections": 2 if specs.get("width", 0) >= 24 else 1,
        
        # Media
        "image_url": website_home.get("image_url"),
        "floorplan_url": website_home.get("floorplan_url"),
        "photos": [],
        "gallery_images": [],
        
        # IDs
        "website_id": website_home.get("id"),
        "detail_url": website_home.get("detail_url"),
        
        # Metadata
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "source": "texashomeoutlet.com",
        "synced": True,
    }
    
    return firestore_doc


def sync_to_firestore(homes: List[Dict], dry_run: bool = True) -> Dict:
    """Sync homes to Firestore database."""
    results = {
        "total": len(homes),
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors": [],
        "homes": []
    }
    
    try:
        # Import Firestore client
        import sys
        sys.path.append('..')
        from database.firestore_client import get_database
        
        db = get_database()
        
        for home in homes:
            try:
                firestore_doc = transform_to_firestore_format(home)
                model_name = firestore_doc["model_name"]
                
                # Check if already exists
                existing = None
                if firestore_doc.get("serial_number"):
                    existing = db.get_inventory_by_serial(firestore_doc["serial_number"])
                
                if dry_run:
                    action = "WOULD_CREATE" if not existing else "WOULD_UPDATE"
                    logger.info(f"{action}: {model_name}")
                    results["homes"].append({
                        "model_name": model_name,
                        "action": action,
                        "doc": firestore_doc
                    })
                    if existing:
                        results["updated"] += 1
                    else:
                        results["created"] += 1
                else:
                    if existing:
                        # Update existing
                        doc_id = existing.get("id")
                        db.update_inventory(doc_id, firestore_doc)
                        results["updated"] += 1
                        logger.info(f"UPDATED: {model_name}")
                    else:
                        # Create new
                        db.create_inventory(firestore_doc)
                        results["created"] += 1
                        logger.info(f"CREATED: {model_name}")
                        
            except Exception as e:
                error_msg = f"Error processing {home.get('model_name')}: {e}"
                logger.error(error_msg)
                results["errors"].append(error_msg)
                
    except ImportError as e:
        logger.error(f"Could not import Firestore client: {e}")
        logger.info("Running in preview mode only...")
        
        # Preview mode - just show what would be synced
        for home in homes:
            firestore_doc = transform_to_firestore_format(home)
            results["homes"].append({
                "model_name": firestore_doc["model_name"],
                "action": "WOULD_CREATE",
                "doc": firestore_doc
            })
            results["created"] += 1
    
    return results


def generate_sync_report(results: Dict, dry_run: bool = True):
    """Generate and save sync report."""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    mode = "DRY_RUN" if dry_run else "ACTUAL"
    
    report = {
        "mode": mode,
        "timestamp": timestamp,
        "results": results
    }
    
    # Save JSON
    json_path = f"data/inventory_sync_{mode}_{timestamp}.json"
    with open(json_path, 'w') as f:
        json.dump(report, f, indent=2, default=str)
    
    # Save human-readable summary
    summary_path = f"data/inventory_sync_{mode}_{timestamp}.txt"
    with open(summary_path, 'w') as f:
        f.write(f"INVENTORY SYNC REPORT - {mode}\n")
        f.write(f"Timestamp: {timestamp}\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"Total homes processed: {results['total']}\n")
        f.write(f"Created: {results['created']}\n")
        f.write(f"Updated: {results['updated']}\n")
        f.write(f"Skipped: {results['skipped']}\n")
        f.write(f"Errors: {len(results['errors'])}\n\n")
        
        if results['errors']:
            f.write("ERRORS:\n")
            for error in results['errors']:
                f.write(f"  - {error}\n")
        
        f.write("\nHOMES:\n")
        for home in results['homes']:
            f.write(f"\n  [{home['action']}] {home['model_name']}\n")
            doc = home.get('doc', {})
            f.write(f"    Manufacturer: {doc.get('manufacturer')}\n")
            f.write(f"    Specs: {doc.get('bedrooms')}BR/{doc.get('bathrooms')}BA, {doc.get('sqft')} sqft\n")
            f.write(f"    Status: {doc.get('status')}\n")
    
    logger.info(f"\n{'='*60}")
    logger.info(f"SYNC COMPLETE ({mode})")
    logger.info(f"{'='*60}")
    logger.info(f"Total: {results['total']}")
    logger.info(f"Created: {results['created']}")
    logger.info(f"Updated: {results['updated']}")
    logger.info(f"Errors: {len(results['errors'])}")
    logger.info(f"\nReports saved:")
    logger.info(f"  - {json_path}")
    logger.info(f"  - {summary_path}")
    
    return json_path, summary_path


def main():
    parser = argparse.ArgumentParser(description='Sync inventory from texashomeoutlet.com to Firestore')
    parser.add_argument('--dry-run', action='store_true', default=True,
                        help='Preview changes without writing to database')
    parser.add_argument('--force', action='store_true',
                        help='Actually write to database (use with caution)')
    
    args = parser.parse_args()
    
    dry_run = not args.force
    
    logger.info("=" * 60)
    logger.info("INVENTORY SYNC FROM WEBSITE")
    logger.info("=" * 60)
    logger.info(f"Mode: {'DRY RUN (preview only)' if dry_run else 'LIVE (writing to database)'}")
    logger.info("")
    
    # Step 1: Scrape website
    logger.info("Step 1: Scraping texashomeoutlet.com...")
    website_homes = scrape_inventory()
    
    if not website_homes:
        logger.error("No homes found on website!")
        return
    
    logger.info(f"Found {len(website_homes)} homes on website\n")
    
    # Step 2: Sync to Firestore
    logger.info("Step 2: Syncing to Firestore...")
    results = sync_to_firestore(website_homes, dry_run=dry_run)
    
    # Step 3: Generate report
    logger.info("\nStep 3: Generating report...")
    json_path, summary_path = generate_sync_report(results, dry_run)
    
    # Step 4: Print next steps
    logger.info("\n" + "=" * 60)
    logger.info("NEXT STEPS:")
    logger.info("=" * 60)
    
    if dry_run:
        logger.info("1. Review the sync report above")
        logger.info("2. To actually sync, run with --force flag:")
        logger.info("   python tools/sync_inventory_from_website.py --force")
    else:
        logger.info("1. Inventory has been synced to Firestore!")
        logger.info("2. Next: Run image audit to find missing photos")
        logger.info("3. Contact manufacturers for missing images")
    
    logger.info("\n4. Update asset_scraper.py with new homes")
    logger.info("5. Refresh the AI platform to see new inventory")


if __name__ == "__main__":
    main()
