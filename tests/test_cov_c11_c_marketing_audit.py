"""Coverage-swarm c11-c: close missing coverage in three request-path modules.

  * tools/marketing_tools.py — ToolContext / inventory_dedupe import fallbacks
    (in-process importlib.reload under patched sys.modules), readiness env
    branches, staff-publication paths, asset-classifier edge branches, and the
    term-missing remainder of the quality-scorer / refinement / fallback lanes.
  * audit_log.py — lazy _get_db success/failure, request-field extraction
    guards, _sanitize_details / _coerce_safe edge branches, and every
    query_audit_log failure/odd-input branch.
  * tools/ops_copilot.py — env/config seams (_location, _model_name, lazy
    managers, _get_db), graceful-degrade branches, and the real _generate_sync /
    _call_model path with a hermetic google.genai fake.

Hermetic only: Firestore emulator is assumed down; every Google SDK touch is
routed through sys.modules + package-attribute patches (the package attribute
wins when the real SDK is installed).

Run: python -m pytest tests/test_cov_c11_c_marketing_audit.py -q
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import audit_log  # noqa: E402
from tools import marketing_tools, ops_copilot  # noqa: E402

# Stash the real _get_db at IMPORT time: test_audit_log.py::_swap_main_audit_db
# permanently replaces audit_log._get_db with a lambda (no monkeypatch), so by
# the time these tests execute the module attribute no longer points at the
# real implementation. Collection imports all modules before any test runs.
_REAL_AUDIT_GET_DB = audit_log._get_db

from tests.test_audit_log import FakeFirestore  # noqa: E402
from tests.test_marketing_tools import (  # noqa: E402
    _fake_tts_module,
    _FakeGenAIClient,
    _patch_tts,
    _strong_script_payload,
)
from tests.test_ops_copilot import (  # noqa: E402
    _FakeApptManager,
    _FakeDB,
    _FakeLeadManager,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _pep562_broken(name: str, broken_names: tuple[str, ...] = ()) -> types.ModuleType:
    """Stub module whose named attributes raise ImportError on access.

    Dunder lookups raise AttributeError so import machinery keeps working; any
    other attribute raises ImportError, which ``from X import Y`` propagates
    (hasattr only swallows AttributeError) — driving the source's except
    ImportError branches. ``broken_names`` empty means every attribute breaks.
    """

    mod = types.ModuleType(name)

    def __getattr__(attr: str):
        if attr.startswith("__") and attr.endswith("__"):
            raise AttributeError(attr)
        if broken_names and attr not in broken_names:
            raise AttributeError(attr)
        raise ImportError(f"simulated missing optional dependency: {attr}")

    mod.__getattr__ = __getattr__  # type: ignore[attr-defined]
    return mod


def _wire_snapshot_fakes(monkeypatch, leads=(), appts=(), collections=None):
    monkeypatch.setattr(ops_copilot, "_lead_manager", lambda pid: _FakeLeadManager(list(leads)))
    monkeypatch.setattr(
        ops_copilot, "_appointment_manager", lambda pid: _FakeApptManager(list(appts))
    )
    monkeypatch.setattr(ops_copilot, "_get_db", lambda: _FakeDB(collections or {}))
    monkeypatch.setattr(ops_copilot, "_load_inventory_snapshot", lambda: None)


# =========================================================================== #
# tools/marketing_tools.py — module-level import fallbacks (L23-24, L30-31)
# =========================================================================== #


def test_tool_context_import_error_fallback_sets_none(monkeypatch):
    """L23-24: when google.adk.tools cannot provide ToolContext, fall back to None.

    In-process importlib.reload under patched sys.modules earns coverage
    (subprocess tests alone do not).
    """
    monkeypatch.setitem(sys.modules, "google.adk.tools", _pep562_broken("google.adk.tools"))
    try:
        importlib.reload(marketing_tools)
        assert marketing_tools.ToolContext is None
    finally:
        sys.modules.pop("google.adk.tools", None)
        importlib.reload(marketing_tools)
    # Restored to the real ADK class (google-adk is installed in this env).
    assert marketing_tools.ToolContext is not None


def test_inventory_dedupe_package_relative_import_fallback(monkeypatch):
    """L30-31: tools.inventory_dedupe failing must fall back to top-level import."""
    monkeypatch.setitem(
        sys.modules,
        "tools.inventory_dedupe",
        _pep562_broken("tools.inventory_dedupe"),
    )
    fake_top = types.ModuleType("inventory_dedupe")
    fake_top.is_preowned = lambda _home: True
    fake_top.suppress_used_home_catalog_photos = lambda home: home
    monkeypatch.setitem(sys.modules, "inventory_dedupe", fake_top)
    try:
        importlib.reload(marketing_tools)
        assert marketing_tools.is_preowned({}) is True
        assert marketing_tools.suppress_used_home_catalog_photos({}) == {}
    finally:
        sys.modules.pop("tools.inventory_dedupe", None)
        sys.modules.pop("inventory_dedupe", None)
        importlib.reload(marketing_tools)
    # Restored to the real package implementation.
    assert marketing_tools.is_preowned({}) is False


# =========================================================================== #
# tools/marketing_tools.py — GenAI client seam + readiness branches
# =========================================================================== #


def test_genai_client_builds_vertex_client_with_defaults(monkeypatch):
    """L117-119: _genai_client lazy import + Vertex construction."""
    import google

    fake = types.ModuleType("google.genai")
    calls: dict = {}

    class _Client:
        def __init__(self, **kwargs):
            calls.update(kwargs)

    fake.Client = _Client
    monkeypatch.setitem(sys.modules, "google.genai", fake)
    monkeypatch.setattr(google, "genai", fake, raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)

    client = marketing_tools._genai_client()
    assert isinstance(client, _Client)
    assert calls == {"vertexai": True, "project": "tho-ai-agent", "location": "us-central1"}


def test_gcp_readiness_reports_missing_sdks(monkeypatch):
    """L142-143 / L148-149: both SDK probes failing populate requirements."""
    import google
    import google.cloud

    # `from google import genai` binds the submodule object itself (no attribute
    # access on it), so a PEP 562 stub cannot break it. Halting the import with
    # a None sys.modules entry + dropping the cached parent attribute is what
    # makes the from-import raise.
    monkeypatch.delattr(google, "genai", raising=False)
    monkeypatch.setitem(sys.modules, "google.genai", None)
    monkeypatch.delattr(google.cloud, "texttospeech", raising=False)
    monkeypatch.setitem(sys.modules, "google.cloud.texttospeech", None)

    result = marketing_tools.get_gcp_ai_readiness()

    assert result["google_genai_sdk"] is False
    assert result["text_to_speech_sdk"] is False
    assert any("google-genai" in req for req in result["requirements"])
    assert any("texttospeech" in req for req in result["requirements"])
    assert result["ready"] is False


def test_gcp_readiness_flags_blank_project_and_location(monkeypatch):
    """L151 / L153: empty env values produce setup requirements."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "")

    result = marketing_tools.get_gcp_ai_readiness()

    assert "Set GOOGLE_CLOUD_PROJECT" in result["requirements"]
    assert "Set GOOGLE_CLOUD_LOCATION" in result["requirements"]
    assert result["ready"] is False


