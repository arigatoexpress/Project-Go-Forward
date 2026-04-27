import sys
import types

from tools import marketing_tools


def test_get_inventory_for_ads_preserves_live_firestore_media(monkeypatch):
    monkeypatch.setattr(
        marketing_tools,
        "_load_inventory_for_marketing",
        lambda: [
            {
                "id": "28102",
                "model_name": "NEW YEAR CLEARANCE SALE / TRU Single Section Delight",
                "manufacturer": "TRU Homes",
                "classification": "Single Wide",
                "status": "Available",
                "pricing": {"display_price": "Call for Price", "price_value": 0},
                "specs": {"beds": 2, "baths": 2, "sq_ft": 820, "dimensions": "14x60"},
                "features": ["Lot Model"],
                "image_url": "https://example.com/live-card.jpg",
                "gallery_images": ["https://example.com/live-card.jpg"],
                "floor_plan_url": "https://example.com/live-floorplan.jpg",
                "matterport_id": "SvVRKXdXUQq",
            }
        ],
    )

    asset_scraper = types.ModuleType("tools.asset_scraper")
    asset_scraper.get_assets_for_home = lambda _name: {
        "images": ["https://example.com/stale-photo.jpg"],
        "floor_plan": "https://example.com/stale-floorplan.jpg",
        "matterport_id": "staleTour",
    }
    asset_scraper.get_matterport_url = (
        lambda tour_id: f"https://my.matterport.com/show/?m={tour_id}&play=1"
    )
    monkeypatch.setitem(sys.modules, "tools.asset_scraper", asset_scraper)

    result = marketing_tools.get_inventory_for_ads(limit=10)

    assert result["success"] is True
    assert result["total_inventory"] == 1
    home = result["homes"][0]
    assert home["id"] == "28102"
    assert home["real_photos"] == ["https://example.com/live-card.jpg"]
    assert home["floor_plan_url"] == "https://example.com/live-floorplan.jpg"
    assert home["matterport_id"] == "SvVRKXdXUQq"
    assert home["matterport_url"].endswith("SvVRKXdXUQq&play=1")
