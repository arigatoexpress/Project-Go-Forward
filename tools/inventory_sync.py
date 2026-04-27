"""
Inventory Sync Tool for Texas Home Outlet

Synchronizes inventory from texashomeoutlet.com to Firestore.
- Scrapes website for current listings
- Maps manufacturer data
- Imports to Firestore with proper structure
- Handles updates for existing inventory

Usage:
    python tools/inventory_sync.py [--dry-run] [--force]

Environment:
    GOOGLE_APPLICATION_CREDENTIALS - Path to service account JSON
    FIRESTORE_PROJECT_ID - GCP project ID (optional, defaults to tho-ai-agent)
"""

import json
import logging
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup
from pydantic import ValidationError

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models import Inventory  # noqa: E402

try:
    from google.cloud import firestore

    GCP_AVAILABLE = True
except ImportError:
    GCP_AVAILABLE = False
    print("Warning: google-cloud-firestore not installed. Running in scrape-only mode.")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Configuration
OLD_SITE_URL = "https://www.texashomeoutlet.com"
CDN_BASE = "https://d132mt2yijm03y.cloudfront.net"

# Manufacturer mapping from website names to standardized names
MANUFACTURER_MAP = {
    "New Vision Manufacturing": {
        "name": "New Vision Manufacturing",
        "manufacturer_id": "3335",
        "phone": "(580) 795-0123",
    },
    "Jessup Housing": {
        "name": "Jessup Housing",
        "manufacturer_id": "3326",
        "phone": "(903) 595-2131",
    },
    "Legacy Housing": {
        "name": "Legacy Housing",
        "manufacturer_id": "1944",  # From CDN pattern
        "phone": None,
    },
    "TRU": {
        "name": "TRU Homes",
        "manufacturer_id": "2007",  # From CDN pattern
        "phone": None,
    },
    "TRU Multi Section": {"name": "TRU Homes", "manufacturer_id": "2007", "phone": None},
    "TRU Single Section": {"name": "TRU Homes", "manufacturer_id": "2007", "phone": None},
    "Solution": {
        "name": "Solution Homes",
        "manufacturer_id": "2025",  # From CDN pattern
        "phone": None,
    },
    "Clayton Sulphur Springs": {
        "name": "Clayton Sulphur Springs",
        "manufacturer_id": "2025",
        "phone": None,
    },
    "Southern Energy Homes": {
        "name": "Southern Energy Homes",
        "manufacturer_id": "1997",
        "phone": None,
    },
    "Schult Waco 2": {"name": "Schult Waco 2", "manufacturer_id": "3315", "phone": None},
    "Legacy Housing 2": {"name": "Legacy Housing", "manufacturer_id": "1944", "phone": None},
    "Clayton Homes": {"name": "Clayton Homes", "manufacturer_id": "clayton", "phone": None},
    "Fleetwood Homes": {"name": "Fleetwood Homes", "manufacturer_id": "fleetwood", "phone": None},
    "Champion Homes": {
        "name": "Champion Homes",
        "manufacturer_id": "3373",  # From CDN pattern
        "phone": None,
    },
    "Premier": {"name": "Premier Homes", "manufacturer_id": "premier", "phone": None},
    "Independent": {
        "name": "Independent Homes",
        "manufacturer_id": "2025",  # From CDN pattern
        "phone": None,
    },
    "Epic Experience": {
        "name": "Epic Experience",
        "manufacturer_id": "1997",  # From CDN pattern
        "phone": None,
    },
    "Alpine Series": {
        "name": "Alpine Series",
        "manufacturer_id": "3315",  # From CDN pattern
        "phone": None,
    },
    "Unknown": {"name": "Various", "manufacturer_id": "various", "phone": None},
}

