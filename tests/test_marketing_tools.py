import base64
import json
import sys
import types
from io import BytesIO
from types import SimpleNamespace

import pytest

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
    # This helper uses a Firestore -> JSON -> sample fallback chain. It must not
    # claim Firestore provenance until the loader returns explicit evidence.
    assert result["source"] == "inventory_fallback_chain"
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
    assert "social_readiness" not in result
    assert result["summary"]["total_views"] is None
    assert result["summary"]["total_engagement"] is None
    assert result["summary"]["new_followers"] is None
    assert result["summary"]["dms_received"] is None
    assert result["summary"]["leads_generated"] is None
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


def test_schedule_social_post_prepares_local_draft(monkeypatch):
    monkeypatch.setenv("PUBLIC_SITE_URL", "https://tho.example.com")

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
    assert result["publish_blocked_reason"] == (
        "Draft prepared locally; no social platform request was made."
    )
    assert result["script_reference"] == "SCRIPT-123"


def test_schedule_instagram_reels_prepares_draft_without_provider_config(monkeypatch):
    monkeypatch.setenv("PUBLIC_SITE_URL", "https://tho.example.com")

    result = marketing_tools.schedule_social_post(
        platform="instagram_reels",
        content_type="video",
        caption="Tour this home",
        video_url="https://cdn.example.com/test.mp4",
    )

    assert result["status"] == "draft_ready"
    assert result["live_integration"] is False
    assert result["publish_attempted"] is False
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


# ---------------------------------------------------------------------------
# Pure helpers (deterministic, no GenAI/GCP) — coverage for the request-path
# module's non-network surface
# ---------------------------------------------------------------------------


class TestSplitEnvList:
    def test_default_used_when_env_missing(self, monkeypatch):
        monkeypatch.delenv("THO_TEST_SPLIT_LIST", raising=False)
        assert marketing_tools._split_env_list("THO_TEST_SPLIT_LIST", "a, b ,,c") == [
            "a",
            "b",
            "c",
        ]

    def test_env_overrides_default_and_strips_blanks(self, monkeypatch):
        monkeypatch.setenv("THO_TEST_SPLIT_LIST", " x ,, y , ")
        assert marketing_tools._split_env_list("THO_TEST_SPLIT_LIST", "d") == ["x", "y"]


class TestUserSafeAiError:
    """Raw provider errors must never leak internals to clients."""

    @pytest.mark.parametrize(
        "raw,code",
        [
            ("403 PERMISSION_DENIED: project tho-ai-agent billing dunning decision is deny", "ai_service_unavailable"),
            ("429 RESOURCE_EXHAUSTED quota exceeded", "ai_rate_limited"),
            ("401 unauthenticated: credentials missing", "ai_auth_error"),
            ("503 connection timed out, service unavailable", "ai_network_error"),
            ("some weird stack trace with project number 12345", "ai_unknown_error"),
        ],
    )
    def test_error_classes_map_to_safe_codes(self, raw, code):
        payload = marketing_tools._user_safe_ai_error(RuntimeError(raw))
        assert payload["error_code"] == code
        assert payload["error"].strip()

    def test_generic_fallback_never_echoes_raw_message(self):
        raw = "vertex internal: project=5555555 billing account closed"
        payload = marketing_tools._user_safe_ai_error(RuntimeError(raw))
        assert payload["error_code"] == "ai_service_unavailable"
        assert raw not in payload["error"]
        assert "5555555" not in payload["error"]

    def test_case_insensitive_matching(self):
        assert (
            marketing_tools._user_safe_ai_error("RESOURCE_EXHAUSTED")["error_code"]
            == "ai_rate_limited"
        )


