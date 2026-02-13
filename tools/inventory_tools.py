"""
Inventory Tools for Texas Home Outlet Sales Agent.

These tools enable the Sales Agent to search inventory and calculate financing.
Uses real data from House Orders spreadsheet (converted to JSON for speed).
"""

from google.adk.tools import ToolContext
from typing import Optional
from datetime import datetime
import json
import os

# Import caching
try:
    # Try absolute import first (for Docker/Cloud Run)
    from caching import cache_get, cache_set
except ImportError:
    try:
        # Try relative import
        from ..caching import cache_get, cache_set
    except ImportError:
        # Fallback for direct script execution from package root
        from my_config_agent.caching import cache_get, cache_set

# Cache Key
INVENTORY_CACHE_KEY = "inventory_dataset"


def _load_inventory_from_firestore():
    """Load inventory from Firestore (cloud-native)"""
    try:
        # Try different import paths for different contexts
        try:
            from ..database.firestore_client import get_database
        except ImportError:
            from my_config_agent.database.firestore_client import get_database
        db = get_database()
        
        # Query all available inventory
        results = db.search_inventory(status="AVAILABLE", limit=100)
        
        if not results:
            return None
        
        # Convert Firestore format to tool format
        inventory = []
        for item in results:
            price_value = item.get("msrp", 0) or 0
            
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
            width = item.get("width", 0)
            classification = "Double Wide" if width >= 28 else "Single Wide"
            
            inventory.append({
                "id": item.get("id", item.get("serial_number", "")),
                "serial_number": item.get("serial_number", ""),
                "manufacturer": item.get("manufacturer", "Unknown"),
                "model_name": item.get("model_name", ""),
                "classification": classification,
                "status": "Available",
                "specs": {
                    "beds": item.get("bedrooms"),
                    "baths": item.get("bathrooms"),
                    "sq_ft": item.get("sq_ft")
                },
                "pricing": {
                    "price_value": price_value,
                    "display_price": f"${price_value:,.0f}" if price_value > 0 else "Call for Price",
                    "price_tier": price_tier,
                    "invoice_amount": item.get("invoice_amount")
                },
                "features": item.get("features", []),
                "marketing_tags": item.get("marketing_tags", []),
                "image_url": item.get("image_url", ""),
                "gallery_images": item.get("gallery_images", [])
            })
        
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
        # "my_config_agent/data/inventory.json", # Handled by relative path above
        "/app/data/inventory.json"
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
        with open(json_path, 'r') as f:
            inventory = json.load(f)
        return inventory
    except Exception as e:
        print(f"[Inventory] Error reading JSON: {e}")
        return None


def _load_inventory():
    """Load inventory with cloud-first strategy and caching"""
    # Check cache (Redis or Local)
    cached_data = cache_get(INVENTORY_CACHE_KEY)
    if cached_data:
        return cached_data
    
    # Strategy 1: Try Firestore (cloud-native)
    # inventory = _load_inventory_from_firestore()
    inventory = None
    
    # Strategy 2: Try local JSON (fast fallback)
    if not inventory:
        inventory = _load_inventory_from_json()
    
    # Strategy 3: Use sample data (ultimate fallback)
    if not inventory:
        inventory = _get_sample_inventory()
    
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
            "pricing": {"price_value": 89900, "display_price": "$89,900", "price_tier": "$75k-$100k"},
            "features": ["Island Kitchen", "Walk-in Closet"],
            "marketing_tags": ["Featured"]
        }
    ]


def search_inventory(
    min_beds: Optional[int] = None,
    max_beds: Optional[int] = None,
    min_baths: Optional[float] = None,
    max_budget: Optional[float] = None,
    classification: Optional[str] = None,
    status: Optional[str] = None,
    features: Optional[list[str]] = None,
    tool_context: ToolContext = None
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
        beds = home["specs"].get("beds")
        baths = home["specs"].get("baths")
        
        if min_beds and (beds is None or beds < min_beds):
            continue
        if max_beds and (beds is None or beds > max_beds):
            continue
        if min_baths and (baths is None or baths < min_baths):
            continue
        if max_budget and home["pricing"]["price_value"] > max_budget:
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
    
    # Sort by price
    results.sort(key=lambda x: x["pricing"]["price_value"])
    
    return {
        "success": True,
        "count": len(results),
        "homes": results,
        "search_summary": f"Found {len(results)} homes matching your criteria.",
        "tip": "Ask about financing options or schedule a showing!" if results else "Try broadening your search criteria."
    }


def calculate_payment(
    price: float,
    down_payment: float = 0.0,
    term_months: int = 240,
    interest_rate: float = 8.5,
    tool_context: ToolContext = None
) -> dict:
    """
    Calculate estimated monthly payment for financing.
    
    Uses standard amortization formula. This is an ESTIMATE only - 
    actual rates depend on credit qualification through 21st Mortgage.
    
    Args:
        price: Home price in dollars
        down_payment: Down payment amount in dollars
        term_months: Loan term in months (default 240 = 20 years)
        interest_rate: Annual interest rate percentage (default 8.5%)
        tool_context: ADK tool context
    
    Returns:
        Dictionary with payment breakdown
    """
    loan_amount = price - down_payment
    
    if loan_amount <= 0:
        return {
            "success": True,
            "message": "With that down payment, you'd own the home outright! No financing needed.",
            "monthly_payment": 0
        }
    
    # Monthly interest rate
    monthly_rate = interest_rate / 100 / 12
    
    # Amortization formula: M = P * [r(1+r)^n] / [(1+r)^n - 1]
    if monthly_rate > 0:
        payment = loan_amount * (monthly_rate * (1 + monthly_rate)**term_months) / ((1 + monthly_rate)**term_months - 1)
    else:
        payment = loan_amount / term_months
    
    return {
        "success": True,
        "home_price": f"${price:,.2f}",
        "down_payment": f"${down_payment:,.2f}",
        "loan_amount": f"${loan_amount:,.2f}",
        "term_years": term_months // 12,
        "interest_rate": f"{interest_rate}%",
        "monthly_payment": f"${payment:,.2f}",
        "monthly_payment_value": round(payment, 2),
        "disclaimer": "This is an ESTIMATE only. Actual rates and terms depend on credit qualification through 21st Mortgage or other lenders. Contact us for a personalized quote!"
    }