# =========================================================================== #
# tools/marketing_tools.py — inventory loading + staff publication
# =========================================================================== #


def test_load_inventory_for_marketing_returns_empty_when_unimportable(monkeypatch):
    """L291-297: both the absolute and package-relative import failing -> []."""
    monkeypatch.setitem(
        sys.modules,
        "tools.inventory_tools",
        _pep562_broken("tools.inventory_tools", ("_load_inventory",)),
    )
    assert marketing_tools._load_inventory_for_marketing() == []


def test_load_inventory_for_marketing_success_and_loader_error(monkeypatch):
    """L292/300-301 (success) and L302-304 (loader raises -> warn + [])."""
    fake = types.ModuleType("tools.inventory_tools")
    fake._load_inventory = lambda: [{"id": "home-1"}]
    monkeypatch.setitem(sys.modules, "tools.inventory_tools", fake)
    assert marketing_tools._load_inventory_for_marketing() == [{"id": "home-1"}]

    def _boom():
        raise RuntimeError("firestore down")

    fake._load_inventory = _boom
    assert marketing_tools._load_inventory_for_marketing() == []


def test_load_inventory_for_marketing_treats_none_as_empty(monkeypatch):
    fake = types.ModuleType("tools.inventory_tools")
    fake._load_inventory = lambda: None
    monkeypatch.setitem(sys.modules, "tools.inventory_tools", fake)
    assert marketing_tools._load_inventory_for_marketing() == []


def test_get_inventory_for_ads_empty_inventory_message(monkeypatch):
    """L347: empty inventory returns the manual-entry guidance payload."""
    monkeypatch.setattr(marketing_tools, "_load_inventory_for_marketing", lambda: [])
    result = marketing_tools.get_inventory_for_ads()
    assert result["success"] is False
    assert "No inventory data" in result["message"]
    assert result["homes"] == []


def _ad_home(home_id: str = "1", **overrides) -> dict:
    home = {
        "id": home_id,
        "model_name": "The Delight",
        "manufacturer": "TRU",
        "status": "Available",
        "pricing": {"display_price": "$89,900", "price_value": 89900},
        "specs": {"beds": 3, "baths": 2, "sq_ft": 1280},
        "features": [],
        "gallery_images": [],
        "image_url": "",
    }
    home.update(overrides)
    return home


def _stub_asset_scraper(monkeypatch, assets_by_name=None):
    asset_scraper = types.ModuleType("tools.asset_scraper")
    assets_by_name = assets_by_name or (lambda _name: None)
    asset_scraper.get_assets_for_home = assets_by_name
    asset_scraper.get_matterport_url = (
        lambda tour_id: f"https://my.matterport.com/show/?m={tour_id}&play=1"
    )
    monkeypatch.setitem(sys.modules, "tools.asset_scraper", asset_scraper)


def test_get_inventory_for_ads_staff_publication_unavailable(monkeypatch):
    """L324-333: staff read returning None is an honest error payload."""
    import tools.inventory_tools as inv_mod

    monkeypatch.setattr(inv_mod, "_load_inventory_for_publication", lambda: None)
    result = marketing_tools.get_inventory_for_ads(staff_publication=True)
    assert result == {
        "success": False,
        "source": "staff_inventory_unavailable",
        "homes": [],
        "error": "Staff inventory is temporarily unavailable.",
    }


def test_get_inventory_for_ads_staff_publication_empty_inventory(monkeypatch):
    """L334-342: staff read returning [] is a successful empty payload."""
    import tools.inventory_tools as inv_mod

    monkeypatch.setattr(inv_mod, "_load_inventory_for_publication", lambda: [])
    result = marketing_tools.get_inventory_for_ads(staff_publication=True)
    assert result["success"] is True
    assert result["source"] == "staff_inventory_with_catalog"
    assert result["total_inventory"] == 0
    assert result["homes"] == []


