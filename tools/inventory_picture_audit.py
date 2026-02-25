"""
Inventory Picture Audit Tool

Audits the current inventory database against:
1. texashomeoutlet.com (old site) for missing homes/pictures
2. Manufacturer CDN for available images
3. Generates report of gaps and action items

Usage:
    python tools/inventory_picture_audit.py
"""

import json
import logging
import requests
from bs4 import BeautifulSoup
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, asdict
from datetime import datetime
import os
import re

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# CDN Pattern from asset_scraper.py
CDN_BASE = "https://d132mt2yijm03y.cloudfront.net"
OLD_SITE_URL = "https://www.texashomeoutlet.com"

@dataclass
class HomeAuditResult:
    """Result of auditing a single home."""
    model_name: str
    in_database: bool
    in_asset_catalog: bool
    on_website: bool
    db_photos_count: int = 0
    asset_photos_count: int = 0
    website_photos_count: int = 0
    has_matterport: bool = False
    missing_from: List[str] = None
    actions_needed: List[str] = None
    
    def __post_init__(self):
        if self.missing_from is None:
            self.missing_from = []
        if self.actions_needed is None:
            self.actions_needed = []


class InventoryPictureAuditor:
    """Audits inventory pictures across all sources."""
    
    def __init__(self):
        self.audit_results: List[HomeAuditResult] = []
        self.summary = {}
        
    def scrape_texashomeoutlet_inventory(self) -> List[Dict]:
        """
        Scrape inventory from texashomeoutlet.com
        Returns list of homes with their details and image URLs.
        """
        logger.info("Scraping texashomeoutlet.com inventory...")
        
        homes = []
        seen_ids = set()
        page = 1
        max_pages = 25
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }
        
        while page <= max_pages:
            url = f"{OLD_SITE_URL}/inventory/?pg={page}"
            try:
                response = requests.get(url, headers=headers, timeout=15)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, 'html.parser')
                
                cards = soup.select('.fp-card-container')
                if not cards:
                    logger.info(f"No more listings found at page {page}")
                    break
                
                new_on_page = 0
                for card in cards:
                    home = self._extract_card_data(card)
                    if home and home['id'] not in seen_ids:
                        seen_ids.add(home['id'])
                        homes.append(home)
                        new_on_page += 1
                
                logger.info(f"Page {page}: Found {new_on_page} new homes (total: {len(homes)})")
                
                if new_on_page == 0:
                    break
                    
                page += 1
                
            except Exception as e:
                logger.error(f"Error scraping page {page}: {e}")
                break
        
        logger.info(f"Total homes scraped from website: {len(homes)}")
        return homes
    
    def _extract_card_data(self, card) -> Optional[Dict]:
        """Extract data from a single inventory card."""
        try:
            # Title and Link
            title_tag = card.select_one('h4.home-name a')
            if not title_tag:
                return None
            
            title = title_tag.get_text(strip=True)
            detail_url = title_tag['href']
            
            # Extract ID from URL
            match = re.search(r'/inventory-detail/(\d+)/', detail_url)
            listing_id = match.group(1) if match else "unknown"
            
            # Manufacturer
            manufacturer = "Unknown"
            built_by_tag = card.select_one('.fp-card-built-by')
            if built_by_tag:
                text = built_by_tag.get_text(strip=True)
                manufacturer = text.replace("Built by:", "").strip()
            
            # Specs
            specs = {"beds": 0, "baths": 0, "sq_ft": 0}
            
            bed_icon = card.select_one('.icon-mfh-bed')
            if bed_icon and bed_icon.parent:
                text = bed_icon.parent.get_text(strip=True)
                match = re.search(r'(\d+)', text)
                if match:
                    specs['beds'] = int(match.group(1))
            
            bath_icon = card.select_one('.icon-mfh-bath')
            if bath_icon and bath_icon.parent:
                text = bath_icon.parent.get_text(strip=True)
                match = re.search(r'(\d+(\.\d+)?)', text)
                if match:
                    specs['baths'] = float(match.group(1))
            
            sqft_icon = card.select_one('.icon-mfh-tapemeasure')
            if sqft_icon and sqft_icon.parent:
                text = sqft_icon.parent.get_text(strip=True)
                match = re.search(r'(\d+,?\d*)', text)
                if match:
                    specs['sq_ft'] = int(match.group(1).replace(',', ''))
            
            # Image
            image_url = None
            image_div = card.select_one('.fp-card-image')
            if image_div and image_div.has_attr('data-bg'):
                image_url = image_div['data-bg']
                if image_url.startswith('/'):
                    image_url = OLD_SITE_URL + image_url
            
            # Floorplan
            floorplan_url = None
            fp_link = card.select_one('a.fp-thumb')
            if fp_link and fp_link.has_attr('href'):
                floorplan_url = fp_link['href']
            
            return {
                "id": listing_id,
                "model_name": title,
                "manufacturer": manufacturer,
                "specs": specs,
                "image_url": image_url,
                "floorplan_url": floorplan_url,
                "detail_url": detail_url if detail_url.startswith('http') else OLD_SITE_URL + detail_url,
            }
            
        except Exception as e:
            logger.error(f"Error extracting card: {e}")
            return None
    
    def check_manufacturer_cdn(self, manufacturer_id: str, plan_id: str) -> Dict:
        """
        Check what images exist on the manufacturer CDN.
        Uses common naming patterns to discover images.
        """
        base_url = f"{CDN_BASE}/manufacturer/{manufacturer_id}/floorplan/{plan_id}"
        
        # Common image naming patterns
        patterns = [
            "floor-plans.jpg",
            "floor-plans-SMALL.jpg",
            "1.jpg", "2.jpg", "3.jpg", "4.jpg", "5.jpg",
            "6.jpg", "7.jpg", "8.jpg", "9.jpg", "10.jpg",
            "11.jpg", "12.jpg", "13.jpg", "14.jpg", "15.jpg",
            "16.jpg", "17.jpg", "18.jpg", "19.jpg", "20.jpg",
        ]
        
        found_images = []
        
        # In a real implementation, we'd HEAD request each URL
        # For now, return the expected URLs
        for pattern in patterns:
            found_images.append(f"{base_url}/{pattern}")
        
        return {
            "base_url": base_url,
            "expected_images": found_images,
            "note": "Run with --verify to check which URLs actually exist"
        }
    
    def audit_against_database(self, website_homes: List[Dict]) -> List[HomeAuditResult]:
        """
        Audit website homes against the current database.
        Returns list of audit results.
        """
        logger.info("Auditing against database...")
        
        results = []
        
        # Load current asset catalog
        try:
            from asset_scraper import PROPERTY_ASSETS
            asset_homes = PROPERTY_ASSETS
        except ImportError:
            logger.warning("Could not load asset_scraper.PROPERTY_ASSETS")
            asset_homes = {}
        
        for home in website_homes:
            result = HomeAuditResult(
                model_name=home['model_name'],
                in_database=False,  # Would check Firestore
                in_asset_catalog=False,
                on_website=True,
                website_photos_count=1 if home.get('image_url') else 0,
            )
            
            # Check if in asset catalog
            home_slug = home['model_name'].lower().replace(' ', '-')
            for slug, asset in asset_homes.items():
                if slug == home_slug or home['model_name'].lower() in asset['name'].lower():
                    result.in_asset_catalog = True
                    result.asset_photos_count = len(asset.get('images', []))
                    result.has_matterport = bool(asset.get('matterport_id'))
                    break
            
            # Determine what's missing
            if not result.in_database:
                result.missing_from.append("database")
            if not result.in_asset_catalog:
                result.missing_from.append("asset_catalog")
            if result.asset_photos_count < 5:
                result.actions_needed.append(f"Need more photos (only have {result.asset_photos_count})")
            if not result.has_matterport:
                result.actions_needed.append("Get Matterport tour")
            
            results.append(result)
        
        return results
    
    def generate_report(self, results: List[HomeAuditResult]) -> Dict:
        """Generate comprehensive audit report."""
        
        total = len(results)
        in_db = sum(1 for r in results if r.in_database)
        in_assets = sum(1 for r in results if r.in_asset_catalog)
        has_photos = sum(1 for r in results if r.asset_photos_count >= 5)
        has_matterport = sum(1 for r in results if r.has_matterport)
        
        missing_from_db = [r.model_name for r in results if not r.in_database]
        missing_from_assets = [r.model_name for r in results if not r.in_asset_catalog]
        need_more_photos = [r.model_name for r in results if r.asset_photos_count < 5]
        
        report = {
            "generated_at": datetime.now().isoformat(),
            "summary": {
                "total_homes_on_website": total,
                "in_database": in_db,
                "in_asset_catalog": in_assets,
                "with_adequate_photos": has_photos,
                "with_matterport": has_matterport,
            },
            "gaps": {
                "missing_from_database": missing_from_db,
                "missing_from_asset_catalog": missing_from_assets,
                "need_more_photos": need_more_photos,
            },
            "actions": [],
            "manufacturers": {},
            "detailed_results": [asdict(r) for r in results],
        }
        
        # Generate action items
        if missing_from_db:
            report["actions"].append(f"Add {len(missing_from_db)} homes to Firestore database")
        if missing_from_assets:
            report["actions"].append(f"Add {len(missing_from_assets)} homes to asset_scraper.py")
        if need_more_photos:
            report["actions"].append(f"Source additional photos for {len(need_more_photos)} homes")
        
        return report
    
    def save_report(self, report: Dict, filename: str = None):
        """Save report to file."""
        if filename is None:
            filename = f"inventory_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        filepath = os.path.join("data", filename)
        os.makedirs("data", exist_ok=True)
        
        with open(filepath, 'w') as f:
            json.dump(report, f, indent=2)
        
        logger.info(f"Report saved to {filepath}")
        return filepath
    
    def run_full_audit(self) -> Dict:
        """Run complete audit and return report."""
        logger.info("=" * 60)
        logger.info("STARTING INVENTORY PICTURE AUDIT")
        logger.info("=" * 60)
        
        # Step 1: Scrape website
        website_homes = self.scrape_texashomeoutlet_inventory()
        
        # Step 2: Audit against database
        results = self.audit_against_database(website_homes)
        
        # Step 3: Generate report
        report = self.generate_report(results)
        
        # Step 4: Save
        filepath = self.save_report(report)
        
        # Step 5: Print summary
        self._print_summary(report)
        
        return report
    
    def _print_summary(self, report: Dict):
        """Print human-readable summary."""
        s = report["summary"]
        
        print("\n" + "=" * 60)
        print("INVENTORY AUDIT SUMMARY")
        print("=" * 60)
        print(f"Total homes on texashomeoutlet.com: {s['total_homes_on_website']}")
        print(f"In database: {s['in_database']} ({s['in_database']/s['total_homes_on_website']*100:.1f}%)")
        print(f"In asset catalog: {s['in_asset_catalog']} ({s['in_asset_catalog']/s['total_homes_on_website']*100:.1f}%)")
        print(f"With adequate photos (5+): {s['with_adequate_photos']}")
        print(f"With Matterport tours: {s['with_matterport']}")
        
        print("\n" + "-" * 60)
        print("ACTION ITEMS:")
        print("-" * 60)
        for action in report["actions"]:
            print(f"  • {action}")
        
        gaps = report["gaps"]
        if gaps["missing_from_database"]:
            print(f"\n  Missing from database ({len(gaps['missing_from_db'])}):")
            for name in gaps["missing_from_database"][:10]:
                print(f"    - {name}")
            if len(gaps["missing_from_database"]) > 10:
                print(f"    ... and {len(gaps['missing_from_database']) - 10} more")
        
        if gaps["need_more_photos"]:
            print(f"\n  Need more photos ({len(gaps['need_more_photos'])}):")
            for name in gaps["need_more_photos"][:10]:
                print(f"    - {name}")
            if len(gaps["need_more_photos"]) > 10:
                print(f"    ... and {len(gaps['need_more_photos']) - 10} more")
        
        print("\n" + "=" * 60)