class TestGcpHelpers:
    def test_project_and_location_defaults(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
        assert marketing_tools._gcp_project() == "tho-ai-agent"
        assert marketing_tools._gcp_location() == "us-central1"

    def test_project_and_location_env_overrides(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "other-project")
        monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-east4")
        assert marketing_tools._gcp_project() == "other-project"
        assert marketing_tools._gcp_location() == "us-east4"


class TestCreativeCanvas:
    def test_vertical_platforms(self):
        for platform in ("tiktok", "instagram_reels", "facebook_story"):
            assert marketing_tools._creative_canvas(platform) == (1080, 1920)

    def test_square_and_default(self):
        assert marketing_tools._creative_canvas("instagram_post") == (1080, 1080)
        assert marketing_tools._creative_canvas("facebook_feed") == (1600, 900)
        assert marketing_tools._creative_canvas("unknown_future_platform") == (1600, 900)


class TestFlyerImageHelpers:
    def _draw(self):
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (400, 400), "white")
        return ImageDraw.Draw(image)

    def test_font_returns_usable_font(self):
        font = marketing_tools._font(18)
        assert font is not None
        bold = marketing_tools._font(18, bold=True)
        assert bold is not None

    def test_wrap_text_empty_and_short(self):
        draw = self._draw()
        assert marketing_tools._wrap_text(draw, "", marketing_tools._font(14), 300) == []
        assert marketing_tools._wrap_text(draw, "hi", marketing_tools._font(14), 300) == ["hi"]

    def test_wrap_text_truncates_with_ellipsis_at_max_lines(self):
        draw = self._draw()
        long_text = "word " * 60
        lines = marketing_tools._wrap_text(draw, long_text, marketing_tools._font(14), 120, max_lines=3)
        assert len(lines) <= 3
        assert lines[-1].endswith("...")
        assert " ".join(lines)[:-3] != long_text.rstrip()

    def test_wrap_text_single_oversized_word_still_placed(self):
        draw = self._draw()
        lines = marketing_tools._wrap_text(
            draw, "supercalifragilisticexpialidocious", marketing_tools._font(14), 20
        )
        assert lines == ["supercalifragilisticexpialidocious"]

    def test_cover_image_crops_to_exact_size(self):
        from PIL import Image

        source = Image.new("RGB", (100, 50), "red")
        covered = marketing_tools._cover_image(source, 40, 40)
        assert covered.size == (40, 40)

    def test_cover_image_upscales_small_source(self):
        from PIL import Image

        source = Image.new("RGB", (10, 10), "blue")
        covered = marketing_tools._cover_image(source, 60, 30)
        assert covered.size == (60, 30)

    def test_load_source_photo_from_base64(self):
        from PIL import Image

        tiny = Image.new("RGB", (4, 4), "green")
        buffer = BytesIO()
        tiny.save(buffer, format="PNG")
        data_uri = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
        image, source = marketing_tools._load_source_photo(image_base64=data_uri)
        assert source == "generated_image"
        assert image.size == (4, 4)

    def test_load_source_photo_from_url(self, monkeypatch):
        from PIL import Image

        tiny = Image.new("RGB", (4, 4), "yellow")
        buffer = BytesIO()
        tiny.save(buffer, format="PNG")

        class _Resp:
            content = buffer.getvalue()

            def raise_for_status(self):
                return None

        monkeypatch.setattr("requests.get", lambda url, timeout: _Resp())
        image, source = marketing_tools._load_source_photo(home_photo_url="https://img.example/x.png")
        assert source == "real_photo"
        assert image.size == (4, 4)

    def test_load_source_photo_fallback_when_nothing_given(self):
        image, source = marketing_tools._load_source_photo()
        assert image is None
        assert source == "branded_fallback"


class TestScriptShapeHelpers:
    def test_coerce_script_text_variants(self):
        coerce = marketing_tools._coerce_script_text
        assert coerce(None) == ""
        assert coerce("text") == "text"
        assert coerce(42) == "42"
        assert coerce(["a", "b"]) == "a\nb"
        assert coerce([{"line": "x"}, "y", 0]) == "x\ny"
        assert coerce({"k": "v", "empty": ""}) == "v"

    def test_normalize_script_shape_coerces_fields(self):
        normalized = marketing_tools._normalize_script_shape(
            {
                "hook": ["punchy", {"alt": "line"}],
                "body": None,
                "suggested_image_prompts": "a single prompt",
                "hashtags": "#homes #forsale not-a-tag #texas",
                "tone": {"t": "casual"},
            }
        )
        assert normalized["hook"] == "punchy\nline"
        assert normalized["body"] == ""
        assert normalized["suggested_image_prompts"] == ["a single prompt"]
        assert normalized["hashtags"] == ["#homes", "#forsale", "#texas"]
        assert normalized["tone"] == "casual"

    def test_normalize_script_shape_rejects_non_list_shapes(self):
        normalized = marketing_tools._normalize_script_shape(
            {"suggested_image_prompts": 42, "hashtags": {"not": "a list"}}
        )
        assert normalized["suggested_image_prompts"] == []
        assert normalized["hashtags"] == []

    def test_normalize_script_shape_non_dict_input_returns_defaulted_shape(self):
        expected = {
            "hook": "",
            "body": "",
            "cta": "",
            "duration_estimate": "",
            "tone": "",
            "suggested_image_prompts": [],
            "hashtags": [],
        }
        assert marketing_tools._normalize_script_shape(None) == expected
        assert marketing_tools._normalize_script_shape("garbage") == expected