def test_get_inventory_for_ads_staff_publication_sets_retrieved_at(monkeypatch):
    """L452: a successful live staff read stamps retrieved_at."""
    import tools.inventory_tools as inv_mod

    monkeypatch.setattr(
        inv_mod, "_load_inventory_for_publication", lambda: [_ad_home()]
    )
    _stub_asset_scraper(monkeypatch)
    result = marketing_tools.get_inventory_for_ads(staff_publication=True)
    assert result["success"] is True
    assert result["source"] == "staff_inventory_with_catalog"
    assert "retrieved_at" in result


def test_get_inventory_for_ads_asset_import_failure_propagates(monkeypatch):
    """L365-367: both asset_scraper import attempts failing -> ImportError."""
    monkeypatch.setattr(
        marketing_tools, "_load_inventory_for_marketing", lambda: [_ad_home()]
    )
    monkeypatch.setitem(
        sys.modules,
        "tools.asset_scraper",
        _pep562_broken("tools.asset_scraper", ("get_assets_for_home",)),
    )
    with pytest.raises(ImportError):
        marketing_tools.get_inventory_for_ads()


def test_get_inventory_for_ads_classifier_fallback_raises_after_scraper_ok(monkeypatch):
    """L367: the scraper half of the fallback succeeds, so the classifier line
    (367) executes before its own relative import raises."""
    monkeypatch.setattr(
        marketing_tools, "_load_inventory_for_marketing", lambda: [_ad_home()]
    )
    monkeypatch.setitem(
        sys.modules,
        "tools.photo_classifier",
        _pep562_broken("tools.photo_classifier", ("apply_classifier_to_home",)),
    )
    with pytest.raises(ImportError):
        marketing_tools.get_inventory_for_ads()


def test_get_inventory_for_ads_applies_asset_categories_and_matterport(monkeypatch):
    """L412 (image_categories backfill) and L417-418 (asset matterport merge)."""
    home = _ad_home()  # no matterport fields, no image_categories
    assets = {
        "images": ["https://cdn.example.com/ext.jpg"],
        "image_categories": {"exterior": ["https://cdn.example.com/ext.jpg"]},
        "matterport_id": "tour-9",
    }
    monkeypatch.setattr(marketing_tools, "_load_inventory_for_marketing", lambda: [home])
    _stub_asset_scraper(monkeypatch, lambda _name: assets)

    result = marketing_tools.get_inventory_for_ads()
    merged = result["homes"][0]
    assert merged["image_categories"] == {"exterior": ["https://cdn.example.com/ext.jpg"]}
    assert merged["matterport_id"] == "tour-9"
    assert merged["matterport_url"].endswith("m=tour-9&play=1")


def test_get_inventory_for_ads_suppresses_catalog_photos_for_preowned(monkeypatch):
    """L422: pre-owned homes pass through suppress_used_home_catalog_photos."""
    home = _ad_home(
        home_id="used-1",
        model_name="Used Gem",
        status="Pre-Owned",
        pricing={"display_price": "$25,000", "price_value": 25000},
        gallery_images=["https://cdn.example.com/unit-shot.jpg"],
    )
    monkeypatch.setattr(marketing_tools, "_load_inventory_for_marketing", lambda: [home])
    _stub_asset_scraper(monkeypatch, lambda _name: {"images": ["https://cdn.example.com/cat.jpg"]})

    result = marketing_tools.get_inventory_for_ads()
    assert result["preowned_homes"] == 1
    suppressed = result["homes"][0]
    # Catalog photo must not survive onto a used listing's galleries.
    assert "https://cdn.example.com/cat.jpg" not in repr(
        suppressed["gallery_images"] + suppressed["real_photos"]
    )


# =========================================================================== #
# tools/marketing_tools.py — image / flyer generation lanes
# =========================================================================== #


def test_generate_ad_image_reports_missing_genai_types(monkeypatch):
    """L511-512: google.genai.types import failure -> SDK-unavailable payload."""
    broken = _pep562_broken("google.genai")
    broken.__path__ = []  # pretend package so fromlist resolution is exercised
    monkeypatch.setitem(sys.modules, "google.genai", broken)

    result = marketing_tools.generate_ad_image(prompt="Front porch")

    assert result["success"] is False
    assert "Google GenAI SDK unavailable" in result["error"]
    assert "hint" in result


def test_font_falls_back_to_pillow_default(monkeypatch):
    """L633: no candidate font file exists -> ImageFont.load_default()."""
    monkeypatch.setattr(marketing_tools.os.path, "exists", lambda _p: False)
    assert marketing_tools._font(12) is not None
    assert marketing_tools._font(12, bold=True) is not None


def test_generate_ad_flyer_without_photo_uses_branded_fallback(monkeypatch, tmp_path):
    """L709: no source photo -> solid branded canvas."""
    monkeypatch.setattr(marketing_tools, "GENERATED_ADS_DIR", str(tmp_path))
    result = marketing_tools.generate_ad_flyer(home_name="The Oak", platform="tiktok")
    assert result["success"] is True
    assert result["source"] == "branded_fallback"
    assert (tmp_path / result["filename"]).is_file()


def test_generate_ad_flyer_failure_returns_safe_error(monkeypatch):
    """L817-819: any PIL/canvas failure maps to the safe error payload."""

    def _boom(_platform):
        raise RuntimeError("PIL exploded")

    monkeypatch.setattr(marketing_tools, "_creative_canvas", _boom)
    result = marketing_tools.generate_ad_flyer(home_name="The Oak")
    assert result["success"] is False
    assert result["error_code"] == "ai_unknown_error"
    assert "PIL exploded" not in result["error"]


