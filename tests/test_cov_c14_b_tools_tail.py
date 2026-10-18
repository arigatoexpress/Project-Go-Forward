"""Coverage campaign c14-b: close the last tail gaps in six tools modules.

Targets (main-venv full-suite missing lines):
- tools/asset_scraper.py:    1216-1223, 1255, 1274, 1286
- tools/catalog_floorplans.py: 33, 60-61, 71, 109-111, 165, 186, 212, 221, 279, 347, 378-380
- tools/document_rag.py:     60-63, 66-69, 98 (Vertex AI client, no network: fake module)
- tools/inventory_dedupe.py: 84, 124, 154, 228, 239
                             (77 `_photo_count` / 190 `_primary_rank` have zero
                             callers anywhere in the repo — dead code, documented
                             in the campaign report, not force-tested)
- tools/partner_webhooks.py: 177-178, 246, 264-265, 324, 344-345
- tools/photo_classifier.py: 115, 140, 148, 220-221, 223, 241, 261, 303, 357,
                             377, 381, 553, 582-583, 604, 613

No network: requests.post is monkeypatched; vertexai is a fake sys.modules entry.
Each test is self-contained (pytest-randomly is not installed).
"""

from __future__ import annotations

import os
import sys
import time
import types
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import (  # noqa: E402
    partner_webhooks,
    photo_classifier,
)
from tools.asset_scraper import (  # noqa: E402
    PROPERTY_ASSETS,
    get_all_assets,
    get_assets_for_home,
    get_homes_with_tours,
    get_matterport_url,
)
from tools.catalog_floorplans import (  # noqa: E402
    _catalog_key,
    _clean_tag,
    _looks_like_duplicate,
    _preserve_legacy_url_aliases,
    _sanitize_public_description,
    _to_int,
    build_orderable_catalog_from_context,
    build_orderable_home,
    merge_orderable_floorplan_catalog,
)
from tools.document_rag import DocumentRAG, VertexAIEmbeddingClient  # noqa: E402
from tools.inventory_dedupe import (  # noqa: E402
    annotate_possible_duplicates,
    is_orderable_new,
    offerings_conflict,
)

CDN = "https://d132mt2yijm03y.cloudfront.net"
MFR_CATALOG_PHOTO = f"{CDN}/manufacturer/3327/floorplan/224354/S-1672-32B-1.jpg"
UNIT_PHOTO = f"{CDN}/dealer/3522/inventory/30643/kitchen.jpg"


# ─── tools/asset_scraper.py ─────────────────────────────────────────────────


def test_get_all_assets_returns_full_catalog_with_totals():
    result = get_all_assets()

    assert result["success"] is True
    assert result["properties"] is PROPERTY_ASSETS
    assert result["total"] == len(PROPERTY_ASSETS)
    assert result["total_images"] == sum(
        len(p.get("images", [])) for p in PROPERTY_ASSETS.values()
    )
    assert result["with_matterport"] == sum(
        1 for p in PROPERTY_ASSETS.values() if p.get("matterport_id")
    )
    assert result["with_images"] == sum(1 for p in PROPERTY_ASSETS.values() if p.get("images"))
    assert result["cdn_base"] and result["matterport_base"]


def test_get_assets_for_home_resolves_inventory_alias():
    # "select legacy s-2468-42a" is an INVENTORY_ALIASES key → select-legacy-s-2468
    asset = get_assets_for_home("Select Legacy S-2468-42A")
    assert asset is PROPERTY_ASSETS["select-legacy-s-2468"]


def test_get_assets_for_home_direct_slug_match():
    asset = get_assets_for_home("the-nassau")
    assert asset is PROPERTY_ASSETS["the-nassau"]


def test_get_assets_for_home_partial_word_match():
    # Neither an alias nor a containment match; one meaningful word overlaps.
    asset = get_assets_for_home("My Whitehaven Home")
    assert asset is PROPERTY_ASSETS["the-whitehaven"]
    assert get_assets_for_home("No Such Home XYZ QRS") is None


def test_get_homes_with_tours_and_matterport_url():
    tours = get_homes_with_tours()
    assert tours, "catalog is expected to contain at least one 3D tour"
    for entry in tours:
        assert entry["matterport_id"]
        assert entry["matterport_url"] == get_matterport_url(entry["matterport_id"])
        assert entry["matterport_url"].startswith("https://my.matterport.com/show/?m=")
    assert get_matterport_url("tour123").endswith("m=tour123&play=1")


# ─── tools/catalog_floorplans.py ────────────────────────────────────────────


