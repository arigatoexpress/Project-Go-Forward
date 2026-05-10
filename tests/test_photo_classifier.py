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
has_real_photo = photo_classifier.has_real_photo
is_floorplan_url = photo_classifier.is_floorplan_url
reorder_for_listing = photo_classifier.reorder_for_listing
split_photos = photo_classifier.split_photos


# ─── Real production sample URLs ───
# NOTE: The 2026-04-30 audit incorrectly classified URLs by path token
# (``/floorplan/``). The manufacturer CDN puts every asset for a
# floorplan-MODEL under ``/manufacturer/{id}/floorplan/{plan_id}/`` —
# that's a NAMESPACE, not a content marker. Detection is now
# filename-based (2026-05-02 fix).
MFR_NAMESPACE_PHOTO = (
    "https://d132mt2yijm03y.cloudfront.net/manufacturer/3327/floorplan/224354/" "S-1672-32B-1.jpg"
)  # exterior — filename has no floorplan token
MFR_FLOORPLAN_DIAGRAM = (
    "https://d132mt2yijm03y.cloudfront.net/manufacturer/3335/floorplan/225053/"
    "the-big-steve-floor-plans.jpg"
)  # actual floorplan — filename contains "floor-plans"
MFR_FLOORPLAN_PDF = (
    "https://d132mt2yijm03y.cloudfront.net/manufacturer/3335/floorplan/225053/" "plans.pdf"
)  # PDF in this domain = floorplan
EXTERIOR_URL = "https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/28102/photo_card.jpg"
EXTERIOR_URL_2 = (
    "https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/30643/exterior-front.jpg"
)
INTERIOR_URL = "https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/30643/kitchen.jpg"


# ─── is_floorplan_url ───


def test_is_floorplan_url_detects_floor_plans_filename_token():
    assert is_floorplan_url(MFR_FLOORPLAN_DIAGRAM) is True


def test_is_floorplan_url_detects_pdf_extension():
    assert is_floorplan_url(MFR_FLOORPLAN_PDF) is True


def test_is_floorplan_url_rejects_mfr_namespace_indexed_photo():
    """Manufacturer CDN indexed photos under /floorplan/{plan}/ are
    exterior shots, NOT floorplan diagrams. The 2026-04-30 audit got
    this backwards and 35/44 homes lost their hero image."""
    assert is_floorplan_url(MFR_NAMESPACE_PHOTO) is False


def test_is_floorplan_url_rejects_dealer_inventory_path():
    assert is_floorplan_url(EXTERIOR_URL) is False


def test_is_floorplan_url_rejects_dealer_interior_path():
    assert is_floorplan_url(INTERIOR_URL) is False


def test_is_floorplan_url_handles_empty_string():
    assert is_floorplan_url("") is False


def test_is_floorplan_url_handles_none():
    assert is_floorplan_url(None) is False


def test_is_floorplan_url_is_case_insensitive():
    upper = MFR_FLOORPLAN_DIAGRAM.replace("floor-plans", "Floor-Plans")
    assert is_floorplan_url(upper) is True


def test_is_floorplan_url_detects_encoded_spaces():
    encoded = MFR_FLOORPLAN_DIAGRAM.replace("floor-plans", "floor%20plans")
    assert is_floorplan_url(encoded) is True


def test_is_floorplan_url_rejects_non_string():
    # Integers, lists, etc. should be treated as not-a-floorplan, not raise.
    assert is_floorplan_url(123) is False  # type: ignore[arg-type]


# ─── split_photos ───


def test_split_photos_separates_groups_and_preserves_order():
    photos = [MFR_FLOORPLAN_DIAGRAM, EXTERIOR_URL, MFR_FLOORPLAN_PDF, INTERIOR_URL]
    exteriors, floorplans = split_photos(photos)
    assert exteriors == [EXTERIOR_URL, INTERIOR_URL]
    assert floorplans == [MFR_FLOORPLAN_DIAGRAM, MFR_FLOORPLAN_PDF]