# Default plan IDs for known models (from asset_scraper.py)
KNOWN_MODEL_PLAN_IDS = {
    "the big steve": "225053",
    "the willison": "225054",
    "the bobby jo": "225062",
    "the big kahuna": "225046",
    "the big boy": "225044",
    "the bowie": "225047",
    "the brazos": "225050",
    "the chupacabra": "225049",
    "the hidalgo": "225055",
    "the liberty": "225048",
    "the nassau": "225060",
    "the schult": "225052",
    "the smith": "225051",
    "the steck": "225058",
    "the victoria": "225045",
    "the waco": "225056",
    "the wendy": "225057",
    "the williamson": "225061",
}


@dataclass
class InventoryItem:
    """Represents a single inventory item."""

    id: str
    model_name: str
    manufacturer: str
    manufacturer_id: str
    plan_id: str | None
    year: int
    is_new: bool
    bedrooms: int
    bathrooms: float
    sqft: int
    width: int
    length: int
    msrp: float
    sale_price: float
    status: str
    serial_number: str | None
    sections: int | None
    condition: str
    image_url: str | None
    hero_image: str | None
    photos: list[str]
    floorplan_url: str | None
    matterport_id: str | None
    tags: list[str]
    description: str | None
    features: list[str]
    location: str
    date_added: str
    source_url: str

    def to_firestore_dict(self) -> dict:
        """Convert to Firestore document format.

        Runs the Inventory Pydantic model as a soft validation gate before
        returning the dict. Validation failures are logged at WARNING with
        the violation reason and the legacy_inventory_id, but the dict is
        still returned so production sync is never blocked. This is intentional
        — see PR #18 / #19 context: surface data quality issues without
        regressing the 43-home live sync.
        """
        # Soft validation — log violations but do not raise.
        try:
            Inventory(
                serial_number=self.serial_number or self.id,
                model_name=self.model_name,
                manufacturer=self.manufacturer,
                msrp=self.msrp if self.msrp else None,
                sale_price=self.sale_price if self.sale_price else None,
                bedrooms=self.bedrooms if self.bedrooms is not None else None,
                bathrooms=int(self.bathrooms) if self.bathrooms is not None else None,
                sqft=self.sqft if self.sqft is not None else None,
                width=self.width if self.width is not None else None,
                length=self.length if self.length is not None else None,
                status=self.status,
            )
        except ValidationError as exc:
            reasons = "; ".join(
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
            )
            logger.warning(
                "Inventory validation failed for legacy_id=%s model=%r: %s",
                self.id,
                self.model_name,
                reasons,
            )

        return {
            "model_name": self.model_name,
            "manufacturer": self.manufacturer,
            "manufacturer_id": self.manufacturer_id,
            "plan_id": self.plan_id,
            "year": self.year,
            "is_new": self.is_new,
            "bedrooms": self.bedrooms,
            "bathrooms": self.bathrooms,
            "sqft": self.sqft,
            "width": self.width,
            "length": self.length,
            "classification": "Double Wide" if self.width >= 24 else "Single Wide",
            "msrp": self.msrp,
            "sale_price": self.sale_price,
            "status": self.status,
            "serial_number": self.serial_number,
            "sections": self.sections,
            "condition": self.condition,
            "image_url": self.image_url,
            "hero_image": self.hero_image,
            "photos": self.photos,
            "floorplan_url": self.floorplan_url,
            "matterport_id": self.matterport_id,
            "tags": self.tags,
            "description": self.description,
            "features": self.features,
            "location": self.location,
            "date_added": self.date_added,
            "source_url": self.source_url,
            "source": "texashomeoutlet.com",
            "legacy_inventory_id": self.id,
            "last_synced": datetime.now().isoformat(),
        }