class TestScoreScriptQuality:
    def _strong_script(self):
        return {
            "hook": "$89,900 gets you 3 bed, 2 bath",
            "body": (
                "[SHOT: exterior] (0:00-0:03) Pull up to the covered porch.\n"
                "[SHOT: kitchen] (0:03-0:08) The island kitchen with pantry storage.\n"
                "[SHOT: master] (0:08-0:12) Master bedroom and laundry room."
            ),
            "cta": "Call 281-555-0100 or visit us in Huffman to book your tour",
        }

    def test_strong_script_passes(self):
        result = marketing_tools._score_script_quality(
            self._strong_script(), home_name="The Delight", platform="tiktok"
        )
        assert result["passed"] is True
        assert result["banned_words_found"] == []
        assert result["average"] >= 7.0

    def test_banned_word_fails_even_with_strong_shape(self):
        script = self._strong_script()
        script["hook"] = "Nestled journey awaits"
        result = marketing_tools._score_script_quality(script, platform="tiktok")
        assert result["passed"] is False
        assert set(result["banned_words_found"]) == {"nestled", "journey"}

    def test_generic_question_hook_flagged(self):
        script = self._strong_script()
        script["hook"] = "are you looking for a new home"
        result = marketing_tools._score_script_quality(script, platform="tiktok")
        assert any("generic question" in issue for issue in result["issues"])

    def test_empty_script_scores_floor_and_fails(self):
        result = marketing_tools._score_script_quality({}, platform="tiktok")
        assert result["passed"] is False
        assert result["average"] < 7.0
        assert set(result["scores"]) == {
            "hook_strength",
            "specificity",
            "authenticity",
            "cta_strength",
            "structure",
        }

    def test_facebook_short_body_flagged(self):
        script = self._strong_script()
        script["body"] = "[SHOT] tiny."
        result = marketing_tools._score_script_quality(script, platform="facebook")
        assert any("Too short for Facebook" in issue for issue in result["issues"])


# ---------------------------------------------------------------------------
# GenAI generation paths — client mocked, real google.genai types module
# ---------------------------------------------------------------------------


class _FakeGenAIClient:
    """Stand-in for google.genai.Client: records calls, replays scripted
    responses (or raises scripted exceptions) for generate_content /
    generate_images."""

    def __init__(self):
        self.models = self
        self.content_responses = []
        self.image_responses = []
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(("content", kwargs))
        response = self.content_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if not isinstance(response, str):
            response = json.dumps(response)
        return SimpleNamespace(text=response)

    def generate_images(self, **kwargs):
        self.calls.append(("images", kwargs))
        response = self.image_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _strong_script_payload():
    return {
        "hook": "$89,900 gets you 3 bed, 2 bath",
        "body": (
            "[SHOT: exterior] (0:00-0:03) Pull up to the covered porch.\n"
            "[SHOT: kitchen] (0:03-0:08) The island kitchen with pantry storage.\n"
            "[SHOT: master] (0:08-0:12) Master bedroom and laundry room."
        ),
        "cta": "Call 281-555-0100 or visit us in Huffman to book your tour",
        "hashtags": ["#manufacturedhomes", "#texashomeoutlet"],
        "duration_estimate": "30-45 seconds",
        "suggested_image_prompts": ["Exterior twilight shot of the home"],
        "tone": "exciting",
    }


def _fake_tts_module():
    tts = types.ModuleType("google.cloud.texttospeech")

    class _TTSClient:
        def synthesize_speech(self, input, voice, audio_config):
            return SimpleNamespace(audio_content=b"MP3AUDIOBYTES")

    tts.TextToSpeechClient = _TTSClient
    tts.VoiceSelectionParams = lambda **kw: SimpleNamespace(**kw)
    tts.AudioConfig = lambda **kw: SimpleNamespace(**kw)
    tts.AudioEncoding = SimpleNamespace(MP3="MP3")
    tts.SynthesisInput = lambda **kw: SimpleNamespace(**kw)
    return tts


