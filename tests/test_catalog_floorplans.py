from tools.catalog_floorplans import (
    ORDERABLE_KIND,
    apply_inventory_kind,
    build_orderable_catalog,
    build_orderable_catalog_from_context,
    merge_orderable_floorplan_catalog,
)


def test_public_inventory_neutralizes_legacy_availability_claims():
    home = {
        "id": "43372",
        "model_name": "The Creole",
        "status": "Available",
        "description": (
            "The Creole is a manufactured home available for sale now. "
            "This home is sold by Texas Home Outlet. Take a 3D Home Tour, check out photos, "
            "and get a price quote on this floor plan today!"
        ),
    }

    apply_inventory_kind(home)

    description = home["description"].lower()
    assert "available for sale now" not in description
    assert "available now" not in description
    assert "is sold by" not in description
    assert "current price and availability" in description
    assert home["is_in_stock"] is False


def test_public_inventory_marks_stock_only_when_explicitly_verified():
    unverified = apply_inventory_kind({"id": "one", "status": "Available"})
    verified = apply_inventory_kind(
        {"id": "two", "status": "Available", "availability_verified": True}
    )

    assert unverified["is_in_stock"] is False
    assert verified["is_in_stock"] is True


def test_build_orderable_catalog_keeps_only_new_manufacturer_floorplans():
    assets = {
        "the-good-plan": {
            "name": "The Good Plan",
            "manufacturer": "New Vision",
            "is_new": True,
            "beds": 3,
            "baths": 2,
            "sqft": 1200,
            "dims": "16x76",
            "floor_plan": "https://example.com/the-good-plan-floor-plans.jpg",
            "images": ["https://example.com/the-good-plan-kitchen-1.jpg"],
        },
        "preowned-home": {
            "name": "Used Home",
            "manufacturer": "Pre-Owned",
            "is_new": False,
        },
    }

    homes = build_orderable_catalog(assets)

    assert len(homes) == 1
    assert homes[0]["id"] == "catalog-the-good-plan"
    assert homes[0]["home_id"] == "catalog-the-good-plan"
    assert homes[0]["status"] == "Orderable"
    assert homes[0]["inventory_kind"] == ORDERABLE_KIND
    assert homes[0]["availability_label"] == "Orderable floorplan"
    assert homes[0]["is_orderable"] is True
    assert homes[0]["is_in_stock"] is False


def test_merge_orderable_catalog_preserves_live_inventory_first_and_labels_counts():
    result = {
        "success": True,
        "source": "legacy_site_live",
        "homes": [
            {
                "id": "28102",
                "model_name": "TRU Single Section / Delight",
                "manufacturer": "TRU Homes",
                "status": "Available",
            },
            {
                "id": "44490",
                "model_name": "PRE-OWNED / Big Blue",
                "manufacturer": "Texas Home Outlet",
                "status": "Pre-Owned",
            },
        ],
    }
    assets = {
        "the-good-plan": {
            "name": "The Good Plan",
            "manufacturer": "New Vision",
            "is_new": True,
            "beds": 3,
            "baths": 2,
            "sqft": 1200,
            "dims": "16x76",
            "floor_plan": "https://example.com/the-good-plan-floor-plans.jpg",
            "images": ["https://example.com/the-good-plan-kitchen-1.jpg"],
        },
    }

    merged = merge_orderable_floorplan_catalog(result, assets=assets)

    assert [home["id"] for home in merged["homes"]] == ["28102", "44490", "catalog-the-good-plan"]
    assert [home["home_id"] for home in merged["homes"]] == [
        "28102",
        "44490",
        "catalog-the-good-plan",
    ]
    assert [home["inventory_kind"] for home in merged["homes"]] == [
        "available_now",
        "pre_owned",
        "orderable_floorplan",
    ]
    assert merged["available_now"] == 1
    assert merged["preowned_homes"] == 1
    assert merged["orderable_floorplans"] == 1
    assert merged["total_inventory"] == 3
    assert merged["returned_inventory"] == 3
    assert merged["current_inventory_count"] == 2
    assert merged["catalog_summary"]["total_public_homes"] == 3


