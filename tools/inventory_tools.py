"""
Inventory Tools for Texas Home Outlet Sales Agent.

These tools enable the Sales Agent to search inventory and calculate financing.
Uses real data from House Orders spreadsheet (converted to JSON for speed).
"""

import json
import os

try:
    from google.adk.tools import ToolContext
except ImportError:
    ToolContext = None

# Import caching
try:
    # Try absolute import first (for Docker/Cloud Run)
    from caching import cache_get, cache_set
except ImportError:
    try:
        # Try relative import
        from ..caching import cache_get, cache_set
    except ImportError:
        # Fallback: define no-ops if caching unavailable
        def cache_get(key):
            return None

        def cache_set(key, value, ttl_seconds=None):
            pass


# Cache Key
INVENTORY_CACHE_KEY = "inventory_dataset"


def _to_number(value):
    """Coerce numeric inventory fields while preserving missing values."""
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return int(number) if number.is_integer() else number


def _normalized_specs(
    raw_specs: dict | None = None, *, beds=None, baths=None, sq_ft=None, dimensions=None
) -> dict:
    """Return compact specs with conservative bath inference for Tex/search."""
    specs = raw_specs or {}
    bed_count = _to_number(beds if beds is not None else specs.get("beds") or specs.get("bedrooms"))
    bath_count = _to_number(
        baths if baths is not None else specs.get("baths") or specs.get("bathrooms")
    )
    if bed_count and not bath_count:
        bath_count = 1 if bed_count <= 2 else 2
    return {
        "beds": bed_count,
        "baths": bath_count,
        "sq_ft": _to_number(
            sq_ft if sq_ft is not None else specs.get("sq_ft") or specs.get("sqft")
        ),
        "dimensions": dimensions if dimensions is not None else specs.get("dimensions", ""),
    }


def _load_inventory_from_firestore():
    """Load inventory from Firestore (cloud-native)"""
    try:
        # Try different import paths for different contexts
        try:
            from ..database.firestore_client import get_database
        except ImportError:
            from database.firestore_client import get_database
        db = get_database()

        # Query all available inventory
        results = db.search_inventory(status="AVAILABLE", limit=100)

        if not results:
            return None

        # Convert Firestore format to tool format
        try:
            from tools.photo_classifier import apply_classifier_to_home
        except ImportError:
            from .photo_classifier import apply_classifier_to_home

        inventory = []
        for item in results:
            price_value = item.get("sale_price") or item.get("msrp", 0) or 0

            # Price tier
            if price_value < 50000:
                price_tier = "Under $50k"
            elif price_value < 75000:
                price_tier = "$50k-$75k"
            elif price_value < 100000:
                price_tier = "$75k-$100k"
            elif price_value < 150000:
                price_tier = "$100k-$150k"
            else:
                price_tier = "$150k+"

            # Determine classification from width
            width = item.get("width", 0) or 0
            classification = item.get("classification") or (
                "Double Wide" if width >= 24 else "Single Wide"
            )
            status = (
                "Available"
                if item.get("status") == "AVAILABLE"
                else item.get("status", "Available")
            )
            if item.get("is_new") is False and status == "Available":
                status = "Pre-Owned"
            gallery_images = item.get("gallery_images") or item.get("photos") or []

            home = {
                "id": item.get("id", item.get("serial_number", "")),
                "serial_number": item.get("serial_number", ""),
                "manufacturer": item.get("manufacturer", "Unknown"),
                "model_name": item.get("model_name", ""),
                "classification": classification,
                "status": status,
                "specs": _normalized_specs(
                    beds=item.get("bedrooms"),
                    baths=item.get("bathrooms"),
                    sq_ft=item.get("sqft") or item.get("sq_ft"),
                    dimensions=f"{item.get('width')}x{item.get('length')}"
                    if item.get("width") and item.get("length")
                    else "",
                ),
                "pricing": {
                    "price_value": price_value,
                    "display_price": f"${price_value:,.0f}"
                    if price_value > 0
                    else "Call for Price",
                    "price_tier": price_tier,
                    "invoice_amount": item.get("invoice_amount"),
                },
                "features": item.get("features", []),
                "marketing_tags": item.get("marketing_tags", []),
                "image_url": item.get("image_url") or item.get("hero_image") or "",
                "gallery_images": gallery_images[:3],
                "real_photos": gallery_images,
                "image_categories": item.get("image_categories", {}),
                "floor_plan_url": item.get("floorplan_url") or item.get("floor_plan_url"),
                "matterport_id": item.get("matterport_id"),
            }
            # URL-based floorplan classifier: ensures image_url is an
            # exterior (or empty) and floorplan_url is populated when
            # the photo list contains a /floorplan/ URL. See
            # tools/photo_classifier.py for detection rules.
            apply_classifier_to_home(home)
            # apply_classifier_to_home writes both spellings of the
            # floorplan key. Drop the new "floorplan_url" key here so
            # the dict shape matches the existing test contract
            # (which expects only "floor_plan_url").
            home["floor_plan_url"] = home.get("floorplan_url") or home.get("floor_plan_url")
            home.pop("floorplan_url", None)
            inventory.append(home)

        return inventory
    except Exception as e:
        # Firestore not available or error - return None to try fallback
        print(f"[Firestore] Not available: {e}")
        return None