# =========================================================================== #
# tools/marketing_tools.py — quality scorer branch remainder
# =========================================================================== #


def _scorable(**overrides) -> dict:
    script = {
        "hook": "a b c d e",
        "body": "short body mentioning the kitchen",
        "cta": "call us today",
    }
    script.update(overrides)
    return script


def test_scorer_flags_overlong_hook():
    """L1093-1095: hooks over 20 words are penalized and flagged."""
    result = marketing_tools._score_script_quality(
        _scorable(hook="word " * 25), platform="tiktok"
    )
    assert any("Hook too long" in issue for issue in result["issues"])


def test_scorer_awards_bare_dollar_sign_hook():
    """L1099: a '$' without a numeric amount still earns the specificity bump."""
    result = marketing_tools._score_script_quality(
        _scorable(hook="cheap $ deal here now"), platform="tiktok"
    )
    assert result["scores"]["hook_strength"] == 8  # 5 + 2 short + 1 dollar-sign


def test_scorer_flags_boring_preamble_hook():
    """L1108-1109: 'in this video...' openers are flagged."""
    result = marketing_tools._score_script_quality(
        _scorable(hook="in this video we tour a home"), platform="tiktok"
    )
    assert any("boring preamble" in issue for issue in result["issues"])


def test_scorer_awards_trend_format_hook():
    """L1111: pov:/wait-for-it hooks get the trend bonus."""
    result = marketing_tools._score_script_quality(
        _scorable(hook="pov: you own this home"), platform="tiktok"
    )
    assert result["scores"]["hook_strength"] == 8  # 5 + 2 short + 1 trend format


def test_scorer_awards_home_name_mention():
    """L1123: naming the featured home boosts specificity."""
    script = _scorable(body="walk through the delight kitchen with pantry")
    with_name = marketing_tools._score_script_quality(
        script, home_name="The Delight", platform="tiktok"
    )
    without = marketing_tools._score_script_quality(
        script, home_name="Other Model", platform="tiktok"
    )
    assert with_name["scores"]["specificity"] == without["scores"]["specificity"] + 2


def test_scorer_flags_exclamation_spam():
    """L1154-1155: more than 3 exclamation marks are penalized."""
    result = marketing_tools._score_script_quality(
        _scorable(body="wow! so! much! energy! here"), platform="tiktok"
    )
    assert any("exclamation" in issue for issue in result["issues"])


def test_scorer_flags_emoji_spam():
    """L1158-1159: more than 2 emojis are penalized."""
    result = marketing_tools._score_script_quality(
        _scorable(body="look 😀😀😀😀 at this"), platform="tiktok"
    )
    assert any("emoji" in issue for issue in result["issues"])


def test_scorer_flags_run_on_sentences():
    """L1163-1164: multiple 25+ word sentences are penalized."""
    sentence_a = "word " * 30
    sentence_b = "more " * 30
    result = marketing_tools._score_script_quality(
        _scorable(body=f"{sentence_a}. {sentence_b}."), platform="tiktok"
    )
    assert any("run-on" in issue for issue in result["issues"])


def test_scorer_awards_question_cta():
    """L1188: a question CTA earns the engagement bump."""
    plain = marketing_tools._score_script_quality(_scorable(), platform="tiktok")
    question = marketing_tools._score_script_quality(
        _scorable(cta="call us today?"), platform="tiktok"
    )
    assert question["scores"]["cta_strength"] == plain["scores"]["cta_strength"] + 1


def test_scorer_awards_partial_shot_markers():
    """L1203: 1-2 [SHOT] markers still earn partial structure credit."""
    result = marketing_tools._score_script_quality(
        _scorable(body="[SHOT: porch] walk in and see the kitchen"),
        platform="tiktok",
    )
    assert result["scores"]["structure"] >= 5  # 4 base + 1 for one marker


def test_scorer_flags_overlong_tiktok_body():
    """L1212-1213: >150-word bodies are flagged for TikTok."""
    result = marketing_tools._score_script_quality(
        _scorable(body="[SHOT: a] " + "word " * 160), platform="tiktok"
    )
    assert any("Too long for TikTok" in issue for issue in result["issues"])


# =========================================================================== #
# tools/marketing_tools.py — refinement lane (L1245, L1287, L1289-1291)
# =========================================================================== #


def test_refine_returns_original_when_quality_passes():
    """L1245: a passing script is never sent back to the model."""

    class _ExplodingClient:
        def __getattr__(self, _name):
            raise AssertionError("model must not be called for passing scripts")

    script = _scorable()
    quality = {"passed": True, "issues": [], "banned_words_found": []}
    assert (
        marketing_tools._refine_script_if_needed(_ExplodingClient(), "m", script, quality, "tiktok")
        is script
    )


def test_refine_takes_first_item_when_model_returns_list(monkeypatch):
    """L1287: a list-shaped model response uses its first element."""
    client = _FakeGenAIClient()
    refined = _strong_script_payload()
    client.content_responses.append(json.dumps([refined]))
    quality = {"passed": False, "issues": ["x"], "banned_words_found": []}

    out = marketing_tools._refine_script_if_needed(
        client, "gemini-test", _scorable(), quality, "tiktok"
    )
    assert out["hook"] == refined["hook"]


