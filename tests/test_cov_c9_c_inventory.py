"""Coverage campaign C — inventory_tools import fallbacks / loaders / filters
and inventory_media_enrichment scraping / run() / main() plumbing.

All Firestore and HTTP access is faked; no network, no emulator.
"""

from __future__ import annotations

import builtins
import csv
import importlib
import importlib.util
import json
import os
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

from tools import inventory_media_enrichment as ime
from tools import inventory_tools

REPO_ROOT = Path(__file__).resolve().parent.parent


def _patch_asset_scraper(monkeypatch, assets, matterport_url=None):
    """Force both absolute and relative asset_scraper imports to a fake."""
    fake = types.SimpleNamespace(
        PROPERTY_ASSETS=assets,
        get_matterport_url=matterport_url or (lambda mid: f"https://mp.example/{mid}"),
    )
    monkeypatch.setitem(sys.modules, "tools.asset_scraper", fake)
    monkeypatch.setattr(sys.modules["tools"], "asset_scraper", fake, raising=False)
    return fake


# --------------------------------------------------------------------------
# inventory_tools: small pure helpers
# --------------------------------------------------------------------------


def test_to_number_rejects_bad_and_nonpositive_values():
    assert inventory_tools._to_number("not-a-number") is None  # L43-44
    assert inventory_tools._to_number(object()) is None  # L43 TypeError
    assert inventory_tools._to_number(-5) is None  # L46
    assert inventory_tools._to_number(0) is None  # L46
    assert inventory_tools._to_number("3.0") == 3
    assert inventory_tools._to_number("2.5") == 2.5


# --------------------------------------------------------------------------
# inventory_tools: Firestore loader branches
# --------------------------------------------------------------------------


def _patch_firestore_db(monkeypatch, database):
    # tools is a top-level package, so the relative "..database.firestore_client"
    # import always fails; only the absolute one consults sys.modules.
    fake_module = types.SimpleNamespace(get_database=lambda: database)
    monkeypatch.setitem(sys.modules, "database.firestore_client", fake_module)


def _tier_home(home_id, price=None, msrp=None, **overrides):
    home = {
        "id": home_id,
        "model_name": f"Model {home_id}",
        "status": "AVAILABLE",
        "is_new": True,
        "width": 16,
        "bedrooms": 2,
        "bathrooms": 2,
        "sqft": 900,
    }
    if price is not None:
        home["sale_price"] = price
    if msrp is not None:
        home["msrp"] = msrp
    home.update(overrides)
    return home


def test_firestore_loader_covers_price_tiers_msrp_and_preowned(monkeypatch):
    class FakeDatabase:
        def search_inventory(self, status, limit):
            assert status == "AVAILABLE"
            assert limit == 100
            return [
                _tier_home("t1", price=40000),
                _tier_home("t2", price=60000),
                _tier_home("t3", price=90000),
                _tier_home("t4", price=120000),
                _tier_home("t5", price=200000),
                _tier_home("t6", msrp=80000),  # sale_price missing -> msrp
                _tier_home("t7", price=0, msrp=0),  # call for price
                _tier_home("t8", price=55000, is_new=False),  # -> Pre-Owned
                _tier_home("t9", price=55000, width=28),  # Double Wide by width
            ]

    _patch_firestore_db(monkeypatch, FakeDatabase())

    homes = inventory_tools._load_inventory_from_firestore()

    tiers = {h["id"]: h["pricing"]["price_tier"] for h in homes}
    assert tiers["t1"] == "Under $50k"
    assert tiers["t2"] == "$50k-$75k"
    assert tiers["t3"] == "$75k-$100k"
    assert tiers["t4"] == "$100k-$150k"
    assert tiers["t5"] == "$150k+"
    assert tiers["t6"] == "$75k-$100k"  # msrp fallback
    assert tiers["t7"] == "Under $50k"
    assert homes[6]["pricing"]["display_price"] == "Call for Price"
    assert next(h for h in homes if h["id"] == "t8")["status"] == "Pre-Owned"
    assert next(h for h in homes if h["id"] == "t9")["classification"] == "Double Wide"


def test_firestore_loader_empty_results_returns_authoritative_empty_list(monkeypatch):
    class FakeDatabase:
        def search_inventory(self, status, limit):
            return []

    _patch_firestore_db(monkeypatch, FakeDatabase())

    assert inventory_tools._load_inventory_from_firestore() == []


def test_firestore_loader_marks_inactive_for_all_status_publication_query(monkeypatch):
    class FakeDatabase:
        def search_inventory(self, status, limit):
            assert status == ""
            assert limit == 500
            return [_tier_home("old-1", price=10000, status="SOLD")]

    _patch_firestore_db(monkeypatch, FakeDatabase())

    homes = inventory_tools._load_inventory_from_firestore(status="")

    assert homes[0]["status"] == "INACTIVE"


def test_firestore_loader_returns_none_when_database_raises(monkeypatch):
    class FakeDatabase:
        def search_inventory(self, status, limit):
            raise RuntimeError("emulator down")

    _patch_firestore_db(monkeypatch, FakeDatabase())

    assert inventory_tools._load_inventory_from_firestore() is None


