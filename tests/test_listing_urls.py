"""Unit tests for in-stock listing URL helpers."""

from tools.listing_urls import (
    attach_listing_urls,
    is_instock_home,
    listing_lastmod,
    listing_page_images,
    listing_path,
    listing_slug,
    match_instock_home,
    stock_id,
)


def test_listing_slug_and_stock_id():
    assert listing_slug("PRE-OWNED / Big Blue") == "pre-owned-big-blue"
    assert stock_id({"id": "big-blue", "legacy_inventory_id": "44490"}) == "44490"
    assert stock_id({"id": "44490", "model_name": "Big Blue"}) == "44490"


def test_instock_paths_skip_orderable_floorplans():
    used = {
        "id": "44490",
        "model_name": "PRE-OWNED / Big Blue",
        "status": "Pre-Owned",
        "inventory_kind": "pre_owned",
    }
    deal = {
        "id": "43372",
        "model_name": "Premier / Creole 3256H32447",
        "status": "Available",
        "inventory_kind": "available_now",
    }
    plan = {
        "id": "floorplan-223034",
        "legacy_plan_id": "223034",
        "model_name": "Skyliner 4732B",
        "status": "Orderable",
        "inventory_kind": "orderable_floorplan",
    }
    assert is_instock_home(used)
    assert listing_path(used) == "/homes/44490-pre-owned-big-blue"
    assert is_instock_home(deal)
    assert listing_path(deal) == "/homes/43372-premier-creole-3256h32447"
    assert not is_instock_home(plan)
    assert listing_path(plan) is None


def test_used_home_photos_are_own_only_and_floorplan_fallback():
    used = {
        "id": "43945",
        "model_name": "PRE-OWNED / Heritage 1684-32A",
        "status": "Pre-Owned",
        "inventory_kind": "pre_owned",
        "real_photos": ["https://lot.example/1.jpg", "https://lot.example/floor-plan.jpg"],
        "gallery_images": ["https://cdn.example/manufacturer/1/floorplan/x/showroom.jpg"],
        "image_url": "https://cdn.example/manufacturer/1/floorplan/x/showroom.jpg",
    }
    assert listing_page_images(used) == ["https://lot.example/1.jpg"]

    no_photos = {
        **used,
        "real_photos": [],
        "image_url": "https://cdn.example/catalog.jpg",
        "floor_plan_url": "https://lot.example/drawing.jpg",
    }
    assert listing_page_images(no_photos) == ["https://lot.example/drawing.jpg"]


def test_match_instock_home_accepts_stock_id_prefix():
    home = {
        "id": "44490",
        "model_name": "PRE-OWNED / Big Blue",
        "status": "Pre-Owned",
        "inventory_kind": "pre_owned",
    }
    assert match_instock_home("/homes/44490-pre-owned-big-blue", [home]) is home
    assert match_instock_home("/homes/44490-old-slug", [home]) is home
    assert match_instock_home("/homes/99999-missing", [home]) is None


def test_lastmod_prefers_record_date():
    assert listing_lastmod({"updated_at": "2026-10-01T15:22:00Z"}) == "2026-10-01"
    assert listing_lastmod({}, today="2026-10-08") == "2026-10-08"


def test_attach_listing_urls_sets_fields():
    homes = [
        {
            "id": "44490",
            "model_name": "PRE-OWNED / Big Blue",
            "status": "Pre-Owned",
            "inventory_kind": "pre_owned",
        }
    ]
    attach_listing_urls(homes)
    assert homes[0]["stock_id"] == "44490"
    assert homes[0]["listing_url"] == "/homes/44490-pre-owned-big-blue"
