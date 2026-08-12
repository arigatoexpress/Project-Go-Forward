import base64
import sys
import types
from io import BytesIO

from tools import marketing_tools, social_publishers


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
    # Real photos are listing photos only; floorplans stay dedicated.
    assert home["real_photos"] == [exterior]
    assert floorplan not in home["gallery_images"]
    # Dedicated floorplan slot is now populated.
    assert home["floor_plan_url"] == floorplan
    assert home["media_quality"]["status"] == "limited_photos"


def test_get_inventory_for_ads_enriches_floorplan_only_listing(monkeypatch):
    floorplan = "https://cdn.example.com/floor-plans.jpg"
    replacement_photo = "https://cdn.example.com/replacement-exterior.jpg"

    monkeypatch.setattr(
        marketing_tools,
        "_load_inventory_for_marketing",
        lambda: [
            {
                "id": "floorplan-only",
                "model_name": "Floorplan Only",
                "manufacturer": "Test",
                "classification": "Single Wide",
                "status": "Available",
                "pricing": {"display_price": "Call for Price", "price_value": 0},
                "specs": {"beds": 3, "baths": 2, "sq_ft": 1000, "dimensions": "16x66"},
                "features": [],
                "image_url": floorplan,
                "gallery_images": [floorplan],
                "real_photos": [floorplan],
                "floor_plan_url": "",
            }
        ],
    )

    asset_scraper = types.ModuleType("tools.asset_scraper")
    asset_scraper.get_assets_for_home = lambda _name: {
        "images": [replacement_photo],
        "floor_plan": floorplan,
    }
    asset_scraper.get_matterport_url = lambda tour_id: ""
    monkeypatch.setitem(sys.modules, "tools.asset_scraper", asset_scraper)

    result = marketing_tools.get_inventory_for_ads(limit=10)

    home = result["homes"][0]
    assert home["image_url"] == replacement_photo
    assert home["real_photos"] == [replacement_photo]
    assert home["gallery_images"] == [replacement_photo]
    assert home["floor_plan_url"] == floorplan
    assert home["media_quality"]["has_real_photo"] is True


def test_get_inventory_for_ads_keeps_floorplan_only_honest_when_catalog_has_no_photo(
    monkeypatch,
):
    floorplan = "https://cdn.example.com/floor-plans.jpg"

    monkeypatch.setattr(
        marketing_tools,
        "_load_inventory_for_marketing",
        lambda: [
            {
                "id": "still-floorplan-only",
                "model_name": "Still Floorplan Only",
                "manufacturer": "Test",
                "classification": "Single Wide",
                "status": "Available",
                "pricing": {"display_price": "Call for Price", "price_value": 0},
                "specs": {},
                "features": [],
                "image_url": floorplan,
                "gallery_images": [floorplan],
                "real_photos": [floorplan],
                "floor_plan_url": "",
            }
        ],
    )

    asset_scraper = types.ModuleType("tools.asset_scraper")
    asset_scraper.get_assets_for_home = lambda _name: {
        "images": [floorplan],
        "floor_plan": floorplan,
    }
    asset_scraper.get_matterport_url = lambda tour_id: ""
    monkeypatch.setitem(sys.modules, "tools.asset_scraper", asset_scraper)

    result = marketing_tools.get_inventory_for_ads(limit=10)

    home = result["homes"][0]
    assert home["image_url"] == ""
    assert home["real_photos"] == []
    assert home["gallery_images"] == []
    assert home["floor_plan_url"] == floorplan
    assert home["media_quality"]["status"] == "floorplan_only"


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


def test_schedule_social_post_stays_draft_without_social_publish_gate(monkeypatch):
    monkeypatch.setenv("PUBLIC_SITE_URL", "https://tho.example.com")
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "token")
    monkeypatch.delenv("THO_SOCIAL_PUBLISH_ENABLED", raising=False)

    result = marketing_tools.schedule_social_post(
        platform="tiktok",
        content_type="video",
        script_id="SCRIPT-123",
        caption="Tour this home",
        hashtags=["#TexasHomeOutlet"],
        video_url="/api/marketing/videos/test.mp4",
    )

    assert result["success"] is True
    assert result["status"] == "draft_ready"
    assert result["live_integration"] is False
    assert result["publish_attempted"] is False
    assert result["video_url"] == "https://tho.example.com/api/marketing/videos/test.mp4"
    assert "publish action" in result["publish_blocked_reason"].lower()
    assert result["script_reference"] == "SCRIPT-123"


def test_schedule_social_post_stays_draft_when_live_publish_is_configured(monkeypatch):
    """The scheduling workflow must never become an immediate-publish shortcut."""
    monkeypatch.setenv("PUBLIC_SITE_URL", "https://tho.example.com")
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "token")
    monkeypatch.setenv("THO_SOCIAL_PUBLISH_ENABLED", "true")
    publish_calls = []
    monkeypatch.setattr(
        social_publishers,
        "_publish_tiktok_video",
        lambda *args: publish_calls.append(args) or {"success": True, "post_id": "live-post"},
    )

    result = marketing_tools.schedule_social_post(
        platform="tiktok",
        content_type="video",
        script_id="SCRIPT-123",
        caption="Tour this home",
        video_url="https://cdn.example.com/test.mp4",
    )

    assert result["success"] is True
    assert result["status"] == "draft_ready"
    assert result["publish_attempted"] is False
    assert publish_calls == []
    assert "publish action" in result["publish_blocked_reason"].lower()


