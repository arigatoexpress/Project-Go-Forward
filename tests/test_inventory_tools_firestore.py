import sys
import types

from tools import inventory_tools


def test_firestore_inventory_loader_maps_current_firestore_fields(monkeypatch):
    class FakeDatabase:
        def search_inventory(self, status, limit):
            assert status == "AVAILABLE"
            assert limit == 100
            return [
                {
                    "id": "44490",
                    "model_name": "Big Blue",
                    "manufacturer": "Pre-Owned",
                    "status": "AVAILABLE",
                    "is_new": False,
                    "bedrooms": 3,
                    "bathrooms": 2,
                    "sqft": 1152,
                    "width": 16,
                    "length": 72,
                    "sale_price": 0,
                    "photos": ["https://example.com/1.jpg", "https://example.com/2.jpg"],
                    "floorplan_url": "https://example.com/floorplan.jpg",
                    "matterport_id": "abc123",
                }
            ]

    fake_module = types.SimpleNamespace(get_database=lambda: FakeDatabase())
    monkeypatch.setitem(sys.modules, "database.firestore_client", fake_module)

    homes = inventory_tools._load_inventory_from_firestore()

    # NOTE — feat/inventory-photo-classification (2026-04-30):
    # The Firestore loader now applies tools.photo_classifier so that
    # image_url is the first exterior photo when one exists. The fixture
    # input has no /floorplan/ URLs in `photos`, so both photos count as
    # exteriors and the first one becomes image_url. The pre-existing
    # floorplan_url is preserved.
    assert homes == [
        {
            "id": "44490",
            "serial_number": "",
            "manufacturer": "Pre-Owned",
            "model_name": "Big Blue",
            "classification": "Single Wide",
            "status": "Pre-Owned",
            "specs": {"beds": 3, "baths": 2, "sq_ft": 1152, "dimensions": "16x72"},
            "pricing": {
                "price_value": 0,
                "display_price": "Call for Price",
                "price_tier": "Under $50k",
                "invoice_amount": None,
            },
            "features": [],
            "marketing_tags": [],
            "image_url": "https://example.com/1.jpg",
            "gallery_images": ["https://example.com/1.jpg", "https://example.com/2.jpg"],
            "real_photos": ["https://example.com/1.jpg", "https://example.com/2.jpg"],
            "image_categories": {},
            "floor_plan_url": "https://example.com/floorplan.jpg",
            "matterport_id": "abc123",
        }
    ]
