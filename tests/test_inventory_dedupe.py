"""Inventory twins: staff list and public /inventory must collapse the same way.

Manage Inventory showed 4 homes twice ("PRE-OWNED / Big Blue" + "Big Blue",
Heritage 1684-32A, Select S-1256-21A, Select S-1272-32A). The public browse
page used a different feed and still showed those twins, plus website-title
pairs like "The Razor" / floorplan-227314.

The shared helper annotates copies; staff hides them in the UI and the public
feed drops them before responding. Firestore is never written.

Run: python -m pytest tests/test_inventory_dedupe.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.inventory_dedupe import (  # noqa: E402
    annotate_possible_duplicates,
    collapse_duplicate_homes,
    is_active_status,
    is_preowned,
    is_stocked_listing,
    normalize_model_key,
    offerings_conflict,
)


def test_normalize_model_key_matches_website_and_bare_titles():
    assert normalize_model_key("PRE-OWNED / Big Blue") == normalize_model_key("Big Blue")
    assert normalize_model_key("PRE-OWNED / Heritage 1684-32A") == normalize_model_key(
        "Heritage 1684-32A"
    )
    assert normalize_model_key("PRE-OWNED / Select S-1256-21A") == normalize_model_key(
        "Select S-1256-21A"
    )
    assert normalize_model_key("The Promotional Series / The Nassau FAC28483A") == "nassau"
    assert normalize_model_key("") == ""


def test_reported_live_duplicates_collapse_to_one_each():
    homes = [
        {"id": "44490", "model_name": "PRE-OWNED / Big Blue", "status": "AVAILABLE"},
        {"id": "fs-big-blue", "model_name": "Big Blue", "status": "AVAILABLE"},
        {"id": "43945", "model_name": "PRE-OWNED / Heritage 1684-32A", "status": "AVAILABLE"},
        {"id": "fs-heritage", "model_name": "Heritage 1684-32A", "status": "AVAILABLE"},
        {"id": "43944", "model_name": "PRE-OWNED / Select S-1256-21A", "status": "AVAILABLE"},
        {"id": "fs-1256", "model_name": "Select S-1256-21A", "status": "AVAILABLE"},
        {"id": "43943", "model_name": "PRE-OWNED / Select S-1272-32A", "status": "AVAILABLE"},
        {"id": "fs-1272", "model_name": "Select S-1272-32A", "status": "AVAILABLE"},
        {"id": "solo", "model_name": "The Nassau", "status": "AVAILABLE"},
    ]
    annotate_possible_duplicates(homes)

    shown = [h for h in homes if not h.get("duplicate_of")]
    assert len(shown) == 5
    # The stocked PRE-OWNED listing keeps its identity.
    by_id = {h["id"]: h for h in homes}
    assert by_id["44490"]["possible_duplicate_ids"] == ["fs-big-blue"]
    assert by_id["fs-big-blue"]["duplicate_of"] == "44490"
    assert "possible_duplicate_ids" not in by_id["solo"]
    assert "duplicate_of" not in by_id["solo"]


def test_same_model_without_serials_stays_two_homes():
    homes = [
        {"id": "lot-a", "model_name": "The Nassau"},
        {"id": "lot-b", "model_name": "The Nassau"},
    ]
    annotate_possible_duplicates(homes)
    assert all("duplicate_of" not in h for h in homes)


def test_bare_model_with_serial_does_not_hide_unserialized_twin():
    homes = [
        {"id": "with-serial", "model_name": "The Nassau", "serial_number": "TXL111"},
        {"id": "no-serial", "model_name": "The Nassau"},
    ]
    annotate_possible_duplicates(homes)
    assert all("duplicate_of" not in h for h in homes)


def test_different_serials_are_different_homes():
    homes = [
        {"id": "a", "model_name": "The Nassau", "serial_number": "TXL111"},
        {"id": "b", "model_name": "The Nassau", "serial_number": "TXL222"},
    ]
    annotate_possible_duplicates(homes)
    assert all("duplicate_of" not in h for h in homes)


def test_same_serial_is_flagged_and_unserialed_left_alone_when_ambiguous():
    homes = [
        {"id": "a", "model_name": "The Nassau", "serial_number": "TXL111"},
        {"id": "b", "model_name": "PRE-OWNED / The Nassau", "serial_number": "txl111"},
        {"id": "c", "model_name": "The Nassau", "serial_number": "TXL222"},
        {"id": "d", "model_name": "The Nassau"},
    ]
    annotate_possible_duplicates(homes)
    by_id = {h["id"]: h for h in homes}
    assert by_id["b"]["possible_duplicate_ids"] == ["a"]
    assert by_id["a"]["duplicate_of"] == "b"
    assert "duplicate_of" not in by_id["c"]
    assert "duplicate_of" not in by_id["d"]


def test_active_record_with_serial_and_photos_is_preferred():
    homes = [
        {"id": "retired", "model_name": "Big Blue", "status": "RETIRED", "serial_number": "S1"},
        {"id": "photos", "model_name": "PRE-OWNED / Big Blue", "real_photos": ["x.jpg"]},
        {"id": "serial", "model_name": "PRE-OWNED / Big Blue", "serial_number": "S1"},
    ]
    annotate_possible_duplicates(homes)
    by_id = {h["id"]: h for h in homes}
    assert sorted(by_id["serial"]["possible_duplicate_ids"]) == ["photos", "retired"]


def test_is_active_status():
    assert is_active_status("AVAILABLE")
    assert is_active_status("pending")
    assert is_active_status(None)
    assert not is_active_status("SOLD")
    assert not is_active_status("retired")


def test_admin_inventory_endpoint_annotates_without_writing(monkeypatch):
    from tests.test_api_v1 import create_client

    client, main, fake_db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    fake_db.collections["inventory"].update(
        {
            "44490": {
                "id": "44490",
                "model_name": "PRE-OWNED / Big Blue",
                "status": "AVAILABLE",
                "legacy_inventory_id": "44490",
            },
            "fs-big-blue": {"id": "fs-big-blue", "model_name": "Big Blue", "status": "AVAILABLE"},
        }
    )
    before = {k: dict(v) for k, v in fake_db.collections["inventory"].items()}

    token = main._create_admin_token()
    resp = client.get("/api/inventory?status=&limit=500", headers={"X-Admin-Token": token})
    assert resp.status_code == 200, resp.text
    rows = {r["id"]: r for r in resp.json()["inventory"]}
    assert rows["44490"]["possible_duplicate_ids"] == ["fs-big-blue"]
    assert rows["fs-big-blue"]["duplicate_of"] == "44490"
    assert rows["44490"]["stock_number"] == "44490"

    assert fake_db.collections["inventory"] == before


def test_collapse_keeps_stocked_identity_and_borrows_catalog_photos():
    homes = [
        {
            "id": "43945",
            "model_name": "PRE-OWNED / Heritage 1684-32A",
            "status": "Pre-Owned",
            "inventory_kind": "pre_owned",
            "display_price": "Call for Price",
            "price_value": 0,
            "real_photos": ["lot-1.jpg", "lot-2.jpg"],
            "detail_url": "https://www.texashomeoutlet.com/inventory-detail/43945/",
        },
        {
            "id": "heritage-1684-32a",
            "model_name": "Heritage 1684-32A",
            "status": "Available",
            "inventory_kind": "available_now",
            "display_price": "$1",
            "price_value": 1,
            "real_photos": ["mfr-1.jpg", "mfr-2.jpg", "mfr-3.jpg", "mfr-4.jpg"],
            "floor_plan_url": "https://example.com/1684-floorplan.jpg",
        },
    ]
    visible = collapse_duplicate_homes(homes, strip_annotations=True)
    assert [home["id"] for home in visible] == ["43945"]
    kept = visible[0]
    assert kept["model_name"] == "PRE-OWNED / Heritage 1684-32A"
    assert kept["status"] == "Pre-Owned"
    assert kept["inventory_kind"] == "pre_owned"
    assert kept["display_price"] == "Call for Price"
    assert kept["price_value"] == 0
    assert kept["detail_url"] == "https://www.texashomeoutlet.com/inventory-detail/43945/"
    assert kept["real_photos"][:2] == ["lot-1.jpg", "lot-2.jpg"]
    assert kept["real_photos"][2:] == ["mfr-1.jpg", "mfr-2.jpg", "mfr-3.jpg", "mfr-4.jpg"]
    assert kept["floor_plan_url"] == "https://example.com/1684-floorplan.jpg"
    assert "possible_duplicate_ids" not in kept
    assert "duplicate_of" not in kept


def _stocked_nassau(**overrides):
    home = {
        "id": "1001",
        "model_name": "PRE-OWNED / Nassau",
        "status": "Pre-Owned",
        "inventory_kind": "pre_owned",
        "display_price": "Call for Price",
        "price_value": 0,
        "detail_url": "https://www.texashomeoutlet.com/inventory-detail/1001/",
        "image_url": "lot-1.jpg",
    }
    home.update(overrides)
    return home


def test_equal_length_distinct_donor_photos_are_kept():
    homes = [
        _stocked_nassau(real_photos=["lot-1.jpg", "lot-2.jpg"]),
        {
            "id": "nassau",
            "model_name": "Nassau",
            "status": "Available",
            "real_photos": ["mfr-1.jpg", "mfr-2.jpg"],
        },
    ]
    visible = collapse_duplicate_homes(homes, strip_annotations=True)
    assert [home["id"] for home in visible] == ["1001"]
    kept = visible[0]
    assert kept["real_photos"] == ["lot-1.jpg", "lot-2.jpg", "mfr-1.jpg", "mfr-2.jpg"]
    assert kept["gallery_images"] == ["lot-1.jpg", "lot-2.jpg", "mfr-1.jpg"]
    assert kept["model_name"] == "PRE-OWNED / Nassau"
    assert kept["status"] == "Pre-Owned"
    assert kept["inventory_kind"] == "pre_owned"
    assert kept["display_price"] == "Call for Price"
    assert kept["price_value"] == 0
    assert kept["detail_url"].endswith("/1001/")
    assert kept["image_url"] == "lot-1.jpg"


def test_shorter_distinct_donor_photos_are_kept():
    homes = [
        _stocked_nassau(real_photos=["lot-1.jpg", "lot-2.jpg", "lot-3.jpg"]),
        {
            "id": "nassau",
            "model_name": "Nassau",
            "status": "Available",
            "real_photos": ["lot-2.jpg", "mfr-1.jpg"],
            "photos": ["lot-2.jpg", "mfr-1.jpg"],
        },
    ]
    visible = collapse_duplicate_homes(homes, strip_annotations=True)
    assert [home["id"] for home in visible] == ["1001"]
    kept = visible[0]
    assert kept["real_photos"] == ["lot-1.jpg", "lot-2.jpg", "lot-3.jpg", "mfr-1.jpg"]
    assert kept["photos"] == ["lot-1.jpg", "lot-2.jpg", "lot-3.jpg", "mfr-1.jpg"]
    assert kept["model_name"] == "PRE-OWNED / Nassau"
    assert kept["status"] == "Pre-Owned"
    assert kept["price_value"] == 0
    assert kept["detail_url"].endswith("/1001/")
    assert kept["image_url"] == "lot-1.jpg"


def test_does_not_collapse_preowned_unit_with_orderable_new_floorplan():
    homes = [
        {
            "id": "43945",
            "model_name": "PRE-OWNED / Heritage 1684-32A",
            "status": "Pre-Owned",
            "inventory_kind": "pre_owned",
        },
        {
            "id": "floorplan-1684",
            "model_name": "Heritage / 1684-32A",
            "status": "Orderable",
            "inventory_kind": "orderable_floorplan",
            "is_orderable": True,
        },
    ]
    annotate_possible_duplicates(homes)
    assert all("duplicate_of" not in home for home in homes)
    assert offerings_conflict(homes[0], homes[1])


def test_does_not_collapse_same_model_with_different_sale_prices():
    homes = [
        {
            "id": "lot-a",
            "model_name": "PRE-OWNED / The Nassau",
            "price_value": 45000,
        },
        {
            "id": "the-nassau",
            "model_name": "The Nassau",
            "price_value": 89900,
        },
    ]
    annotate_possible_duplicates(homes)
    assert all("duplicate_of" not in home for home in homes)


def test_two_prefixed_listings_with_different_serials_stay_two_homes():
    """An empty bare overlay must not glue two real serialized units together."""
    homes = [
        {
            "id": "1001",
            "model_name": "PRE-OWNED / Nassau",
            "serial_number": "TXL111",
            "status": "Pre-Owned",
            "inventory_kind": "pre_owned",
            "display_price": "Call for Price",
            "price_value": 0,
            "detail_url": "https://www.texashomeoutlet.com/inventory-detail/1001/",
        },
        {
            "id": "1002",
            "model_name": "PRE-OWNED / Nassau",
            "serial_number": "TXL222",
            "status": "Pre-Owned",
            "inventory_kind": "pre_owned",
            "display_price": "Call for Price",
            "price_value": 0,
            "detail_url": "https://www.texashomeoutlet.com/inventory-detail/1002/",
        },
        {
            "id": "nassau",
            "model_name": "Nassau",
            "status": "Available",
            "display_price": "Call for Price",
            "price_value": 0,
        },
    ]
    visible = collapse_duplicate_homes(homes)
    assert [home["id"] for home in visible] == ["1001", "1002"]
    by_id = {home["id"]: home for home in homes}
    assert by_id["nassau"]["duplicate_of"] in {"1001", "1002"}
    assert by_id["1001"]["model_name"] == "PRE-OWNED / Nassau"
    assert by_id["1001"]["serial_number"] == "TXL111"
    assert by_id["1001"]["status"] == "Pre-Owned"
    assert by_id["1001"]["price_value"] == 0
    assert by_id["1001"]["detail_url"].endswith("/1001/")
    assert by_id["1002"]["model_name"] == "PRE-OWNED / Nassau"
    assert by_id["1002"]["serial_number"] == "TXL222"
    assert by_id["1002"]["status"] == "Pre-Owned"
    assert by_id["1002"]["detail_url"].endswith("/1002/")


def test_two_prefixed_listings_with_different_prices_stay_two_homes():
    """An unpriced bare overlay must not glue two differently priced units together."""
    homes = [
        {
            "id": "1001",
            "model_name": "PRE-OWNED / Nassau",
            "status": "Pre-Owned",
            "inventory_kind": "pre_owned",
            "display_price": "$45,000",
            "price_value": 45000,
            "sale_price": 45000,
        },
        {
            "id": "1002",
            "model_name": "PRE-OWNED / Nassau",
            "status": "Pre-Owned",
            "inventory_kind": "pre_owned",
            "display_price": "$62,000",
            "price_value": 62000,
            "sale_price": 62000,
        },
        {
            "id": "nassau",
            "model_name": "Nassau",
            "status": "Available",
            "display_price": "Call for Price",
            "price_value": 0,
        },
    ]
    visible = collapse_duplicate_homes(homes)
    assert [home["id"] for home in visible] == ["1001", "1002"]
    by_id = {home["id"]: home for home in homes}
    assert by_id["nassau"]["duplicate_of"] in {"1001", "1002"}
    assert by_id["1001"]["price_value"] == 45000
    assert by_id["1001"]["sale_price"] == 45000
    assert by_id["1001"]["display_price"] == "$45,000"
    assert by_id["1001"]["model_name"] == "PRE-OWNED / Nassau"
    assert by_id["1002"]["price_value"] == 62000
    assert by_id["1002"]["sale_price"] == 62000
    assert by_id["1002"]["display_price"] == "$62,000"
    assert by_id["1002"]["status"] == "Pre-Owned"


def test_identity_helpers():
    assert is_stocked_listing({"id": "43945"})
    assert is_stocked_listing({"id": "slug", "legacy_inventory_id": "44490"})
    assert not is_stocked_listing({"id": "heritage-1684-32a"})
    assert is_preowned({"model_name": "PRE-OWNED / Big Blue"})
    assert is_preowned({"inventory_kind": "pre_owned"})
    assert not is_preowned({"model_name": "The Razor", "inventory_kind": "available_now"})


def _public_inventory_client(monkeypatch, homes):
    from tests.test_api_v1 import _isolate_inventory_merge, create_client

    client, main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    monkeypatch.setenv("INVENTORY_SOURCE", "firestore")
    _isolate_inventory_merge(monkeypatch, main)
    payload = {
        "success": True,
        "source": "staff_inventory_with_catalog",
        "homes": [dict(home) for home in homes],
        "total_inventory": len(homes),
    }
    monkeypatch.setattr(main, "get_inventory_for_ads", lambda **_kwargs: dict(payload))
    return client, main


def test_public_inventory_collapses_preowned_and_website_title_twins(monkeypatch):
    client, main = _public_inventory_client(
        monkeypatch,
        [
            {"id": "44490", "model_name": "PRE-OWNED / Big Blue", "status": "AVAILABLE"},
            {
                "id": "big-blue",
                "model_name": "Big Blue",
                "status": "AVAILABLE",
                "real_photos": ["lot.jpg"],
            },
            {"id": "43945", "model_name": "PRE-OWNED / Heritage 1684-32A", "status": "AVAILABLE"},
            {"id": "heritage", "model_name": "Heritage 1684-32A", "status": "AVAILABLE"},
            {"id": "43944", "model_name": "PRE-OWNED / Select S-1256-21A", "status": "AVAILABLE"},
            {"id": "select-1256", "model_name": "Select S-1256-21A", "status": "AVAILABLE"},
            {"id": "43943", "model_name": "PRE-OWNED / Select S-1272-32A", "status": "AVAILABLE"},
            {"id": "select-1272", "model_name": "Select S-1272-32A", "status": "AVAILABLE"},
            {
                "id": "the-razor",
                "model_name": "The Razor",
                "status": "Available",
                "real_photos": ["a.jpg", "b.jpg"],
            },
            {
                "id": "floorplan-227314",
                "model_name": "New Vision / The Razor",
                "status": "Orderable",
                "real_photos": ["a.jpg"],
            },
        ],
    )

    data = client.get("/api/marketing/inventory-context").json()
    ids = [home["id"] for home in data["homes"]]
    assert ids == ["44490", "43945", "43944", "43943", "floorplan-227314"]
    assert data["total_inventory"] == 5
    assert data["homes"][0]["model_name"] == "PRE-OWNED / Big Blue"
    assert all("duplicate_of" not in home for home in data["homes"])
    assert all("possible_duplicate_ids" not in home for home in data["homes"])
    assert main._seo_public_homes()["homes"] == data["homes"]


def test_public_inventory_keeps_distinct_homes_that_only_share_a_model(monkeypatch):
    client, _main = _public_inventory_client(
        monkeypatch,
        [
            {"id": "lot-a", "model_name": "The Nassau"},
            {"id": "lot-b", "model_name": "The Nassau"},
            {"id": "with-serial", "model_name": "The Nassau", "serial_number": "TXL111"},
            {"id": "no-serial", "model_name": "The Nassau"},
        ],
    )

    data = client.get("/api/marketing/inventory-context").json()
    assert [home["id"] for home in data["homes"]] == [
        "lot-a",
        "lot-b",
        "with-serial",
        "no-serial",
    ]
    assert data["total_inventory"] == 4


def test_public_inventory_collapses_same_serial_only(monkeypatch):
    client, _main = _public_inventory_client(
        monkeypatch,
        [
            {"id": "a", "model_name": "The Nassau", "serial_number": "TXL111"},
            {"id": "b", "model_name": "PRE-OWNED / The Nassau", "serial_number": "txl111"},
            {"id": "c", "model_name": "The Nassau", "serial_number": "TXL222"},
            {"id": "d", "model_name": "The Nassau"},
        ],
    )

    data = client.get("/api/marketing/inventory-context").json()
    assert [home["id"] for home in data["homes"]] == ["b", "c", "d"]
    assert data["homes"][0]["model_name"] == "PRE-OWNED / The Nassau"
    assert data["total_inventory"] == 3


# Minimal fields from the 2026-10-07 public feed. Do not store the live JSON.
_LIVE_PUBLIC_PAIRS = [
    {
        "id": "44490",
        "model_name": "PRE-OWNED / Big Blue",
        "inventory_kind": "pre_owned",
        "real_photos": ["a.jpg"] * 4,
    },
    {"id": "big-blue", "model_name": "Big Blue", "inventory_kind": "pre_owned"},
    {
        "id": "43945",
        "model_name": "PRE-OWNED / Heritage 1684-32A",
        "inventory_kind": "pre_owned",
        "real_photos": ["a.jpg"] * 4,
    },
    {
        "id": "heritage-1684-32a",
        "model_name": "Heritage 1684-32A",
        "inventory_kind": "pre_owned",
        "real_photos": ["a.jpg"] * 8,
    },
    {
        "id": "43944",
        "model_name": "PRE-OWNED / Select S-1256-21A",
        "inventory_kind": "pre_owned",
        "real_photos": ["a.jpg"] * 4,
    },
    {
        "id": "select-s-1256-21a",
        "model_name": "Select S-1256-21A",
        "inventory_kind": "pre_owned",
        "real_photos": ["a.jpg"] * 11,
    },
    {
        "id": "43943",
        "model_name": "PRE-OWNED / Select S-1272-32A",
        "inventory_kind": "pre_owned",
        "real_photos": ["a.jpg"] * 4,
    },
    {
        "id": "select-s-1272-32a",
        "model_name": "Select S-1272-32A",
        "inventory_kind": "pre_owned",
        "real_photos": ["a.jpg"] * 9,
    },
    {
        "id": "the-razor",
        "model_name": "The Razor",
        "inventory_kind": "available_now",
        "real_photos": ["a.jpg"],
    },
    {
        "id": "floorplan-227314",
        "model_name": "New Vision / The Razor",
        "inventory_kind": "orderable_floorplan",
        "real_photos": ["a.jpg"],
    },
    {
        "id": "28527",
        "model_name": "PRE-OWNED / Heritage 1672-32C",
        "inventory_kind": "pre_owned",
        "real_photos": ["a.jpg"] * 3,
    },
    {"id": "heritage-1672-32c", "model_name": "Heritage 1672-32C", "inventory_kind": "pre_owned"},
    {
        "id": "floorplan-230325",
        "model_name": "Palmetto / The Claiborne 1676H32006",
        "inventory_kind": "orderable_floorplan",
    },
    {
        "id": "floorplan-232414",
        "model_name": "Premier / The Claiborne 1676H32006",
        "inventory_kind": "orderable_floorplan",
    },
    {
        "id": "floorplan-230137",
        "model_name": "Skyline / The Royal 1660-H-22001",
        "inventory_kind": "orderable_floorplan",
    },
    {
        "id": "floorplan-227807",
        "model_name": "Premier / The Royal 1660-H-22001",
        "inventory_kind": "orderable_floorplan",
    },
]


def test_live_public_feed_pairs_collapse_and_manufacturer_twins_stay():
    """Replay the live public pairs without committing the live JSON."""
    homes = [dict(home) for home in _LIVE_PUBLIC_PAIRS]
    annotate_possible_duplicates(homes)
    by_id = {home["id"]: home for home in homes}

    assert by_id["44490"]["possible_duplicate_ids"] == ["big-blue"]
    assert by_id["big-blue"]["duplicate_of"] == "44490"
    assert by_id["43945"]["possible_duplicate_ids"] == ["heritage-1684-32a"]
    assert by_id["heritage-1684-32a"]["duplicate_of"] == "43945"
    assert by_id["43944"]["possible_duplicate_ids"] == ["select-s-1256-21a"]
    assert by_id["select-s-1256-21a"]["duplicate_of"] == "43944"
    assert by_id["43943"]["possible_duplicate_ids"] == ["select-s-1272-32a"]
    assert by_id["select-s-1272-32a"]["duplicate_of"] == "43943"
    assert by_id["floorplan-227314"]["possible_duplicate_ids"] == ["the-razor"]
    assert by_id["the-razor"]["duplicate_of"] == "floorplan-227314"
    assert by_id["28527"]["possible_duplicate_ids"] == ["heritage-1672-32c"]
    assert by_id["heritage-1672-32c"]["duplicate_of"] == "28527"

    for home_id in (
        "floorplan-230325",
        "floorplan-232414",
        "floorplan-230137",
        "floorplan-227807",
    ):
        assert "duplicate_of" not in by_id[home_id]
        assert "possible_duplicate_ids" not in by_id[home_id]

    visible = collapse_duplicate_homes([dict(home) for home in _LIVE_PUBLIC_PAIRS])
    assert [home["id"] for home in visible] == [
        "44490",
        "43945",
        "43944",
        "43943",
        "floorplan-227314",
        "28527",
        "floorplan-230325",
        "floorplan-232414",
        "floorplan-230137",
        "floorplan-227807",
    ]
    assert visible[0]["model_name"] == "PRE-OWNED / Big Blue"
    assert visible[1]["model_name"] == "PRE-OWNED / Heritage 1684-32A"
    assert visible[2]["model_name"] == "PRE-OWNED / Select S-1256-21A"
    assert visible[3]["model_name"] == "PRE-OWNED / Select S-1272-32A"


def test_public_inventory_live_pairs_on_public_path(monkeypatch):
    client, _main = _public_inventory_client(monkeypatch, _LIVE_PUBLIC_PAIRS)
    data = client.get("/api/marketing/inventory-context").json()
    assert [home["id"] for home in data["homes"]] == [
        "44490",
        "43945",
        "43944",
        "43943",
        "floorplan-227314",
        "28527",
        "floorplan-230325",
        "floorplan-232414",
        "floorplan-230137",
        "floorplan-227807",
    ]
    assert data["total_inventory"] == 10
    assert data["homes"][1]["model_name"] == "PRE-OWNED / Heritage 1684-32A"
    assert data["homes"][1]["inventory_kind"] == "pre_owned"


def test_public_inventory_saved_legacy_sample_before_after_counts(monkeypatch):
    """Pin visitor-visible counts against the saved live snapshot + catalog."""
    from tests.test_api_v1 import create_client
    from tools.catalog_floorplans import merge_orderable_floorplan_catalog

    snapshot = json.loads(
        (REPO_ROOT / "data" / "legacy_site" / "legacy_inventory_context.json").read_text()
    )
    catalog = json.loads(
        (REPO_ROOT / "data" / "legacy_site" / "legacy_floorplan_catalog_context.json").read_text()
    )
    before = merge_orderable_floorplan_catalog(
        snapshot,
        assets={},
        floorplan_context=catalog,
    )

    client, main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    monkeypatch.delenv("INVENTORY_SOURCE", raising=False)
    monkeypatch.setattr(main, "load_legacy_inventory_context", lambda **_kwargs: dict(snapshot))
    monkeypatch.setattr(
        main, "load_legacy_floorplan_catalog_context", lambda **_kwargs: dict(catalog)
    )
    monkeypatch.setattr(main, "PROPERTY_ASSETS", {})
    monkeypatch.setattr(main, "_overlay_staff_photos", lambda homes: None)

    after = client.get("/api/marketing/inventory-context").json()
    assert after["success"] is True
    assert after["total_inventory"] == len(after["homes"])
    # The saved snapshot has the four prefixed website titles but not their
    # bare-name Firestore twins, so this sample does not shrink. The live
    # public feed (Firestore + website homes) is covered by the tests above.
    assert before["total_inventory"] == 279
    assert after["total_inventory"] == 279
    shown_ids = {str(home["id"]) for home in after["homes"]}
    assert {"44490", "43945", "43944", "43943"} <= shown_ids