def test_schedule_instagram_reels_reports_missing_meta_config(monkeypatch):
    monkeypatch.setenv("PUBLIC_SITE_URL", "https://tho.example.com")
    monkeypatch.delenv("META_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", raising=False)

    result = marketing_tools.schedule_social_post(
        platform="instagram_reels",
        content_type="video",
        caption="Tour this home",
        video_url="https://cdn.example.com/test.mp4",
    )

    assert result["status"] == "draft_ready"
    assert result["live_integration"] is False
    assert "publish action" in result["publish_blocked_reason"].lower()
    assert result["optimal_times"] == ["9:00 AM", "12:00 PM", "5:00 PM"]


def test_gcp_ai_readiness_reports_config_without_generation(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "tho-ai-agent")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    result = marketing_tools.get_gcp_ai_readiness()

    assert result["success"] is True
    assert result["project"] == "tho-ai-agent"
    assert result["models"]["gemini"] == "gemini-2.5-flash"
    assert result["models"]["imagen"] == "imagen-4.0-generate-001"
    assert "requirements" in result


def test_script_quality_normalizes_structured_model_fields():
    script = marketing_tools._normalize_script_shape(
        {
            "hook": ["This one works hard."],
            "body": [
                {"shot": "Kitchen", "line": "2 bed, 2 bath with real cabinet space."},
                "Call from the lot.",
            ],
            "cta": {"line": "Call to tour today"},
            "hashtags": "#TexasHomeOutlet #HoustonHomes",
            "suggested_image_prompts": [{"prompt": "Sunny exterior photo"}],
        }
    )

    assert script["hook"] == "This one works hard."
    assert "Kitchen" in script["body"]
    assert script["cta"] == "Call to tour today"
    assert script["hashtags"] == ["#TexasHomeOutlet", "#HoustonHomes"]
    assert script["suggested_image_prompts"] == ["Sunny exterior photo"]


# ── UTM-tagged CTA links on Ad Studio posts (opt-in, env-gated) ─────────────


def test_campaign_slug_rule():
    assert (
        social_publishers._campaign_slug("TRU Single Section Delight")
        == "tru-single-section-delight"
    )
    assert social_publishers._campaign_slug("  The Oak  ") == "the-oak"
    assert social_publishers._campaign_slug("28x60 / Clayton!!!") == "28x60-clayton"
    assert social_publishers._campaign_slug(None) == "ad-studio"
    assert social_publishers._campaign_slug("   ") == "ad-studio"


def test_utm_cta_no_op_when_disabled(monkeypatch):
    monkeypatch.delenv("THO_UTM_CTA_ENABLED", raising=False)
    monkeypatch.setenv("PUBLIC_SITE_URL", "https://www.texashomeoutlet.com")
    assert social_publishers._utm_cta_link("tiktok", "The Oak") is None


def test_utm_cta_no_op_without_origin(monkeypatch):
    monkeypatch.setenv("THO_UTM_CTA_ENABLED", "1")
    monkeypatch.delenv("PUBLIC_SITE_URL", raising=False)
    monkeypatch.setattr(social_publishers, "_canonical_origin", lambda: None)
    assert social_publishers._utm_cta_link("tiktok", "The Oak") is None


def test_utm_cta_link_when_enabled(monkeypatch):
    monkeypatch.setenv("THO_UTM_CTA_ENABLED", "1")
    monkeypatch.setenv("PUBLIC_SITE_URL", "https://www.texashomeoutlet.com/")
    link = social_publishers._utm_cta_link("tiktok", "TRU Single Section")
    assert link.startswith("https://www.texashomeoutlet.com/?")
    assert "utm_source=tiktok" in link
    assert "utm_medium=social" in link
    assert "utm_campaign=tru-single-section" in link


def test_schedule_post_appends_utm_cta_only_when_enabled(monkeypatch):
    from tools.marketing_tools import schedule_social_post

    monkeypatch.setenv("PUBLIC_SITE_URL", "https://www.texashomeoutlet.com")

    # Disabled (default): caption unchanged, no utm.
    monkeypatch.delenv("THO_UTM_CTA_ENABLED", raising=False)
    off = schedule_social_post(
        platform="facebook",  # non-tiktok/ig -> local draft, never a live publish
        content_type="image",
        caption="New home just listed",
        home_name="The Oak 2860",
    )
    assert "utm_" not in off.get("caption", "")

    # Enabled: caption gains a UTM CTA derived from the featured home name.
    monkeypatch.setenv("THO_UTM_CTA_ENABLED", "1")
    on = schedule_social_post(
        platform="facebook",
        content_type="image",
        caption="New home just listed",
        home_name="The Oak 2860",
    )
    cap = on.get("caption", "")
    assert "New home just listed" in cap
    assert "utm_campaign=the-oak-2860" in cap