def test_build_orderable_catalog_from_legacy_floorplan_context():
    context = {
        "success": True,
        "source": "legacy_floorplan_catalog_snapshot",
        "homes": [
            {
                "id": "floorplan-223034",
                "legacy_plan_id": "223034",
                "model_name": "Skyliner / 4732B",
                "manufacturer": "Skyline Homes",
                "image_url": "https://example.com/kitchen-1.jpg",
                "real_photos": ["https://example.com/kitchen-1.jpg"],
                "floorplan_url": "https://example.com/floor-plans.jpg",
                "features": [
                    "https://www.texashomeoutlet.com/Orderable Floorplan",
                    "https://www.texashomeoutlet.com/Manufactured",
                    "https://outside.example.com/not-a-public-tag",
                ],
            }
        ],
    }

    homes = build_orderable_catalog_from_context(context)

    assert homes[0]["inventory_kind"] == ORDERABLE_KIND
    assert homes[0]["status"] == "Orderable"
    assert homes[0]["availability_label"] == "Orderable floorplan"
    assert homes[0]["is_orderable"] is True
    assert homes[0]["features"] == ["Orderable Floorplan", "Manufactured"]
    assert all(not feature.startswith("http") for feature in homes[0]["features"])


def test_merge_prefers_legacy_floorplan_context_and_dedupes_current_home():
    current = {
        "success": True,
        "source": "legacy_site_live",
        "homes": [
            {
                "id": "43372",
                "model_name": "Premier / Creole 3256H32447",
                "manufacturer": "Champion Homes",
                "status": "Available",
                "floorplan_url": "https://example.com/creole-catalog-floorplan.jpg",
                "detail_url": (
                    "https://www.texashomeoutlet.com/inventory-detail/43372/"
                    "texas-home-outlet/huffman/premier/"
                ),
                "quote_url": ("https://www.texashomeoutlet.com/quote/inventory/43372/dealer/3522/"),
            }
        ],
    }
    floorplans = {
        "success": True,
        "source": "legacy_floorplan_catalog_snapshot",
        "source_url": "https://www.texashomeoutlet.com/floor-plans/",
        "homes": [
            {
                "id": "floorplan-235424",
                "legacy_plan_id": "235424",
                "model_name": "Premier / Creole 3256H32447",
                "manufacturer": "Champion Homes",
                "status": "Orderable",
                "floorplan_url": "https://example.com/creole-floorplan.jpg",
                "detail_url": (
                    "https://www.texashomeoutlet.com/plan/235424/premier/" "creole-3256h32447/"
                ),
                "quote_url": ("https://www.texashomeoutlet.com/quote/floorplan/235424/dealer/3522"),
            },
            {
                "id": "floorplan-223034",
                "legacy_plan_id": "223034",
                "model_name": "Skyliner / 4732B",
                "manufacturer": "Skyline Homes",
                "status": "Orderable",
                "detail_url": "https://www.texashomeoutlet.com/plan/223034/skyliner/4732b/",
                "quote_url": "https://www.texashomeoutlet.com/quote/floorplan/223034/dealer/3522",
            },
        ],
    }
    assets = {
        "asset-only-plan": {
            "name": "Asset Only Plan",
            "manufacturer": "New Vision",
            "is_new": True,
        }
    }

    merged = merge_orderable_floorplan_catalog(
        current,
        assets=assets,
        floorplan_context=floorplans,
    )

    assert [home["id"] for home in merged["homes"]] == ["43372", "floorplan-223034"]
    assert merged["available_now"] == 1
    assert merged["orderable_floorplans"] == 1
    assert merged["orderable_floorplans_added"] == 1
    assert merged["catalog_source"] == "legacy_floorplan_catalog_snapshot"
    assert merged["catalog_floorplan_source_count"] == 2
    survivor, unrelated = merged["homes"]
    assert survivor["legacy_detail_aliases"] == [
        "https://www.texashomeoutlet.com/plan/235424/premier/creole-3256h32447/"
    ]
    assert survivor["legacy_quote_aliases"] == [
        "https://www.texashomeoutlet.com/quote/floorplan/235424/dealer/3522"
    ]
    assert "legacy_detail_aliases" not in unrelated
    assert "legacy_quote_aliases" not in unrelated