def test_sanitize_public_description_empty_returns_empty():
    assert _sanitize_public_description("") == ""
    assert _sanitize_public_description(None) == ""


def test_to_int_coercion_and_failure():
    assert _to_int(None) is None
    assert _to_int("") is None
    assert _to_int("16") == 16
    assert _to_int(28.0) == 28
    assert _to_int("not-a-number") is None  # ValueError branch
    assert _to_int(object()) is None  # TypeError branch


def test_clean_tag_empty_and_url_handling():
    assert _clean_tag("") == ""
    assert _clean_tag(None) == ""
    # Non-THO URL is not a display tag.
    assert _clean_tag("https://other-site.example/plan-one.jpg") == ""
    assert _clean_tag("random words  tag") == "random words tag"


def test_catalog_key_falls_back_to_model_pair():
    home = {"manufacturer": "Champion", "model_name": "Canyon Lake 16763B"}
    assert _catalog_key(home) == "model:champion:canyon lake 16763b"
    assert _catalog_key({"plan_id": "224354"}) == "plan:224354"


def test_looks_like_duplicate_rejects_short_model_names():
    left = {"manufacturer": "New Vision", "model_name": "ABC"}
    right = {"manufacturer": "New Vision Homes", "model_name": "ABCDE K"}
    # Normalized model keys under 6 chars can never match by name similarity.
    assert _looks_like_duplicate(left, right) is False
    # Identical long model + overlapping manufacturer still matches.
    near = {"manufacturer": "New Vision Homes", "model_name": "ABCDE K Extended"}
    assert _looks_like_duplicate(right, near) is True


def test_preserve_legacy_url_aliases_collects_and_dedupes():
    survivor = {
        "plan_id": "p1",
        "detail_url": "https://tho.example/homes/canyon",
        "quote_url": "https://tho.example/quote/canyon",
        "legacy_detail_aliases": ["https://tho.example/old-detail", ""],
        "legacy_quote_aliases": ["https://tho.example/old-quote"],
    }
    duplicate = {
        "plan_id": "p1",
        "detail_url": "https://tho.example/homes/canyon",  # == canonical → skipped
        "quote_url": "https://tho.example/old-quote",  # already collected → skipped
        "legacy_detail_aliases": ["https://tho.example/older-detail", 123],  # non-str dropped
        "legacy_quote_aliases": ["https://tho.example/older-quote", None],
    }

    _preserve_legacy_url_aliases(survivor, duplicate)

    assert survivor["legacy_detail_aliases"] == [
        "https://tho.example/old-detail",
        "https://tho.example/older-detail",
    ]
    assert survivor["legacy_quote_aliases"] == [
        "https://tho.example/old-quote",
        "https://tho.example/older-quote",
    ]


def test_preserve_legacy_url_aliases_requires_strong_identity():
    survivor = {"detail_url": "https://tho.example/a"}
    duplicate = {"model_name": "Unrelated", "detail_url": "https://tho.example/b"}
    _preserve_legacy_url_aliases(survivor, duplicate)
    assert "legacy_detail_aliases" not in survivor


def test_build_orderable_home_rejects_preowned_manufacturer():
    asset = {"is_new": True, "manufacturer": "Pre-Owned ", "name": "Used Plan"}
    assert build_orderable_home("used-plan", asset) is None


def test_build_orderable_catalog_from_context_skips_non_dict_rows():
    context = {
        "homes": [
            {"id": "h1", "model_name": "The Vail", "manufacturer": "New Vision"},
            "not-a-home-row",
            None,
        ]
    }
    homes = build_orderable_catalog_from_context(context)
    assert len(homes) == 1
    assert homes[0]["id"] == "h1"


def test_merge_orderable_catalog_defaults_to_local_asset_catalog():
    merged = merge_orderable_floorplan_catalog({"homes": []}, assets=None)
    assert merged["orderable_floorplans"] == merged["total_inventory"]
    assert merged["orderable_floorplans"] > 0
    assert merged["orderable_floorplans_added"] == merged["orderable_floorplans"]


# ─── tools/document_rag.py ──────────────────────────────────────────────────


class _FakeEmbedding:
    def __init__(self, values):
        self.values = values


class _FakeTextEmbeddingModel:
    def __init__(self, name):
        self.name = name

    @classmethod
    def from_pretrained(cls, model_name):
        return cls(model_name)

    def get_embeddings(self, texts):
        return [_FakeEmbedding([1.0, 0.0, 0.0]) for _ in texts]