def test_firestore_loader_photo_classifier_relative_import_fallback(monkeypatch):
    """Absolute `tools.photo_classifier` import fails; relative one succeeds."""

    class FakeDatabase:
        def search_inventory(self, status, limit):
            return [_tier_home("p1", price=50000)]

    _patch_firestore_db(monkeypatch, FakeDatabase())

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "tools.photo_classifier" and level == 0:
            raise ImportError("simulated absolute-import failure")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    homes = inventory_tools._load_inventory_from_firestore()

    assert [h["id"] for h in homes] == ["p1"]


def test_json_loader_photo_classifier_relative_import_fallback(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "inventory.json").write_text(json.dumps([{"id": "j1", "model_name": "J"}]))
    monkeypatch.chdir(tmp_path)

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "tools.photo_classifier" and level == 0:
            raise ImportError("simulated absolute-import failure")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    result = inventory_tools._load_inventory_from_json()

    assert [home["id"] for home in result] == ["j1"]


def test_load_website_homes_relative_asset_scraper_import_fallback(monkeypatch):
    assets = {
        "w1": {"name": "Web One", "is_new": True, "images": ["https://cdn.example/a.jpg"]},
    }
    _patch_asset_scraper(monkeypatch, assets)

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "tools.asset_scraper" and level == 0:
            raise ImportError("simulated absolute-import failure")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    homes = inventory_tools._load_website_homes()

    assert [home["id"] for home in homes] == ["w1"]


# --------------------------------------------------------------------------
# inventory_tools: publication merge / JSON fallback / website homes / cache
# --------------------------------------------------------------------------


def test_load_inventory_for_publication_merges_and_skips_managed_ids(monkeypatch):
    managed = [
        {"id": "web-1", "model_name": "Web One", "status": "Available"},
        {"id": "mgr-1", "model_name": "Managed One", "status": "Pre-Owned"},
        {"id": "ret-1", "model_name": "Retired One", "status": "INACTIVE"},
    ]
    website = [
        {"id": "web-1", "model_name": "Web One Duplicate", "status": "Available"},
        {"id": "web-2", "model_name": "Web Two", "status": "Available"},
    ]
    monkeypatch.setattr(
        inventory_tools, "_load_inventory_from_firestore", lambda **kw: managed
    )
    monkeypatch.setattr(inventory_tools, "_load_website_homes", lambda: website)

    result = inventory_tools._load_inventory_for_publication()

    ids = [h["id"] for h in result]
    assert ids == ["web-1", "mgr-1", "web-2"]  # INACTIVE dropped, duplicate id skipped


def test_load_inventory_for_publication_returns_none_when_firestore_fails(monkeypatch):
    monkeypatch.setattr(inventory_tools, "_load_inventory_from_firestore", lambda **kw: None)

    assert inventory_tools._load_inventory_for_publication() is None


def test_load_inventory_from_json_missing_file_returns_none(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)  # none of the candidate paths exist here

    assert inventory_tools._load_inventory_from_json() is None
    assert "JSON data file not found" in capsys.readouterr().out


def test_load_inventory_from_json_reads_and_classifies(tmp_path, monkeypatch):
    data = [
        {"id": "j1", "model_name": "Json One"},
        {"id": "j2", "model_name": "Json Two"},
    ]
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "inventory.json").write_text(json.dumps(data))
    monkeypatch.chdir(tmp_path)  # candidate path "data/inventory.json" resolves here

    result = inventory_tools._load_inventory_from_json()

    assert [home["id"] for home in result] == ["j1", "j2"]
    # The classifier annotates each loaded home with normalized media fields.
    assert result[0]["media_quality"]["status"] == "missing_photos"


def test_load_inventory_from_json_invalid_content_returns_none(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "inventory.json").write_text("{not valid json")
    monkeypatch.chdir(tmp_path)

    assert inventory_tools._load_inventory_from_json() is None


def test_load_website_homes_maps_assets_with_variants(monkeypatch):
    assets = {
        "new-dw": {
            "name": "Big Double",
            "manufacturer": "New Vision Manufacturing",
            "dims": "32x76",
            "beds": 4,
            "baths": 2,
            "sqft": 1800,
            "is_new": True,
            "images": ["https://cdn.example/a.jpg", "https://cdn.example/b.jpg"],
            "floorplan": "https://cdn.example/fp.jpg",
            "matterport_id": "mp-1",
        },
        "bad-dims": {
            "name": "Weird Dims",
            "dims": "ABx32",  # int("AB") raises ValueError -> width stays 0
            "is_new": False,
            "images": [],
        },
        "plain": {
            "name": "Plain",
            "is_new": False,
            "images": ["https://cdn.example/c.jpg"],
        },
    }
    _patch_asset_scraper(monkeypatch, assets)

    homes = inventory_tools._load_website_homes()

    by_id = {h["id"]: h for h in homes}
    new_dw = by_id["new-dw"]
    assert new_dw["classification"] == "Double Wide"
    assert new_dw["status"] == "Available"
    assert new_dw["specs"]["dimensions"] == "32x76"
    assert new_dw["image_url"] == "https://cdn.example/a.jpg"
    assert new_dw["matterport_url"] == "https://mp.example/mp-1"
    assert by_id["bad-dims"]["classification"] == "Single Wide"  # ValueError -> width 0
    assert by_id["bad-dims"]["status"] == "Pre-Owned"
    assert by_id["bad-dims"]["image_url"] == ""
    assert by_id["plain"]["matterport_url"] is None
    assert all(h["source"] == "website" for h in homes)


