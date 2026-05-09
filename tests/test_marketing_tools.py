import base64
import sys
import types
from io import BytesIO

from tools import marketing_tools


def test_get_inventory_for_ads_preserves_live_firestore_media(monkeypatch):
    monkeypatch.setattr(
        marketing_tools,
        "_load_inventory_for_marketing",
        lambda: [
            {
                "id": "28102",
                "model_name": "NEW YEAR CLEARANCE SALE / TRU Single Section Delight",
                "manufacturer": "TRU Homes",
                "classification": "Single Wide",
                "status": "Available",
                "pricing": {"display_price": "Call for Price", "price_value": 0},
                "specs": {"beds": 2, "baths": 2, "sq_ft": 820, "dimensions": "14x60"},
                "features": ["Lot Model"],
                "image_url": "https://example.com/live-card.jpg",
                "gallery_images": ["https://example.com/live-card.jpg"],
                "floor_plan_url": "https://example.com/live-floorplan.jpg",
                "matterport_id": "SvVRKXdXUQq",
            }
        ],
    )

    asset_scraper = types.ModuleType("tools.asset_scraper")
    asset_scraper.get_assets_for_home = lambda _name: {
        "images": ["https://example.com/stale-photo.jpg"],
        "floor_plan": "https://example.com/stale-floorplan.jpg",
        "matterport_id": "staleTour",
    }
    asset_scraper.get_matterport_url = (
        lambda tour_id: f"https://my.matterport.com/show/?m={tour_id}&play=1"
    )
    monkeypatch.setitem(sys.modules, "tools.asset_scraper", asset_scraper)

    result = marketing_tools.get_inventory_for_ads(limit=10)

    assert result["success"] is True
    assert result["total_inventory"] == 1
    home = result["homes"][0]
    assert home["id"] == "28102"
    assert home["real_photos"] == ["https://example.com/live-card.jpg"]
    assert home["floor_plan_url"] == "https://example.com/live-floorplan.jpg"
    assert home["matterport_id"] == "SvVRKXdXUQq"
    assert home["matterport_url"].endswith("SvVRKXdXUQq&play=1")


def test_get_inventory_for_ads_demotes_floorplan_hero_to_floorplan_url(monkeypatch):
    """2026-04-30 prod-audit shape: image_url is a /floorplan/ URL, real_photos[0]
    is the same floorplan, floorplan_url is empty. Classifier must promote the
    first exterior to image_url and move the floorplan into floorplan_url.
    """
    # Actual floorplan diagram (filename-based detection)
    floorplan = (
        "https://d132mt2yijm03y.cloudfront.net/manufacturer/3327/floorplan/"
        "224354/floor-plans.jpg"
    )
    exterior = "https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/30643/" "photo_card.jpg"

    monkeypatch.setattr(
        marketing_tools,
        "_load_inventory_for_marketing",
        lambda: [
            {
                "id": "30643",
                "model_name": "Buggy Listing",
                "manufacturer": "Test",
                "classification": "Single Wide",
                "status": "Available",
                "pricing": {"display_price": "Call for Price", "price_value": 0},
                "specs": {"beds": 3, "baths": 2, "sq_ft": 1000, "dimensions": "16x66"},
                "features": [],
                "image_url": floorplan,
                "gallery_images": [floorplan, exterior],
                "real_photos": [floorplan, exterior],
                "floor_plan_url": "",
            }
        ],
    )

    asset_scraper = types.ModuleType("tools.asset_scraper")
    asset_scraper.get_assets_for_home = lambda _name: None
    asset_scraper.get_matterport_url = lambda tour_id: ""
    monkeypatch.setitem(sys.modules, "tools.asset_scraper", asset_scraper)

    result = marketing_tools.get_inventory_for_ads(limit=10)

    home = result["homes"][0]
    # Hero is now the exterior shot.
    assert home["image_url"] == exterior
    # Real photos: exteriors first, then floorplans.
    assert home["real_photos"][0] == exterior
    # Dedicated floorplan slot is now populated.
    assert home["floor_plan_url"] == floorplan


def test_content_performance_uses_honest_local_readiness(monkeypatch, tmp_path):
    monkeypatch.setattr(
        marketing_tools,
        "_load_inventory_for_marketing",
        lambda: [
            {
                "model_name": "Photo Ready",
                "status": "Available",
                "image_url": "https://example.com/photo.jpg",
            },
            {
                "model_name": "Budget Deal",
                "status": "Pre-Owned",
                "gallery_images": ["https://example.com/gallery.jpg"],
            },
        ],
    )
    monkeypatch.setattr(marketing_tools.tiktok_handler, "is_configured", lambda: False)
    monkeypatch.setattr(marketing_tools, "GENERATED_ADS_DIR", str(tmp_path / "ads"))
    (tmp_path / "ads").mkdir()
    (tmp_path / "ads" / "creative.png").write_bytes(b"fake image")
    (tmp_path / "data" / "generated_videos").mkdir(parents=True)
    (tmp_path / "data" / "generated_videos" / "tour.mp4").write_bytes(b"fake video")
    real_dirname = marketing_tools.os.path.dirname

    def fake_dirname(path):
        if str(path).endswith("marketing_tools.py"):
            return str(tmp_path / "tools")
        if str(path) == str(tmp_path / "tools"):
            return str(tmp_path)
        return real_dirname(path)

    monkeypatch.setattr(marketing_tools.os.path, "dirname", fake_dirname)

    result = marketing_tools.analyze_content_performance()

    assert result["source"] == "local_readiness"
    assert result["social_analytics_connected"] is False
    assert result["summary"]["total_views"] == 0
    assert result["summary"]["generated_images"] == 1
    assert result["summary"]["generated_videos"] == 1
    assert result["summary"]["inventory_count"] == 2
    assert result["summary"]["photo_ready_homes"] == 2
    assert "15.2K" not in str(result)
    assert result["top_performing_content"][0]["views_label"] == "1 generated videos"
    assert any("pre-owned" in rec.lower() for rec in result["recommendations"])


def test_generate_ad_flyer_creates_downloadable_social_creative(monkeypatch, tmp_path):
    from PIL import Image

    monkeypatch.setattr(marketing_tools, "GENERATED_ADS_DIR", str(tmp_path))
    source = Image.new("RGB", (640, 480), "#8aa0a8")
    buf = BytesIO()
    source.save(buf, format="PNG")
    image_base64 = base64.b64encode(buf.getvalue()).decode("ascii")

    result = marketing_tools.generate_ad_flyer(
        home_name="The Test Home",
        home_price="$89,900",
        home_specs={"beds": 3, "baths": 2, "sq_ft": 1280, "dimensions": "28x56"},
        headline="3 Bed Home Ready to Tour",
        body="Real home photos, clear specs, and a call to action in one finished flyer.",
        cta="Call THO to tour today",
        platform="instagram_post",
        image_base64=image_base64,
    )

    assert result["success"] is True
    assert result["creative_type"] == "social_flyer"
    assert result["source"] == "generated_image"
    assert result["download_url"].startswith("/api/marketing/images/")
    assert (tmp_path / result["filename"]).is_file()
    assert len(base64.b64decode(result["image_base64"])) > 1000