def test_refine_failure_returns_original_script():
    """L1289-1291: a refinement exception degrades to the original script."""
    client = _FakeGenAIClient()
    client.content_responses.append(RuntimeError("refine exploded"))
    original = _scorable()
    quality = {"passed": False, "issues": ["x"], "banned_words_found": ["nestled"]}

    out = marketing_tools._refine_script_if_needed(client, "m", original, quality, "tiktok")
    assert out is original


# =========================================================================== #
# tools/marketing_tools.py — content-script generation branches
# =========================================================================== #


def test_generate_content_script_reports_missing_genai_types(monkeypatch):
    """L1337-1338: google.genai.types import failure -> gcp_not_ready payload."""
    broken = _pep562_broken("google.genai")
    broken.__path__ = []
    monkeypatch.setitem(sys.modules, "google.genai", broken)

    result = marketing_tools.generate_content_script(home_name="The Delight")

    assert result["success"] is False
    assert result["status"] == "gcp_not_ready"
    assert "Google GenAI SDK unavailable" in result["error"]


def test_generate_content_script_asset_import_failure_propagates(monkeypatch):
    """L1359-1360: both asset_scraper import attempts failing -> ImportError."""
    client = _FakeGenAIClient()
    client.content_responses.append(_strong_script_payload())
    monkeypatch.setattr(marketing_tools, "_genai_client", lambda: client)
    monkeypatch.setitem(
        sys.modules,
        "tools.asset_scraper",
        _pep562_broken("tools.asset_scraper", ("get_assets_for_home",)),
    )
    with pytest.raises(ImportError):
        marketing_tools.generate_content_script(home_name="The Delight")


def test_generate_content_script_auto_loads_inventory_context(monkeypatch):
    """L1389-1402: with no home_name, priced inventory is inlined as context."""
    client = _FakeGenAIClient()
    client.content_responses.append(_strong_script_payload())
    monkeypatch.setattr(marketing_tools, "_genai_client", lambda: client)
    monkeypatch.setattr(
        marketing_tools,
        "_load_inventory_for_marketing",
        lambda: [
            _ad_home("a", model_name="The Mansion", pricing={"display_price": "$150,000", "price_value": 150000}),
            _ad_home("b", model_name="The Starter", pricing={"display_price": "$29,900", "price_value": 29900}),
        ],
    )

    result = marketing_tools.generate_content_script(platform="tiktok")

    assert result["success"] is True
    prompt_sent = client.calls[0][1]["contents"]
    assert "CURRENT INVENTORY HIGHLIGHTS" in prompt_sent
    assert "The Mansion" in prompt_sent
    assert "$150,000" in prompt_sent


def test_generate_content_script_accepts_single_item_list_response(monkeypatch):
    """L1606: a one-element JSON array is unwrapped to the single script."""
    client = _FakeGenAIClient()
    client.content_responses.append([_strong_script_payload()])
    monkeypatch.setattr(marketing_tools, "_genai_client", lambda: client)

    result = marketing_tools.generate_content_script(home_name="The Delight")

    assert result["success"] is True
    assert result["variations"] == 1
    assert result["script"]["hook"].startswith("$89,900")


# =========================================================================== #
# tools/marketing_tools.py — social scheduling / performance / voiceover
# =========================================================================== #


def test_schedule_social_post_relative_import_failure_propagates(monkeypatch):
    """L1814-1815: both social_publishers import attempts failing -> ImportError."""
    monkeypatch.setitem(
        sys.modules,
        "tools.social_publishers",
        _pep562_broken("tools.social_publishers", ("prepare_social_post_draft",)),
    )
    with pytest.raises(ImportError):
        marketing_tools.schedule_social_post(platform="tiktok", content_type="video")


def test_analyze_content_photo_classifier_import_failure_propagates(monkeypatch):
    """L1853-1854: both photo_classifier import attempts failing -> ImportError."""
    monkeypatch.setattr(marketing_tools, "_load_inventory_for_marketing", lambda: [])
    monkeypatch.setitem(
        sys.modules,
        "tools.photo_classifier",
        _pep562_broken("tools.photo_classifier", ("has_real_photo",)),
    )
    with pytest.raises(ImportError):
        marketing_tools.analyze_content_performance()


def test_analyze_content_counts_survive_missing_directories(monkeypatch, tmp_path):
    """L1865-1866: an unscannable creative directory counts as zero, not a crash."""
    monkeypatch.setattr(marketing_tools, "_load_inventory_for_marketing", lambda: [])
    monkeypatch.setattr(marketing_tools, "GENERATED_ADS_DIR", str(tmp_path / "missing" / "ads"))
    result = marketing_tools.analyze_content_performance()
    assert result["success"] is True
    assert result["summary"]["generated_images"] == 0


def test_voiceover_truncates_scripts_over_tts_limit(monkeypatch):
    """L1980: scripts over 5000 chars are truncated before synthesis."""
    _patch_tts(monkeypatch, _fake_tts_module())

    long_script = "word " * 1200  # 5999 chars, well past the TTS limit
    result = marketing_tools.generate_script_voiceover(long_script)

    assert result["success"] is True
    # 5000 chars of "word word ..." == exactly 1000 words.
    assert result["word_count"] == 1000
    assert result["content_preview"].endswith("...")


# =========================================================================== #
# audit_log.py — lazy client + reset hook (L153-162, L168)
# =========================================================================== #