def _patch_tts(monkeypatch, fake):
    """Route `from google.cloud import texttospeech` to the fake. Patching
    only sys.modules is not enough once the real submodule has been imported
    anywhere in the session — the parent package attribute wins."""
    import google.cloud

    monkeypatch.setitem(sys.modules, "google.cloud.texttospeech", fake)
    monkeypatch.setattr(google.cloud, "texttospeech", fake, raising=False)


class TestGenerateAdImage:
    def test_success_writes_file_and_returns_base64(self, monkeypatch, tmp_path):
        client = _FakeGenAIClient()
        client.image_responses.append(
            SimpleNamespace(
                generated_images=[SimpleNamespace(image=SimpleNamespace(image_bytes=b"PNGDATA"))]
            )
        )
        monkeypatch.setattr(marketing_tools, "_genai_client", lambda: client)
        monkeypatch.setattr(marketing_tools, "GENERATED_ADS_DIR", str(tmp_path))

        result = marketing_tools.generate_ad_image(
            prompt="Cozy living room", home_name="The Delight", platform="tiktok"
        )

        assert result["success"] is True
        assert result["image_base64"] == base64.b64encode(b"PNGDATA").decode("utf-8")
        assert result["model_used"] == "imagen-4.0-generate-001"
        assert result["aspect_ratio"] == "9:16"  # platform default
        assert result["home_featured"] == "The Delight"
        assert (tmp_path / result["filename"]).read_bytes() == b"PNGDATA"

    def test_falls_back_to_next_model_on_failure(self, monkeypatch, tmp_path):
        client = _FakeGenAIClient()
        client.image_responses.append(RuntimeError("quota exceeded"))
        client.image_responses.append(
            SimpleNamespace(
                generated_images=[SimpleNamespace(image=SimpleNamespace(image_bytes=b"IMG"))]
            )
        )
        monkeypatch.setattr(marketing_tools, "_genai_client", lambda: client)
        monkeypatch.setattr(marketing_tools, "GENERATED_ADS_DIR", str(tmp_path))

        result = marketing_tools.generate_ad_image(prompt="Kitchen island")

        assert result["success"] is True
        assert result["model_used"] == "imagen-3.0-generate-002"
        assert len(client.calls) == 2

    def test_all_models_fail_returns_user_safe_error(self, monkeypatch):
        client = _FakeGenAIClient()
        client.image_responses = [RuntimeError("boom1"), RuntimeError("boom2"), RuntimeError("boom3")]
        monkeypatch.setattr(marketing_tools, "_genai_client", lambda: client)

        result = marketing_tools.generate_ad_image(prompt="Backyard deck")

        assert result["success"] is False
        assert "error" in result
        assert "hint" in result

    def test_empty_generation_result_flagged_as_filtered(self, monkeypatch):
        client = _FakeGenAIClient()
        client.image_responses.append(SimpleNamespace(generated_images=[]))
        monkeypatch.setattr(marketing_tools, "_genai_client", lambda: client)

        result = marketing_tools.generate_ad_image(prompt="Front porch")

        assert result["success"] is False
        assert result["filtered"] is True