class InventorySync:
    """Syncs inventory from website to Firestore."""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.db = None
        self.stats = {"scraped": 0, "added": 0, "updated": 0, "skipped": 0, "errors": 0}

        if not dry_run and GCP_AVAILABLE:
            self._init_firestore()

    def _init_firestore(self):
        """Initialize Firestore client."""
        try:
            project_id = self._firestore_project_id()
            self.db = firestore.Client(project=project_id)
            logger.info(f"Connected to Firestore project: {project_id}")
        except Exception as e:
            logger.error(f"Failed to initialize Firestore: {e}")
            self.db = None

    def _firestore_project_id(self) -> str:
        """Return the active THO Firestore project ID."""
        return os.environ.get("FIRESTORE_PROJECT_ID", "tho-ai-agent")

    def scrape_website_inventory(self) -> list[dict]:
        """
        Scrape all inventory from texashomeoutlet.com
        Returns list of raw home data from website.
        """
        logger.info("Scraping texashomeoutlet.com inventory...")

        homes = []
        seen_ids = set()
        page = 1
        max_pages = 25

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }

        while page <= max_pages:
            url = self._inventory_page_url(page)
            try:
                response = requests.get(url, headers=headers, timeout=15)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")

                cards = soup.select(".fp-card-container")
                if not cards:
                    logger.info(f"No more listings found at page {page}")
                    break

                new_on_page = 0
                for card in cards:
                    home = self._extract_card_data(card)
                    if home and home["id"] not in seen_ids:
                        seen_ids.add(home["id"])
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
        self.stats["scraped"] = len(homes)
        return homes

    def _inventory_page_url(self, page: int) -> str:
        """Return the legacy provider URL for an inventory page.

        Page 1 intentionally uses the canonical /inventory/ URL. The provider's
        /inventory/?pg=1 variant can omit sale labels and floorplan metadata for
        the same listings.
        """
        if page <= 1:
            return f"{OLD_SITE_URL}/inventory/"
        return f"{OLD_SITE_URL}/inventory/?pg={page}"

    def _extract_card_data(self, card) -> dict | None:
        """Extract data from a single inventory card."""
        try:
            # Title and Link
            title_tag = card.select_one("h4.home-name a")
            if not title_tag:
                return None

            title = title_tag.get_text(strip=True)
            detail_url = title_tag["href"]

            # Extract ID from URL
            match = re.search(r"/inventory-detail/(\d+)/", detail_url)
            listing_id = match.group(1) if match else "unknown"

            # Manufacturer
            manufacturer = "Unknown"
            built_by_tag = card.select_one(".fp-card-built-by")
            if built_by_tag:
                text = built_by_tag.get_text(strip=True)
                manufacturer = text.replace("Built by:", "").strip()

            # Specs
            specs = {"beds": 0, "baths": 0, "sq_ft": 0, "width": 0, "length": 0}

            bed_icon = card.select_one(".icon-mfh-bed")
            if bed_icon and bed_icon.parent:
                text = bed_icon.parent.get_text(strip=True)
                match = re.search(r"(\d+)", text)
                if match:
                    specs["beds"] = int(match.group(1))

            bath_icon = card.select_one(".icon-mfh-bath")
            if bath_icon and bath_icon.parent:
                text = bath_icon.parent.get_text(strip=True)
                match = re.search(r"(\d+(\.\d+)?)", text)
                if match:
                    specs["baths"] = float(match.group(1))

            sqft_icon = card.select_one(".icon-mfh-tapemeasure")
            if sqft_icon and sqft_icon.parent:
                text = sqft_icon.parent.get_text(strip=True)
                match = re.search(r"(\d+,?\d*)", text)
                if match:
                    specs["sq_ft"] = int(match.group(1).replace(",", ""))

            dim_icon = card.select_one(".icon-mfh-lengthwidth")
            if dim_icon and dim_icon.parent:
                text = dim_icon.parent.get_text(" ", strip=True)
                match = re.search(r"(\d+)'\s*\d*\"?\s*x\s*(\d+)'\s*\d*\"?", text)
                if match:
                    specs["width"] = int(match.group(1))
                    specs["length"] = int(match.group(2))

            # Image
            image_url = None
            image_div = card.select_one(".fp-card-image")
            if image_div and image_div.has_attr("data-bg"):
                image_url = image_div["data-bg"]
                if image_url.startswith("/"):
                    image_url = OLD_SITE_URL + image_url

            # Floorplan
            floorplan_url = None
            fp_link = card.select_one("a.fp-thumb")
            if fp_link and fp_link.has_attr("href"):
                floorplan_url = fp_link["href"]

            matterport_id = None
            tour_link = card.select_one("a.tour-thumb[href]")
            if tour_link:
                matterport_id = self._extract_matterport_id(tour_link.get("href"))

            tags = [
                tag.get_text(" ", strip=True)
                for tag in card.select(".fp-card-tags .label")
                if tag.get_text(" ", strip=True)
            ]

            # Price - try to extract from various locations
            price = 0.0
            price_tag = card.select_one(".fp-card-price")
            if price_tag:
                price_text = price_tag.get_text(strip=True)
                match = re.search(r"[\d,]+\.?\d*", price_text.replace(",", ""))
                if match:
                    price = float(match.group(0).replace(",", ""))

            # Year (for pre-owned homes)
            year = datetime.now().year
            year_match = re.search(r"\b(20\d{2})\b", title)
            if year_match:
                year = int(year_match.group(1))

            # Determine if new based on current page labels/title.
            tag_text = " ".join(tags).lower()
            title_lower = title.lower()
            is_new = "pre-owned" not in title_lower and "used" not in tag_text

            return {
                "id": listing_id,
                "model_name": title,
                "manufacturer": manufacturer,
                "specs": specs,
                "price": price,
                "year": year,
                "is_new": is_new,
                "image_url": image_url,
                "floorplan_url": floorplan_url,
                "matterport_id": matterport_id,
                "tags": tags,
                "detail_url": detail_url
                if detail_url.startswith("http")
                else OLD_SITE_URL + detail_url,
            }

        except Exception as e:
            logger.error(f"Error extracting card: {e}")
            return None

    def _extract_matterport_id(self, url: str | None) -> str | None:
        """Extract a Matterport model ID from a show URL."""
        if not url:
            return None
        parsed = urlparse(url)
        ids = parse_qs(parsed.query).get("m", [])
        return ids[0] if ids else None

    def _normalize_model_key(self, model_name: str) -> str:
        """Normalize model names enough to match legacy titles to Firestore docs."""
        value = (model_name or "").lower()
        if "/" in value:
            value = value.split("/")[-1]
        value = value.replace("new year clearance sale", "")
        value = value.replace("pre-owned", "")
        value = value.replace("tru single section", "")
        value = value.replace("tru multi section", "")
        value = re.sub(r"\b(fac|els|slt|cee|tru|sap)\d+[a-z0-9]*\b", "", value)
        value = re.sub(r"\b\d{4}h\d+\b", "", value)
        value = re.sub(r"[^a-z0-9]+", " ", value).strip()
        if value.startswith("the "):
            value = value[4:]
        return value

    def _get_manufacturer_info(
        self, manufacturer_name: str, cdn_url: str = None, floorplan_url: str = None
    ) -> dict:
        """Get standardized manufacturer info."""
        # Try exact match first
        if manufacturer_name in MANUFACTURER_MAP:
            return MANUFACTURER_MAP[manufacturer_name]

        # Try case-insensitive match
        for key, value in MANUFACTURER_MAP.items():
            if key.lower() == manufacturer_name.lower():
                return value

        # Partial match
        for key, value in MANUFACTURER_MAP.items():
            if key.lower() in manufacturer_name.lower() or manufacturer_name.lower() in key.lower():
                return value

        # Try to detect from CDN URLs
        for url in [cdn_url, floorplan_url]:
            if url:
                # Extract manufacturer ID from CDN URL pattern
                match = re.search(r"/manufacturer/(\d+)/", url)
                if match:
                    mfr_id = match.group(1)
                    # Look up manufacturer by ID
                    for key, value in MANUFACTURER_MAP.items():
                        if value.get("manufacturer_id") == mfr_id:
                            return value
                    # Return generic with detected ID
                    return {"name": manufacturer_name, "manufacturer_id": mfr_id, "phone": None}

        return MANUFACTURER_MAP["Unknown"]

    def _get_plan_id(self, model_name: str) -> str | None:
        """Get plan ID for known models."""
        model_lower = model_name.lower()

        # Direct lookup
        if model_lower in KNOWN_MODEL_PLAN_IDS:
            return KNOWN_MODEL_PLAN_IDS[model_lower]

        # Try without "the"
        model_without_the = model_lower.replace("the ", "").strip()
        if model_without_the in KNOWN_MODEL_PLAN_IDS:
            return KNOWN_MODEL_PLAN_IDS[model_without_the]

        # Partial match
        for known_name, plan_id in KNOWN_MODEL_PLAN_IDS.items():
            if known_name in model_lower or model_without_the in known_name:
                return plan_id

        return None

    def _build_cdn_urls(self, manufacturer_id: str, plan_id: str) -> list[str]:
        """Build expected CDN URLs for a home."""
        base_url = f"{CDN_BASE}/manufacturer/{manufacturer_id}/floorplan/{plan_id}"

        # Standard image naming patterns
        patterns = []
        for i in range(1, 21):
            patterns.append(f"{base_url}/{i}.jpg")

        # Also try floorplan
        patterns.append(f"{base_url}/floor-plans.jpg")

        return patterns

    def transform_to_inventory_item(self, raw_data: dict) -> InventoryItem:
        """Transform scraped data to InventoryItem."""
        manufacturer_info = self._get_manufacturer_info(
            raw_data["manufacturer"], raw_data.get("image_url"), raw_data.get("floorplan_url")
        )
        plan_id = self._get_plan_id(raw_data["model_name"])

        # Build photo list from CDN if we have plan_id
        photos = []
        if plan_id and manufacturer_info["manufacturer_id"] != "unknown":
            photos = self._build_cdn_urls(manufacturer_info["manufacturer_id"], plan_id)

        # If we have a website image but no CDN photos, use the website image
        hero_image = raw_data.get("image_url")
        if not photos and hero_image:
            photos = [hero_image]

        return InventoryItem(
            id=raw_data["id"],
            model_name=raw_data["model_name"],
            manufacturer=manufacturer_info["name"],
            manufacturer_id=manufacturer_info["manufacturer_id"],
            plan_id=plan_id,
            year=raw_data.get("year", datetime.now().year),
            is_new=raw_data.get("is_new", True),
            bedrooms=raw_data["specs"].get("beds", 0),
            bathrooms=raw_data["specs"].get("baths", 0),
            sqft=raw_data["specs"].get("sq_ft", 0),
            width=raw_data["specs"].get("width", 0),
            length=raw_data["specs"].get("length", 0),
            msrp=raw_data.get("price", 0),
            sale_price=raw_data.get("price", 0),
            status="AVAILABLE",
            serial_number=None,
            sections=2 if raw_data["specs"].get("width", 0) >= 24 else 1,
            condition="New" if raw_data.get("is_new", True) else "Used",
            image_url=hero_image,
            hero_image=hero_image,
            photos=photos,
            floorplan_url=raw_data.get("floorplan_url"),
            matterport_id=raw_data.get("matterport_id"),
            tags=raw_data.get("tags", []),
            description=None,  # Would need to scrape detail page
            features=[],
            location="Huffman, TX",
            date_added=datetime.now().isoformat(),
            source_url=raw_data.get("detail_url", ""),
        )

    def check_existing_inventory(self, inventory_id: str) -> dict | None:
        """Check if inventory already exists in Firestore."""
        if not self.db:
            return None

        try:
            doc_ref = self.db.collection("inventory").document(inventory_id)
            doc = doc_ref.get()
            if doc.exists:
                data = doc.to_dict()
                data["id"] = doc.id
                return data
            return None
        except Exception as e:
            logger.error(f"Error checking existing inventory: {e}")
            return None

    def find_existing_inventory_by_model(self, model_name: str) -> dict | None:
        """Find an existing inventory item by normalized model name."""
        if not self.db:
            return None

        target_key = self._normalize_model_key(model_name)
        if not target_key:
            return None

        try:
            docs = self.db.collection("inventory").limit(500).stream()
            for doc in docs:
                data = doc.to_dict() or {}
                if self._normalize_model_key(data.get("model_name", "")) == target_key:
                    data["id"] = doc.id
                    return data
        except Exception as e:
            logger.error(f"Error matching existing inventory by model: {e}")
        return None

    def sync_to_firestore(self, items: list[InventoryItem], force_update: bool = False):
        """Sync inventory items to Firestore."""
        if self.dry_run:
            logger.info("DRY RUN: Would sync %d items to Firestore", len(items))
            for item in items[:3]:  # Show first 3
                logger.info("  - %s (ID: %s)", item.model_name, item.id)
            if len(items) > 3:
                logger.info("  ... and %d more", len(items) - 3)
            return

        if not self.db:
            logger.error("Firestore not available. Use --dry-run to see what would be synced.")
            return

        for item in items:
            try:
                existing = self.check_existing_inventory(item.id)
                if not existing:
                    existing = self.find_existing_inventory_by_model(item.model_name)

                if existing and not force_update:
                    logger.debug(f"Skipping existing inventory: {item.model_name}")
                    self.stats["skipped"] += 1
                    continue

                doc_id = existing.get("id") if existing else item.id
                doc_ref = self.db.collection("inventory").document(doc_id)
                data = item.to_firestore_dict()

                if existing:
                    # Update existing
                    doc_ref.update(data)
                    logger.info(f"Updated: {item.model_name}")
                    self.stats["updated"] += 1
                else:
                    # Create new
                    doc_ref.set(data)
                    logger.info(f"Added: {item.model_name}")
                    self.stats["added"] += 1

            except Exception as e:
                logger.error(f"Error syncing {item.model_name}: {e}")
                self.stats["errors"] += 1

    def run_sync(self, force_update: bool = False) -> dict:
        """Run full sync process."""
        logger.info("=" * 60)
        logger.info("STARTING INVENTORY SYNC")
        logger.info("=" * 60)

        # Step 1: Scrape website
        raw_homes = self.scrape_website_inventory()

        if not raw_homes:
            logger.error("No homes scraped from website. Aborting.")
            return self.stats

        # Step 2: Transform to inventory items
        logger.info("\nTransforming %d homes to inventory format...", len(raw_homes))
        inventory_items = []
        for home in raw_homes:
            try:
                item = self.transform_to_inventory_item(home)
                inventory_items.append(item)
            except Exception as e:
                logger.error(f"Error transforming {home.get('model_name')}: {e}")
                self.stats["errors"] += 1

        logger.info("Transformed %d items successfully", len(inventory_items))

        # Step 3: Save preview to file (for review)
        preview_file = (
            f"data/inventory_sync_preview_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        os.makedirs("data", exist_ok=True)
        with open(preview_file, "w") as f:
            json.dump([asdict(item) for item in inventory_items], f, indent=2, default=str)
        logger.info("Preview saved to: %s", preview_file)

        # Step 4: Sync to Firestore
        logger.info("\nSyncing to Firestore...")
        self.sync_to_firestore(inventory_items, force_update)

        # Step 5: Print summary
        self._print_summary()

        return self.stats

    def _print_summary(self):
        """Print sync summary."""
        print("\n" + "=" * 60)
        print("INVENTORY SYNC SUMMARY")
        print("=" * 60)
        print(f"Homes scraped:     {self.stats['scraped']}")
        print(f"Added to DB:       {self.stats['added']}")
        print(f"Updated in DB:     {self.stats['updated']}")
        print(f"Skipped (exists):  {self.stats['skipped']}")
        print(f"Errors:            {self.stats['errors']}")
        print("=" * 60)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Sync inventory from website to Firestore")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be synced without making changes"
    )
    parser.add_argument("--force", action="store_true", help="Update existing inventory items")
    parser.add_argument(
        "--scrape-only", action="store_true", help="Only scrape website, do not sync to Firestore"
    )

    args = parser.parse_args()

    # Run sync
    sync = InventorySync(dry_run=args.dry_run or args.scrape_only)
    stats = sync.run_sync(force_update=args.force)

    # Exit with error code if there were errors
    sys.exit(0 if stats["errors"] == 0 else 1)


if __name__ == "__main__":
    main()
