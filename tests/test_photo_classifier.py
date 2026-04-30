"""Unit tests for tools.photo_classifier.

Sample URLs are pulled directly from the 2026-04-30 production audit so
the suite locks in real-world detection behavior.
"""

import importlib.util
import os
import sys

# Import tools.photo_classifier directly without triggering tools/__init__.py,
# which pulls in heavy optional deps (pypdf, google-adk) we don't need here.
_PC_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tools",
    "photo_classifier.py",
)
_spec = importlib.util.spec_from_file_location("photo_classifier", _PC_PATH)
photo_classifier = importlib.util.module_from_spec(_spec)
sys.modules["photo_classifier"] = photo_classifier
_spec.loader.exec_module(photo_classifier)

apply_classifier_to_home = photo_classifier.apply_classifier_to_home
is_floorplan_url = photo_classifier.is_floorplan_url
reorder_for_listing = photo_classifier.reorder_for_listing
split_photos = photo_classifier.split_photos


# ─── Real production sample URLs (2026-04-30 audit) ───
FLOORPLAN_URL = (
    "https://d132mt2yijm03y.cloudfront.net/manufacturer/3327/floorplan/224354/" "S-1672-32B-1.jpg"
)
FLOORPLAN_URL_2 = (
    "https://d132mt2yijm03y.cloudfront.net/manufacturer/3335/floorplan/225053/"
    "the-big-steve-floor-plans.jpg"
)
EXTERIOR_URL = "https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/28102/" "photo_card.jpg"
EXTERIOR_URL_2 = (
    "https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/30643/" "exterior-front.jpg"
)
INTERIOR_URL = "https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/30643/" "kitchen.jpg"


# ─── is_floorplan_url ───


def test_is_floorplan_url_detects_real_floorplan_path():
    assert is_floorplan_url(FLOORPLAN_URL) is True


def test_is_floorplan_url_detects_second_real_floorplan():
    assert is_floorplan_url(FLOORPLAN_URL_2) is True


def test_is_floorplan_url_rejects_dealer_inventory_path():
    assert is_floorplan_url(EXTERIOR_URL) is False


def test_is_floorplan_url_rejects_dealer_interior_path():
    assert is_floorplan_url(INTERIOR_URL) is False


def test_is_floorplan_url_handles_empty_string():
    assert is_floorplan_url("") is False


def test_is_floorplan_url_handles_none():
    assert is_floorplan_url(None) is False


def test_is_floorplan_url_is_case_insensitive():
    upper = FLOORPLAN_URL.replace("/floorplan/", "/FloorPlan/")
    assert is_floorplan_url(upper) is True


def test_is_floorplan_url_rejects_non_string():
    # Integers, lists, etc. should be treated as not-a-floorplan, not raise.
    assert is_floorplan_url(123) is False  # type: ignore[arg-type]


# ─── split_photos ───


def test_split_photos_separates_groups_and_preserves_order():
    photos = [FLOORPLAN_URL, EXTERIOR_URL, FLOORPLAN_URL_2, INTERIOR_URL]
    exteriors, floorplans = split_photos(photos)
    assert exteriors == [EXTERIOR_URL, INTERIOR_URL]
    assert floorplans == [FLOORPLAN_URL, FLOORPLAN_URL_2]


def test_split_photos_drops_empty_and_none():
    photos = [None, "", FLOORPLAN_URL, EXTERIOR_URL]
    exteriors, floorplans = split_photos(photos)
    assert exteriors == [EXTERIOR_URL]
    assert floorplans == [FLOORPLAN_URL]


def test_split_photos_handles_empty_input():
    exteriors, floorplans = split_photos([])
    assert exteriors == []
    assert floorplans == []


# ─── reorder_for_listing ───


def test_reorder_mix_puts_exteriors_first_floorplans_last():
    photos = [FLOORPLAN_URL, EXTERIOR_URL, INTERIOR_URL]
    result = reorder_for_listing(photos, current_image_url=FLOORPLAN_URL)

    assert result["image_url"] == EXTERIOR_URL
    assert result["real_photos"] == [EXTERIOR_URL, INTERIOR_URL, FLOORPLAN_URL]
    assert result["floorplan_url"] == FLOORPLAN_URL
    assert result["gallery_images"] == [EXTERIOR_URL, INTERIOR_URL, FLOORPLAN_URL]