def test_load_website_homes_returns_empty_when_scraper_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "tools.asset_scraper", None)

    assert inventory_tools._load_website_homes() == []


def test_load_inventory_cache_hit_short_circuits(monkeypatch):
    cached = [{"id": "cached"}]
    calls = {"set": 0}
    monkeypatch.setattr(inventory_tools, "cache_get", lambda key: cached)
    monkeypatch.setattr(
        inventory_tools, "cache_set", lambda *a, **k: calls.__setitem__("set", calls["set"] + 1)
    )

    assert inventory_tools._load_inventory() is cached
    assert calls["set"] == 0


def test_load_inventory_healthy_result_is_cached_and_merges_website(monkeypatch):
    captured = {}
    monkeypatch.setattr(inventory_tools, "cache_get", lambda key: None)
    monkeypatch.setattr(
        inventory_tools,
        "cache_set",
        lambda key, value, ttl_seconds=None: captured.update(
            key=key, value=value, ttl=ttl_seconds
        ),
    )
    firestore_homes = [{"id": "f1", "model_name": "Alpha"}]
    website_homes = [
        {"id": "w1", "model_name": "alpha"},  # duplicate model name, skipped
        {"id": "w2", "model_name": "Beta"},
    ]
    monkeypatch.setattr(
        inventory_tools, "_load_inventory_from_firestore", lambda **kw: list(firestore_homes)
    )
    monkeypatch.setattr(inventory_tools, "_load_website_homes", lambda: list(website_homes))

    inventory = inventory_tools._load_inventory()

    assert [h["id"] for h in inventory] == ["f1", "w2"]
    assert captured["key"] == inventory_tools.INVENTORY_CACHE_KEY
    assert captured["ttl"] == 300