def test_fuzzy_model_variants_do_not_create_legacy_url_aliases():
    current = {
        "homes": [
            {
                "id": "current-fiesta",
                "model_name": "Fiesta 2.0",
                "manufacturer": "Cavco Homes",
                "status": "Available",
                "detail_url": "https://www.texashomeoutlet.com/inventory-detail/81001/current/fiesta-2-0/",
            },
            {
                "id": "current-8831p",
                "model_name": "Classic / 8831P",
                "manufacturer": "Champion Homes",
                "status": "Available",
                "detail_url": "https://www.texashomeoutlet.com/inventory-detail/81002/current/8831p/",
            },
        ]
    }
    floorplans = {
        "source": "legacy_floorplan_catalog_snapshot",
        "homes": [
            {
                "id": "floorplan-91001",
                "legacy_plan_id": "91001",
                "model_name": "Fiesta",
                "manufacturer": "Cavco Homes",
                "detail_url": "https://www.texashomeoutlet.com/plan/91001/cavco/fiesta/",
                "quote_url": "https://www.texashomeoutlet.com/quote/floorplan/91001/dealer/3522",
            },
            {
                "id": "floorplan-91002",
                "legacy_plan_id": "91002",
                "model_name": "Classic / 8831",
                "manufacturer": "Champion Homes",
                "detail_url": "https://www.texashomeoutlet.com/plan/91002/champion/8831/",
                "quote_url": "https://www.texashomeoutlet.com/quote/floorplan/91002/dealer/3522",
            },
        ],
    }

    merged = merge_orderable_floorplan_catalog(
        current,
        assets={},
        floorplan_context=floorplans,
    )

    # The broad catalog de-dupe still suppresses these substring matches, but
    # their distinct model identities are not safe evidence for URL redirects.
    assert [home["id"] for home in merged["homes"]] == [
        "current-fiesta",
        "current-8831p",
    ]
    for survivor in merged["homes"]:
        assert "legacy_detail_aliases" not in survivor
        assert "legacy_quote_aliases" not in survivor


def test_merge_preserves_explicit_home_id_and_fills_only_missing_values():
    merged = merge_orderable_floorplan_catalog(
        {
            "homes": [
                {
                    "id": "source-id",
                    "home_id": "stable-explicit-id",
                    "model_name": "Explicit Identity",
                    "status": "Available",
                },
                {
                    "id": 12345,
                    "model_name": "Numeric Identity",
                    "status": "Available",
                },
            ]
        },
        assets={},
    )

    assert [home["home_id"] for home in merged["homes"]] == [
        "stable-explicit-id",
        "12345",
    ]


def test_merge_computes_duplicate_signatures_once_per_home(monkeypatch):
    """A large catalog must not renormalize every home for every comparison."""
    from tools import catalog_floorplans

    homes = [
        {
            "id": f"performance-{index:03d}",
            "model_name": f"Performance Model {index:03d} Unique",
            "manufacturer": f"Maker {index:03d} Brand",
            "status": "Available",
        }
        for index in range(60)
    ]
    calls = {"identity": 0, "model": 0, "manufacturer": 0}
    original_identity = catalog_floorplans._identity_keys
    original_model = catalog_floorplans._model_key
    original_manufacturer = catalog_floorplans._manufacturer_key

    def counted_identity(home):
        calls["identity"] += 1
        return original_identity(home)

    def counted_model(home):
        calls["model"] += 1
        return original_model(home)

    def counted_manufacturer(home):
        calls["manufacturer"] += 1
        return original_manufacturer(home)

    monkeypatch.setattr(catalog_floorplans, "_identity_keys", counted_identity)
    monkeypatch.setattr(catalog_floorplans, "_model_key", counted_model)
    monkeypatch.setattr(catalog_floorplans, "_manufacturer_key", counted_manufacturer)

    merged = catalog_floorplans.merge_orderable_floorplan_catalog(
        {"homes": homes},
        assets={},
    )

    assert len(merged["homes"]) == len(homes)
    assert calls == {
        "identity": len(homes),
        "model": len(homes),
        "manufacturer": len(homes),
    }