def test_split_photos_treats_mfr_namespace_photo_as_exterior():
    """Regression: photos under /manufacturer/{id}/floorplan/{plan}/{n}.jpg
    are exteriors, not floorplans."""
    photos = [MFR_NAMESPACE_PHOTO, MFR_FLOORPLAN_DIAGRAM]
    exteriors, floorplans = split_photos(photos)
    assert exteriors == [MFR_NAMESPACE_PHOTO]
    assert floorplans == [MFR_FLOORPLAN_DIAGRAM]


def test_split_photos_drops_empty_and_none():
    photos = [None, "", MFR_FLOORPLAN_DIAGRAM, EXTERIOR_URL]
    exteriors, floorplans = split_photos(photos)
    assert exteriors == [EXTERIOR_URL]
    assert floorplans == [MFR_FLOORPLAN_DIAGRAM]


def test_split_photos_handles_empty_input():
    exteriors, floorplans = split_photos([])
    assert exteriors == []
    assert floorplans == []


# ─── reorder_for_listing ───


def test_reorder_mix_puts_exteriors_first_floorplans_last():
    photos = [MFR_FLOORPLAN_DIAGRAM, EXTERIOR_URL, INTERIOR_URL]
    result = reorder_for_listing(photos, current_image_url=MFR_FLOORPLAN_DIAGRAM)

    assert result["image_url"] == EXTERIOR_URL
    assert result["real_photos"] == [EXTERIOR_URL, INTERIOR_URL]
    assert result["floorplan_url"] == MFR_FLOORPLAN_DIAGRAM
    assert result["floorplan_urls"] == [MFR_FLOORPLAN_DIAGRAM]
    assert result["gallery_images"] == [EXTERIOR_URL, INTERIOR_URL]
    assert result["media_quality"]["status"] == "limited_photos"


def test_reorder_mfr_namespace_photos_become_hero():
    """Regression for the 35/44 homes that lost their hero image: a list
    of photos that all live under ``/manufacturer/{id}/floorplan/{plan}/``
    should still produce a hero ``image_url`` (the first indexed photo),
    NOT an empty image_url with everything dumped into floorplans."""
    photos = [MFR_NAMESPACE_PHOTO, MFR_FLOORPLAN_DIAGRAM]
    result = reorder_for_listing(photos, current_image_url=MFR_NAMESPACE_PHOTO)

    assert result["image_url"] == MFR_NAMESPACE_PHOTO
    assert result["floorplan_url"] == MFR_FLOORPLAN_DIAGRAM
    assert result["real_photos"] == [MFR_NAMESPACE_PHOTO]


def test_reorder_all_floorplans_returns_none_image_url():
    """When EVERY photo is an actual floorplan diagram, image_url is None."""
    photos = [MFR_FLOORPLAN_DIAGRAM, MFR_FLOORPLAN_PDF]
    result = reorder_for_listing(photos, current_image_url=MFR_FLOORPLAN_DIAGRAM)

    assert result["image_url"] is None
    assert result["floorplan_url"] == MFR_FLOORPLAN_DIAGRAM
    assert result["floorplan_urls"] == [MFR_FLOORPLAN_DIAGRAM, MFR_FLOORPLAN_PDF]
    assert result["real_photos"] == []
    assert result["gallery_images"] == []
    assert result["media_quality"] == {
        "status": "floorplan_only",
        "has_real_photo": False,
        "photo_count": 0,
        "floorplan_count": 2,
        "issues": ["floorplan_only"],
    }