@pytest.fixture
def fake_vertexai(monkeypatch):
    """Install a network-free ``vertexai.language_models`` package."""
    vertexai = types.ModuleType("vertexai")
    language_models = types.ModuleType("vertexai.language_models")
    language_models.TextEmbeddingModel = _FakeTextEmbeddingModel
    vertexai.language_models = language_models
    monkeypatch.setitem(sys.modules, "vertexai", vertexai)
    monkeypatch.setitem(sys.modules, "vertexai.language_models", language_models)
    return language_models


def test_vertex_client_lazy_imports_and_embeds(fake_vertexai):
    client = VertexAIEmbeddingClient()
    assert client.model_name == "text-embedding-004"
    assert client._model.name == "text-embedding-004"

    empty = client.embed([])
    assert empty.shape == (0, 768)
    assert empty.dtype == np.float32

    rows = client.embed(["alpha", "beta"])
    assert rows.shape == (2, 3)
    assert rows.dtype == np.float32
    assert rows[1].tolist() == [1.0, 0.0, 0.0]


def test_embedding_client_property_builds_default(fake_vertexai, tmp_path):
    rag = DocumentRAG(index_dir=tmp_path)
    assert rag._embedding_client is None
    client = rag.embedding_client
    assert isinstance(client, VertexAIEmbeddingClient)
    assert client.model_name == "text-embedding-004"
    assert rag.embedding_client is client  # cached after first build


# ─── tools/inventory_dedupe.py ──────────────────────────────────────────────


def test_offerings_conflict_reads_nested_pricing_dict():
    left = {"pricing": {"price_value": 100000}}
    right = {"pricing": {"price_value": 120000}}
    assert offerings_conflict(left, right) is True
    assert offerings_conflict(left, {"pricing": {"price_value": 100000}}) is False
    # pricing present but not a dict → ignored, no conflict
    assert offerings_conflict({"pricing": "n/a"}, {"pricing": {"price_value": 1}}) is False


def test_offerings_conflict_preowned_vs_orderable_either_order():
    orderable = {"status": "Orderable", "inventory_kind": "orderable_floorplan"}
    preowned = {"model_name": "PRE-OWNED / Big Blue"}
    assert offerings_conflict(orderable, preowned) is True
    assert offerings_conflict(preowned, orderable) is True


def test_is_orderable_new_via_explicit_flag():
    assert is_orderable_new({"is_orderable": True}) is True
    assert is_orderable_new({"is_orderable": False}) is False
    assert is_orderable_new({"status": "Available"}) is False


def test_is_preowned_detects_prefix_in_model_name():
    from tools.inventory_dedupe import is_preowned

    # Status containing "pre"+"owned" with no other signal.
    assert is_preowned({"status": "PRE-OWNED"}) is True
    # Model-name regex is the last resort.
    assert is_preowned({"model_name": "PRE-OWNED / Big Blue"}) is True
    assert is_preowned({"model_name": "preowned special"}) is True
    assert is_preowned({"model_name": "Big Blue"}) is False


def test_catalog_survivor_borrows_photos_and_floorplans():
    """Non-stocked catalog survivor merges donor photos / floorplan_urls."""
    donor_photo = "https://tho.example/media/donor-kitchen.jpg"
    homes = [
        {
            "id": "fs-big-blue",
            "model_name": "PRE-OWNED / Big Blue",
            "status": "AVAILABLE",
            "real_photos": ["https://tho.example/media/own-exterior.jpg"],
        },
        {
            "id": "floorplan-99",  # not numeric → not a stocked listing
            "model_name": "Big Blue",
            "status": "AVAILABLE",
            "photos": [donor_photo],
            "floorplan_urls": ["https://tho.example/media/plan.pdf"],
        },
    ]

    annotate_possible_duplicates(homes)

    primary = next(h for h in homes if h["id"] == "fs-big-blue")
    donor = next(h for h in homes if h["id"] == "floorplan-99")
    assert donor["duplicate_of"] == "fs-big-blue"
    merged = ["https://tho.example/media/own-exterior.jpg", donor_photo]
    assert primary["real_photos"] == merged
    # "photos" existed on the donor → mirror written on the survivor
    assert primary["photos"] == merged
    assert primary["gallery_images"] == merged[:3]
    assert primary["image_url"] == merged[0]
    # donor floorplan_urls backfilled onto the survivor
    assert primary["floorplan_urls"] == ["https://tho.example/media/plan.pdf"]