def _load_inventory_from_json():
    """Load inventory from pre-processed JSON file (fast)"""
    # Try multiple paths for robustness
    possible_paths = [
        os.path.join(os.path.dirname(__file__), "..", "data", "inventory.json"),
        os.path.join(os.path.dirname(__file__), "data", "inventory.json"),
        "data/inventory.json",
        # Relative path handled above
        "/app/data/inventory.json",
    ]

    json_path = None
    for path in possible_paths:
        if os.path.exists(path):
            json_path = path
            break

    if not json_path:
        print(f"[Inventory] JSON data file not found in: {possible_paths}")
        return None

    try:
        with open(json_path) as f:
            inventory = json.load(f)
        # Apply URL-based floorplan classifier to keep behavior consistent
        # with the Firestore loader (image_url = exterior, floorplan_url =
        # first floorplan). See tools/photo_classifier.py.
        try:
            from tools.photo_classifier import apply_classifier_to_home
        except ImportError:
            from .photo_classifier import apply_classifier_to_home
        for home in inventory or []:
            apply_classifier_to_home(home)
        return inventory
    except Exception as e:
        print(f"[Inventory] Error reading JSON: {e}")
        return None


def _load_website_homes():
    """Load website homes from asset_scraper.py catalog (New Vision models + pre-owned with galleries)."""
    try:
        from tools.asset_scraper import PROPERTY_ASSETS, get_matterport_url
        from tools.photo_classifier import apply_classifier_to_home
    except ImportError:
        try:
            from .asset_scraper import PROPERTY_ASSETS, get_matterport_url
            from .photo_classifier import apply_classifier_to_home
        except ImportError:
            return []

    homes = []
    for slug, asset in PROPERTY_ASSETS.items():
        width = 0
        dims = asset.get("dims", "")
        if "x" in dims:
            try:
                width = int(dims.split("x")[0])
            except ValueError:
                pass
        classification = "Double Wide" if width >= 28 else "Single Wide"

        price_value = 0
        asset_images = asset.get("images") or []
        home = {
            "id": slug,
            "manufacturer": asset.get("manufacturer", "New Vision Manufacturing"),
            "model_name": asset["name"],
            "classification": classification,
            "status": "Available" if asset.get("is_new") else "Pre-Owned",
            "specs": _normalized_specs(
                beds=asset.get("beds"),
                baths=asset.get("baths"),
                sq_ft=asset.get("sqft"),
                dimensions=asset.get("dims"),
            ),
            "pricing": {
                "price_value": price_value,
                "display_price": "Call for Price",
                "price_tier": "Call for Price",
            },
            "features": [],
            "marketing_tags": [],
            "image_url": asset_images[0] if asset_images else "",
            "gallery_images": asset_images[:3],
            "real_photos": asset_images,
            "image_categories": asset.get("image_categories", {}),
            "floor_plan_url": asset.get("floor_plan"),
            "matterport_id": asset.get("matterport_id"),
            "matterport_url": get_matterport_url(asset["matterport_id"])
            if asset.get("matterport_id")
            else None,
            "source": "website",
        }
        homes.append(apply_classifier_to_home(home))
    return homes