def test_reorder_explicit_floorplan_hint_handles_ambiguous_filename():
    """A URL in a floorplan field remains a floorplan even without a token."""
    ambiguous = "https://d132mt2yijm03y.cloudfront.net/manufacturer/3335/floorplan/225061/Aspen.jpg"
    result = reorder_for_listing(
        [ambiguous],
        current_image_url=ambiguous,
        floorplan_urls=[ambiguous],
    )

    assert result["image_url"] is None
    assert result["real_photos"] == []
    assert result["floorplan_url"] == ambiguous
    assert result["media_quality"]["status"] == "floorplan_only"


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
        "floorplan_urls": [],
        "media_quality": {
            "status": "missing_photos",
            "has_real_photo": False,
            "photo_count": 0,
            "floorplan_count": 0,
            "issues": ["missing_real_photos"],
        },
    }


def test_reorder_gallery_images_capped_at_three():
    photos = [
        EXTERIOR_URL,
        EXTERIOR_URL_2,
        INTERIOR_URL,
        MFR_FLOORPLAN_DIAGRAM,
        MFR_FLOORPLAN_PDF,
    ]
    result = reorder_for_listing(photos, current_image_url=None)
    assert len(result["gallery_images"]) == 3


# ─── apply_classifier_to_home ───


def test_apply_classifier_fixes_floorplan_as_hero_bug():
    """When image_url is an actual floorplan diagram and a real
    exterior shot is available, swap them."""
    home = {
        "id": "test",
        "image_url": MFR_FLOORPLAN_DIAGRAM,
        "real_photos": [MFR_FLOORPLAN_DIAGRAM, EXTERIOR_URL, INTERIOR_URL],
        "gallery_images": [MFR_FLOORPLAN_DIAGRAM, EXTERIOR_URL, INTERIOR_URL],
        "floorplan_url": "",
    }
    result = apply_classifier_to_home(home)

    assert result is home  # mutates in-place
    assert result["image_url"] == EXTERIOR_URL
    assert result["real_photos"][0] == EXTERIOR_URL
    assert MFR_FLOORPLAN_DIAGRAM not in result["real_photos"]
    assert MFR_FLOORPLAN_DIAGRAM not in result["gallery_images"]
    assert result["floorplan_url"] == MFR_FLOORPLAN_DIAGRAM
    assert result["floor_plan_url"] == MFR_FLOORPLAN_DIAGRAM  # legacy spelling kept in sync


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
        "image_url": MFR_FLOORPLAN_DIAGRAM,
        "gallery_images": [EXTERIOR_URL, MFR_FLOORPLAN_DIAGRAM],
    }
    apply_classifier_to_home(home)
    assert home["image_url"] == EXTERIOR_URL
    assert home["floorplan_url"] == MFR_FLOORPLAN_DIAGRAM


def test_apply_classifier_empty_home_no_crash():
    home: dict = {}
    apply_classifier_to_home(home)
    assert home["image_url"] == ""
    assert home["real_photos"] == []
    assert home["gallery_images"] == []
    assert home["floorplan_url"] == ""
    assert home["media_quality"]["status"] == "missing_photos"


def test_apply_classifier_non_dict_passthrough():
    # Defensive: callers may map() over a None or a list; don't crash.
    assert apply_classifier_to_home(None) is None  # type: ignore[arg-type]


def test_apply_classifier_filters_floorplan_only_categories():
    home = {
        "image_url": MFR_FLOORPLAN_DIAGRAM,
        "real_photos": [MFR_FLOORPLAN_DIAGRAM],
        "image_categories": {"interior": ["the-big-steve-floor-plans.jpg"]},
    }
    apply_classifier_to_home(home)

    assert home["image_url"] == ""
    assert home["real_photos"] == []
    assert home["gallery_images"] == []
    assert home["image_categories"] == {}
    assert home["media_quality"]["status"] == "floorplan_only"
    assert has_real_photo(home) is False


def test_module_exports_filename_token_constants():
    assert "floorplan" in photo_classifier.FLOORPLAN_FILENAME_TOKENS
    assert "floor-plan" in photo_classifier.FLOORPLAN_FILENAME_TOKENS
    assert ".pdf" in photo_classifier.FLOORPLAN_FILE_EXTS