def test_stocked_survivor_keeps_only_own_photos():
    homes = [
        {
            "id": "44490",  # numeric → stocked lot listing
            "model_name": "PRE-OWNED / Big Blue",
            "status": "AVAILABLE",
            "real_photos": ["https://tho.example/media/lot-photo.jpg"],
        },
        {
            "id": "floorplan-99",
            "model_name": "Big Blue",
            "status": "AVAILABLE",
            "real_photos": [MFR_CATALOG_PHOTO],
        },
    ]
    annotate_possible_duplicates(homes)
    primary = next(h for h in homes if h["id"] == "44490")
    assert primary["real_photos"] == ["https://tho.example/media/lot-photo.jpg"]
    assert "photos" not in primary


# ─── tools/partner_webhooks.py ──────────────────────────────────────────────


@pytest.fixture
def clean_webhook_env(monkeypatch):
    """Strip inherited PARTNER_WEBHOOK_* env and reset the unsigned guard."""
    partner_webhooks._reset_unsigned_guard_for_tests()
    for name in list(os.environ):
        if name.startswith("PARTNER_WEBHOOK_"):
            monkeypatch.delenv(name, raising=False)


class _Resp:
    status_code = 200
    text = ""


def test_verify_v2_rejects_newline_header_values():
    now = time.time()
    headers = {
        "X-THO-Signature-V2": "v2=deadbeef",
        "X-THO-Timestamp": str(int(now)),
        "X-THO-Event": "bad\nevent",  # signed_message_v2 raises ValueError
        "X-THO-Partner": "etai",
        "X-THO-Delivery": "d1",
    }
    assert partner_webhooks.verify_partner_webhook(headers, b"{}", "key", now=now) is False


def test_deliver_one_without_signing_key_sends_unsigned_headers(monkeypatch):
    seen = {}

    def _post(url, data=None, headers=None, timeout=None):
        seen["url"] = url
        seen["headers"] = dict(headers)
        return _Resp()

    monkeypatch.setattr(partner_webhooks.requests, "post", _post)

    partner_webhooks._deliver_one(
        "etai", "https://example.com/hook", "deal.funded", {"id": 1}, "", None, 1.0
    )

    assert seen["url"] == "https://example.com/hook"
    assert seen["headers"]["X-THO-Event"] == "deal.funded"
    assert seen["headers"]["X-THO-Partner"] == "etai"
    assert seen["headers"]["X-THO-Delivery"]
    assert "X-THO-Timestamp" not in seen["headers"]
    assert "X-THO-Signature" not in seen["headers"]  # unsigned branch
    assert "X-THO-Signature-V2" not in seen["headers"]


def test_deliver_one_surfaces_unexpected_exception_types(monkeypatch, caplog):
    def _raise(*a, **kw):
        raise ValueError("boom")  # not a requests.exceptions.RequestException

    monkeypatch.setattr(partner_webhooks.requests, "post", _raise)

    with caplog.at_level("WARNING"):
        partner_webhooks._deliver_one(
            "etai", "https://example.com/hook", "deal.funded", {}, "signing-key", None, 1.0
        )

    assert any("ValueError: boom" in r.getMessage() for r in caplog.records)


def test_dispatch_unknown_partner_filter_returns_empty(monkeypatch, clean_webhook_env):
    monkeypatch.setenv("PARTNER_WEBHOOK_URL_ETAI", "https://example.com/etai")
    monkeypatch.setenv("PARTNER_WEBHOOK_SIGNING_KEY", "k")
    posted = []
    monkeypatch.setattr(
        partner_webhooks.requests, "post", lambda *a, **kw: posted.append(1) or _Resp()
    )

    result = partner_webhooks.dispatch_partner_event("deal.funded", {}, partner_ids=["nope"])

    assert result == []
    assert posted == []


def test_dispatch_blocking_logs_future_errors(monkeypatch, clean_webhook_env, caplog):
    monkeypatch.setenv("PARTNER_WEBHOOK_URL_ETAI", "https://example.com/etai")
    monkeypatch.setenv("PARTNER_WEBHOOK_SIGNING_KEY", "k")

    def _boom(*a, **kw):
        raise RuntimeError("worker exploded")

    monkeypatch.setattr(partner_webhooks, "_deliver_one", _boom)

    with caplog.at_level("WARNING"):
        partners = partner_webhooks.dispatch_partner_event("deal.funded", {}, blocking=True)

    assert partners == ["etai"]
    assert any("worker exploded" in r.getMessage() for r in caplog.records)


def test_log_delivery_swallows_firestore_failures(monkeypatch, caplog):
    class _BrokenDB:
        def collection(self, name):
            raise ConnectionError("emulator down")

    with caplog.at_level("WARNING"):
        partner_webhooks._log_delivery(
            _BrokenDB(), "etai", "deal.funded", "d1", 200, True, None, "https://x"
        )

    assert any("activities/ log failed" in r.getMessage() for r in caplog.records)