def test_get_db_lazy_success_uses_env_project(monkeypatch):
    """L153/155/157/158: a working SDK import builds and caches the client."""
    import google.cloud

    import audit_log

    fake_fs = types.ModuleType("google.cloud.firestore")
    sentinel = object()
    created: dict = {}

    def _client(project):
        created["project"] = project
        return sentinel

    fake_fs.Client = _client
    monkeypatch.setitem(sys.modules, "google.cloud.firestore", fake_fs)
    monkeypatch.setattr(google.cloud, "firestore", fake_fs, raising=False)

    audit_log._reset_client_for_tests()
    try:
        assert _REAL_AUDIT_GET_DB() is sentinel
        assert created["project"] == "tho-test-local"  # conftest default
        # Second call is served from the module cache.
        assert _REAL_AUDIT_GET_DB() is sentinel
    finally:
        audit_log._reset_client_for_tests()


def test_get_db_failure_returns_none_and_stays_none(monkeypatch, caplog):
    """L159-162: SDK/client failure logs a warning and caches None."""
    import google.cloud

    import audit_log

    fake_fs = types.ModuleType("google.cloud.firestore")

    def _boom(project):
        raise RuntimeError("no credentials in test env")

    fake_fs.Client = _boom
    monkeypatch.setitem(sys.modules, "google.cloud.firestore", fake_fs)
    monkeypatch.setattr(google.cloud, "firestore", fake_fs, raising=False)

    audit_log._reset_client_for_tests()
    try:
        with caplog.at_level(logging.WARNING, logger="audit_log"):
            assert _REAL_AUDIT_GET_DB() is None
        assert any("Firestore client unavailable" in rec.message for rec in caplog.records)
        # None is cached — a second call must not retry the SDK.
        assert _REAL_AUDIT_GET_DB() is None
    finally:
        audit_log._reset_client_for_tests()


def test_reset_client_for_tests_hook():
    import audit_log

    audit_log._firestore_client = object()
    audit_log._reset_client_for_tests()
    assert audit_log._firestore_client is None


# =========================================================================== #
# audit_log.py — request-field extraction guards (L182-183, L191-192)
# =========================================================================== #


def test_client_ip_extraction_failure_returns_empty(monkeypatch):
    """L182-183: a broken tools.client_ip.get_client_ip degrades to ""."""
    import audit_log
    import tools.client_ip

    def _boom(request, default=""):
        raise RuntimeError("unusual request shape")

    monkeypatch.setattr(tools.client_ip, "get_client_ip", _boom)
    assert audit_log._client_ip_from_request(object()) == ""


def test_user_agent_extraction_failure_returns_empty():
    """L191-192: a headers object that raises degrades to ""."""
    import audit_log

    class _EvilHeaders:
        def get(self, *_args, **_kwargs):
            raise RuntimeError("no headers")

    request = SimpleNamespace(headers=_EvilHeaders())
    assert audit_log._user_agent_from_request(request) == ""


# =========================================================================== #
# audit_log.py — sanitize / coerce edge branches
# =========================================================================== #


def test_sanitize_details_rejects_non_dict():
    """L205: non-dict details become an explicit invalid marker."""
    import audit_log

    assert audit_log._sanitize_details(["not", "a", "dict"]) == {
        "_invalid": "details must be a dict"
    }


def test_sanitize_details_skips_non_string_keys():
    """L210: non-string keys are dropped, string keys survive."""
    import audit_log

    assert audit_log._sanitize_details({1: "x", None: "y", "ok": True}) == {"ok": True}


def test_sanitize_details_truncates_oversized_payload():
    """L225: details over the 4 KiB cap collapse to a truncation marker."""
    import audit_log

    huge = {f"key_{i}": "x" * 500 for i in range(20)}
    result = audit_log._sanitize_details(huge)
    assert result["_truncated"] is True
    assert result["size_bytes"] > 4096


def test_coerce_safe_truncates_excessive_depth():
    """L232: nesting deeper than 4 levels collapses to '<truncated>'."""
    import audit_log

    nested = "deep"
    for _ in range(6):
        nested = {"a": nested}
    result = audit_log._coerce_safe(nested)
    node = result
    for _ in range(5):
        node = node["a"]
    assert node == "<truncated>"


def test_coerce_safe_skips_non_string_dict_keys():
    """L244: non-string keys are skipped inside nested dicts too."""
    import audit_log

    assert audit_log._coerce_safe({1: "x", "ok": 1}) == {"ok": 1}


def test_coerce_safe_stringifies_unknown_types():
    """L254-255: unknown types fall back to str(value) capped at 500 chars."""
    import audit_log

    class Weird:
        def __str__(self):
            return "weird-value"

    assert audit_log._coerce_safe(Weird()) == "weird-value"
    assert audit_log._coerce_safe("s" * 600) == "s" * 500


def test_coerce_safe_unprintable_values_become_marker():
    """L256-257: a __str__ that raises collapses to '<unprintable>'."""
    import audit_log

    class Evil:
        def __str__(self):
            raise RuntimeError("cannot print me")

    assert audit_log._coerce_safe(Evil()) == "<unprintable>"


# =========================================================================== #
# audit_log.py — log_admin_action + query_audit_log remainder
# =========================================================================== #


def test_log_admin_action_warns_on_unknown_target_type(monkeypatch, caplog):
    """L296: unknown target_type is logged with a drift warning."""
    import audit_log

    fake = FakeFirestore()
    monkeypatch.setattr(audit_log, "_get_db", lambda: fake)
    with caplog.at_level(logging.WARNING, logger="audit_log"):
        audit_log.log_admin_action(
            actor="admin", action="deal.create", target_type="wallet", target_id="x"
        )
    assert any("unknown target_type" in rec.message.lower() for rec in caplog.records)
    assert len(fake.collections["audit_log"]._docs) == 1


