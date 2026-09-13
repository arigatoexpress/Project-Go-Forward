"""Golden staff-to-storefront publication checks using a private fake database."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_api_v1 import create_client

from tools import inventory_tools, marketing_tools

REAL_MARKETING_LOADER = marketing_tools.get_inventory_for_ads


def test_firestore_string_numbers_do_not_silently_discard_staff_inventory(monkeypatch):
    import types

    db = types.SimpleNamespace(
        search_inventory=lambda **kwargs: [
            {
                "id": "lot-1",
                "model_name": "Synthetic double",
                "status": "AVAILABLE",
                "width": "28",
                "length": "60",
                "sale_price": "92000",
                "bedrooms": "3",
            }
        ]
    )
    monkeypatch.setitem(
        sys.modules, "database.firestore_client", types.SimpleNamespace(get_database=lambda: db)
    )
    homes = inventory_tools._load_inventory_from_firestore()
    assert len(homes) == 1
    assert homes[0]["classification"] == "Double Wide"
    assert homes[0]["pricing"]["price_value"] == 92000


def test_publication_does_not_turn_internal_msrp_into_advertised_price(monkeypatch):
    import types

    db = types.SimpleNamespace(
        search_inventory=lambda **kwargs: [
            {
                "id": "lot-1",
                "model_name": "Synthetic",
                "status": "AVAILABLE",
                "sale_price": 0,
                "msrp": 99999,
            }
        ]
    )
    monkeypatch.setitem(
        sys.modules, "database.firestore_client", types.SimpleNamespace(get_database=lambda: db)
    )
    monkeypatch.setattr(inventory_tools, "_load_website_homes", lambda: [])
    homes = inventory_tools._load_inventory_for_publication()
    assert homes[0]["pricing"]["display_price"] == "Call for Price"
    assert homes[0]["pricing"]["price_value"] == 0


def test_retiring_one_unit_does_not_hide_a_different_listing_with_the_same_model(monkeypatch):
    monkeypatch.setattr(
        inventory_tools,
        "_load_inventory_from_firestore",
        lambda **kwargs: [
            {"id": "lot-1", "model_name": "Synthetic", "status": "RETIRED"},
        ],
    )
    monkeypatch.setattr(
        inventory_tools,
        "_load_website_homes",
        lambda: [
            {"id": "static-1", "model_name": "Synthetic", "status": "Available"},
        ],
    )
    assert [h["id"] for h in inventory_tools._load_inventory_for_publication()] == ["static-1"]


def test_missing_status_is_not_published_as_available(monkeypatch):
    import types

    db = types.SimpleNamespace(
        search_inventory=lambda **kwargs: [
            {"id": "unverified", "model_name": "Unknown status"},
            {"id": "wrong-case", "model_name": "Unapproved state", "status": "Available"},
            {"id": "not-an-enum", "model_name": "Unapproved used state", "status": "Pre-Owned"},
            {"id": "listed", "model_name": "Available model", "status": "AVAILABLE"},
        ]
    )
    monkeypatch.setitem(
        sys.modules, "database.firestore_client", types.SimpleNamespace(get_database=lambda: db)
    )
    monkeypatch.setattr(inventory_tools, "_load_website_homes", lambda: [])
    assert [h["id"] for h in inventory_tools._load_inventory_for_publication()] == ["listed"]


def test_staff_edits_and_retirement_publish_without_cached_or_static_resurrection(monkeypatch):
    client, main, db, _ = create_client(monkeypatch)
    monkeypatch.setitem(sys.modules, "tools.inventory_tools", inventory_tools)
    monkeypatch.setenv("INVENTORY_SOURCE", "firestore")
    monkeypatch.setattr(main, "get_inventory_for_ads", REAL_MARKETING_LOADER)
    monkeypatch.setattr(main, "_overlay_staff_photos", lambda homes: None)
    monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
    monkeypatch.setattr(main, "PROPERTY_ASSETS", {})
    monkeypatch.setattr(main, "load_legacy_floorplan_catalog_context", lambda **k: {"homes": []})
    monkeypatch.setattr(
        inventory_tools,
        "cache_get",
        lambda key: [{"id": "stale", "model_name": "Stale listing", "pricing": {"price_value": 0}}],
    )
    monkeypatch.setattr(
        inventory_tools,
        "_load_website_homes",
        lambda: [
            {"id": "lot-1", "model_name": "First model", "status": "Available", "pricing": {}},
            {
                "id": "supplement",
                "model_name": "Untouched catalog",
                "status": "Available",
                "pricing": {},
            },
        ],
    )
    db.collections["inventory"] = {
        "lot-1": {
            "id": "lot-1",
            "model_name": "First model",
            "status": "AVAILABLE",
            "is_new": True,
            "sale_price": 0,
            "serial_number": "PRIVATE-IDENTITY",
        }
    }
    main.app.dependency_overrides[main.require_admin] = lambda: True
    try:
        response = client.get("/api/marketing/inventory-context")
        assert response.headers["Cache-Control"] == "no-cache"
        before = response.json()
        assert [h["id"] for h in before["homes"]] == ["lot-1", "supplement"]
        assert before["source_status"]["reported_source"] == "staff_inventory_with_catalog"
        assert before["source_status"]["freshness"] == "unknown"
        assert "PRIVATE-IDENTITY" not in str(before)
        assert client.put("/api/inventory/lot-1", json={"model_name": "Changed model"}).json()[
            "success"
        ]
        changed = client.get("/api/marketing/inventory-context").json()
        assert changed["homes"][0]["model_name"] == "Changed model"
        assert client.delete("/api/inventory/lot-1").json()["success"]
        retired = client.get("/api/marketing/inventory-context").json()
        assert [h["id"] for h in retired["homes"]] == ["supplement"]
        assert retired["success"] is True
    finally:
        main.app.dependency_overrides.clear()


def test_staff_publication_empty_does_not_load_drafting_fallbacks(monkeypatch):
    monkeypatch.setattr(inventory_tools, "_load_inventory_for_publication", lambda: [])
    monkeypatch.setattr(
        marketing_tools,
        "_load_inventory_for_marketing",
        lambda: (_ for _ in ()).throw(AssertionError("draft fallback")),
    )
    result = REAL_MARKETING_LOADER(staff_publication=True)
    assert result["success"] is True
    assert result["homes"] == []
    assert result["source"] == "staff_inventory_with_catalog"


def test_staff_publication_failure_is_not_reported_as_fresh_or_empty_success(monkeypatch):
    monkeypatch.setattr(inventory_tools, "_load_inventory_for_publication", lambda: None)
    result = REAL_MARKETING_LOADER(staff_publication=True)
    assert result["success"] is False
    assert result["homes"] == []
    assert result["source"] == "staff_inventory_unavailable"


def test_failed_staff_read_can_keep_orderable_catalog_without_claiming_current_homes(monkeypatch):
    client, main, _, _ = create_client(monkeypatch)
    monkeypatch.setenv("INVENTORY_SOURCE", "firestore")
    monkeypatch.setattr(
        main,
        "get_inventory_for_ads",
        lambda **kwargs: {
            "success": False,
            "source": "staff_inventory_unavailable",
            "homes": [],
            "error": "Staff inventory is temporarily unavailable.",
        },
    )
    monkeypatch.setattr(main, "_overlay_staff_photos", lambda homes: None)
    monkeypatch.setattr(
        main, "load_legacy_floorplan_catalog_context", lambda **kwargs: {"homes": []}
    )
    monkeypatch.setattr(
        main,
        "PROPERTY_ASSETS",
        {
            "catalog-1": {"name": "Orderable plan", "is_new": True, "images": []},
            "old-used": {"name": "Historical used home", "is_new": False, "images": []},
        },
    )
    data = client.get("/api/marketing/inventory-context").json()
    assert data["current_inventory_count"] == 0
    assert data["orderable_floorplans"] == 1
    assert all(home["inventory_kind"] == "orderable_floorplan" for home in data["homes"])
    assert "inventory_source_unavailable" in data["warnings"]
    assert data["source_status"]["freshness"] == "unknown"


def test_admin_edit_form_retains_classification_and_features(monkeypatch):
    client, main, db, _ = create_client(monkeypatch)
    main.app.dependency_overrides[main.require_admin] = lambda: True
    monkeypatch.setattr(main, "_load_legacy_inventory_media_index", lambda: {})
    db.collections["inventory"] = {
        "lot-1": {
            "id": "lot-1",
            "model_name": "Double",
            "status": "AVAILABLE",
            "classification": "Double Wide",
            "features": ["Porch"],
        }
    }
    try:
        home = client.get("/api/inventory?status=").json()["inventory"][0]
        assert home["classification"] == "Double Wide"
        assert home["features"] == ["Porch"]
        db.collections["inventory"]["lot-1"]["classification"] = None
        db.collections["inventory"]["lot-1"]["width"] = "28"
        home = client.get("/api/inventory?status=").json()["inventory"][0]
        assert home["classification"] == "Double Wide"
    finally:
        main.app.dependency_overrides.clear()
