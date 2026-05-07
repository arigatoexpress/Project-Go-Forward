from tools.inventory_media_enrichment import (
    build_update,
    categorize_photo_url,
    extract_detail_media,
    normalize_media_url,
    order_photo_urls,
)


def test_normalize_media_url_promotes_thumb_xl_to_full_size():
    url = "https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/43372/home-kit-1_thumb_xl.jpg"

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


def test_build_update_normalizes_raw_firestore_media_fields():
    current = {
        "model_name": "Creole",
        "photos": ["https://cdn.example.com/home-kit-1.jpg"],
        "matterport_id": "abc123",
    }

    update = build_update("43372", current, None)

    assert update["real_photos"] == ["https://cdn.example.com/home-kit-1.jpg"]
    assert update["gallery_images"] == ["https://cdn.example.com/home-kit-1.jpg"]
    assert update["matterport_url"] == "https://my.matterport.com/show/?m=abc123&play=1"
    assert "last_media_synced" in update
