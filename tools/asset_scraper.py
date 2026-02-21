"""
Asset Scraper for Texas Home Outlet.

Scrapes texashomeoutlet.com for real property images (CloudFront CDN)
and Matterport 3D tour IDs, then caches them for use in Ad Studio.

CDN pattern: https://d132mt2yijm03y.cloudfront.net/manufacturer/{id}/floorplan/{id}/{filename}
Pre-owned:   https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/{id}/{filename}
Matterport:  https://my.matterport.com/show/?m={TOUR_ID}
"""

import re
import json
import logging
import os
from typing import Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

CDN_BASE = "https://d132mt2yijm03y.cloudfront.net"
MATTERPORT_BASE = "https://my.matterport.com/show/?m="
ASSET_CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "property_assets.json")

# ─── Hardcoded asset catalog (scraped from texashomeoutlet.com Feb 2026) ───
# This avoids runtime scraping — we cache the known assets from the website.

PROPERTY_ASSETS = {
    # ──── NEW HOMES ────
    "the-big-steve": {
        "name": "The Big Steve",
        "beds": 3, "baths": 2, "sqft": 1680, "dims": "32x56",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": "225053",
        "floor_plan": f"{CDN_BASE}/manufacturer/3335/floorplan/225053/the-big-steve-floor-plans.jpg",
        "images": [
            f"{CDN_BASE}/manufacturer/3335/floorplan/225053/big-steve-kit-1.jpg",
            f"{CDN_BASE}/manufacturer/3335/floorplan/225053/big-steve-kit-2.jpg",
            f"{CDN_BASE}/manufacturer/3335/floorplan/225053/big-steve-kit-3.jpg",
            f"{CDN_BASE}/manufacturer/3335/floorplan/225053/big-steve-kit-4.jpg",
            f"{CDN_BASE}/manufacturer/3335/floorplan/225053/big-steve-int-1.jpg",
            f"{CDN_BASE}/manufacturer/3335/floorplan/225053/big-steve-int-2.jpg",
            f"{CDN_BASE}/manufacturer/3335/floorplan/225053/big-steve-int-3.jpg",
            f"{CDN_BASE}/manufacturer/3335/floorplan/225053/big-steve-int-4.jpg",
        ],
        "matterport_id": "JbALitnto3g",
        "is_new": True,
    },
    "the-willison": {
        "name": "The Willison",
        "beds": 3, "baths": 2, "sqft": 1180, "dims": "16x76",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": "225054",
        "floor_plan": f"{CDN_BASE}/manufacturer/3335/floorplan/225054/The-Willison-floor-plans.jpg",
        "images": [f"{CDN_BASE}/manufacturer/3335/floorplan/225054/The-Willison-floor-plans.jpg"],
        "matterport_id": "X4kxMpwHFCj",
        "is_new": True,
    },
    "the-bobby-jo": {
        "name": "The Bobby Jo",
        "beds": 2, "baths": 2, "sqft": 900, "dims": "16x66",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": "225062",
        "floor_plan": f"{CDN_BASE}/manufacturer/3335/floorplan/225062/the%20bobby%20jo%20floor-plans.jpg",
        "images": [f"{CDN_BASE}/manufacturer/3335/floorplan/225062/the%20bobby%20jo%20floor-plans.jpg"],
        "matterport_id": "soxcQGiu2Zg",
        "is_new": True,
    },
    "the-vail": {
        "name": "The Vail (Park Model)",
        "beds": 1, "baths": 1, "sqft": 396, "dims": "12x34",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": "225063",
        "floor_plan": f"{CDN_BASE}/manufacturer/3335/floorplan/225063/the-vail-floor-plans.jpg",
        "images": [f"{CDN_BASE}/manufacturer/3335/floorplan/225063/the-vail-floor-plans.jpg"],
        "matterport_id": "vBEByY7x1sL",
        "is_new": True,
    },
    "the-whitehaven": {
        "name": "The Whitehaven",
        "beds": 3, "baths": 2, "sqft": 1568, "dims": "28x56",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": "227079",
        "floor_plan": f"{CDN_BASE}/manufacturer/3335/floorplan/227079/Whitehaven%20floor-plans.jpg",
        "images": [f"{CDN_BASE}/manufacturer/3335/floorplan/227079/Whitehaven%20floor-plans.jpg"],
        "matterport_id": "tMC8frQ8vvH",
        "is_new": True,
    },
    "the-sherman": {
        "name": "The Sherman",
        "beds": 4, "baths": 2, "sqft": 1944, "dims": "28x72",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": "227080",
        "floor_plan": f"{CDN_BASE}/manufacturer/3335/floorplan/227080/sherman%20floor-plans-SMALL.jpg",
        "images": [f"{CDN_BASE}/manufacturer/3335/floorplan/227080/sherman%20floor-plans-SMALL.jpg"],
        "matterport_id": "28Zpa86S53Q",
        "is_new": True,
    },
    "the-fiesta": {
        "name": "The Fiesta",
        "beds": 3, "baths": 2, "sqft": 1344, "dims": "28x48",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": "227313",
        "floor_plan": f"{CDN_BASE}/manufacturer/3335/floorplan/227313/fiesta%20fixed%20floor-plans-SMALL.jpg",
        "images": [f"{CDN_BASE}/manufacturer/3335/floorplan/227313/fiesta%20fixed%20floor-plans-SMALL.jpg"],
        "matterport_id": "dHUHPwxVnLg",
        "is_new": True,
    },
    "the-charleston": {
        "name": "The Charleston",
        "beds": 3, "baths": 2, "sqft": 1815, "dims": "28x66",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": "231478",
        "floor_plan": f"{CDN_BASE}/manufacturer/3335/floorplan/231478/The%20Charleston%20floor-plans-SMALL%20%282%29.jpg",
        "images": [f"{CDN_BASE}/manufacturer/3335/floorplan/231478/The%20Charleston%20floor-plans-SMALL%20%282%29.jpg"],
        "matterport_id": "qChSMQi5t5E",
        "is_new": True,
    },
    "the-tony": {
        "name": "The Tony",
        "beds": 3, "baths": 2, "sqft": 1012, "dims": "16x76",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": "231916",
        "floor_plan": f"{CDN_BASE}/manufacturer/3335/floorplan/231916/the%20tony%20floor-plans-SMALL.png",
        "images": [f"{CDN_BASE}/manufacturer/3335/floorplan/231916/the%20tony%20floor-plans-SMALL.png"],
        "matterport_id": "HtLxXhBdNmG",
        "is_new": True,
    },
    "the-stephens": {
        "name": "The Stephens",
        "beds": 3, "baths": 2, "sqft": 1680, "dims": "28x64",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": "232171",
        "floor_plan": f"{CDN_BASE}/manufacturer/3335/floorplan/232171/The%20Stephens%20floor-plans-SMALL.jpg",
        "images": [f"{CDN_BASE}/manufacturer/3335/floorplan/232171/The%20Stephens%20floor-plans-SMALL.jpg"],
        "matterport_id": "k3YazXae32T",
        "is_new": True,
    },
    "the-copperwood": {
        "name": "The Copperwood (Park Model)",
        "beds": 1, "baths": 1, "sqft": 396, "dims": "12x34",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": "232172",
        "floor_plan": f"{CDN_BASE}/manufacturer/3335/floorplan/232172/The%20Copperwood%20floor-plans-SMALL.png",
        "images": [f"{CDN_BASE}/manufacturer/3335/floorplan/232172/The%20Copperwood%20floor-plans-SMALL.png"],
        "matterport_id": "ZCSYfEbBY3W",
        "is_new": True,
    },
    "the-anderson": {
        "name": "The Anderson (Park Model)",
        "beds": 1, "baths": 1, "sqft": 396, "dims": "12x40",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": "232844",
        "floor_plan": f"{CDN_BASE}/manufacturer/3335/floorplan/232844/The%20Anderson%20floor-plans-SMALL.jpg",
        "images": [f"{CDN_BASE}/manufacturer/3335/floorplan/232844/The%20Anderson%20floor-plans-SMALL.jpg"],
        "matterport_id": "PaB34i7g27v",
        "is_new": True,
    },
    "the-suite-sara": {
        "name": "The Suite Sara",
        "beds": 4, "baths": 2, "sqft": 1560, "dims": "30x52",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": "234918",
        "floor_plan": f"{CDN_BASE}/manufacturer/3335/floorplan/234918/Suite%20Sara.jpg",
        "images": [f"{CDN_BASE}/manufacturer/3335/floorplan/234918/Suite%20Sara.jpg"],
        "matterport_id": "JkYCD7EonBP",
        "is_new": True,
    },
    "the-big-josh": {
        "name": "The Big Josh",
        "beds": 4, "baths": 3, "sqft": 2280, "dims": "32x76",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": "235402",
        "floor_plan": f"{CDN_BASE}/manufacturer/3335/floorplan/235402/The%20Big%20Josh.jpg",
        "images": [f"{CDN_BASE}/manufacturer/3335/floorplan/235402/The%20Big%20Josh.jpg"],
        "matterport_id": "nMza1rTBGKi",
        "is_new": True,
    },
    "the-big-bo": {
        "name": "The Big Bo",
        "beds": 3, "baths": 2, "sqft": 1360, "dims": "18x80",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": "235403",
        "floor_plan": f"{CDN_BASE}/manufacturer/3335/floorplan/235403/The%20Big%20Bo.jpg",
        "images": [f"{CDN_BASE}/manufacturer/3335/floorplan/235403/The%20Big%20Bo.jpg"],
        "matterport_id": "8DsvhvabUqW",
        "is_new": True,
    },
    "the-fiesta-2": {
        "name": "The Fiesta 2.0",
        "beds": 3, "baths": 2, "sqft": 1296, "dims": "28x56",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": None,
        "floor_plan": None,
        "images": [],
        "matterport_id": "ib9PxMThwCy",
        "is_new": True,
    },
    # ──── PRE-OWNED HOMES ────
    "big-blue": {
        "name": "Big Blue",
        "beds": 3, "baths": 2, "sqft": 1152, "dims": "16x72",
        "manufacturer": "Pre-Owned",
        "manufacturer_id": None, "plan_id": None,
        "floor_plan": f"{CDN_BASE}/dealer/3522/inventory/44490/floor-plans-SMALL.jpg",
        "images": [f"{CDN_BASE}/dealer/3522/inventory/44490/floor-plans-SMALL.jpg"],
        "matterport_id": None,
        "is_new": False,
    },
    "the-cottage": {
        "name": "The Cottage",
        "beds": 2, "baths": 1, "sqft": 864, "dims": "16x54",
        "manufacturer": "Pre-Owned",
        "manufacturer_id": None, "plan_id": None,
        "floor_plan": f"{CDN_BASE}/dealer/3522/inventory/44491/floor-plans-SMALL%20%281%29.jpg",
        "images": [f"{CDN_BASE}/dealer/3522/inventory/44491/floor-plans-SMALL%20%281%29.jpg"],
        "matterport_id": None,
        "is_new": False,
    },
    "select-legacy-s-2468": {
        "name": "Select Legacy S-2468-42A",
        "beds": 4, "baths": 2, "sqft": 1483, "dims": "24x68",
        "manufacturer": "Pre-Owned",
        "manufacturer_id": "1944", "plan_id": "223000",
        "floor_plan": f"{CDN_BASE}/manufacturer/1944/floorplan/223000/S-2468-42A-floor-plans.jpg",
        "images": [f"{CDN_BASE}/manufacturer/1944/floorplan/223000/S-2468-42A-floor-plans.jpg"],
        "matterport_id": "r7T4g8z8iLo",
        "is_new": False,
    },
}