class TestGenerateContentScript:
    def test_single_script_success(self, monkeypatch):
        client = _FakeGenAIClient()
        client.content_responses.append(_strong_script_payload())
        monkeypatch.setattr(marketing_tools, "_genai_client", lambda: client)

        result = marketing_tools.generate_content_script(
            home_name="The Delight", home_price="$89,900", platform="tiktok"
        )

        assert result["success"] is True
        assert result["status"] == "ready_for_production"
        assert result["variations"] == 1
        assert result["script"]["hook"].startswith("$89,900")
        assert result["script"]["quality_score"] >= 7.0
        assert result["hashtags"] == ["#manufacturedhomes", "#texashomeoutlet"]
        assert result["model_used"] == "gemini-2.5-flash"

    def test_variations_return_multiple_scripts(self, monkeypatch):
        client = _FakeGenAIClient()
        payload = _strong_script_payload()
        client.content_responses.append([payload, {**payload, "hook": "$1,100/month rent? Own instead"}])
        monkeypatch.setattr(marketing_tools, "_genai_client", lambda: client)

        result = marketing_tools.generate_content_script(
            home_name="The Delight", variations=2, platform="tiktok"
        )

        assert result["success"] is True
        assert result["variations"] == 2
        assert len(result["scripts"]) == 2
        assert result["scripts"][1]["hook"] == "$1,100/month rent? Own instead"
        assert len(result["quality_scores"]) == 2

    def test_built_prompts_omit_unapproved_price_and_urgency(self, monkeypatch):
        client = _FakeGenAIClient()
        payload = _strong_script_payload()
        client.content_responses.append(payload)
        client.content_responses.append([payload, payload])
        monkeypatch.setattr(marketing_tools, "_genai_client", lambda: client)

        marketing_tools.generate_content_script(home_name="The Delight", variations=1)
        marketing_tools.generate_content_script(home_name="The Delight", variations=2)

        assert len(client.calls) == 2
        for _kind, kwargs in client.calls:
            text = kwargs["contents"].lower()
            assert "listing prices" not in text
            assert "apartment vs" not in text
            assert "limited time" not in text
            assert "families this month" not in text
            assert "bold price claim" not in text

    def test_low_quality_script_triggers_refinement(self, monkeypatch):
        client = _FakeGenAIClient()
        weak = {"hook": "Are you looking for a new home?", "body": "It is nice.", "cta": "Call us."}
        refined = _strong_script_payload()
        client.content_responses.append(weak)
        client.content_responses.append(refined)  # consumed by the refinement pass
        monkeypatch.setattr(marketing_tools, "_genai_client", lambda: client)

        result = marketing_tools.generate_content_script(home_name="The Delight")

        assert result["success"] is True
        assert result["script"]["hook"].startswith("$89,900")
        assert len(client.calls) == 2  # generation + refinement
        assert client.calls[1][0] == "content"

    def test_home_assets_add_photo_and_matterport_context(self, monkeypatch):
        client = _FakeGenAIClient()
        client.content_responses.append(_strong_script_payload())
        monkeypatch.setattr(marketing_tools, "_genai_client", lambda: client)

        asset_scraper = types.ModuleType("tools.asset_scraper")
        asset_scraper.get_assets_for_home = lambda _name: {
            "images": ["https://example.com/kitchen.jpg"],
            "matterport_id": "tour123",
            "image_categories": {"exterior": ["https://example.com/kitchen.jpg"]},
        }
        asset_scraper.get_matterport_url = (
            lambda tour_id: f"https://my.matterport.com/show/?m={tour_id}&play=1"
        )
        monkeypatch.setitem(sys.modules, "tools.asset_scraper", asset_scraper)

        result = marketing_tools.generate_content_script(home_name="The Delight")

        assert result["success"] is True
        assert result["real_photos"] == ["https://example.com/kitchen.jpg"]
        assert "tour123" in result["matterport_url"]
        assert result["matterport_id"] == "tour123"
        # Prompt must reference the real photos and 3D tour.
        prompt_sent = client.calls[0][1]["contents"]
        assert "kitchen.jpg" in prompt_sent
        assert "3D TOUR AVAILABLE" in prompt_sent

    def test_all_models_fail_returns_error_status(self, monkeypatch):
        client = _FakeGenAIClient()
        client.content_responses = [RuntimeError("down1"), RuntimeError("down2")]
        monkeypatch.setattr(marketing_tools, "_genai_client", lambda: client)

        result = marketing_tools.generate_content_script(home_name="The Delight")

        assert result["success"] is False
        assert result["status"] == "error"


