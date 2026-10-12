"""Used homes show unit photos only; manufacturer catalog photos stay off them.

Live public feed (2026-10-08): listings 43945, 43944, and 43943 each carry a
real on-lot photo plus manufacturer model-gallery images, and 28527 carries
only that gallery. Those gallery URLs live under
``/manufacturer/{id}/floorplan/{plan_id}/``. Unit photos live under the dealer
inventory path or THO's own storage bucket. New / orderable listings keep the
manufacturer gallery. Floorplan drawings stay in the floorplan fields.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.inventory_dedupe import (  # noqa: E402
    collapse_duplicate_homes,
    suppress_used_home_catalog_photos,
)
from tools.photo_classifier import is_manufacturer_catalog_photo_url  # noqa: E402

CDN = "https://d132mt2yijm03y.cloudfront.net"
REAL = "https://storage.googleapis.com/tho-inventory-assets/inventory/43945/hero.jpg"
DEALER = f"{CDN}/dealer/3522/inventory/43945/1.jpg"
CAT_A = f"{CDN}/manufacturer/1944/floorplan/1391/Heritage%201684-32A-kitchen-1.jpg"
CAT_B = f"{CDN}/manufacturer/1944/floorplan/1391/Heritage%201684-32A-kitchen-2.jpg"
CAT_DONOR = f"{CDN}/manufacturer/1944/floorplan/1391/Heritage%201684-32A-kitchen-9.jpg"
FLOOR = "https://storage.googleapis.com/tho-inventory-assets/inventory/43945/floorplan.jpg"
MFR_FLOOR = f"{CDN}/manufacturer/1944/floorplan/1391/1684-32A-floor-plans.jpg"
EXT_1 = f"{CDN}/manufacturer/1944/floorplan/1383/Heritage%20H-1672-32C-ext-1.jpg"
EXT_2 = f"{CDN}/manufacturer/1944/floorplan/1383/Heritage%20H-1672-32C-ext-2.jpg"
EXT_3 = f"{CDN}/manufacturer/1944/floorplan/1383/Heritage%20H-1672-32C-ext-3.jpg"
HERITAGE_FLOOR = "https://storage.googleapis.com/tho-inventory-assets/inventory/28527/hero.jpg"


def _urls(home: dict) -> list[str]:
    found: list[str] = []
    for field in ("image_url", "hero_image"):
        value = home.get(field)
        if isinstance(value, str) and value:
            found.append(value)
    for field in ("real_photos", "photos", "gallery_images"):
        value = home.get(field)
        if isinstance(value, list):
            found.extend(url for url in value if isinstance(url, str) and url)
    categories = home.get("image_categories")
    if isinstance(categories, dict):
        for values in categories.values():
            if isinstance(values, list):
                found.extend(
                    url for url in values if isinstance(url, str) and url.startswith("http")
                )
    return found


def _mixed_used() -> dict:
    return {
        "id": "43945",
        "model_name": "PRE-OWNED / Heritage 1684-32A",
        "inventory_kind": "pre_owned",
        "status": "Pre-Owned",
        "is_new": False,
        "image_url": CAT_A,
        "hero_image": CAT_A,
        "real_photos": [REAL, CAT_A, CAT_B, MFR_FLOOR],
        "photos": [REAL, CAT_A, CAT_B],
        "gallery_images": [CAT_A, CAT_B, REAL],
        "floorplan_url": FLOOR,
        "floor_plan_url": FLOOR,
        "image_categories": {"kitchen": [CAT_A, CAT_B], "exterior": [REAL]},
    }


def _catalog_only_used() -> dict:
    return {
        "id": "28527",
        "model_name": "PRE-OWNED / Heritage 1672-32C",
        "inventory_kind": "pre_owned",
        "status": "Pre-Owned",
        "is_new": False,
        "image_url": EXT_1,
        "hero_image": EXT_1,
        "real_photos": [EXT_1, EXT_2, EXT_3],
        "photos": [EXT_1, EXT_2, EXT_3],
        "gallery_images": [EXT_1, EXT_2, EXT_3],
        "floorplan_url": HERITAGE_FLOOR,
        "floor_plan_url": HERITAGE_FLOOR,
        "image_categories": {"exterior": [EXT_1, EXT_2, EXT_3]},
    }


def _new_catalog_home() -> dict:
    return {
        "id": "catalog-fiesta",
        "model_name": "The Fiesta",
        "inventory_kind": "orderable_floorplan",
        "status": "Orderable",
        "is_new": True,
        "image_url": CAT_A,
        "real_photos": [CAT_A, CAT_B],
        "gallery_images": [CAT_A, CAT_B],
        "floorplan_url": MFR_FLOOR,
        "floor_plan_url": MFR_FLOOR,
    }


def test_catalog_photo_urls_match_the_manufacturer_gallery_namespace():
    assert is_manufacturer_catalog_photo_url(CAT_A) is True
    assert is_manufacturer_catalog_photo_url(EXT_1) is True
    assert is_manufacturer_catalog_photo_url(DEALER) is False
    assert is_manufacturer_catalog_photo_url(REAL) is False
    assert is_manufacturer_catalog_photo_url(MFR_FLOOR) is False
    assert is_manufacturer_catalog_photo_url(HERITAGE_FLOOR) is False
    assert is_manufacturer_catalog_photo_url("") is False
    assert is_manufacturer_catalog_photo_url(None) is False


def test_used_home_with_mixed_photos_keeps_only_real_ones():
    home = suppress_used_home_catalog_photos(_mixed_used())

    assert home["real_photos"] == [REAL]
    assert home["gallery_images"] == [REAL]
    assert home["image_url"] == REAL
    assert home["photos"] == [REAL]
    assert CAT_A not in _urls(home)
    assert CAT_B not in _urls(home)
    assert home["floorplan_url"] == FLOOR
    assert home["floor_plan_url"] == FLOOR
    assert MFR_FLOOR not in home["real_photos"]
    assert home["media_quality"]["has_real_photo"] is True


def test_used_home_with_only_catalog_photos_is_empty_and_still_listable():
    visible = collapse_duplicate_homes([_catalog_only_used()])

    assert [home["id"] for home in visible] == ["28527"]
    home = visible[0]
    assert home["real_photos"] == []
    assert home["gallery_images"] == []
    assert home["photos"] == []
    assert home["image_url"] == ""
    assert home["hero_image"] == ""
    assert home["floorplan_url"] == HERITAGE_FLOOR
    assert home["floor_plan_url"] == HERITAGE_FLOOR
    assert home["media_quality"]["has_real_photo"] is False
    assert home["media_quality"]["status"] == "floorplan_only"
    assert not any(is_manufacturer_catalog_photo_url(url) for url in _urls(home))


def test_new_catalog_listing_keeps_manufacturer_photos():
    home = suppress_used_home_catalog_photos(_new_catalog_home())

    assert home["real_photos"] == [CAT_A, CAT_B]
    assert home["image_url"] == CAT_A
    assert home["gallery_images"] == [CAT_A, CAT_B]
    assert home["floorplan_url"] == MFR_FLOOR


def test_dedupe_does_not_reintroduce_donor_catalog_photos_onto_a_used_home():
    donor = {
        "id": "heritage-1684-32a",
        "model_name": "Heritage 1684-32A",
        "inventory_kind": "pre_owned",
        "status": "Pre-Owned",
        "real_photos": [CAT_DONOR, CAT_A, DEALER],
        "photos": [CAT_DONOR, DEALER],
        "image_url": CAT_DONOR,
        "floorplan_url": MFR_FLOOR,
    }
    # Not a numeric lot id, so photo borrow runs. Catalog URLs must still not
    # land on the used survivor; a real dealer photo and a missing floorplan may.
    overlay = {
        "id": "lot-overlay",
        "model_name": "PRE-OWNED / The Jackson",
        "inventory_kind": "pre_owned",
        "status": "Pre-Owned",
        "is_new": False,
        "serial_number": "TX-43945",
        "real_photos": [REAL, CAT_B],
        "image_url": "",
        "floorplan_url": "",
    }
    overlay_donor = {
        "id": "the-jackson",
        "model_name": "The Jackson",
        "inventory_kind": "pre_owned",
        "status": "Pre-Owned",
        "real_photos": [CAT_DONOR, DEALER],
        "photos": [CAT_DONOR, DEALER],
        "image_url": CAT_DONOR,
        "floorplan_url": MFR_FLOOR,
    }
    visible = collapse_duplicate_homes(
        [
            _mixed_used(),
            donor,
            overlay,
            overlay_donor,
            _catalog_only_used(),
            _new_catalog_home(),
        ]
    )
    by_id = {home["id"]: home for home in visible}

    assert "heritage-1684-32a" not in by_id
    stocked = by_id["43945"]
    assert stocked["real_photos"] == [REAL]
    assert CAT_DONOR not in _urls(stocked)
    assert DEALER not in stocked["real_photos"]
    assert stocked["floorplan_url"] == FLOOR

    borrowed = by_id["lot-overlay"]
    assert borrowed["real_photos"] == [REAL, DEALER]
    assert CAT_DONOR not in _urls(borrowed)
    assert CAT_B not in _urls(borrowed)
    assert borrowed["floorplan_url"] == MFR_FLOOR

    assert by_id["28527"]["real_photos"] == []
    assert by_id["28527"]["floorplan_url"] == HERITAGE_FLOOR
    assert by_id["catalog-fiesta"]["real_photos"] == [CAT_A, CAT_B]
    assert by_id["catalog-fiesta"]["floorplan_url"] == MFR_FLOOR


def test_public_inventory_serves_used_homes_without_catalog_photos(monkeypatch):
    from tests.test_inventory_dedupe import _public_inventory_client

    client, _main = _public_inventory_client(
        monkeypatch,
        [_mixed_used(), _catalog_only_used(), _new_catalog_home()],
    )

    data = client.get("/api/marketing/inventory-context").json()
    by_id = {home["id"]: home for home in data["homes"]}

    assert set(by_id) == {"43945", "28527", "catalog-fiesta"}
    assert by_id["43945"]["real_photos"] == [REAL]
    assert by_id["43945"]["image_url"] == REAL
    plans = [by_id["43945"].get("floorplan_url"), *(by_id["43945"].get("floorplan_urls") or [])]
    assert FLOOR in plans
    assert MFR_FLOOR not in by_id["43945"]["real_photos"]
    assert not any(is_manufacturer_catalog_photo_url(url) for url in _urls(by_id["43945"]))

    empty = by_id["28527"]
    assert empty["real_photos"] == []
    assert empty["gallery_images"] == []
    assert empty["image_url"] == ""
    assert empty["floorplan_url"] == HERITAGE_FLOOR
    assert empty["media_quality"]["status"] == "floorplan_only"

    assert by_id["catalog-fiesta"]["real_photos"] == [CAT_A, CAT_B]
    assert by_id["catalog-fiesta"]["image_url"] == CAT_A


def test_staff_inventory_strips_used_catalog_photos_and_uses_the_placeholder(monkeypatch):
    from tests.test_api_v1 import create_client

    client, main, db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    token = main._create_admin_token()
    db.collections["inventory"].clear()
    mixed = _mixed_used()
    mixed["status"] = "AVAILABLE"
    catalog_only = _catalog_only_used()
    catalog_only["status"] = "AVAILABLE"
    new_home = _new_catalog_home()
    new_home["status"] = "AVAILABLE"
    db.collections["inventory"]["43945"] = mixed
    db.collections["inventory"]["28527"] = catalog_only
    db.collections["inventory"]["catalog-fiesta"] = new_home
    monkeypatch.setattr(
        main,
        "load_legacy_inventory_context",
        lambda **_kwargs: {"success": True, "homes": []},
    )
    main._INVENTORY_MEDIA_INDEX_CACHE["loaded_at"] = 0.0
    main._INVENTORY_MEDIA_INDEX_CACHE["index"] = {}

    response = client.get("/api/inventory", headers={"X-Admin-Token": token})

    assert response.status_code == 200
    by_id = {item["id"]: item for item in response.json()["inventory"]}
    assert by_id["43945"]["real_photos"] == [REAL]
    assert by_id["43945"]["image_url"] == REAL
    plans = [by_id["43945"].get("floorplan_url"), *(by_id["43945"].get("floorplan_urls") or [])]
    assert FLOOR in plans
    assert MFR_FLOOR not in by_id["43945"]["real_photos"]
    assert not any(is_manufacturer_catalog_photo_url(url) for url in _urls(by_id["43945"]))

    empty = by_id["28527"]
    assert empty["real_photos"] == []
    assert empty["image_url"] == "/tex-icon.svg"
    assert empty["image_placeholder"] is True
    assert empty["floorplan_url"] == HERITAGE_FLOOR
    assert not any(is_manufacturer_catalog_photo_url(url) for url in _urls(empty))

    assert by_id["catalog-fiesta"]["real_photos"] == [CAT_A, CAT_B]