def get_all_assets() -> dict:
    """Return the full property asset catalog."""
    return {
        "success": True,
        "properties": PROPERTY_ASSETS,
        "total": len(PROPERTY_ASSETS),
        "with_matterport": sum(1 for p in PROPERTY_ASSETS.values() if p.get("matterport_id")),
        "with_images": sum(1 for p in PROPERTY_ASSETS.values() if p.get("images")),
        "cdn_base": CDN_BASE,
        "matterport_base": MATTERPORT_BASE,
    }


# ─── Alias mapping: Inventory model names → website display names ───
# Bridges the gap between manufacturer model IDs and friendly names on texashomeoutlet.com
INVENTORY_ALIASES = {
    # Map known inventory model numbers to asset scraper slugs
    # Add more as inventory/website correlation is discovered
    "select legacy s-2468-42a": "select-legacy-s-2468",
}


def get_assets_for_home(home_name: str) -> Optional[dict]:
    """Look up assets for a home by name (fuzzy match)."""
    name_lower = home_name.lower().strip()

    # Check alias mapping first (inventory model name → website slug)
    alias_slug = INVENTORY_ALIASES.get(name_lower)
    if alias_slug and alias_slug in PROPERTY_ASSETS:
        return PROPERTY_ASSETS[alias_slug]

    # Direct slug match
    for slug, asset in PROPERTY_ASSETS.items():
        if slug == name_lower.replace(" ", "-"):
            return asset

    # Name contains match
    for slug, asset in PROPERTY_ASSETS.items():
        if name_lower in asset["name"].lower() or asset["name"].lower() in name_lower:
            return asset

    # Partial word match (at least 2 non-common words must match)
    for slug, asset in PROPERTY_ASSETS.items():
        name_words = set(name_lower.split())
        asset_words = set(asset["name"].lower().split())
        common = {"the", "a", "an", "model", "park"}
        meaningful_matches = (name_words & asset_words) - common
        if len(meaningful_matches) >= 1:
            return asset

    return None


def get_matterport_url(tour_id: str) -> str:
    """Build a Matterport embed URL from a tour ID."""
    return f"{MATTERPORT_BASE}{tour_id}&play=1"


def get_homes_with_tours() -> list:
    """Return all homes that have Matterport 3D tours."""
    return [
        {
            "name": asset["name"],
            "slug": slug,
            "matterport_id": asset["matterport_id"],
            "matterport_url": get_matterport_url(asset["matterport_id"]),
            "beds": asset["beds"],
            "baths": asset["baths"],
            "sqft": asset["sqft"],
            "floor_plan": asset.get("floor_plan"),
            "is_new": asset["is_new"],
        }
        for slug, asset in PROPERTY_ASSETS.items()
        if asset.get("matterport_id")
    ]