def test_reorder_all_floorplans_returns_none_image_url():
    """The audit found 35/44 homes in this exact state."""
    photos = [FLOORPLAN_URL, FLOORPLAN_URL_2]
    result = reorder_for_listing(photos, current_image_url=FLOORPLAN_URL)

    assert result["image_url"] is None
    assert result["floorplan_url"] == FLOORPLAN_URL
    assert result["real_photos"] == [FLOORPLAN_URL, FLOORPLAN_URL_2]
    assert result["gallery_images"] == [FLOORPLAN_URL, FLOORPLAN_URL_2]


def test_reorder_all_exteriors_leaves_floorplan_url_empty():
    photos = [EXTERIOR_URL, INTERIOR_URL, EXTERIOR_URL_2]
    result = reorder_for_listing(photos, current_image_url=EXTERIOR_URL)

    assert result["image_url"] == EXTERIOR_URL
    assert result["floorplan_url"] == ""
    assert result["real_photos"] == [EXTERIOR_URL, INTERIOR_URL, EXTERIOR_URL_2]


def test_reorder_dedupes_when_image_url_already_in_real_photos():
    photos = [EXTERIOR_URL, EXTERIOR_URL, INTERIOR_URL]
    result = reorder_for_listing(photos, current_image_url=EXTERIOR_URL)

    assert result["real_photos"] == [EXTERIOR_URL, INTERIOR_URL]


def test_reorder_handles_empty_photos_list():
    result = reorder_for_listing([], current_image_url=None)
    assert result == {
        "image_url": None,
        "real_photos": [],
        "gallery_images": [],
        "floorplan_url": "",
    }


def test_reorder_gallery_images_capped_at_three():
    photos = [
        EXTERIOR_URL,
        EXTERIOR_URL_2,
        INTERIOR_URL,
        FLOORPLAN_URL,
        FLOORPLAN_URL_2,
    ]
    result = reorder_for_listing(photos, current_image_url=None)
    assert len(result["gallery_images"]) == 3


# ─── apply_classifier_to_home ───


def test_apply_classifier_fixes_audited_home_shape():
    """Models the exact bug from the 2026-04-30 audit:
    image_url is a floorplan, real_photos[0] is a floorplan, floorplan_url empty.
    """
    home = {
        "id": "test",
        "image_url": FLOORPLAN_URL,
        "real_photos": [FLOORPLAN_URL, EXTERIOR_URL, INTERIOR_URL],
        "gallery_images": [FLOORPLAN_URL, EXTERIOR_URL, INTERIOR_URL],
        "floorplan_url": "",
    }
    result = apply_classifier_to_home(home)

    assert result is home  # mutates in-place
    assert "/floorplan/" not in result["image_url"]
    assert result["image_url"] == EXTERIOR_URL
    assert result["real_photos"][0] == EXTERIOR_URL
    assert result["floorplan_url"] == FLOORPLAN_URL
    assert result["floor_plan_url"] == FLOORPLAN_URL  # legacy spelling kept in sync


def test_apply_classifier_preserves_existing_floorplan_url_when_no_floorplan_in_photos():
    home = {
        "image_url": EXTERIOR_URL,
        "real_photos": [EXTERIOR_URL, INTERIOR_URL],
        "floorplan_url": "https://example.com/already-set.jpg",
    }
    apply_classifier_to_home(home)
    assert home["floorplan_url"] == "https://example.com/already-set.jpg"


def test_apply_classifier_handles_missing_real_photos_falls_back_to_gallery():
    home = {
        "image_url": FLOORPLAN_URL,
        "gallery_images": [EXTERIOR_URL, FLOORPLAN_URL],
    }
    apply_classifier_to_home(home)
    assert home["image_url"] == EXTERIOR_URL
    assert home["floorplan_url"] == FLOORPLAN_URL


def test_apply_classifier_empty_home_no_crash():
    home: dict = {}
    apply_classifier_to_home(home)
    assert home["image_url"] == ""
    assert home["real_photos"] == []
    assert home["gallery_images"] == []
    assert home["floorplan_url"] == ""


def test_apply_classifier_non_dict_passthrough():
    # Defensive: callers may map() over a None or a list; don't crash.
    assert apply_classifier_to_home(None) is None  # type: ignore[arg-type]


def test_module_exports_path_token_constant():
    assert photo_classifier.FLOORPLAN_URL_PATH_TOKEN == "/floorplan/"