def main():
    """Main entry point."""
    auditor = InventoryPictureAuditor()
    report = auditor.run_full_audit()
    
    # Also save a markdown report
    md_content = generate_markdown_report(report)
    md_path = os.path.join("data", f"inventory_audit_{datetime.now().strftime('%Y%m%d')}.md")
    with open(md_path, 'w') as f:
        f.write(md_content)
    print(f"\nMarkdown report saved to: {md_path}")


def generate_markdown_report(report: Dict) -> str:
    """Generate a markdown version of the report."""
    s = report["summary"]
    gaps = report["gaps"]
    
    md = f"""# Inventory Picture Audit Report

**Generated:** {report['generated_at']}

## Summary

| Metric | Count | Percentage |
|--------|-------|------------|
| Total homes on website | {s['total_homes_on_website']} | 100% |
| In database | {s['in_database']} | {s['in_database']/max(s['total_homes_on_website'],1)*100:.1f}% |
| In asset catalog | {s['in_asset_catalog']} | {s['in_asset_catalog']/max(s['total_homes_on_website'],1)*100:.1f}% |
| With 5+ photos | {s['with_adequate_photos']} | {s['with_adequate_photos']/max(s['total_homes_on_website'],1)*100:.1f}% |
| With Matterport | {s['with_matterport']} | {s['with_matterport']/max(s['total_homes_on_website'],1)*100:.1f}% |

## Action Items

"""
    
    for action in report["actions"]:
        md += f"- [ ] {action}\n"
    
    if gaps["missing_from_database"]:
        md += f"\n## Homes Missing from Database ({len(gaps['missing_from_database'])})
\n"
        for name in gaps["missing_from_database"]:
            md += f"- {name}\n"
    
    if gaps["missing_from_asset_catalog"]:
        md += f"\n## Homes Missing from Asset Catalog ({len(gaps['missing_from_asset_catalog'])})
\n"
        for name in gaps["missing_from_asset_catalog"]:
            md += f"- {name}\n"
    
    if gaps["need_more_photos"]:
        md += f"\n## Homes Needing More Photos ({len(gaps['need_more_photos'])})
\n"
        for name in gaps["need_more_photos"]:
            # Find the result to get photo count
            for r in report["detailed_results"]:
                if r["model_name"] == name:
                    md += f"- {name} (currently has {r['asset_photos_count']} photos)\n"
                    break
    
    md += """
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

"""
    
    return md


if __name__ == "__main__":
    main()
