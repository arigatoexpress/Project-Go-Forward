"""The staff editor's advertised price is independent from internal MSRP."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_api_v1 import create_client

from tools import inventory_tools, marketing_tools

PUBLIC_LOADER = marketing_tools.get_inventory_for_ads


def test_public_price_load_save_and_clear_matches_storefront(monkeypatch):
    client, main, db, _ = create_client(monkeypatch)
    monkeypatch.setitem(sys.modules, "tools.inventory_tools", inventory_tools)
    monkeypatch.setattr(main, "get_inventory_for_ads", PUBLIC_LOADER)
    monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
    monkeypatch.setattr(main, "_load_legacy_inventory_media_index", lambda: {})
    monkeypatch.setattr(main, "_overlay_staff_photos", lambda homes: None)
    monkeypatch.setattr(main, "PROPERTY_ASSETS", {})
    monkeypatch.setattr(
        main, "load_legacy_floorplan_catalog_context", lambda **kwargs: {"homes": []}
    )
    monkeypatch.setattr(inventory_tools, "_load_website_homes", lambda: [])
    monkeypatch.setenv("INVENTORY_SOURCE", "firestore")
    db.collections["inventory"] = {
        "listing": {
            "id": "listing",
            "model_name": "Synthetic",
            "status": "AVAILABLE",
            "msrp": 999999,
        }
    }
    main.app.dependency_overrides[main.require_admin] = lambda: True
    try:
        home = client.get("/api/inventory").json()["inventory"][0]
        assert home["public_sale_price"] is None
        for amount, displayed in [
            (89900, "$89,900"),
            (89900.50, "$89,900.50"),
            (0, "Call for Price"),
        ]:
            assert client.put("/api/inventory/listing", json={"sale_price": amount}).json()[
                "success"
            ]
            home = client.get("/api/inventory").json()["inventory"][0]
            assert home["public_sale_price"] == amount
            public = client.get("/api/marketing/inventory-context").json()["homes"][0]
            assert public["display_price"] == displayed
            assert public["price_value"] == amount
            assert db.collections["inventory"]["listing"]["msrp"] == 999999
    finally:
        main.app.dependency_overrides.clear()


@pytest.mark.parametrize("value", [-1, "Infinity", "NaN", 123.456])
def test_invalid_public_price_is_rejected_without_updating_record(monkeypatch, value):
    client, main, db, _ = create_client(monkeypatch)
    monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
    main.app.dependency_overrides[main.require_admin] = lambda: True
    db.collections["inventory"] = {"listing": {"id": "listing", "sale_price": 25000}}
    try:
        response = client.put("/api/inventory/listing", json={"sale_price": value})
        assert response.status_code == 400
        assert db.collections["inventory"]["listing"]["sale_price"] == 25000
    finally:
        main.app.dependency_overrides.clear()
