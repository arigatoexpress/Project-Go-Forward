from tools.inventory_media_enrichment import (
    DetailMedia,
    _detail_media_by_model,
    _prune_timestamp_only_update,
    _select_detail_media,
    build_update,
    categorize_photo_url,
    extract_detail_media,
    normalize_media_url,
    order_photo_urls,
)


def test_normalize_media_url_promotes_thumb_xl_to_full_size():
    url = (
        "https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/43372/home-kit-1_thumb_xl.jpg"
    )

    assert normalize_media_url(url).endswith("/home-kit-1.jpg")


def test_order_photo_urls_prefers_exterior_then_kitchen():
    urls = [
        "https://cdn.example.com/home-bath-1.jpg",
        "https://cdn.example.com/home-kit-1.jpg",
        "https://cdn.example.com/home-ext-1.jpg",
        "https://cdn.example.com/home-bed-1.jpg",
    ]

    ordered = order_photo_urls(urls)

    assert ordered[0].endswith("home-ext-1.jpg")
    assert categorize_photo_url(ordered[1]) == "kitchen"


def test_extract_detail_media_prefers_dealer_photos_and_matterport():
    html = """
    <a href="https://my.matterport.com/show/?play=1&m=mTvc6YoSRTx">tour</a>
    <a href="https://d132mt2yijm03y.cloudfront.net/manufacturer/3373/floorplan/235424/Creole%203256H32447.jpg">floor</a>
    <a href="https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/43372/Creole-kit-1.jpg">kitchen</a>
    <img src="https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/43372/Creole-ext-1_thumb_xl.jpg">
    """

    media = extract_detail_media(html, "43372", "https://example.com/detail")

    assert media.matterport_id == "mTvc6YoSRTx"
    assert media.floorplan_url.endswith("Creole%203256H32447.jpg")
    assert media.photos[0].endswith("Creole-ext-1.jpg")
    assert media.photos[1].endswith("Creole-kit-1.jpg")
    assert media.image_categories["exterior"] == ["Creole-ext-1.jpg"]


def test_extract_detail_media_filters_unrelated_manufacturer_photos():
    html = """
    <a href="https://d132mt2yijm03y.cloudfront.net/manufacturer/1944/floorplan/1383/1672-32C-floor-plans.jpg">floor</a>
    <img src="https://d132mt2yijm03y.cloudfront.net/manufacturer/3290/floorplan/198104/de_Vaca_S64F.jpg">
    <img src="https://d132mt2yijm03y.cloudfront.net/manufacturer/1944/floorplan/1383/Heritage-1672-kitchen-1.jpg">
    """

    media = extract_detail_media(html, "28527", "https://example.com/detail")

    assert media.floorplan_url.endswith("1672-32C-floor-plans.jpg")
    assert media.photos == [
        "https://d132mt2yijm03y.cloudfront.net/manufacturer/1944/floorplan/1383/Heritage-1672-kitchen-1.jpg"
    ]


def test_select_detail_media_matches_unique_legacy_model_name():
    detail = DetailMedia(
        listing_id="44490",
        detail_url="https://www.texashomeoutlet.com/inventory-detail/44490/",
        photos=["https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/44490/1.jpg"],
    )
    raw_homes = [{"id": "44490", "model_name": "PRE-OWNED / Big Blue"}]
    detail_by_model = _detail_media_by_model(raw_homes, {"44490": detail})

    selected = _select_detail_media(
        "big-blue",
        {"model_name": "Big Blue"},
        {"44490": detail},
        detail_by_model,
    )

    assert selected == detail


def test_prune_timestamp_only_update_skips_broad_firestore_churn():
    current = {"photos": ["https://cdn.example.com/home-1.jpg"]}
    update = {
        "photos": ["https://cdn.example.com/home-1.jpg"],
        "last_media_synced": "2026-05-10T00:00:00+00:00",
    }

    pruned, changed = _prune_timestamp_only_update(current, update)

    assert pruned == {"photos": ["https://cdn.example.com/home-1.jpg"]}
    assert changed == []


def test_build_update_normalizes_raw_firestore_media_fields():
    current = {
        "model_name": "Creole",
        "photos": ["https://cdn.example.com/home-kit-1.jpg"],
        "matterport_id": "abc123",
    }

    update = build_update("43372", current, None)

    assert update["real_photos"] == ["https://cdn.example.com/home-kit-1.jpg"]
    assert update["gallery_images"] == ["https://cdn.example.com/home-kit-1.jpg"]
    assert update["media_quality"]["status"] == "limited_photos"
    assert update["matterport_url"] == "https://my.matterport.com/show/?m=abc123&play=1"
    assert "last_media_synced" in update


def test_build_update_clears_floorplan_only_photo_fields():
    floorplan = "https://cdn.example.com/floor-plans.jpg"
    current = {
        "model_name": "Floorplan Only",
        "image_url": floorplan,
        "hero_image": floorplan,
        "photos": [floorplan],
        "real_photos": [floorplan],
        "gallery_images": [floorplan],
    }

    update = build_update("44490", current, None)

    assert update["image_url"] == ""
    assert update["hero_image"] == ""
    assert update["photos"] == []
    assert update["real_photos"] == []
    assert update["gallery_images"] == []
    assert update["floorplan_url"] == floorplan
    assert update["floor_plan_url"] == floorplan
    assert update["media_quality"]["status"] == "floorplan_only"