def test_log_admin_action_skipped_without_db(monkeypatch, caplog):
    """L315-316: no Firestore client -> warn-and-skip, never a raise."""
    import audit_log

    monkeypatch.setattr(audit_log, "_get_db", lambda: None)
    with caplog.at_level(logging.WARNING, logger="audit_log"):
        audit_log.log_admin_action(
            actor="admin", action="deal.create", target_type="deal", target_id="d-1"
        )
    assert any("skipped" in rec.message.lower() for rec in caplog.records)


def test_query_audit_log_bad_limit_falls_back_to_default(monkeypatch):
    """L347-348: a non-numeric limit falls back to 100."""
    import audit_log

    fake = FakeFirestore()
    monkeypatch.setattr(audit_log, "_get_db", lambda: fake)
    assert audit_log.query_audit_log(limit="not-a-number") == []


def test_query_audit_log_returns_empty_without_db(monkeypatch):
    """L352: no Firestore client -> empty list."""
    import audit_log

    monkeypatch.setattr(audit_log, "_get_db", lambda: None)
    assert audit_log.query_audit_log() == []


def test_query_audit_log_filters_by_target_type(monkeypatch):
    """L362: the target_type where-filter composes with the others."""
    import audit_log

    fake = FakeFirestore()
    monkeypatch.setattr(audit_log, "_get_db", lambda: fake)
    audit_log.log_admin_action(
        actor="admin", action="customer.update", target_type="customer", target_id="c-1"
    )
    audit_log.log_admin_action(
        actor="admin", action="deal.create", target_type="deal", target_id="d-1"
    )

    rows = audit_log.query_audit_log(target_type="customer")
    assert len(rows) == 1
    assert rows[0]["target_type"] == "customer"


class _LimitBoomQuery:
    """Query whose .limit() raises (Firestore inequality+limit quirk)."""

    def __init__(self, snaps):
        self._snaps = snaps

    def limit(self, _n):
        raise RuntimeError("limit unsupported alongside inequality")

    def stream(self, timeout: float | None = None):
        return iter(self._snaps)


class _LimitBoomCollection:
    def __init__(self, snaps):
        self._snaps = snaps

    def where(self, _field, _op, _value):
        return _LimitBoomQuery(self._snaps)

    def limit(self, n):
        return _LimitBoomQuery(self._snaps)

    def stream(self, timeout: float | None = None):
        return iter(self._snaps)


def test_query_audit_log_limit_fallback_stream(monkeypatch):
    """L373-374: when query.limit() raises, the plain stream is used instead."""

    import audit_log

    class _Snap:
        id = "s-1"

        def to_dict(self):
            return {"timestamp": "2026-01-01T00:00:00+00:00", "actor": "admin"}

    class _DB:
        def collection(self, _name):
            return _LimitBoomCollection([_Snap()])

    monkeypatch.setattr(audit_log, "_get_db", lambda: _DB())
    rows = audit_log.query_audit_log(actor="admin")
    assert len(rows) == 1
    assert rows[0]["id"] == "s-1"


def test_query_audit_log_skips_undecodable_snapshots(monkeypatch):
    """L380-381: a snapshot whose to_dict() raises is skipped, not fatal."""

    import audit_log

    class _PoisonSnap:
        id = "poison"

        def to_dict(self):
            raise RuntimeError("corrupt snapshot")

    class _GoodSnap:
        id = "good"

        def to_dict(self):
            return {"timestamp": "2026-01-02T00:00:00+00:00"}

    class _DB:
        def collection(self, _name):
            return _LimitBoomCollection([_PoisonSnap(), _GoodSnap()])

    monkeypatch.setattr(audit_log, "_get_db", lambda: _DB())
    rows = audit_log.query_audit_log()
    assert [r["id"] for r in rows] == ["good"]


def test_query_audit_log_query_failure_returns_empty(monkeypatch, caplog):
    """L388-390: a top-level query failure logs and returns []."""
    import audit_log

    class _BoomDB:
        def collection(self, _name):
            raise RuntimeError("firestore gone")

    monkeypatch.setattr(audit_log, "_get_db", lambda: _BoomDB())
    with caplog.at_level(logging.WARNING, logger="audit_log"):
        assert audit_log.query_audit_log() == []
    assert any("query failed" in rec.message.lower() for rec in caplog.records)


# =========================================================================== #
# tools/ops_copilot.py — env/config seams (L51, L57-62, L66-80)
# =========================================================================== #