# ─── tools/photo_classifier.py ──────────────────────────────────────────────


def test_looks_like_photo_filename_empty_stem():
    assert photo_classifier._looks_like_photo_filename(".jpg") is False
    assert photo_classifier._looks_like_photo_filename("S-1672-32B-1.jpg") is True


def test_bare_model_floorplan_rejects_non_image_extension():
    url = f"{CDN}/manufacturer/3328/floorplan/234947/plan"
    assert photo_classifier._looks_like_bare_model_floorplan(url, "plan.gif") is False
    assert photo_classifier._looks_like_bare_model_floorplan(url, "jackson.jpg") is True


def test_placeholder_url_guards():
    assert photo_classifier._is_placeholder_url(None) is False
    assert photo_classifier._is_placeholder_url("") is False
    assert photo_classifier._is_placeholder_url(123) is False
    assert photo_classifier._is_placeholder_url("https://x/photo.jpg") is False
    assert photo_classifier._is_placeholder_url("https://x/placeholder-home.jpg") is True


def test_generic_stock_photo_url_www_and_malformed():
    assert photo_classifier.is_generic_stock_photo_url("https://www.unsplash.com/p/1.jpg") is True
    # urlparse raises ValueError on a malformed URL → False, not an exception
    assert photo_classifier.is_generic_stock_photo_url("http://[::1") is False


def test_foreign_unit_photo_requires_identifiers():
    assert photo_classifier.home_photo_ids(None) == set()
    assert photo_classifier.home_photo_ids("nope") == set()
    url = f"{CDN}/dealer/3522/inventory/30643/photo.jpg"
    assert photo_classifier.is_foreign_unit_photo_url(url, {}) is False
    assert photo_classifier.is_foreign_unit_photo_url(url, {"id": "99999"}) is True
    assert photo_classifier.is_foreign_unit_photo_url(url, {"id": "30643"}) is False


def test_normalize_url_and_as_url_list_guards():
    assert photo_classifier._normalize_url(None) == ""
    assert photo_classifier._normalize_url(42) == ""
    assert photo_classifier._as_url_list(42) == []
    assert photo_classifier._as_url_list("https://x/a.jpg") == ["https://x/a.jpg"]
    assert photo_classifier._as_url_list(None) == []


def test_normalize_image_categories_skips_bad_values():
    categories = {
        "interior": [UNIT_PHOTO, "", 123, None, "the-plan-floor-plans.jpg"],
        "exterior": "not-a-list",
        "gallery": [],
    }
    normalized = photo_classifier._normalize_image_categories(categories, [UNIT_PHOTO])
    assert normalized == {"interior": [UNIT_PHOTO]}


def test_strip_catalog_photos_guards_and_category_cleanup():
    assert photo_classifier.strip_manufacturer_catalog_photos(None) is None

    home = {
        "image_url": MFR_CATALOG_PHOTO,
        "real_photos": [MFR_CATALOG_PHOTO, UNIT_PHOTO],
        "image_categories": {
            "gallery": [MFR_CATALOG_PHOTO],
            "note": "keep-me-raw",  # non-list value hits the preserve branch
        },
    }
    result = photo_classifier.strip_manufacturer_catalog_photos(home)
    assert result is home
    assert home["real_photos"] == [UNIT_PHOTO]
    assert home["image_url"] == UNIT_PHOTO
    # non-list category survives the strip loop but is dropped by the
    # classifier's stricter normalization afterwards
    assert home["image_categories"] == {}


def test_strip_preserves_saved_floorplans_and_merges_classifier_found():
    floorplan_url = f"{CDN}/manufacturer/3335/floorplan/225053/the-big-steve-floor-plans.jpg"
    saved = "https://tho.example/media/saved-plan.pdf"
    home = {
        "image_url": MFR_CATALOG_PHOTO,
        "real_photos": [MFR_CATALOG_PHOTO, floorplan_url],
        "floorplan_urls": [saved],
    }
    photo_classifier.strip_manufacturer_catalog_photos(home)
    # The classifier found `floorplan_url` inside the photo list; it is
    # appended to the preserved saved_floorplans list (merge branch).
    assert home["floorplan_urls"] == [saved, floorplan_url]
    assert home["floorplan_url"] == floorplan_url
    assert home["floor_plan_url"] == floorplan_url


def test_get_real_photo_urls_guard():
    assert photo_classifier.get_real_photo_urls(None) == []
    assert photo_classifier.get_real_photo_urls("nope") == []
    assert photo_classifier.has_real_photo(None) is False