def _load_inventory():
    """Load inventory with cloud-first strategy and caching.

    Merges on-lot inventory (from JSON/Firestore) with website catalog homes
    (from asset_scraper.py) so the agent can find ANY home the user sees.
    """
    # Check cache (Redis or Local)
    cached_data = cache_get(INVENTORY_CACHE_KEY)
    if cached_data:
        return cached_data

    # Strategy 1: Try Firestore (cloud-native)
    inventory = _load_inventory_from_firestore()

    # Strategy 2: Try local JSON (fast fallback)
    if not inventory:
        inventory = _load_inventory_from_json()

    # Strategy 3: Use sample data (ultimate fallback)
    if not inventory:
        inventory = _get_sample_inventory()

    # Merge website homes — avoid duplicates by checking model names
    existing_names = {h["model_name"].lower() for h in inventory}
    website_homes = _load_website_homes()
    for wh in website_homes:
        if wh["model_name"].lower() not in existing_names:
            inventory.append(wh)
            existing_names.add(wh["model_name"].lower())

    # Save to cache if found
    if inventory:
        cache_set(INVENTORY_CACHE_KEY, inventory, ttl_seconds=300)

    return inventory


def _get_sample_inventory():
    """Fallback sample inventory"""
    return [
        {
            "id": "tho-2024-001",
            "manufacturer": "Jessup Housing",
            "model_name": "The Nassau",
            "classification": "Double Wide",
            "status": "Available",
            "specs": {"beds": 3, "baths": 2, "sq_ft": 1264},
            "pricing": {
                "price_value": 89900,
                "display_price": "$89,900",
                "price_tier": "$75k-$100k",
            },
            "features": ["Island Kitchen", "Walk-in Closet"],
            "marketing_tags": ["Featured"],
        }
    ]


def search_inventory(
    min_beds: int | None = None,
    max_beds: int | None = None,
    min_baths: float | None = None,
    max_budget: float | None = None,
    classification: str | None = None,
    status: str | None = None,
    features: list[str] | None = None,
    limit: int = 12,
    tool_context: ToolContext = None,
) -> dict:
    """
    Search THO inventory based on customer preferences.

    Args:
        min_beds: Minimum number of bedrooms
        max_beds: Maximum number of bedrooms
        min_baths: Minimum number of bathrooms
        max_budget: Maximum price budget in dollars
        classification: Home type - "Single Wide" or "Double Wide"
        status: Home condition - "Available" (new homes) or "Pre-Owned" (used homes). Leave empty to search all.
        features: Required features list (e.g., ["Pre-Owned", "4 Bedroom"])
        limit: Maximum number of homes to return to Tex. Total matches are still reported.
        tool_context: ADK tool context

    Returns:
        Dictionary with matching homes and search summary
    """
    results = []

    # Load inventory with cloud-first strategy (cached)
    inventory = _load_inventory()

    for home in inventory:
        # Apply filters
        # Note: JSON data already has integer/float types for specs
        home["specs"] = _normalized_specs(home.get("specs"))
        beds = home["specs"].get("beds")
        baths = home["specs"].get("baths")

        if min_beds and (beds is None or beds < min_beds):
            continue
        if max_beds and (beds is None or beds > max_beds):
            continue
        if min_baths and (baths is None or baths < min_baths):
            continue
        if max_budget:
            price_val = home.get("pricing", {}).get("price_value", 0)
            if price_val > 0 and price_val > max_budget:
                continue
        if classification and home.get("classification", "").lower() != classification.lower():
            continue
        if status:
            home_status = home.get("status", "").lower()
            search_status = status.lower()
            # Support partial matching: "pre-owned" matches "Pre-Owned", "available" matches "Available"
            if search_status not in home_status and home_status not in search_status:
                continue
        if features:
            home_features = [f.lower() for f in home.get("features", [])]
            if not all(f.lower() in home_features for f in features):
                continue

        results.append(home)

    # Sort by price (homes without pricing go last)
    results.sort(key=lambda x: x.get("pricing", {}).get("price_value", 0) or 999999999)

    limited_results = results[: max(1, min(limit or 12, 25))]

    return {
        "success": True,
        "count": len(limited_results),
        "total_matches": len(results),
        "homes": limited_results,
        "search_summary": f"Showing {len(limited_results)} of {len(results)} homes matching your criteria.",
        "tip": "Book an appointment to visit our showroom!"
        if results
        else "Try broadening your search criteria.",
    }