def test_location_env_override(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-east4")
    assert ops_copilot._location() == "us-east4"
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    assert ops_copilot._location() == "us-central1"


def test_model_name_env_override_wins(monkeypatch):
    """L57-59: COPILOT_MODEL short-circuits the config lookup."""
    monkeypatch.setenv("COPILOT_MODEL", "gemini-c11c-test")
    assert ops_copilot._model_name() == "gemini-c11c-test"


def test_model_name_falls_back_to_config_loader(monkeypatch):
    """L60-62: without the env override the model comes from config.yaml."""
    monkeypatch.delenv("COPILOT_MODEL", raising=False)
    import config_loader

    monkeypatch.setattr(config_loader, "get_model_config", lambda: {"model": "cfg-model"})
    assert ops_copilot._model_name() == "cfg-model"


def test_lazy_managers_resolve_from_sys_modules(monkeypatch):
    """L66-68 / L72-74: managers are built from lazily-imported modules."""
    lm = types.ModuleType("lead_management")
    am = types.ModuleType("appointment_manager")

    class _LM:
        def __init__(self, project_id):
            self.project_id = project_id

    class _AM(_LM):
        pass

    lm.LeadManager = _LM
    am.AppointmentManager = _AM
    monkeypatch.setitem(sys.modules, "lead_management", lm)
    monkeypatch.setitem(sys.modules, "appointment_manager", am)

    lead_mgr = ops_copilot._lead_manager("proj-a")
    appt_mgr = ops_copilot._appointment_manager("proj-b")
    assert isinstance(lead_mgr, _LM) and lead_mgr.project_id == "proj-a"
    assert isinstance(appt_mgr, _AM) and appt_mgr.project_id == "proj-b"


def test_get_db_resolves_database_client(monkeypatch):
    """L78-80: _get_db unwraps database.firestore_client.get_database()."""
    import database.firestore_client as fc

    sentinel = object()
    monkeypatch.setattr(fc, "get_database", lambda: SimpleNamespace(db=sentinel))
    assert ops_copilot._get_db() is sentinel


# =========================================================================== #
# tools/ops_copilot.py — graceful-degrade branches
# =========================================================================== #


def test_count_by_status_degrades_on_error(monkeypatch):
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(ops_copilot, "_get_db", _boom)
    assert ops_copilot._count_by_status("inventory") == {"error": "unavailable"}


def test_inventory_freshness_failure_degrades(monkeypatch):
    """L139-141: an unexpected snapshot failure -> {'available': False}."""

    def _boom():
        raise RuntimeError("snapshot corrupt")

    monkeypatch.setattr(ops_copilot, "_load_inventory_snapshot", _boom)
    assert ops_copilot.get_inventory_freshness() == {"available": False}


def test_command_center_enabled_swallows_errors(monkeypatch):
    """L150-151: a Notion config hiccup means the switch stays off."""
    from tools import notion_client

    def _boom():
        raise RuntimeError("notion config broken")

    monkeypatch.setattr(notion_client, "is_command_center_enabled", _boom)
    assert ops_copilot._command_center_enabled() is False


def test_firestore_feedback_degrades_on_error(monkeypatch):
    """L172-174: a Firestream read failure degrades to an error stub."""

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(ops_copilot, "_get_db", _boom)
    assert ops_copilot._firestore_feedback() == {"error": "unavailable"}


def test_snapshot_appointments_failure_is_isolated(monkeypatch):
    """L202-204: the appointments section degrades without sinking the snapshot."""

    def _boom(_project_id):
        raise RuntimeError("appointments down")

    _wire_snapshot_fakes(monkeypatch)
    # Wire the shared fakes FIRST, then override the appointments manager —
    # _wire_snapshot_fakes would otherwise overwrite this patch.
    monkeypatch.setattr(ops_copilot, "_appointment_manager", _boom)
    snap = asyncio.run(ops_copilot.get_business_snapshot())
    assert snap["appointments"] == {"error": "unavailable"}
    assert snap["leads"]["total"] == 0


# =========================================================================== #
# tools/ops_copilot.py — _generate_sync / _call_model / run_copilot remainder
# =========================================================================== #


def _patch_fake_genai(monkeypatch):
    """Hermetic google.genai fake: patch sys.modules AND the package attribute."""
    import google

    fake_types = types.ModuleType("google.genai.types")
    captured: dict = {}
    fake_types.GenerateContentConfig = lambda **kw: captured.update(kw) or SimpleNamespace(
        **kw
    )

    fake_genai = types.ModuleType("google.genai")
    fake_genai.__path__ = []
    fake_genai.types = fake_types
    client_holder: dict = {}

    class _Models:
        def generate_content(self, **kwargs):
            captured["call"] = kwargs
            return SimpleNamespace(text="  hello staff  ")

    class _Client:
        def __init__(self, **kwargs):
            client_holder["init"] = kwargs
            self.models = _Models()

    fake_genai.Client = _Client
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", fake_types)
    monkeypatch.setattr(google, "genai", fake_genai, raising=False)
    return captured, client_holder


def test_generate_sync_builds_vertex_call(monkeypatch):
    """L477-491: the blocking Gemini call assembles client + config + model."""
    captured, client_holder = _patch_fake_genai(monkeypatch)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "tho-ai-agent")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.setenv("COPILOT_MODEL", "gemini-c11c-test")

    contents = [{"role": "user", "parts": [{"text": "hi"}]}]
    reply = ops_copilot._generate_sync("sys-instr", contents)

    assert reply == "hello staff"
    assert client_holder["init"] == {
        "vertexai": True,
        "project": "tho-ai-agent",
        "location": "us-central1",
    }
    assert captured["system_instruction"] == "sys-instr"
    assert captured["call"]["model"] == "gemini-c11c-test"
    assert captured["call"]["contents"] == contents
    assert captured["temperature"] == 0.3
    assert captured["max_output_tokens"] == 1024


def test_call_model_runs_generate_sync_in_threadpool(monkeypatch):
    """L496: _call_model delegates to _generate_sync via run_in_threadpool."""
    _patch_fake_genai(monkeypatch)

    reply = asyncio.run(ops_copilot._call_model("sys", []))
    assert reply == "hello staff"


def test_run_copilot_fallback_when_model_returns_empty_reply(monkeypatch):
    """L527: an empty model reply gets the friendly not-sure fallback."""
    _wire_snapshot_fakes(monkeypatch)

    async def _empty(_system_instruction, _contents):
        return ""

    monkeypatch.setattr(ops_copilot, "_call_model", _empty)
    out = asyncio.run(ops_copilot.run_copilot("how many leads today"))
    assert out["error"] is False
    assert "not sure how to help" in out["reply"]