def test_load_inventory_falls_back_to_json_then_sample_and_skips_cache(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(inventory_tools, "cache_get", lambda key: None)
    monkeypatch.setattr(
        inventory_tools, "cache_set", lambda *a, **k: captured.update(called=True)
    )
    monkeypatch.setattr(inventory_tools, "_load_inventory_from_firestore", lambda **kw: None)
    monkeypatch.setattr(inventory_tools, "_load_website_homes", lambda: [])

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    json_file = data_dir / "inventory.json"
    monkeypatch.chdir(tmp_path)

    # JSON fallback path (healthy result is cached)
    json_file.write_text(json.dumps([{"id": "j1", "model_name": "Json One"}]))
    inventory = inventory_tools._load_inventory()
    assert [home["id"] for home in inventory] == ["j1"]
    assert captured == {"called": True}

    # Both real sources fail (corrupt JSON -> sample fallback, never cached)
    captured.clear()
    json_file.write_text("{corrupt")
    inventory = inventory_tools._load_inventory()
    assert inventory[0]["id"] == "tho-2024-001"
    assert captured == {}


# --------------------------------------------------------------------------
# inventory_tools: search_inventory filter branches
# --------------------------------------------------------------------------


def _search_home(home_id, **kwargs):
    home = {
        "id": home_id,
        "model_name": home_id,
        "classification": "Single Wide",
        "status": "Available",
        "specs": {"beds": 2, "baths": 2, "sq_ft": 800},
        "pricing": {"price_value": 60000, "display_price": "$60,000"},
        "features": ["island kitchen"],
    }
    home.update(kwargs)
    return home


def test_search_inventory_covers_all_filter_branches(monkeypatch):
    homes = [
        _search_home("ok"),
        _search_home("no-beds", specs={"beds": None, "baths": None, "sq_ft": 100}),
        _search_home("three-bed", specs={"beds": 3, "baths": 2, "sq_ft": 1000}),
        _search_home("pricey", pricing={"price_value": 90000, "display_price": "$90,000"}),
        _search_home("double", classification="Double Wide"),
        _search_home("preowned", status="Pre-Owned"),
        _search_home("nofeat", features=[]),
        _search_home("unpriced", pricing={"price_value": 0, "display_price": "Call"}),
    ]
    monkeypatch.setattr(inventory_tools, "_load_inventory", lambda: homes)

    # min_beds drops the no-beds home; max_beds drops three-bed
    result = inventory_tools.search_inventory(min_beds=2, max_beds=2)
    assert "no-beds" not in {h["id"] for h in result["homes"]}
    assert "three-bed" not in {h["id"] for h in result["homes"]}

    # min_baths drops homes whose inferred baths are below 2
    result = inventory_tools.search_inventory(min_baths=2)
    assert {h["id"] for h in result["homes"]} == {
        "ok",
        "three-bed",
        "pricey",
        "double",
        "preowned",
        "nofeat",
        "unpriced",
    }

    # max_budget keeps the unpriced home (price 0 is not filtered out)
    result = inventory_tools.search_inventory(max_budget=70000)
    assert "unpriced" in {h["id"] for h in result["homes"]}
    assert "pricey" not in {h["id"] for h in result["homes"]}

    # classification filter
    result = inventory_tools.search_inventory(classification="double wide")
    assert {h["id"] for h in result["homes"]} == {"double"}

    # status partial matching both directions
    result = inventory_tools.search_inventory(status="pre-owned")
    assert {h["id"] for h in result["homes"]} == {"preowned"}

    # features filter drops homes missing the feature
    result = inventory_tools.search_inventory(features=["Island Kitchen"])
    assert "nofeat" not in {h["id"] for h in result["homes"]}


def test_search_inventory_no_matches_tip(monkeypatch):
    monkeypatch.setattr(inventory_tools, "_load_inventory", lambda: [_search_home("ok")])

    result = inventory_tools.search_inventory(min_beds=9)

    assert result["homes"] == []
    assert result["tip"] == "Try broadening your search criteria."


# --------------------------------------------------------------------------
# inventory_tools: import-fallback coverage via subprocesses (no sys.modules
# pollution of the pytest process)
# --------------------------------------------------------------------------


def _run_python(code, *args):
    return subprocess.run(
        [sys.executable, "-c", code, *[str(a) for a in args]],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=120,
    )


_IMPORT_FALLBACK_CODE = textwrap.dedent(
    """
    import importlib
    import pathlib
    import sys

    repo = pathlib.Path(sys.argv[1])
    sys.path.insert(0, str(repo))
    # Initial load through tools/__init__ with real deps available.
    import tools.inventory_tools as it

    # Then force ImportError for both optional dependencies and reload so the
    # module-level except branches run in this subprocess only.
    sys.modules["google.adk.tools"] = None
    sys.modules["caching"] = None
    it = importlib.reload(it)

    assert it.ToolContext is None
    assert it.cache_get("anything") is None
    it.cache_set("k", "v")  # no-op must not raise
    print("FALLBACKS_OK")
    """
)


def test_import_fallbacks_toolcontext_and_caching_noops():
    result = _run_python(_IMPORT_FALLBACK_CODE, REPO_ROOT)

    assert result.returncode == 0, result.stderr
    assert "FALLBACKS_OK" in result.stdout


_RELATIVE_CACHING_CODE = textwrap.dedent(
    """
    import importlib.util
    import pathlib
    import sys
    import types

    repo = pathlib.Path(sys.argv[1])
    sys.modules["caching"] = None  # absolute import must fail

    # Build an "app" package whose parent-relative "..caching" import succeeds.
    app = types.ModuleType("app")
    app.__path__ = []
    tools_pkg = types.ModuleType("app.tools")
    tools_pkg.__path__ = []
    caching = types.ModuleType("app.caching")
    sentinel = object()
    caching.cache_get = lambda key: sentinel
    caching.cache_set = lambda *a, **k: None
    sys.modules.update({"app": app, "app.tools": tools_pkg, "app.caching": caching})

    spec = importlib.util.spec_from_file_location(
        "app.tools.inventory_tools", repo / "tools" / "inventory_tools.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["app.tools.inventory_tools"] = module
    spec.loader.exec_module(module)

    assert module.cache_get("x") is sentinel  # relative import won
    print("RELATIVE_OK")
    """
)


def test_import_relative_caching_fallback_when_absolute_fails():
    result = _run_python(_RELATIVE_CACHING_CODE, REPO_ROOT)

    assert result.returncode == 0, result.stderr
    assert "RELATIVE_OK" in result.stdout


_ABSENT = object()


def _restore_sys_module(name, previous):
    if previous is _ABSENT:
        sys.modules.pop(name, None)
    else:
        sys.modules[name] = previous


def test_import_fallbacks_in_process_with_sys_modules_restore():
    """In-process counterpart of the subprocess fallback test so the module
    lines earn coverage; sys.modules and module bindings are restored in a
    finally block (landmine 5)."""
    from google.adk.tools import ToolContext as real_tool_context

    import caching as real_caching

    it = inventory_tools
    saved_gt = sys.modules.get("google.adk.tools", _ABSENT)
    saved_caching = sys.modules.get("caching", _ABSENT)
    try:
        sys.modules["google.adk.tools"] = None
        sys.modules["caching"] = None
        fallback = importlib.reload(it)

        assert fallback.ToolContext is None  # L13-14
        assert fallback.cache_get("anything") is None  # L26-27 no-op
        fallback.cache_set("k", "v", ttl_seconds=60)  # L29-30 no-op
    finally:
        _restore_sys_module("google.adk.tools", saved_gt)
        _restore_sys_module("caching", saved_caching)
        restored = importlib.reload(it)

    assert restored.ToolContext is real_tool_context
    assert restored.cache_get is real_caching.cache_get
    assert it.search_inventory  # module object intact for other tests


def test_import_relative_caching_success_in_process():
    """In-process counterpart: absolute "caching" fails but the parent-package
    relative import (..caching -> app.caching) succeeds, covering L21-23."""
    sentinel = object()
    app = types.ModuleType("app")
    app.__path__ = []
    app_tools = types.ModuleType("app.tools")
    app_tools.__path__ = []
    app_caching = types.ModuleType("app.caching")
    app_caching.cache_get = lambda key: sentinel
    app_caching.cache_set = lambda *a, **k: None

    names = ("caching", "app", "app.tools", "app.caching", "app.tools.inventory_tools")
    saved = {name: sys.modules.get(name, _ABSENT) for name in names}
    try:
        sys.modules["caching"] = None
        sys.modules["app"] = app
        sys.modules["app.tools"] = app_tools
        sys.modules["app.caching"] = app_caching

        spec = importlib.util.spec_from_file_location(
            "app.tools.inventory_tools", REPO_ROOT / "tools" / "inventory_tools.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["app.tools.inventory_tools"] = module
        spec.loader.exec_module(module)

        assert module.cache_get("x") is sentinel
    finally:
        for name in names:
            _restore_sys_module(name, saved[name])


# --------------------------------------------------------------------------
# inventory_media_enrichment: small helpers
# --------------------------------------------------------------------------


def test_normalize_media_url_handles_empty():
    assert ime.normalize_media_url(None) == ""
    assert ime.normalize_media_url("") == ""


def test_is_dealer_inventory_url_without_listing_id_matches_any_dealer_asset():
    url = "https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/43372/x.jpg"

    assert ime._is_dealer_inventory_url(url) is True
    assert ime._is_dealer_inventory_url(url, None) is True
    assert ime._is_dealer_inventory_url(url, "43372") is True
    assert ime._is_dealer_inventory_url(url, "99999") is False
    assert ime._is_dealer_inventory_url("https://cdn.example/x.jpg", "43372") is False


def test_manufacturer_floorplan_prefix_returns_none_for_non_floorplan_url():
    assert ime._manufacturer_floorplan_prefix("https://cdn.example/photos/1.jpg") is None
    prefix = ime._manufacturer_floorplan_prefix(
        "https://d132mt2yijm03y.cloudfront.net/manufacturer/1944/floorplan/1383/plan.jpg"
    )
    assert prefix == "/manufacturer/1944/floorplan/1383/"


def test_categorize_photo_url_utility_and_default():
    assert ime.categorize_photo_url("https://cdn.example/home-laundry-1.jpg") == "utility"
    assert ime.categorize_photo_url("https://cdn.example/home-uti-1.jpg") == "utility"
    assert ime.categorize_photo_url("https://cdn.example/home-mystery-1.jpg") == "interior"


def test_detail_media_matterport_url_property():
    with_id = ime.DetailMedia(listing_id="1", detail_url="https://x/", matterport_id="abc")
    assert with_id.matterport_url == ime.get_matterport_url("abc")

    without_id = ime.DetailMedia(listing_id="1", detail_url="https://x/")
    assert without_id.matterport_url is None


def test_extract_detail_media_legacy_manufacturer_model_as_floorplan():
    # Dealer photos present but no recognizable floorplan URL: the legacy
    # fallback promotes the manufacturer model-name image to floorplan.
    html = """
    <img src="https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/30641/home-ext-1.jpg">
    <img src="https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/30641/home-kit-1.jpg">
    <a href="https://d132mt2yijm03y.cloudfront.net/manufacturer/3373/Creole-3256H.jpg">model</a>
    """

    media = ime.extract_detail_media(html, "30641", "https://example.com/detail")

    assert media.floorplan_url.endswith("Creole-3256H.jpg")
    assert all("dealer/3522" in url for url in media.photos)


def test_detail_media_by_model_skips_homes_without_detail_and_nonunique():
    detail_a = ime.DetailMedia(listing_id="111", detail_url="https://x/1")
    detail_b = ime.DetailMedia(listing_id="222", detail_url="https://x/2")
    detail_b_dup = ime.DetailMedia(listing_id="223", detail_url="https://x/2b")
    raw_homes = [
        {"id": "111", "model_name": "Alpha"},
        {"id": "missing", "model_name": "Ghost"},  # no detail -> skipped
        {"id": "222", "model_name": "Beta"},
        {"id": "222b", "model_name": "Beta"},  # same listing id -> not unique
    ]

    by_model = ime._detail_media_by_model(
        raw_homes, {"111": detail_a, "222": detail_b, "222b": detail_b_dup}
    )

    assert by_model == {"alpha": detail_a}


def test_select_detail_media_legacy_inventory_id_and_no_match():
    legacy_detail = ime.DetailMedia(listing_id="44490", detail_url="https://x/")
    detail_media = {"44490": legacy_detail}

    selected = ime._select_detail_media(
        "doc-1", {"legacy_inventory_id": "44490"}, detail_media, {}
    )
    assert selected is legacy_detail

    assert ime._select_detail_media("doc-2", {"model_name": "Nope"}, detail_media, {}) is None


def test_build_update_extracts_matterport_id_from_url():
    current = {
        "model_name": "Creole",
        "photos": ["https://cdn.example/home-ext-1.jpg"],
        "matterport_url": "https://my.matterport.com/show/?m=xyz123&play=1",
    }

    update = ime.build_update("43372", current, None)

    assert update["matterport_id"] == "xyz123"
    assert update["matterport_url"] == ime.get_matterport_url("xyz123")


def test_write_plan_csv_and_write_json(tmp_path):
    csv_path = tmp_path / "nested" / "plan.csv"
    rows = [
        {
            "id": "doc-1",
            "model_name": "Alpha",
            "photo_count_before": 1,
            "photo_count_after": 3,
            "matched_listing_id": "111",
            "dealer_detail_scraped": True,
            "detail_url": "https://x/",
            "matterport_after": False,
            "changed_fields": "photos",
        }
    ]
    ime._write_plan_csv(csv_path, rows)
    with csv_path.open(newline="") as handle:
        parsed = list(csv.DictReader(handle))
    assert parsed[0]["id"] == "doc-1"
    assert parsed[0]["dealer_detail_scraped"] == "True"

    json_path = tmp_path / "nested" / "data.json"
    ime._write_json(json_path, {"a": [1, 2]})
    assert json.loads(json_path.read_text()) == {"a": [1, 2]}


# --------------------------------------------------------------------------
# inventory_media_enrichment: scraping with a fake requests.Session
# --------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, text="", url="https://example.com/detail", status_error=None):
        self.text = text
        self.url = url
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error is not None:
            raise self._status_error


class _FakeSession:
    def __init__(self, responses):
        self.headers = {}
        self._responses = responses
        self.requested = []

    def get(self, url, timeout=None):
        self.requested.append(url)
        response = self._responses.get(url)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def fake_requests(monkeypatch):
    """Patch requests.Session on the inventory_media_enrichment namespace."""
    sessions = []

    def session_factory():
        session = _FakeSession(factory.responses)
        sessions.append(session)
        return session

    factory = types.SimpleNamespace(responses={}, sessions=sessions, Session=session_factory)
    monkeypatch.setattr(ime, "requests", factory)
    return factory


_SCRAPE_HTML = """
<img src="https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/43372/Creole-ext-1.jpg">
"""


def test_scrape_detail_media_success_skip_and_failure(fake_requests):
    url_ok = "https://www.texashomeoutlet.com/inventory-detail/43372/ok/"
    url_fail = "https://www.texashomeoutlet.com/inventory-detail/43372/fail/"
    fake_requests.responses.update(
        {
            url_ok: _FakeResponse(text=_SCRAPE_HTML, url=url_ok),
            url_fail: _FakeResponse(status_error=RuntimeError("http 500")),
        }
    )
    raw_homes = [
        {"id": "43372", "detail_url": url_ok},
        {"id": "", "detail_url": url_ok},  # no listing id -> skipped
        {"id": "skip-me", "detail_url": ""},  # no detail url -> skipped
        {"id": "43372-fail", "detail_url": url_fail},  # scrape error -> logged, skipped
    ]

    media = ime.scrape_detail_media(raw_homes)

    assert set(media) == {"43372"}
    assert media["43372"].photos[0].endswith("Creole-ext-1.jpg")
    assert fake_requests.sessions[0].requested == [url_ok, url_fail]


def test_scrape_known_detail_media_success_skip_and_failure(fake_requests):
    url_ok = "https://www.texashomeoutlet.com/inventory-detail/30641/select-legacy/"
    url_fail = "https://www.texashomeoutlet.com/inventory-detail/99999/broken/"
    fake_requests.responses.update(
        {
            url_ok: _FakeResponse(text=_SCRAPE_HTML, url=url_ok),
            url_fail: _FakeResponse(status_error=RuntimeError("timeout")),
        }
    )
    detail_urls = {
        "select-legacy-s-2468": url_ok,
        "no-listing": "https://www.texashomeoutlet.com/not-a-detail-page/",
        "broken": url_fail,
    }

    media = ime.scrape_known_detail_media(detail_urls)

    assert set(media) == {"select-legacy-s-2468"}
    assert media["select-legacy-s-2468"].listing_id == "30641"


# --------------------------------------------------------------------------
# inventory_media_enrichment: run() end-to-end with fakes
# --------------------------------------------------------------------------


class _FakeFirestoreDoc:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    def to_dict(self):
        return dict(self._data)


class _FakeFirestoreQuery:
    def __init__(self, docs):
        self._docs = docs

    def limit(self, _n):
        return self

    def stream(self):
        return iter(self._docs)


class _FakeDocumentRef:
    def __init__(self, client, doc_id):
        self._client = client
        self._id = doc_id

    def update(self, update):
        self._client.applied_updates.append((self._id, update))


class _FakeFirestoreCollection:
    def __init__(self, client, name):
        self._client = client
        self._name = name

    def limit(self, _n):
        return _FakeFirestoreQuery(self._client.docs_by_collection[self._name])

    def document(self, doc_id):
        return _FakeDocumentRef(self._client, doc_id)


class _FakeFirestoreClient:
    def __init__(self, docs):
        self.docs_by_collection = {"inventory": docs}
        self.applied_updates = []
        self.project = None

    def collection(self, name):
        return _FakeFirestoreCollection(self, name)


_DEALER_PHOTOS = [
    "https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/44490/Blue-ext-1.jpg",
    "https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/44490/Blue-kit-1.jpg",
]


def _detail_for(listing_id="44490"):
    return ime.DetailMedia(
        listing_id=listing_id,
        detail_url=f"https://www.texashomeoutlet.com/inventory-detail/{listing_id}/",
        photos=list(_DEALER_PHOTOS),
    )


def _patch_run_dependencies(monkeypatch, docs, detail_media=None, known_media=None):
    """Patch InventorySync, scrapers, and firestore.Client for run()."""
    detail_media = detail_media if detail_media is not None else {"44490": _detail_for()}
    known_media = known_media if known_media is not None else {}
    raw_homes = [{"id": "44490", "model_name": "Big Blue"}]

    class FakeInventorySync:
        def __init__(self, dry_run=True):
            assert dry_run is True

        def scrape_website_inventory(self):
            return list(raw_homes)

    monkeypatch.setattr(ime, "InventorySync", FakeInventorySync)
    monkeypatch.setattr(ime, "scrape_detail_media", lambda homes: dict(detail_media))
    monkeypatch.setattr(ime, "scrape_known_detail_media", lambda urls: dict(known_media))

    client = _FakeFirestoreClient(docs)

    def client_factory(project):
        client.project = project
        return client

    monkeypatch.setattr(ime, "firestore", types.SimpleNamespace(Client=client_factory))
    return client


def test_run_dry_run_writes_plan_and_backup(monkeypatch, tmp_path):
    # Pre-seed the "untouched" doc with exactly the update build_update would
    # produce, so only last_media_synced differs and the update is pruned.
    untouched_current = {"model_name": "Static", "photos": list(_DEALER_PHOTOS)}
    precomputed = ime.build_update("untouched", untouched_current, None)
    untouched_current.update(
        {key: value for key, value in precomputed.items() if key != "last_media_synced"}
    )

    docs = [
        _FakeFirestoreDoc("big-blue", {"model_name": "Big Blue", "photos": []}),
        _FakeFirestoreDoc("untouched", untouched_current),
    ]
    client = _patch_run_dependencies(monkeypatch, docs)

    summary = ime.run(
        project="test-project",
        output_dir=tmp_path,
        apply=False,
        detail_urls_by_doc_id={"extra": "https://www.texashomeoutlet.com/inventory-detail/1/x/"},
    )

    assert summary["mode"] == "DRY_RUN"
    assert summary["project"] == "test-project"
    assert summary["doc_count"] == 2
    assert summary["scraped_detail_count"] == 1
    assert summary["update_count"] == 1  # only big-blue changed
    assert summary["applied"] == 0
    assert client.applied_updates == []
    for key in ("backup_path", "plan_path", "csv_path", "summary_path"):
        assert Path(summary[key]).exists()

    plan = json.loads(Path(summary["plan_path"]).read_text())
    big_blue = next(row for row in plan["plan"] if row["id"] == "big-blue")
    assert big_blue["matched_listing_id"] == "44490"
    assert big_blue["dealer_detail_scraped"] is True
    assert big_blue["photo_count_after"] == 2


def test_run_apply_writes_changed_docs_and_only_ids_filter(monkeypatch, tmp_path):
    docs = [
        _FakeFirestoreDoc("big-blue", {"model_name": "Big Blue", "photos": []}),
        _FakeFirestoreDoc("other", {"model_name": "Other", "photos": []}),
    ]
    known_calls = {}

    class FakeInventorySync:
        def __init__(self, dry_run=True):
            pass

        def scrape_website_inventory(self):
            return [{"id": "44490", "model_name": "Big Blue"}]

    monkeypatch.setattr(ime, "InventorySync", FakeInventorySync)
    monkeypatch.setattr(ime, "scrape_detail_media", lambda homes: {"44490": _detail_for()})

    def fake_known(urls):
        known_calls["urls"] = dict(urls)
        return {"select-legacy-s-2468": _detail_for("30641")}

    monkeypatch.setattr(ime, "scrape_known_detail_media", fake_known)
    client = _FakeFirestoreClient(docs)

    def client_factory(project):
        return client

    monkeypatch.setattr(ime, "firestore", types.SimpleNamespace(Client=client_factory))

    summary = ime.run(
        project="test-project",
        output_dir=tmp_path,
        apply=True,
        only_ids={"big-blue"},
        detail_urls_by_doc_id={"select-legacy-s-2468": "https://x/", "other": "https://y/"},
    )

    assert summary["mode"] == "APPLY"
    # only_ids filters the known-detail map down to ids in the allow-list
    assert known_calls["urls"] == {}
    assert [doc_id for doc_id, _ in client.applied_updates] == ["big-blue"]
    assert summary["applied"] == 1
    assert summary["update_count"] == 1
    # "other" was skipped before plan/update work because of only_ids
    plan = json.loads(Path(summary["plan_path"]).read_text())
    assert [row["id"] for row in plan["plan"]] == ["big-blue"]


# --------------------------------------------------------------------------
# inventory_media_enrichment: main() argparse plumbing
# --------------------------------------------------------------------------


@pytest.fixture
def fake_run(monkeypatch):
    calls = []

    def run_fake(**kwargs):
        calls.append(kwargs)
        return {"mode": kwargs["apply"] and "APPLY" or "DRY_RUN", "stub": True}

    monkeypatch.setattr(ime, "run", run_fake)
    return calls


def test_main_dry_run_defaults_and_output_dir(fake_run, tmp_path, capsys):
    exit_code = ime.main(["--dry-run", "--output-dir", str(tmp_path)])

    assert exit_code == 0
    assert fake_run[0]["apply"] is False
    assert fake_run[0]["only_ids"] is None
    assert fake_run[0]["detail_urls_by_doc_id"] == {}
    printed = json.loads(capsys.readouterr().out)
    assert printed == {"mode": "DRY_RUN", "stub": True}


def test_main_no_flags_defaults_to_dry_run(fake_run, tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "env-project")
    monkeypatch.setenv("THO_LOCAL_DATA_DIR", str(tmp_path))
    ime.main(["-v"])

    assert fake_run[0]["apply"] is False
    assert fake_run[0]["project"] == "env-project"


def test_main_apply_with_only_ids_and_detail_urls(fake_run):
    exit_code = ime.main(
        [
            "--apply",
            "--only-id",
            "doc-a",
            "--only-id",
            "doc-b",
            "--detail-url",
            "doc-a=https://www.texashomeoutlet.com/inventory-detail/1/x/",
            "--project",
            "custom-project",
        ]
    )

    assert exit_code == 0
    call = fake_run[0]
    assert call["apply"] is True
    assert call["only_ids"] == {"doc-a", "doc-b"}
    assert call["detail_urls_by_doc_id"] == {
        "doc-a": "https://www.texashomeoutlet.com/inventory-detail/1/x/"
    }
    assert call["project"] == "custom-project"


def test_main_rejects_dry_run_and_apply_together(fake_run):
    with pytest.raises(SystemExit):
        ime.main(["--dry-run", "--apply"])

    assert fake_run == []


def test_main_rejects_malformed_detail_url(fake_run):
    with pytest.raises(SystemExit):
        ime.main(["--dry-run", "--detail-url", "no-equals-sign"])
    with pytest.raises(SystemExit):
        ime.main(["--dry-run", "--detail-url", "=https://x/"])
    with pytest.raises(SystemExit):
        ime.main(["--dry-run", "--detail-url", "doc-a="])

    assert fake_run == []


# --------------------------------------------------------------------------
# inventory_media_enrichment: __main__ entrypoint + sys.path bootstrap
# --------------------------------------------------------------------------

def test_main_module_entrypoint_executes_via_runpy(monkeypatch, tmp_path):
    """`raise SystemExit(main())` (L656): execute the script with
    run_name=__main__ while the heavy dependencies are faked on the modules
    the fresh namespace imports (in-process so monkeypatches apply)."""
    import google.cloud.firestore as firestore_module
    import requests as requests_module

    import tools.inventory_sync

    class FakeInventorySync:
        def __init__(self, dry_run=True):
            pass

        def scrape_website_inventory(self):
            return []

    monkeypatch.setattr(tools.inventory_sync, "InventorySync", FakeInventorySync)

    def fake_session_factory():
        class OfflineSession(_FakeSession):
            def get(self, url, timeout=None):
                raise RuntimeError("no network in tests")

        return OfflineSession({})

    monkeypatch.setattr(requests_module, "Session", fake_session_factory)

    docs = [_FakeFirestoreDoc("doc-1", {"model_name": "Solo", "photos": []})]
    client = _FakeFirestoreClient(docs)

    def client_factory(project):
        client.project = project
        return client

    monkeypatch.setattr(firestore_module, "Client", client_factory)

    monkeypatch.setattr(
        sys,
        "argv",
        ["inventory_media_enrichment.py", "--dry-run", "--output-dir", str(tmp_path)],
    )

    # Hide the repo root so the module-level bootstrap (sys.path.insert) runs
    # in this fresh namespace; tools submodules resolve via sys.modules /
    # package __path__ regardless.
    repo = str(REPO_ROOT)
    monkeypatch.setattr(
        sys,
        "path",
        [entry for entry in sys.path if os.path.abspath(entry or os.curdir) != repo],
    )

    import runpy

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(Path(ime.__file__).resolve()), run_name="__main__")

    assert exc_info.value.code == 0
    expected_project = os.environ.get("GOOGLE_CLOUD_PROJECT", "tho-ai-agent")
    assert client.project == expected_project
    assert list(tmp_path.glob("*_summary_*.json"))  # artifacts written to --output-dir


_SYS_PATH_BOOTSTRAP_CODE = textwrap.dedent(
    """
    import importlib.util
    import pathlib
    import sys

    repo = pathlib.Path(sys.argv[1]).resolve()
    # Simulate a launcher context where the repo root is NOT importable yet.
    sys.path = [
        p for p in sys.path if pathlib.Path(p or ".").resolve() != repo
    ]
    assert str(repo) not in sys.path

    spec = importlib.util.spec_from_file_location(
        "ime_bootstrap_probe", repo / "tools" / "inventory_media_enrichment.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["ime_bootstrap_probe"] = module  # dataclasses probe sys.modules
    spec.loader.exec_module(module)

    assert str(repo) in sys.path  # module-level bootstrap inserted it
    print("BOOTSTRAP_OK")
    """
)


def test_module_inserts_repo_root_into_sys_path_when_missing():
    result = _run_python(_SYS_PATH_BOOTSTRAP_CODE, REPO_ROOT)

    assert result.returncode == 0, result.stderr
    assert "BOOTSTRAP_OK" in result.stdout
