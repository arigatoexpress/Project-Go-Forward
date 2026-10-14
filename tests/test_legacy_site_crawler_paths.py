"""Edge-path tests for tools/legacy_site_crawler.py beyond the extraction fixtures in
test_legacy_site_crawler.py.

Covers:
  * URL/session helpers and pure parsing helpers (empty/exception branches)
  * extract_* None returns and quote-form plan ids
  * extract_detail_media raw-floorplan promotion + uppercase-scheme matterport fallback
  * _merge_card_and_detail price/manufacturer fallbacks
  * scrape_legacy_inventory / scrape_legacy_floorplan_catalog with a fake session
    (multi-page, dedupe-break, detail failure, no-details mode)
  * _snapshot_media_index / _merge_snapshot_media skip branches
  * snapshot loaders (missing / corrupt / valid for the floorplan snapshot)
  * load_legacy_*_context cache/live/snapshot/exception matrices
  * write_manifest / write_floorplan_catalog_manifest (incl. empty-homes header branch)
  * main() CLI dispatch (default, --no-details, --floorplans)
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))

from test_legacy_site_crawler import (  # noqa: E402
    CARD_HTML,
    DETAIL_HTML,
    FLOORPLAN_CARD_HTML,
)

from tools import legacy_site_crawler as lsc  # noqa: E402

CDN = "https://d132mt2yijm03y.cloudfront.net"
BASE = lsc.LEGACY_BASE_URL


@pytest.fixture(autouse=True)
def _isolate_shared_legacy_cache():
    """caching._local_cache is process-global; keep this file's loader tests
    from leaking cache entries into other test modules."""
    import caching

    saved = dict(caching._local_cache)
    caching._local_cache.clear()
    yield
    caching._local_cache.clear()
    caching._local_cache.update(saved)


# ─── URL / session helpers ──────────────────────────────────────────────────


def test_session_has_inventory_user_agent():
    session = lsc._session()
    assert "THOInventoryParity" in session.headers["User-Agent"]


def test_page_url_helpers():
    assert lsc._inventory_page_url(1) == lsc.LEGACY_INVENTORY_URL
    assert lsc._inventory_page_url(3) == f"{lsc.LEGACY_INVENTORY_URL}?pg=3"
    assert lsc._floorplan_page_url(1) == lsc.LEGACY_FLOORPLAN_URL
    assert lsc._floorplan_page_url(2) == f"{lsc.LEGACY_FLOORPLAN_URL}?pg=2"


def test_absolute_url_empty_and_relative():
    assert lsc._absolute_url(None) == ""
    assert lsc._absolute_url("") == ""
    assert lsc._absolute_url("/inventory/", base_url="https://x.example") == (
        "https://x.example/inventory/"
    )


# ─── Pure parsing helpers: empty / exception branches ───────────────────────


def test_dedupe_text_skips_empty_and_duplicates():
    assert lsc._dedupe_text([None, "", "a", "a", " b ", "b", "c"]) == ["a", "b", "c"]


def test_parse_dimensions_without_match():
    assert lsc._parse_dimensions("Unknown") == ("Unknown", 0, 0)


def test_extract_matterport_id_empty_and_malformed():
    assert lsc._extract_matterport_id(None) == ""
    assert lsc._extract_matterport_id("") == ""
    # urlparse raises ValueError on an unbracketed IPv6 host
    assert lsc._extract_matterport_id("http://[unclosed/?m=abc") == ""


def test_parse_price_without_digits_passthrough():
    assert lsc._parse_price("Contact for details") == ("Contact for details", 0)
    assert lsc._parse_price("Call for pricing") == ("Call for Price", 0)
    assert lsc._parse_price(None) == ("Call for Price", 0)


def test_parse_price_formats_dollar_amounts():
    assert lsc._parse_price("$5,000") == ("$5,000", 5000)
    assert lsc._parse_price("129900") == ("$129,900", 129900)


def test_classification_and_status_branches():
    assert lsc._classification({}, ["Park Model"], "") == "Park Model"
    assert lsc._classification({"width": 16}, [], "x") == "Single Wide"
    assert lsc._classification({"width": 0}, [], "x") == "Manufactured Home"
    assert lsc._status(["Pre-Owned"], "") == "Pre-Owned"
    assert lsc._status([], "gently used home") == "Pre-Owned"
    assert lsc._status(["New"], "fresh") == "Available"


def test_parse_labeled_specs_without_container():
    specs = lsc._parse_labeled_specs(None)
    assert specs == {
        "beds": 0,
        "baths": 0,
        "sq_ft": 0,
        "dimensions": "",
        "width": 0,
        "length": 0,
    }


def test_parse_detail_info_without_container():
    soup = BeautifulSoup("<html><body><p>no info</p></body></html>", "html.parser")
    assert lsc._parse_detail_info(soup) == {}


def test_parse_detail_info_label_without_value():
    soup = BeautifulSoup(
        '<div class="detail-info">Beds:<span>3</span>Baths:</div>',
        "html.parser",
    )
    info = lsc._parse_detail_info(soup)
    assert info["specs"]["beds"] == 3
    assert info["specs"]["baths"] == 0.0  # label present, value empty


def test_dealer_inventory_url_requires_marker():
    assert lsc._is_dealer_inventory_url(f"{CDN}/dealer/9999/inventory/1/x.jpg") is False
    assert (
        lsc._is_dealer_inventory_url(f"{CDN}/dealer/3522/inventory/1/x.jpg") is True
    )
    assert (
        lsc._is_dealer_inventory_url(f"{CDN}/dealer/3522/inventory/1/x.jpg", "1")
        is True
    )
    assert (
        lsc._is_dealer_inventory_url(f"{CDN}/dealer/3522/inventory/1/x.jpg", "2")
        is False
    )


def test_extract_plan_id_quote_form():
    assert lsc._extract_plan_id(f"{BASE}/quote/floorplan/223034/dealer/3522") == "223034"
    assert lsc._extract_plan_id(f"{BASE}/plan/99/x/") == "99"
    assert lsc._extract_plan_id(f"{BASE}/unrelated/") == ""


def test_categorize_photo_url_branches():
    cat = lsc.categorize_photo_url
    assert cat(f"{CDN}/a/master-bath-1.jpg") == "bathroom"
    assert cat(f"{CDN}/a/bed-2.jpg") == "bedroom"
    assert cat(f"{CDN}/a/laundry.jpg") == "utility"
    assert cat(f"{CDN}/a/living-room.jpg") == "interior"
    assert cat(f"{CDN}/a/IMG_0001.jpg") == "interior"


# ─── extract_* None returns ─────────────────────────────────────────────────


def test_extract_inventory_card_requires_title_link():
    card = BeautifulSoup(
        '<div class="fp-card-container"><p>no link</p></div>', "html.parser"
    )
    assert lsc.extract_inventory_card(card) is None


def test_extract_inventory_card_requires_numeric_listing_id():
    card = BeautifulSoup(
        '<div class="fp-card-container">'
        '<h4 class="home-name"><a href="/inventory-detail/abc/x/">Mystery</a></h4>'
        "</div>",
        "html.parser",
    )
    assert lsc.extract_inventory_card(card) is None


def test_extract_floorplan_card_requires_title_link():
    card = BeautifulSoup(
        '<div class="fp-card-container"><p>nothing</p></div>', "html.parser"
    )
    assert lsc.extract_floorplan_card(card) is None


def test_extract_floorplan_card_requires_numeric_plan_id():
    card = BeautifulSoup(
        '<div class="fp-card-container">'
        '<h4 class="home-name"><a href="/plan/abc/sky/">Sky</a></h4>'
        "</div>",
        "html.parser",
    )
    assert lsc.extract_floorplan_card(card) is None


# ─── extract_detail_media: raw floorplan promotion + matterport fallback ────


def test_detail_media_promotes_raw_floorplan_and_ignores_case_of_matterport_scheme():
    html = f"""
    <html><head><meta name="description" content="Desc" /></head><body>
      <h1>Model X</h1>
      <figure>
        <a href="{CDN}/dealer/3522/inventory/99999/kitchen-1.jpg"><img /></a>
        <figcaption>Kitchen</figcaption>
      </figure>
      <img src="{CDN}/manufacturer/3373/floorplan/235424/Model-X-floor-plans.jpg" />
      <a href="HTTPS://my.matterport.com/show/?m=zztop">Tour</a>
    </body></html>
    """
    media = lsc.extract_detail_media(html, "99999", f"{BASE}/inventory-detail/99999/x/")

    # Figure photo categorized; no layout figure, so the raw manufacturer
    # floorplan URL is promoted into floorplan_urls.
    assert media["photos"] == [f"{CDN}/dealer/3522/inventory/99999/kitchen-1.jpg"]
    assert media["image_categories"]["kitchen"] == media["photos"]
    assert media["floorplan_urls"] == [
        f"{CDN}/manufacturer/3373/floorplan/235424/Model-X-floor-plans.jpg"
    ]
    # Uppercase scheme evades MATTERPORT_RE; the <a> link fallback still finds it.
    assert media["matterport_id"] == "zztop"


# ─── _merge_card_and_detail fallbacks ───────────────────────────────────────


def test_merge_falls_back_to_card_price_and_defaults():
    card = lsc.extract_inventory_card(
        BeautifulSoup(CARD_HTML, "html.parser").select_one(".fp-card-container")
    )
    card["display_price"] = "$5,000"
    card["price_value"] = 5000
    detail = {
        "display_price": None,
        "price_value": None,
        "manufacturer": "",
        "model_name": "",
        "photos": [],
        "floorplan_url": "",
        "floorplan_urls": [],
        "matterport_id": "",
        "detail_url": "",
        "quote_url": "",
        "description": "",
        "specs": {},
        "image_categories": {},
    }
    merged = lsc._merge_card_and_detail(card, detail)
    assert merged["price_value"] == 5000
    assert merged["display_price"] == "$5,000"
    assert merged["manufacturer"] == "Champion Homes"
    assert merged["model_name"] == "Premier / Creole 3256H32447"


# ─── scrape loops with a fake session ───────────────────────────────────────


class _FakeResponse:
    def __init__(self, text: str, url: str, error: Exception | None = None):
        self.text = text
        self.url = url
        self._error = error

    def raise_for_status(self):
        if self._error:
            raise self._error


class _FakeSession:
    """Minimal requests.Session stand-in keyed by exact URL."""

    def __init__(self, pages: dict[str, _FakeResponse]):
        self._pages = pages
        self.requested: list[str] = []

    def get(self, url: str, timeout=None):
        self.requested.append(url)
        if url not in self._pages:
            raise AssertionError(f"unexpected GET {url}")
        return self._pages[url]


def _card_page(*cards: str) -> str:
    return "<html><body>" + "".join(cards) + "</body></html>"


def test_scrape_inventory_paginates_dedupes_and_enriches(monkeypatch):
    detail_url = f"{BASE}/inventory-detail/43372/texas-home-outlet/huffman/premier/"
    pages = {
        lsc.LEGACY_INVENTORY_URL: _FakeResponse(
            _card_page(CARD_HTML), lsc.LEGACY_INVENTORY_URL
        ),
        f"{lsc.LEGACY_INVENTORY_URL}?pg=2": _FakeResponse(
            _card_page(CARD_HTML), f"{lsc.LEGACY_INVENTORY_URL}?pg=2"
        ),
        detail_url: _FakeResponse(DETAIL_HTML, detail_url),
    }
    session = _FakeSession(pages)
    monkeypatch.setattr(lsc, "_session", lambda: session)

    homes = lsc.scrape_legacy_inventory(max_pages=5, include_details=True)

    # Page 2 repeats the same card -> new_on_page == 0 -> stop paginating.
    assert len(homes) == 1
    assert homes[0]["id"] == "43372"
    assert homes[0]["matterport_id"] == "mTvc6YoSRTx"
    assert f"{lsc.LEGACY_INVENTORY_URL}?pg=3" not in session.requested


def test_scrape_inventory_detail_failure_still_merges(monkeypatch, caplog):
    detail_url = f"{BASE}/inventory-detail/43372/texas-home-outlet/huffman/premier/"
    pages = {
        lsc.LEGACY_INVENTORY_URL: _FakeResponse(
            _card_page(CARD_HTML), lsc.LEGACY_INVENTORY_URL
        ),
        f"{lsc.LEGACY_INVENTORY_URL}?pg=2": _FakeResponse(
            "<html></html>", f"{lsc.LEGACY_INVENTORY_URL}?pg=2"
        ),
        detail_url: _FakeResponse("", detail_url, error=RuntimeError("boom")),
    }
    session = _FakeSession(pages)
    monkeypatch.setattr(lsc, "_session", lambda: session)

    with caplog.at_level(logging.WARNING):
        homes = lsc.scrape_legacy_inventory(max_pages=5)

    assert len(homes) == 1
    assert homes[0]["id"] == "43372"
    assert any("detail scrape failed" in r.message for r in caplog.records)


def test_scrape_inventory_without_details_skips_detail_fetches(monkeypatch):
    pages = {
        lsc.LEGACY_INVENTORY_URL: _FakeResponse(
            _card_page(CARD_HTML), lsc.LEGACY_INVENTORY_URL
        ),
        f"{lsc.LEGACY_INVENTORY_URL}?pg=2": _FakeResponse(
            "<html></html>", f"{lsc.LEGACY_INVENTORY_URL}?pg=2"
        ),
    }
    session = _FakeSession(pages)
    monkeypatch.setattr(lsc, "_session", lambda: session)

    homes = lsc.scrape_legacy_inventory(max_pages=5, include_details=False)

    assert [h["id"] for h in homes] == ["43372"]
    assert all("inventory-detail" not in url for url in session.requested)


def test_scrape_floorplan_catalog_paginates_until_empty(monkeypatch):
    pages = {
        lsc.LEGACY_FLOORPLAN_URL: _FakeResponse(
            _card_page(FLOORPLAN_CARD_HTML), lsc.LEGACY_FLOORPLAN_URL
        ),
        f"{lsc.LEGACY_FLOORPLAN_URL}?pg=2": _FakeResponse(
            "<html></html>", f"{lsc.LEGACY_FLOORPLAN_URL}?pg=2"
        ),
    }
    session = _FakeSession(pages)
    monkeypatch.setattr(lsc, "_session", lambda: session)

    homes = lsc.scrape_legacy_floorplan_catalog(max_pages=5)

    assert [h["id"] for h in homes] == ["floorplan-223034"]
    assert f"{lsc.LEGACY_FLOORPLAN_URL}?pg=3" not in session.requested


def test_scrape_floorplan_catalog_skips_junk_and_dedupe_break(monkeypatch):
    junk = '<div class="fp-card-container"><p>no plan link</p></div>'
    pages = {
        lsc.LEGACY_FLOORPLAN_URL: _FakeResponse(
            _card_page(FLOORPLAN_CARD_HTML, junk), lsc.LEGACY_FLOORPLAN_URL
        ),
        f"{lsc.LEGACY_FLOORPLAN_URL}?pg=2": _FakeResponse(
            _card_page(FLOORPLAN_CARD_HTML), f"{lsc.LEGACY_FLOORPLAN_URL}?pg=2"
        ),
    }
    session = _FakeSession(pages)
    monkeypatch.setattr(lsc, "_session", lambda: session)

    homes = lsc.scrape_legacy_floorplan_catalog(max_pages=5)

    # Junk card skipped; page 2 is a pure duplicate -> new_on_page 0 -> stop.
    assert [h["id"] for h in homes] == ["floorplan-223034"]
    assert f"{lsc.LEGACY_FLOORPLAN_URL}?pg=3" not in session.requested


# ─── _snapshot_media_index / _merge_snapshot_media branches ─────────────────


def test_merge_snapshot_media_no_index_returns_homes_unchanged(monkeypatch):
    monkeypatch.setattr(lsc, "_load_snapshot_context", lambda: None)
    homes = [{"id": "1", "real_photos": ["p"]}]
    assert lsc._snapshot_media_index() == {}
    assert lsc._merge_snapshot_media(homes) is homes


def test_merge_snapshot_media_skip_and_copy_branches(monkeypatch):
    snapshot_home_good = {
        "id": "1",
        "media_quality": {"has_real_photo": True, "photo_count": 2},
        "real_photos": ["https://x/snap-1.jpg", "https://x/snap-2.jpg"],
        "image_url": "https://x/snap-1.jpg",
        "retrieved_at": "2026-01-01T00:00:00+00:00",
    }
    snapshot_home_no_photo = {"id": "2", "media_quality": {"photo_count": 1}}
    snapshot = {
        "homes": [
            "junk-not-a-dict",
            snapshot_home_good,
            snapshot_home_no_photo,
        ]
    }
    monkeypatch.setattr(lsc, "_load_snapshot_context", lambda: snapshot)

    live_better = {
        "id": "3",
        "media_quality": {"has_real_photo": True, "photo_count": 5},
        "real_photos": [f"https://x/live-{i}.jpg" for i in range(5)],
    }
    live_copy = {
        "id": "1",
        "media_quality": {"has_real_photo": False, "photo_count": 0},
        "real_photos": [],
        "image_url": "https://x/card.jpg",
        "source_provenance": {"source_owner": "Texas Home Outlet"},
    }
    live_missing = {"id": "404", "real_photos": ["q"]}
    live_worse_snapshot_no_photo = {
        "id": "2",
        "media_quality": {"has_real_photo": False},
        "real_photos": [],
    }
    # 906-909: snapshot has real photos but live set is at least as good -> skip
    live_good_enough = {
        "id": "1",
        "media_quality": {"has_real_photo": True, "photo_count": 5},
        "real_photos": [f"https://x/good-{i}.jpg" for i in range(5)],
    }
    # 902-905: manufacturer-plan recovery already applied and live has a real
    # photo -> snapshot must not overwrite the recovery
    live_recovered = {
        "id": "1",
        "media_quality": {"has_real_photo": True, "photo_count": 1},
        "real_photos": ["https://x/recovered.jpg"],
        "media_recovery": {"source": "exact_manufacturer_plan_cdn"},
    }
    homes = [
        live_better,
        live_copy,
        live_missing,
        live_worse_snapshot_no_photo,
        live_good_enough,
        live_recovered,
    ]

    out = lsc._merge_snapshot_media(homes)

    # 906-909: live photos already better than snapshot -> untouched
    assert out[0]["real_photos"] == [f"https://x/live-{i}.jpg" for i in range(5)]
    assert "media_snapshot_fallback" not in (out[0].get("source_provenance") or {})
    # 911-924: snapshot fields + provenance fallback marker copied
    assert out[1]["real_photos"] == snapshot_home_good["real_photos"]
    assert out[1]["image_url"] == snapshot_home_good["image_url"]
    assert out[1]["source_provenance"]["media_snapshot_fallback"]["source"] == (
        "legacy_inventory_snapshot"
    )
    # 897: id absent from the snapshot index -> untouched
    assert out[2]["real_photos"] == ["q"]
    assert "media_snapshot_fallback" not in (out[2].get("source_provenance") or {})
    # 900: snapshot entry has no real photo -> untouched
    assert out[3]["real_photos"] == []
    assert "media_snapshot_fallback" not in (out[3].get("source_provenance") or {})
    # 909: live set at least as good as snapshot -> untouched
    assert out[4]["real_photos"] == [f"https://x/good-{i}.jpg" for i in range(5)]
    assert "media_snapshot_fallback" not in (out[4].get("source_provenance") or {})
    # 905: exact-manufacturer recovery already applied -> untouched
    assert out[5]["real_photos"] == ["https://x/recovered.jpg"]
    assert "media_snapshot_fallback" not in (out[5].get("source_provenance") or {})


# ─── snapshot loaders ───────────────────────────────────────────────────────


def test_load_snapshot_context_missing_and_corrupt(tmp_path, monkeypatch, caplog):
    missing = tmp_path / "nope.json"
    monkeypatch.setattr(lsc, "SNAPSHOT_PATH", missing)
    assert lsc._load_snapshot_context() is None  # not exists -> None

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json")
    monkeypatch.setattr(lsc, "SNAPSHOT_PATH", corrupt)
    with caplog.at_level(logging.WARNING):
        assert lsc._load_snapshot_context() is None
    assert any("failed to load legacy inventory snapshot" in r.message
               for r in caplog.records)


def test_load_floorplan_snapshot_context_branches(tmp_path, monkeypatch, caplog):
    missing = tmp_path / "nope.json"
    monkeypatch.setattr(lsc, "FLOORPLAN_SNAPSHOT_PATH", missing)
    assert lsc._load_floorplan_snapshot_context() is None

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{nope")
    monkeypatch.setattr(lsc, "FLOORPLAN_SNAPSHOT_PATH", corrupt)
    with caplog.at_level(logging.WARNING):
        assert lsc._load_floorplan_snapshot_context() is None
    assert any("failed to load floorplan catalog snapshot" in r.message
               for r in caplog.records)

    valid = tmp_path / "valid.json"
    valid.write_text(json.dumps({"homes": [{"id": "floorplan-1"}]}))
    monkeypatch.setattr(lsc, "FLOORPLAN_SNAPSHOT_PATH", valid)
    context = lsc._load_floorplan_snapshot_context()
    assert context["source"] == "legacy_floorplan_catalog_snapshot"
    assert context["snapshot_path"] == str(valid)
    assert context["success"] is True
    assert "snapshot" in context["message"]


# ─── load_legacy_inventory_context matrix ───────────────────────────────────


def test_load_inventory_context_returns_cached(monkeypatch):
    cached = {"homes": [], "total_inventory": 0, "media_summary": {}}
    monkeypatch.setattr(lsc, "cache_get", lambda key: cached)
    monkeypatch.setattr(
        lsc,
        "build_legacy_inventory_context",
        lambda limit=100: (_ for _ in ()).throw(AssertionError("no live crawl")),
    )
    assert lsc.load_legacy_inventory_context() is not None


def test_load_inventory_context_live_success_sets_cache(monkeypatch):
    monkeypatch.setattr(lsc, "cache_get", lambda key: None)
    monkeypatch.setattr(lsc, "_load_snapshot_context", lambda: None)
    built = {"success": True, "homes": [{"id": "1"}], "total_inventory": 1}
    monkeypatch.setattr(
        lsc, "build_legacy_inventory_context", lambda limit=100: built
    )
    cache_calls = []
    monkeypatch.setattr(
        lsc, "cache_set", lambda key, value, ttl_seconds=None: cache_calls.append(key)
    )

    context = lsc.load_legacy_inventory_context(limit=100, force_refresh=True)
    assert context["total_inventory"] == 1
    assert cache_calls == [lsc.LEGACY_CACHE_KEY]


def test_load_inventory_context_snapshot_without_homes_falls_to_live(monkeypatch):
    monkeypatch.setattr(lsc, "cache_get", lambda key: None)
    monkeypatch.setattr(
        lsc, "_load_snapshot_context", lambda: {"success": True, "homes": []}
    )
    built = {"success": True, "homes": [{"id": "live"}], "total_inventory": 1}
    monkeypatch.setattr(
        lsc, "build_legacy_inventory_context", lambda limit=100: built
    )
    monkeypatch.setattr(lsc, "cache_set", lambda *a, **k: None)
    assert lsc.load_legacy_inventory_context()["homes"][0]["id"] == "live"


def test_load_inventory_context_live_failure_falls_back_to_snapshot(monkeypatch):
    """Snapshot missing on the preferred path but present after a live failure."""
    monkeypatch.setattr(lsc, "cache_get", lambda key: None)
    monkeypatch.setattr(lsc, "cache_set", lambda *a, **k: None)
    monkeypatch.setattr(
        lsc,
        "build_legacy_inventory_context",
        lambda limit=100: (_ for _ in ()).throw(RuntimeError("crawl failed")),
    )
    snapshot = {
        "success": True,
        "retrieved_at": "2026-01-01T00:00:00+00:00",
        "homes": [{"id": "archived", "model_name": "Archived"}],
        "total_inventory": 1,
    }
    # First read (preferred path) finds nothing; the exception handler reads again.
    reads = iter([None, snapshot])
    monkeypatch.setattr(lsc, "_load_snapshot_context", lambda: next(reads))

    context = lsc.load_legacy_inventory_context()
    assert context["homes"][0]["id"] == "archived"


def test_load_inventory_context_live_failure_without_snapshot_errors(monkeypatch):
    monkeypatch.setattr(lsc, "cache_get", lambda key: None)
    monkeypatch.setattr(
        lsc,
        "build_legacy_inventory_context",
        lambda limit=100: (_ for _ in ()).throw(RuntimeError("crawl failed")),
    )
    monkeypatch.setattr(lsc, "_load_snapshot_context", lambda: None)
    context = lsc.load_legacy_inventory_context()
    assert context["success"] is False
    assert context["homes"] == []
    assert "crawl failed" in context["error"]


# ─── load_legacy_floorplan_catalog_context matrix ───────────────────────────


def test_load_floorplan_context_returns_cached(monkeypatch):
    cached = {"homes": [{"id": "floorplan-cached"}], "total_inventory": 1}
    monkeypatch.setattr(lsc, "cache_get", lambda key: cached)
    monkeypatch.setattr(
        lsc,
        "build_legacy_floorplan_catalog_context",
        lambda limit=500: (_ for _ in ()).throw(AssertionError("no live crawl")),
    )
    assert lsc.load_legacy_floorplan_catalog_context()["homes"][0]["id"] == (
        "floorplan-cached"
    )


def test_load_floorplan_context_live_success_sets_cache(monkeypatch):
    monkeypatch.setattr(lsc, "cache_get", lambda key: None)
    monkeypatch.setattr(lsc, "_load_floorplan_snapshot_context", lambda: None)
    built = {
        "success": True,
        "homes": [{"id": "floorplan-live"}],
        "total_inventory": 1,
    }
    monkeypatch.setattr(
        lsc, "build_legacy_floorplan_catalog_context", lambda limit=500: built
    )
    cache_calls = []
    monkeypatch.setattr(
        lsc, "cache_set", lambda key, value, ttl_seconds=None: cache_calls.append(key)
    )
    context = lsc.load_legacy_floorplan_catalog_context(force_refresh=True)
    assert context["homes"][0]["id"] == "floorplan-live"
    assert cache_calls == [lsc.LEGACY_FLOORPLAN_CACHE_KEY]


def test_load_floorplan_context_live_failure_falls_back_to_snapshot(monkeypatch):
    """Snapshot missing on the preferred path but present after a live failure."""
    monkeypatch.setattr(lsc, "cache_get", lambda key: None)
    monkeypatch.setattr(lsc, "cache_set", lambda *a, **k: None)
    monkeypatch.setattr(
        lsc,
        "build_legacy_floorplan_catalog_context",
        lambda limit=500: (_ for _ in ()).throw(RuntimeError("crawl failed")),
    )
    snapshot = {
        "success": True,
        "homes": [{"id": "floorplan-archived", "model_name": "Archived Plan"}],
        "total_inventory": 1,
    }
    reads = iter([None, snapshot])
    monkeypatch.setattr(lsc, "_load_floorplan_snapshot_context", lambda: next(reads))

    context = lsc.load_legacy_floorplan_catalog_context()
    assert context["homes"][0]["id"] == "floorplan-archived"


def test_load_floorplan_context_live_failure_without_snapshot_errors(monkeypatch):
    monkeypatch.setattr(lsc, "cache_get", lambda key: None)
    monkeypatch.setattr(
        lsc,
        "build_legacy_floorplan_catalog_context",
        lambda limit=500: (_ for _ in ()).throw(RuntimeError("crawl failed")),
    )
    monkeypatch.setattr(lsc, "_load_floorplan_snapshot_context", lambda: None)
    context = lsc.load_legacy_floorplan_catalog_context()
    assert context["success"] is False
    assert context["homes"] == []
    assert "crawl failed" in context["error"]


# ─── build_legacy_floorplan_catalog_context ─────────────────────────────────


def test_log_snapshot_staleness_edge_inputs():
    assert lsc._log_snapshot_staleness(None) is None
    assert lsc._log_snapshot_staleness({}) is None
    assert lsc._log_snapshot_staleness({"retrieved_at": ""}) is None
    assert lsc._log_snapshot_staleness({"retrieved_at": "not-a-date"}) is None


def test_build_floorplan_catalog_context_counts_and_limits(monkeypatch):
    homes = [
        {"id": f"floorplan-{i}", "status": "Orderable"} for i in range(3)
    ]
    monkeypatch.setattr(
        lsc, "scrape_legacy_floorplan_catalog", lambda max_pages=40: homes
    )
    context = lsc.build_legacy_floorplan_catalog_context(limit=2)
    assert context["source"] == "legacy_floorplan_catalog_live"
    assert context["total_inventory"] == 3
    assert context["orderable_floorplans"] == 3
    assert context["returned_inventory"] == 2
    assert len(context["homes"]) == 2
    assert "3 orderable floorplans" in context["message"]


# ─── write_manifest / write_floorplan_catalog_manifest ──────────────────────


def _sample_home(home_id: str) -> dict:
    return {
        "id": home_id,
        "legacy_plan_id": home_id.removeprefix("floorplan-"),
        "model_name": "Model X",
        "manufacturer": "Champion",
        "classification": "Double Wide",
        "status": "Available",
        "detail_url": f"{BASE}/inventory-detail/{home_id}/",
        "quote_url": f"{BASE}/quote/{home_id}/",
        "floorplan_url": f"{CDN}/floorplan-{home_id}.jpg",
        "matterport_id": "mp1",
        "media_quality": {
            "photo_count": 4,
            "floorplan_count": 1,
            "status": "photo_ready",
        },
    }


def test_write_manifest_writes_json_csv_and_report(tmp_path):
    context = {
        "source_url": lsc.LEGACY_INVENTORY_URL,
        "retrieved_at": "2026-10-14T00:00:00+00:00",
        "total_inventory": 2,
        "media_summary": {"photo_ready": 2},
        "homes": [_sample_home("1"), {**_sample_home("2"), "media_quality": None}],
    }
    paths = lsc.write_manifest(context, tmp_path)

    written = json.loads(Path(paths["json"]).read_text())
    assert written["total_inventory"] == 2

    csv_text = Path(paths["csv"]).read_text()
    header = csv_text.splitlines()[0]
    assert header.startswith("id,model_name,manufacturer,status")
    assert "photo_count" in header and "matterport" in header
    assert csv_text.count("\n") == 3  # header + 2 rows

    report = Path(paths["report"]).read_text()
    assert "Legacy Site Inventory Asset Report" in report
    assert "Listings: 2" in report


def test_write_manifest_empty_homes_uses_default_csv_header(tmp_path):
    context = {"homes": [], "total_inventory": 0}
    paths = lsc.write_manifest(context, tmp_path)
    header = Path(paths["csv"]).read_text().splitlines()[0]
    assert header == (
        "id,model_name,manufacturer,status,detail_url,quote_url,"
        "photo_count,floorplan_count,media_status,matterport"
    )


def test_write_floorplan_manifest_writes_all_artifacts(tmp_path):
    context = {
        "source_url": lsc.LEGACY_FLOORPLAN_URL,
        "retrieved_at": "2026-10-14T00:00:00+00:00",
        "total_inventory": 1,
        "media_summary": {"floorplan_only": 1},
        "homes": [_sample_home("floorplan-9")],
    }
    paths = lsc.write_floorplan_catalog_manifest(context, tmp_path)

    written = json.loads(Path(paths["json"]).read_text())
    assert written["homes"][0]["legacy_plan_id"] == "9"

    csv_text = Path(paths["csv"]).read_text()
    header = csv_text.splitlines()[0]
    assert header.startswith("id,legacy_plan_id,model_name,manufacturer,classification")
    assert "floorplan_url" in header

    report = Path(paths["report"]).read_text()
    assert "Floorplan Catalog Report" in report
    assert "Orderable floorplans: 1" in report


def test_write_floorplan_manifest_empty_homes_default_header(tmp_path):
    context = {"homes": [], "total_inventory": 0}
    paths = lsc.write_floorplan_catalog_manifest(context, tmp_path)
    header = Path(paths["csv"]).read_text().splitlines()[0]
    assert header == (
        "id,legacy_plan_id,model_name,manufacturer,classification,"
        "detail_url,quote_url,floorplan_url,photo_count,floorplan_count,"
        "matterport"
    )


# ─── main() CLI dispatch ────────────────────────────────────────────────────


def test_main_default_mode_dispatches(monkeypatch, capsys, tmp_path):
    calls = {}

    def _fake_build(limit=100, max_pages=25, include_details=True):
        calls["build"] = (limit, max_pages, include_details)
        return {"total_inventory": 7}

    def _fake_write(context, output_dir):
        calls["write"] = output_dir
        return {"json": "j", "csv": "c", "report": "r"}

    monkeypatch.setattr(lsc, "build_legacy_inventory_context", _fake_build)
    monkeypatch.setattr(lsc, "write_manifest", _fake_write)
    monkeypatch.setattr(
        sys, "argv", ["prog", "--output-dir", str(tmp_path), "--max-pages", "3",
                      "--limit", "50"]
    )

    assert lsc.main() == 0
    assert calls["build"] == (50, 3, True)
    assert calls["write"] == tmp_path
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is True
    assert out["total_inventory"] == 7
    assert out["paths"] == {"json": "j", "csv": "c", "report": "r"}


def test_main_no_details_flag(monkeypatch, capsys, tmp_path):
    calls = {}
    monkeypatch.setattr(
        lsc,
        "build_legacy_inventory_context",
        lambda limit=100, max_pages=25, include_details=True: calls.update(
            include_details=include_details
        )
        or {"total_inventory": 0},
    )
    monkeypatch.setattr(
        lsc, "write_manifest", lambda context, output_dir: {"json": "j"}
    )
    monkeypatch.setattr(
        sys, "argv", ["prog", "--no-details", "--output-dir", str(tmp_path)]
    )
    assert lsc.main() == 0
    assert calls["include_details"] is False


def test_main_floorplans_mode_dispatches(monkeypatch, capsys, tmp_path):
    calls = {}
    monkeypatch.setattr(
        lsc,
        "build_legacy_floorplan_catalog_context",
        lambda limit=500, max_pages=40: calls.update(limit=limit)
        or {"total_inventory": 2},
    )
    monkeypatch.setattr(
        lsc,
        "write_floorplan_catalog_manifest",
        lambda context, output_dir: calls.update(written=True)
        or {"json": "j", "csv": "c", "report": "r"},
    )
    monkeypatch.setattr(
        sys, "argv", ["prog", "--floorplans", "--output-dir", str(tmp_path)]
    )
    assert lsc.main() == 0
    # main()'s --limit default is 100 and is passed straight through
    assert calls == {"limit": 100, "written": True}
    assert json.loads(capsys.readouterr().out)["total_inventory"] == 2