class TestGetTrendingContentIdeas:
    def _inventory(self):
        return [
            {
                "id": "1",
                "model_name": "The Mansion",
                "manufacturer": "TRU",
                "status": "Available",
                "pricing": {"display_price": "$150,000", "price_value": 150000},
                "specs": {"beds": 4, "baths": 3, "sq_ft": 2000},
            },
            {
                "id": "2",
                "model_name": "The Starter",
                "manufacturer": "TRU",
                "status": "Available",
                "pricing": {"display_price": "$29,900", "price_value": 29900},
                "specs": {"beds": 2, "baths": 1, "sq_ft": 700},
            },
            {
                "id": "3",
                "model_name": "Used Gem",
                "manufacturer": "Fleetwood",
                "status": "Pre-Owned",
                "pricing": {"display_price": "$25,000", "price_value": 25000},
                "specs": {"beds": 2, "baths": 2, "sq_ft": 900},
            },
        ]

    def test_inventory_driven_ideas(self, monkeypatch):
        monkeypatch.setattr(
            marketing_tools, "_load_inventory_for_marketing", lambda: self._inventory()
        )

        result = marketing_tools.get_trending_content_ideas(
            recent_sales=7, current_promotions=["Red Tag Sale"]
        )

        assert result["success"] is True
        assert result["inventory_loaded"] is True
        assert result["inventory_count"] == 3
        types_seen = {idea["type"] for idea in result["content_ideas"]}
        # Priced inventory drives the home tour and the financing idea.
        # A current promotion still adds a clearance idea.
        assert "home_tour" in types_seen
        assert "financing_tips" in types_seen
        assert "clearance_alert" in types_seen
        assert "customer_story" not in types_seen
        assert "myth_busting" in types_seen  # staple content
        # Most expensive home is the featured tour, without an unapproved price.
        assert result["top_priority"]["home_id"] == "1"
        assert result["top_priority"]["title"] == "Home Tour: The Mansion. Call for price."
        assert "$150,000" not in result["top_priority"]["title"]
        idea_text = " ".join(
            f"{idea.get('title', '')} {idea.get('notes', '')}" for idea in result["content_ideas"]
        ).lower()
        assert "limited time" not in idea_text
        assert "families this month" not in idea_text
        assert "apartment vs" not in idea_text
        assert "affordable price" not in idea_text
        # Promotion adds another clearance idea.
        promo_ideas = [
            idea for idea in result["content_ideas"] if "Red Tag Sale" in idea.get("title", "")
        ]
        assert len(promo_ideas) == 1
        approved = self._inventory()
        approved[0]["pricing"]["owner_approved_ad_price"] = True
        monkeypatch.setattr(
            marketing_tools, "_load_inventory_for_marketing", lambda: approved
        )
        approved_result = marketing_tools.get_trending_content_ideas()
        assert approved_result["top_priority"]["title"] == "Home Tour: The Mansion. $150,000."

    def test_empty_inventory_still_returns_staples(self, monkeypatch):
        monkeypatch.setattr(marketing_tools, "_load_inventory_for_marketing", lambda: [])

        result = marketing_tools.get_trending_content_ideas()

        assert result["success"] is True
        assert result["inventory_loaded"] is False
        types_seen = {idea["type"] for idea in result["content_ideas"]}
        assert types_seen == {"myth_busting", "behind_scenes", "faq"}


class TestGenerateScriptVoiceover:
    SCRIPT = (
        "[SHOT: exterior] (0:00-0:03) Pull up to the covered porch.\n"
        "The island kitchen with pantry storage.\n"
        "Call 281-555-0100 to book your tour today!"
    )

    def test_success_strips_markers_and_returns_audio(self, monkeypatch):
        _patch_tts(monkeypatch, _fake_tts_module())

        result = marketing_tools.generate_script_voiceover(self.SCRIPT, speaking_rate=1.5)

        assert result["success"] is True
        assert result["audio_base64"] == base64.b64encode(b"MP3AUDIOBYTES").decode("utf-8")
        assert result["voice"] == "en-US-Neural2-D"
        assert result["provider"] == "google-cloud-tts"
        assert result["word_count"] > 0
        assert "[SHOT" not in result["content_preview"]
        assert "(0:00-0:03)" not in result["content_preview"]
        # Duration estimate: slower rate inflates the estimate.
        assert result["estimated_duration_seconds"] > 0

    def test_short_script_rejected(self, monkeypatch):
        _patch_tts(monkeypatch, _fake_tts_module())

        result = marketing_tools.generate_script_voiceover("Hi")

        assert result["success"] is False
        assert "too short" in result["error"]

    def test_tts_failure_returns_user_safe_error(self, monkeypatch):
        tts = _fake_tts_module()

        class _BoomClient:
            def synthesize_speech(self, input, voice, audio_config):
                raise RuntimeError("TTS unavailable")

        tts.TextToSpeechClient = _BoomClient
        _patch_tts(monkeypatch, tts)

        result = marketing_tools.generate_script_voiceover(self.SCRIPT)

        assert result["success"] is False
        assert "setup_instructions" in result
