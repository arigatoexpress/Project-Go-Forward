"""Tests for the server-side SEO surface (seo_routes.py).

Covers: robots.txt, sitemap.xml, per-route head injection (title/canonical/
OG/JSON-LD), legacy detail URLs served 200 with crawlable content, quote-URL
301s, soft-404 avoidance, and noindex on admin routes.
"""

import html
import json
import logging
import re
import sys
from pathlib import Path

import pytest

from config_loader import (
    business_address,
    business_city,
    business_name,
    business_phone,
    get_business,
)

sys.path.insert(0, str(Path(__file__).parent))

from test_api_v1 import create_client

FAKE_HOMES = [
    {
        "id": "43372",
        "legacy_inventory_id": "43372",
        "model_name": "Premier / Creole 3256H32447",
        "manufacturer": "Champion Homes",
        "classification": "Double Wide",
        "status": "Available",
        "detail_url": "https://www.texashomeoutlet.com/inventory-detail/43372/texas-home-outlet/huffman/premier/",
        "quote_url": "https://www.texashomeoutlet.com/quote/inventory/123/43372",
        "legacy_detail_aliases": [
            "https://www.texashomeoutlet.com/plan/235424/premier/creole-3256h32447/"
        ],
        "legacy_quote_aliases": [
            "https://www.texashomeoutlet.com/quote/floorplan/235424/dealer/3522"
        ],
        "hero_image": "https://img.example.com/creole.jpg",
        "price_value": 129900,
        "display_price": "$129,900",
        "specs": {"beds": 4, "baths": 2, "dimensions": "32x56"},
        "description": "Spacious double-section home.",
    },
    {
        "id": "floorplan-223034",
        "legacy_plan_id": "223034",
        "model_name": "Skyliner 4732B",
        "manufacturer": "Skyline",
        "classification": "Single Wide",
        "status": "Orderable",
        "detail_url": "https://www.texashomeoutlet.com/plan/223034/skyliner/4732b/",
        "quote_url": "https://www.texashomeoutlet.com/quote/plan/223034",
        "price_value": None,
        "specs": {"beds": 3, "baths": 2},
    },
    {
        "id": "44490",
        "stock_number": "44490",
        "model_name": "PRE-OWNED / Big Blue",
        "manufacturer": "Pre-Owned",
        "classification": "Single Wide",
        "status": "Pre-Owned",
        "inventory_kind": "pre_owned",
        "display_price": "Call for Price",
        "price_value": None,
        "image_url": (
            "https://d132mt2yijm03y.cloudfront.net/manufacturer/1944/"
            "floorplan/1391/Heritage-kitchen-1.jpg"
        ),
        "real_photos": [
            "https://img.example.com/lot-44490.jpg",
            (
                "https://d132mt2yijm03y.cloudfront.net/manufacturer/1944/"
                "floorplan/1391/Heritage-kitchen-1.jpg"
            ),
        ],
        "gallery_images": [
            "https://img.example.com/catalog-should-not-appear.jpg",
            (
                "https://d132mt2yijm03y.cloudfront.net/manufacturer/1944/"
                "floorplan/1391/Heritage-kitchen-1.jpg"
            ),
        ],
        "floor_plan_url": "https://img.example.com/big-blue-floorplan.jpg",
        "specs": {"beds": 3, "baths": 2, "sqft": 1152},
        "updated_at": "2026-10-01T12:00:00Z",
    },
]


@pytest.fixture(autouse=True)
def isolated_seo_shell(monkeypatch, tmp_path):
    """Use a fresh SPA shell, independent of builds and other test suites."""
    import seo_routes

    shell = tmp_path / "index.html"
    source = Path(__file__).resolve().parent.parent / "frontend" / "index.html"
    shell.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(seo_routes, "_index_html_path", str(shell))
    monkeypatch.setattr(seo_routes, "_shell_cache", None)


def seo_client(monkeypatch):
    client, main, db, logger = create_client(monkeypatch)
    import seo_routes

    monkeypatch.setattr(seo_routes, "_get_homes", lambda: FAKE_HOMES)
    # bust the registry cache between tests
    monkeypatch.setattr(seo_routes, "_registry_cache", None)
    monkeypatch.setattr(seo_routes, "_registry_built_at", 0.0)
    return client, main


def test_robots_txt_allows_public_blocks_admin(monkeypatch):
    client, _ = seo_client(monkeypatch)
    response = client.get("/robots.txt")
    assert response.status_code == 200
    body = response.text
    assert "Disallow: /crm" in body
    assert "Disallow: /ad-studio" in body
    assert "Disallow: /staff" in body
    assert "Disallow: /team" in body
    assert "Disallow: /api/admin/" in body
    assert "Sitemap: https://www.texashomeoutlet.com/sitemap.xml" in body
    # Never block assets or the public inventory API the renderer depends on
    assert "Disallow: /assets" not in body
    assert "Disallow: /api/marketing" not in body
    # We WANT AI visibility: no AI-crawler blocks
    assert "GPTBot" not in body and "ClaudeBot" not in body


def _sitemap_entries(body):
    entries = []
    for chunk in re.findall(r"<url>(.*?)</url>", body, re.DOTALL):
        loc = re.search(r"<loc>([^<]+)</loc>", chunk).group(1)
        lastmod = re.search(r"<lastmod>([^<]+)</lastmod>", chunk)
        entries.append((loc, lastmod.group(1) if lastmod else None))
    return entries


def test_sitemap_lists_static_and_detail_urls(monkeypatch):
    client, _ = seo_client(monkeypatch)
    response = client.get("/sitemap.xml")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    body = response.text
    assert "https://www.texashomeoutlet.com/" in body
    assert "sapphirealpha.xyz" not in body
    assert "/inventory</loc>" in body
    assert "/inventory-detail/43372/texas-home-outlet/huffman/premier/</loc>" in body
    assert "/plan/223034/skyliner/4732b/</loc>" in body
    assert "/plan/235424/" not in body
    assert "/quote/floorplan/235424" not in body
    assert "/homes/44490-pre-owned-big-blue</loc>" in body
    assert "/chat</loc>" in body
    assert "/homes/43372-premier-creole-3256h32447</loc>" in body
    assert "<lastmod>2026-10-18</lastmod>" in body
    # Google ignores priority/changefreq. Unchanged pages omit lastmod.
    assert "<priority>" not in body and "<changefreq>" not in body
    entries = _sitemap_entries(body)
    chat = next(entry for entry in entries if entry[0].endswith("/chat"))
    assert chat[1] is None
    home = next(entry for entry in entries if entry[0].rstrip("/").endswith("texashomeoutlet.com"))
    assert home[1] == "2026-10-18"


def test_homepage_head_is_injected(monkeypatch):
    client, _ = seo_client(monkeypatch)
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert "Manufactured Homes for Sale in Huffman, TX" in body
    assert 'rel="canonical"' in body
    assert '<link rel="canonical" href="https://www.texashomeoutlet.com/"' in body
    assert "sapphirealpha.xyz" not in body
    assert body.count("<title>") == 1  # static template title replaced, not duplicated
    assert '"@type": "LocalBusiness"' in body
    assert '"postalCode": "77336"' in body


def test_inventory_routes_preload_the_responsive_hero_image(monkeypatch):
    client, _ = seo_client(monkeypatch)

    for path in ("/", "/inventory"):
        body = client.get(path).text
        assert '<link rel="preload" as="image"' in body
        assert 'href="https://img.example.com/creole.jpg"' in body
        assert "imagesrcset=" not in body
        assert "?w=400" not in body
        assert 'fetchpriority="high"' in body
        assert 'name="twitter:title"' in body
        assert 'property="og:locale" content="en_US"' in body

    assert '<link rel="preload" as="image"' not in client.get("/contact").text


def test_resizable_hero_preloads_the_same_origin_lcp_image(monkeypatch):
    """Staff and catalog heroes preload one small same-origin JPEG.

    The original file stays out of the head so a phone does not spend its
    first bytes on a full-size photo. Category pages keep their own hero.
    """
    import seo_routes
    from tools.lcp_image import LCP_IMAGE_PATH

    staff = "/api/inventory/photos/43372/lot.jpg"
    home = {
        **FAKE_HOMES[0],
        "classification": "Single Wide",
        "hero_image": staff,
    }
    client, _ = seo_client(monkeypatch)
    monkeypatch.setattr(seo_routes, "_get_homes", lambda: [home, FAKE_HOMES[2]])

    for path in ("/", "/inventory"):
        body = client.get(path).text
        assert f'href="{LCP_IMAGE_PATH}"' in body
        assert 'id="tho-lcp"' in body
        assert 'class="tho-lcp"' in body
        assert "style=" not in body.split('id="tho-lcp"', 1)[1].split(">", 1)[0]
        assert staff not in body
        assert "imagesrcset=" not in body
        assert "?w=" not in body

    contact = client.get("/contact").text
    assert "tho-lcp" not in contact
    assert '<link rel="preload" as="image"' not in contact

    category = client.get("/single-wide").text
    assert "tho-lcp" not in category
    assert LCP_IMAGE_PATH not in category

    listing = client.get("/homes/44490-pre-owned-big-blue").text
    assert 'id="tho-lcp"' in listing
    assert f'href="{LCP_IMAGE_PATH}"' in listing
    assert 'loading="lazy"' in listing
    assert 'fetchpriority="low"' in listing
    assert "https://img.example.com/lot-44490.jpg" in listing

    cdn = "https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/43372/home-ext-1.jpg"
    cdn_home = {**FAKE_HOMES[0], "hero_image": cdn}
    monkeypatch.setattr(seo_routes, "_get_homes", lambda: [cdn_home])
    monkeypatch.setattr(seo_routes, "_registry_cache", None)
    cdn_page = client.get("/inventory").text
    assert f'href="{LCP_IMAGE_PATH}"' in cdn_page
    assert 'id="tho-lcp"' in cdn_page
    assert f'href="{cdn}"' not in cdn_page


def test_inventory_hero_preload_prefers_an_available_home(monkeypatch):
    import seo_routes

    floorplan = {
        **FAKE_HOMES[1],
        "hero_image": "https://img.example.com/orderable.jpg",
    }
    client, _ = seo_client(monkeypatch)
    monkeypatch.setattr(seo_routes, "_get_homes", lambda: [floorplan, FAKE_HOMES[0]])

    body = client.get("/").text
    assert 'href="https://img.example.com/creole.jpg"' in body
    assert 'href="https://img.example.com/orderable.jpg"' not in body


def test_homepage_contains_crawlable_links_to_details(monkeypatch):
    client, _ = seo_client(monkeypatch)
    body = client.get("/").text
    assert 'href="/inventory-detail/43372/texas-home-outlet/huffman/premier/"' in body
    assert 'href="/plan/223034/skyliner/4732b/"' in body


def test_legacy_detail_url_serves_200_with_product_jsonld(monkeypatch):
    client, _ = seo_client(monkeypatch)
    response = client.get("/inventory-detail/43372/texas-home-outlet/huffman/premier/")
    assert response.status_code == 200
    body = response.text
    assert "<title>Premier / Creole 3256H32447" in body
    jsonld_blocks = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', body, re.DOTALL
    )
    products = [json.loads(b) for b in jsonld_blocks if '"Product"' in b]
    assert products, "Product JSON-LD missing"
    product = products[0]
    assert product["offers"]["price"] == "129900"
    assert product["brand"]["name"] == "Champion Homes"
    assert "availability" not in product["offers"]


def test_public_seo_never_claims_unverified_live_or_ready_now_inventory(monkeypatch):
    client, _ = seo_client(monkeypatch)

    for path in ("/", "/inventory", "/faq", "/manufactured-homes-in-humble-tx"):
        body = client.get(path).text.lower()
        assert "live inventory" not in body, path
        assert "ready now" not in body, path


def test_call_for_price_home_emits_no_product_snippet(monkeypatch):
    # A "call for price" home (no price_value) cannot satisfy Google's Product
    # snippet rule (offers/review/aggregateRating, and a valid Offer needs a
    # real price) without fabricating data, so it must emit NO Product JSON-LD
    # rather than an invalid one Search Console would flag. The page still
    # renders fully (title/OG/crawlable body).
    client, _ = seo_client(monkeypatch)
    response = client.get("/plan/223034/skyliner/4732b/")
    assert response.status_code == 200
    products = [
        json.loads(b)
        for b in re.findall(
            r'<script type="application/ld\+json">(.*?)</script>', response.text, re.DOTALL
        )
        if '"Product"' in b
    ]
    assert not products, "call-for-price home must not emit a Product snippet"


def test_trailing_slash_variants_301_to_canonical(monkeypatch):
    client, _ = seo_client(monkeypatch)
    # Public routes: slash variant -> no-slash canonical
    for path in ("/contact/", "/inventory/", "/appointments/"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 301, path
        assert response.headers["location"] == path.rstrip("/"), path
    # Detail URLs: the canonical carries a trailing slash (legacy format);
    # the slashless variant must 301 to it, not serve duplicate 200s.
    response = client.get(
        "/inventory-detail/43372/texas-home-outlet/huffman/premier",
        follow_redirects=False,
    )
    assert response.status_code == 301
    assert response.headers["location"] == (
        "/inventory-detail/43372/texas-home-outlet/huffman/premier/"
    )


def test_accented_detail_url_does_not_redirect_loop(monkeypatch):
    # Regression (F3): a percent-encoded non-ASCII char in a plan slug
    # (e.g. "é" == "%C3%A9"). _build_registry stores the ENCODED path
    # (urlparse does not decode) while Starlette DECODES the request path, so
    # a naive `raw_path != canonical_path` compared "cortés" vs "cort%C3%A9s"
    # and 301'd to itself forever -> ERR_TOO_MANY_REDIRECTS, an unindexable
    # page. The two real-world victims were the Compass "Cortés"/"Fernández"
    # plans, both listed in sitemap.xml.
    import seo_routes

    accented = [
        {
            "id": "floorplan-222026",
            "legacy_plan_id": "222026",
            "model_name": "Compass / Cortés 230",
            "manufacturer": "Compass",
            "status": "Orderable",
            "detail_url": "https://www.texashomeoutlet.com/plan/222026/compass/cort%C3%A9s-230/",
            "specs": {"beds": 3, "baths": 2},
        }
    ]
    client, _ = seo_client(monkeypatch)
    monkeypatch.setattr(seo_routes, "_get_homes", lambda: accented)
    monkeypatch.setattr(seo_routes, "_registry_cache", None)
    monkeypatch.setattr(seo_routes, "_registry_built_at", 0.0)

    # The canonical (percent-encoded) URL must serve 200, not loop on 301.
    resp = client.get("/plan/222026/compass/cort%C3%A9s-230/", follow_redirects=False)
    assert resp.status_code == 200, f"expected 200, got {resp.status_code} (loop?)"
    assert "Cort" in resp.text  # rendered the home page, not a redirect

    # A genuinely different slug for the same id still 301s once to canonical.
    resp2 = client.get("/plan/222026/compass/wrong-slug-230/", follow_redirects=False)
    assert resp2.status_code == 301
    assert resp2.headers["location"] == "/plan/222026/compass/cort%C3%A9s-230/"


def test_legacy_vendor_pages_301_to_relevant_targets(monkeypatch):
    # Old WordPress/Yoast marketing, brand, and city pages must 301 (not hard-404)
    # to preserve search equity on cutover. Source: old site page-sitemap.xml.
    client, _ = seo_client(monkeypatch)
    cases = {
        "/tru-homes/": "/inventory",  # brand
        "/manufactured-homes-in-jasper-tx/": "/inventory",  # unserved city (still legacy 301)
        "/homes/": "/inventory",  # generic legacy 'homes' landing
        "/financing/": "/financing",  # now a real public page
        "/about-us/": "/about",  # now a real public page
        "/accessibility-statement/": "/",  # boilerplate
    }
    for path, target in cases.items():
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 301, path
        assert response.headers["location"] == target, path
    # Case-insensitive + slashless variants resolve the same.
    response = client.get("/Financing", follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["location"] == "/financing"
    # A genuinely unknown marketing path is NOT over-broadly caught — still 404.
    response = client.get("/totally-made-up-page/", follow_redirects=False)
    assert response.status_code == 404


def test_unknown_detail_id_is_404(monkeypatch):
    client, _ = seo_client(monkeypatch)
    response = client.get("/inventory-detail/99999/whatever/")
    assert response.status_code == 404


def test_bare_inventory_detail_deep_link_reaches_lot_home_listing(monkeypatch):
    """Regression: /inventory-detail/<stock id> for a lot home (no legacy
    detail_url) 404'd on live (stock 28102) while /inventory opened it."""
    client, _ = seo_client(monkeypatch)
    expected = "/homes/44490-pre-owned-big-blue"

    for deep_link in (
        "/inventory-detail/44490",
        "/inventory-detail/44490/",
        "/inventory-detail/44490/texas-home-outlet/huffman/big-blue/",
    ):
        response = client.get(deep_link, follow_redirects=False)
        assert response.status_code == 302, deep_link
        assert response.headers["location"] == expected, deep_link
        assert "no-store" in response.headers["cache-control"], deep_link

    landed = client.get("/inventory-detail/44490")
    assert landed.status_code == 200
    assert "Big Blue" in landed.text


def test_stock_id_deep_link_does_not_capture_plan_family_or_sold_stock(monkeypatch):
    client, _ = seo_client(monkeypatch)

    # /plan/ ids are catalog plan ids; a matching lot stock number must not hijack them.
    plan = client.get("/plan/44490/some/plan/", follow_redirects=False)
    assert plan.status_code == 404
    assert "location" not in plan.headers

    import seo_routes

    sold = {**FAKE_HOMES[2], "status": "Sold"}
    monkeypatch.setattr(seo_routes, "_get_homes", lambda: [FAKE_HOMES[0], sold])
    monkeypatch.setattr(seo_routes, "_registry_cache", None)
    monkeypatch.setattr(seo_routes, "_registry_built_at", 0.0)

    gone = client.get("/inventory-detail/44490", follow_redirects=False)
    assert gone.status_code == 404
    assert "location" not in gone.headers

    # Real legacy detail ids still win over the stock fallback.
    legacy = client.get(
        "/inventory-detail/43372/texas-home-outlet/huffman/premier/", follow_redirects=False
    )
    assert legacy.status_code == 200


def test_quote_urls_301_to_detail_pages(monkeypatch):
    client, _ = seo_client(monkeypatch)
    response = client.get("/quote/inventory/123/43372", follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["location"] == (
        "/inventory-detail/43372/texas-home-outlet/huffman/premier/"
    )
    fallback = client.get("/quote/unknown/thing", follow_redirects=False)
    assert fallback.status_code == 301
    assert fallback.headers["location"] == "/inventory"


def test_deduped_legacy_product_aliases_temporarily_redirect_to_survivor(monkeypatch):
    client, _ = seo_client(monkeypatch)
    canonical = "/inventory-detail/43372/texas-home-outlet/huffman/premier/"

    for alias in (
        "/plan/235424/premier/creole-3256h32447/",
        "/plan/235424/premier/an-old-slug-for-the-same-home/",
        "/quote/floorplan/235424/dealer/3522",
    ):
        response = client.get(alias, follow_redirects=False)
        assert response.status_code == 302, alias
        assert response.headers["location"] == canonical, alias
        assert "no-store" in response.headers["cache-control"], alias

    survivor = client.get(canonical, follow_redirects=False)
    assert survivor.status_code == 200


def test_detail_alias_route_family_does_not_capture_same_id_on_other_prefix(monkeypatch):
    client, _ = seo_client(monkeypatch)

    response = client.get(
        "/inventory-detail/235424/unrelated/same-numeric-id/",
        follow_redirects=False,
    )

    assert response.status_code == 404
    assert "location" not in response.headers


def test_legacy_plan_becomes_canonical_when_inventory_survivor_disappears(monkeypatch):
    client, _ = seo_client(monkeypatch)
    alias = "/plan/235424/premier/creole-3256h32447/"

    redirected = client.get(alias, follow_redirects=False)
    assert redirected.status_code == 302
    assert "no-store" in redirected.headers["cache-control"]

    import seo_routes

    canonical_plan = {
        "id": "floorplan-235424",
        "legacy_plan_id": "235424",
        "model_name": "Premier / Creole 3256H32447",
        "manufacturer": "Champion Homes",
        "status": "Orderable",
        "detail_url": "https://www.texashomeoutlet.com" + alias,
        "quote_url": "https://www.texashomeoutlet.com/quote/floorplan/235424/dealer/3522",
        "specs": {"beds": 4, "baths": 2},
    }
    monkeypatch.setattr(seo_routes, "_get_homes", lambda: [FAKE_HOMES[1], canonical_plan])
    monkeypatch.setattr(seo_routes, "_registry_cache", None)
    monkeypatch.setattr(seo_routes, "_registry_built_at", 0.0)

    canonical = client.get(alias, follow_redirects=False)
    assert canonical.status_code == 200
    assert 'rel="canonical" href="https://www.texashomeoutlet.com' + alias + '"' in canonical.text


def test_unknown_route_returns_real_404_not_soft_404(monkeypatch):
    client, _ = seo_client(monkeypatch)
    response = client.get("/this-page-does-not-exist")
    assert response.status_code == 404
    assert "noindex" in response.text


def test_ad_studio_alias_is_the_google_ads_staff_route(monkeypatch):
    """Staff type /ad-studio. It must open the noindex staff app, not a 404."""
    client, _ = seo_client(monkeypatch)
    response = client.get("/ad-studio", follow_redirects=False)
    assert response.status_code == 200
    assert '<meta name="robots" content="noindex" />' in response.text
    assert client.get("/this-is-not-ad-studio").status_code == 404


def test_admin_routes_are_noindex_but_200(monkeypatch):
    client, _ = seo_client(monkeypatch)
    for path in ("/crm", "/documents", "/studio", "/ad-studio", "/staff", "/team"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert '<meta name="robots" content="noindex" />' in response.text, path
        assert 'rel="canonical"' not in response.text, path


def test_public_routes_have_unique_titles_and_canonicals(monkeypatch):
    client, _ = seo_client(monkeypatch)
    titles = {}
    for path in (
        "/",
        "/inventory",
        "/contact",
        "/appointments",
        "/about",
        "/financing",
        "/faq",
        "/warranty",
        "/delivery",
    ):
        body = client.get(path).text
        title = re.search(r"<title>(.*?)</title>", body, re.DOTALL).group(1)
        titles[path] = title
        canonical = re.search(r'rel="canonical" href="([^"]+)"', body).group(1)
        assert canonical.endswith(path if path != "/" else "/"), path
    assert len(set(titles.values())) == len(titles), "titles must be unique per route"


def test_trust_content_pages_have_crawlable_blocks(monkeypatch):
    client, _ = seo_client(monkeypatch)
    cases = {
        "/about": "About Texas Home Outlet",
        "/financing": "Manufactured Home Financing in Huffman, TX",
        "/faq": "Frequently Asked Questions",
        "/warranty": "Warranty &amp; Service",
        "/delivery": "Delivery &amp; Setup",
    }
    for path, h1_snippet in cases.items():
        body = client.get(path).text
        assert f"<h1>{h1_snippet}" in body, path
        assert '"@type": "LocalBusiness"' in body, path
        assert "noindex" not in body, path


def test_faq_page_emits_faqpage_jsonld(monkeypatch):
    client, _ = seo_client(monkeypatch)
    body = client.get("/faq").text
    faq_pages = [
        json.loads(b)
        for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, re.DOTALL)
        if '"FAQPage"' in b
    ]
    assert faq_pages, "FAQPage JSON-LD missing"
    entities = faq_pages[0].get("mainEntity", [])
    assert len(entities) >= 5, "expected at least 5 FAQ entries"
    assert all(e.get("@type") == "Question" for e in entities)
    assert all(e.get("acceptedAnswer", {}).get("@type") == "Answer" for e in entities)
    financing = next(
        e for e in entities if e.get("name") == "How does financing work for a manufactured home?"
    )
    financing_text = financing["acceptedAnswer"]["text"]
    assert "may qualify for FHA Title I/II, VA, or USDA programs." in financing_text
    assert "Ask us about lenders that finance manufactured homes." in financing_text


def test_legacy_about_us_redirects_to_new_about_page(monkeypatch):
    client, _ = seo_client(monkeypatch)
    response = client.get("/about-us", follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["location"] == "/about"


def test_legacy_financing_trailing_slash_redirects_to_public_page(monkeypatch):
    client, _ = seo_client(monkeypatch)
    # "/financing/" should canonicalize to the real "/financing" public page.
    response = client.get("/financing/", follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["location"] == "/financing"


def test_trust_content_pages_listed_in_sitemap(monkeypatch):
    client, _ = seo_client(monkeypatch)
    body = client.get("/sitemap.xml").text
    for path in ("/about", "/financing", "/faq", "/warranty", "/delivery"):
        assert f"{path}</loc>" in body, path


def test_llms_txt_has_noindex_header(monkeypatch):
    client, _ = seo_client(monkeypatch)
    response = client.get("/llms.txt")
    assert response.status_code == 200
    assert response.headers.get("x-robots-tag") == "noindex"
    # uptime checks and crawlers probe with HEAD; must not fall into the
    # catch-all 404
    assert client.head("/llms.txt").status_code == 200


def test_jsonld_escapes_script_breakout(monkeypatch):
    """Crawled values containing </script> must not escape the JSON-LD block."""
    client, _ = seo_client(monkeypatch)
    import seo_routes

    evil = dict(FAKE_HOMES[0])
    evil["description"] = 'nice home</script><script>alert("xss")</script>'
    monkeypatch.setattr(seo_routes, "_get_homes", lambda: [evil])
    monkeypatch.setattr(seo_routes, "_registry_cache", None)
    monkeypatch.setattr(seo_routes, "_registry_built_at", 0.0)

    body = client.get("/inventory-detail/43372/texas-home-outlet/huffman/premier/").text
    jsonld = re.search(
        r'<script type="application/ld\+json">(.*?)</script>', body, re.DOTALL
    ).group(1)
    assert "</script>" not in jsonld
    assert "\\u003c" in jsonld
    # payload still parses to the original value
    assert "</script>" in json.loads(jsonld)["description"]
    assert '<script>alert("xss")</script>' not in body


def test_seo_failure_never_breaks_page_serving(monkeypatch):
    """If the inventory provider blows up, pages still render."""
    client, _ = seo_client(monkeypatch)
    import seo_routes

    def boom():
        raise RuntimeError("inventory backend down")

    monkeypatch.setattr(seo_routes, "_get_homes", boom)
    monkeypatch.setattr(seo_routes, "_registry_cache", None)
    response = client.get("/")
    assert response.status_code == 200
    assert client.get("/sitemap.xml").status_code == 200


# ── Analytics / ad-pixel injection (opt-in, runtime env-gated) ──────────────

_ANALYTICS_ENV = ("GA4_MEASUREMENT_ID", "GTM_CONTAINER_ID", "META_PIXEL_ID", "TIKTOK_PIXEL_ID")


def test_analytics_no_op_when_unconfigured(monkeypatch):
    """No analytics env -> no third-party snippet anywhere (head unchanged)."""
    for var in _ANALYTICS_ENV:
        monkeypatch.delenv(var, raising=False)
    client, _ = seo_client(monkeypatch)
    body = client.get("/").text
    assert "googletagmanager.com" not in body
    assert "fbq(" not in body
    assert "TiktokAnalyticsObject" not in body


def test_ga4_loader_emitted_only_with_valid_id_and_defaults_to_denied(monkeypatch):
    for var in _ANALYTICS_ENV:
        monkeypatch.delenv(var, raising=False)
    client, _ = seo_client(monkeypatch)
    # malformed id -> treated as unset, no snippet
    monkeypatch.setenv("GA4_MEASUREMENT_ID", "not-a-valid-id")
    assert "googletagmanager.com/gtag/js" not in client.get("/").text
    # Valid ID -> consent bootloader is present, but the third-party script is
    # created only after an explicit stored grant (no eager <script src>).
    monkeypatch.setenv("GA4_MEASUREMENT_ID", "G-ABC1234XYZ")
    body = client.get("/").text
    assert '"GA4_MEASUREMENT_ID":"G-ABC1234XYZ"' in body
    assert '<script src="/tho-analytics.js"></script>' in body
    assert '<script async src="https://www.googletagmanager.com/gtag/js' not in body
    js = client.get("/tho-analytics.js").text
    assert "https://www.googletagmanager.com/gtag/js?id=" in js
    assert "analytics_storage:'denied'" in js
    assert "ad_storage:'denied'" in js
    assert "ad_user_data:'granted',ad_personalization:'denied'" in js
    assert "__THO_ENABLE_ANALYTICS__" in js
    assert "__THO_ANALYTICS_CONFIGURED__=true" in js
    assert "send_page_view:false" in js


def test_meta_and_tiktok_pixels_emitted_with_valid_ids(monkeypatch):
    for var in _ANALYTICS_ENV:
        monkeypatch.delenv(var, raising=False)
    client, _ = seo_client(monkeypatch)
    monkeypatch.setenv("META_PIXEL_ID", "1234567890123456")
    monkeypatch.setenv("TIKTOK_PIXEL_ID", "CABCDEF1234567890GHIJK")
    body = client.get("/").text
    assert '"META_PIXEL_ID":"1234567890123456"' in body
    assert '"TIKTOK_PIXEL_ID":"CABCDEF1234567890GHIJK"' in body
    js = client.get("/tho-analytics.js").text
    assert "w.fbq('init',id)" in js
    assert "ttq.load(id)" in js


def test_analytics_bootloader_uses_one_first_party_consent_key(monkeypatch):
    for var in _ANALYTICS_ENV:
        monkeypatch.delenv(var, raising=False)
    client, _ = seo_client(monkeypatch)
    monkeypatch.setenv("GTM_CONTAINER_ID", "GTM-ABC1234")
    assert '"GTM_CONTAINER_ID":"GTM-ABC1234"' in client.get("/").text
    js = client.get("/tho-analytics.js").text
    assert "tho_analytics_consent_v1" in js
    assert "localStorage.getItem" in js
    assert "pref==='granted'" in js


def test_analytics_never_emitted_on_noindex_routes(monkeypatch):
    """Even with valid IDs set, pixels must NOT load on operator/admin/404
    (noindex) surfaces — only on indexable public pages."""
    for var in _ANALYTICS_ENV:
        monkeypatch.delenv(var, raising=False)
    client, _ = seo_client(monkeypatch)
    monkeypatch.setenv("GA4_MEASUREMENT_ID", "G-ABC1234XYZ")
    monkeypatch.setenv("META_PIXEL_ID", "1234567890123456")
    # public page DOES get the pixels...
    assert "G-ABC1234XYZ" in client.get("/").text
    # ...but noindex admin routes and the 404 page do NOT.
    for path in ("/crm", "/documents", "/this-page-does-not-exist"):
        body = client.get(path).text
        assert "G-ABC1234XYZ" not in body, path
        assert "fbq('init'" not in body, path
        assert "googletagmanager.com" not in body, path


def test_og_image_defaults_to_bundled_card(monkeypatch):
    """F4: public pages emit the bundled brand og:image/twitter:image by
    default (the 1200x630 raster ships with the frontend build, so the URL
    cannot dangle); OG_IMAGE_URL="" explicitly opts out."""
    monkeypatch.setenv("OG_IMAGE_URL", "")
    client, _ = seo_client(monkeypatch)
    assert 'property="og:image"' not in client.get("/").text
    monkeypatch.delenv("OG_IMAGE_URL", raising=False)
    body = client.get("/").text
    assert re.search(r'<meta property="og:image" content="https?://[^"]+/og-card\.png"', body)
    assert 'name="twitter:image"' in body


def test_detail_page_prefers_home_photo_for_og_image(monkeypatch):
    """A detail page with a hero image uses the HOME photo (so sharing a
    specific home shows that home), not the brand default."""
    monkeypatch.setenv("OG_IMAGE_URL", "/og-card.png")
    client, _ = seo_client(monkeypatch)
    body = client.get("/inventory-detail/43372/texas-home-outlet/huffman/premier/").text
    m = re.search(r'<meta property="og:image" content="([^"]+)"', body)
    assert m, "detail page should have an og:image"
    assert "og-card.png" not in m.group(1)  # used the home photo, not the default


def _jsonld_blocks(body):
    return [
        json.loads(b)
        for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, re.DOTALL)
    ]


def test_local_business_jsonld_has_local_seo_fields(monkeypatch):
    # The homepage LocalBusiness node should carry the recommended local-pack
    # signals Google uses for maps placement: priceRange + areaServed (cities).
    # These are config-driven (config.yaml business.*), so they ride along on
    # every public page without code changes.
    client, _ = seo_client(monkeypatch)
    biz = next(b for b in _jsonld_blocks(client.get("/").text) if b.get("@type") == "LocalBusiness")
    assert biz["priceRange"]  # non-empty (config: "$$")
    served = {c["name"] for c in biz["areaServed"]}
    assert {"Huffman", "Humble", "Baytown"} <= served
    assert all(c["@type"] == "City" for c in biz["areaServed"])


def test_local_business_jsonld_has_organization_entity_fields(monkeypatch):
    # Organization-level signals enrich Google's knowledge-graph understanding:
    # logo (config-driven), a sales contactPoint, and knowsAbout topics. All
    # accurate + additive — no price/review/FAQ data, so no policy risk.
    client, _ = seo_client(monkeypatch)
    biz = next(b for b in _jsonld_blocks(client.get("/").text) if b.get("@type") == "LocalBusiness")
    assert biz["logo"].startswith("http") and biz["logo"].endswith(".svg")
    cp = biz["contactPoint"]
    assert cp["@type"] == "ContactPoint"
    assert cp["contactType"] == "sales" and cp["telephone"]
    assert "Manufactured homes" in biz["knowsAbout"]


def test_local_business_jsonld_links_verified_google_place(monkeypatch):
    client, _ = seo_client(monkeypatch)
    biz = next(b for b in _jsonld_blocks(client.get("/").text) if b.get("@type") == "LocalBusiness")
    google_profiles = [url for url in biz["sameAs"] if "google.com/maps" in url]

    assert google_profiles == [
        "https://www.google.com/maps/search/?api=1&query=Texas%20Home%20Outlet&query_place_id=ChIJkaBdugirQIYROqKB6XUKQk8"
    ]


def test_city_landing_page_serves_200_with_local_content(monkeypatch):
    # A served city (config area_served) gets a real local-SEO landing page.
    client, _ = seo_client(monkeypatch)
    r = client.get("/manufactured-homes-in-humble-tx")
    assert r.status_code == 200
    body = r.text
    assert "<h1>Manufactured &amp; Mobile Homes in Humble, TX</h1>" in body
    assert "Homes and floorplans offered by our Huffman showroom:" in body
    assert "listed at that showroom" not in body
    assert 'rel="canonical"' in body and "/manufactured-homes-in-humble-tx" in body
    assert '"@type": "LocalBusiness"' in body
    assert "tel:" in body and "/inventory" in body  # NAP + CTA


def test_served_city_no_longer_301s_to_inventory(monkeypatch):
    # Baytown was a legacy-vendor 301 -> /inventory; now a real city page (200).
    client, _ = seo_client(monkeypatch)
    r = client.get("/manufactured-homes-in-baytown-tx", follow_redirects=False)
    assert r.status_code == 200
    assert "Baytown, TX" in r.text


def test_unserved_city_still_301s(monkeypatch):
    # Jasper isn't a served city -> keep the legacy 301 (no misleading page).
    client, _ = seo_client(monkeypatch)
    r = client.get("/manufactured-homes-in-jasper-tx", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/inventory"


def test_city_page_trailing_slash_301s_to_canonical(monkeypatch):
    client, _ = seo_client(monkeypatch)
    r = client.get("/manufactured-homes-in-humble-tx/", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/manufactured-homes-in-humble-tx"


def test_city_pages_listed_in_sitemap(monkeypatch):
    client, _ = seo_client(monkeypatch)
    body = client.get("/sitemap.xml").text
    assert "/manufactured-homes-in-humble-tx</loc>" in body
    assert "/manufactured-homes-in-baytown-tx</loc>" in body
    assert "/locations</loc>" in body


def _visible_home_hrefs(body: str) -> list[str]:
    visible = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", body)
    return re.findall(r'href="(/homes/[^"]+)"', visible)


def test_city_page_renders_at_most_eight_home_links(monkeypatch):
    """A catalog larger than the cap still publishes only 8 home links."""
    import seo_routes

    client, _ = seo_client(monkeypatch)
    homes = []
    for index in range(12):
        stock = str(50000 + index)
        homes.append(
            {
                "id": stock,
                "stock_number": stock,
                "model_name": f"Model {index:02d}",
                "status": "Pre-Owned",
                "inventory_kind": "pre_owned",
                "display_price": "Call for Price",
            }
        )
    monkeypatch.setattr(seo_routes, "_get_homes", lambda: homes)
    monkeypatch.setattr(seo_routes, "_registry_cache", None)
    monkeypatch.setattr(seo_routes, "_registry_built_at", 0.0)
    body = client.get("/manufactured-homes-in-humble-tx").text
    assert len(homes) > 8
    assert _visible_home_hrefs(body) == [
        f"/homes/{50000 + index}-model-{index:02d}" for index in range(8)
    ]


def test_city_sitemap_lastmod_follows_facts_file(monkeypatch, tmp_path):
    import seo_routes

    client, _ = seo_client(monkeypatch)
    facts_path = (
        Path(__file__).resolve().parent.parent
        / "frontend"
        / "src"
        / "content"
        / "cityLocalFacts.json"
    )
    facts = json.loads(facts_path.read_text(encoding="utf-8"))
    monkeypatch.setattr(seo_routes, "_city_facts_cache", None)
    entries = _sitemap_entries(client.get("/sitemap.xml").text)
    city = next(item for item in entries if item[0].endswith("/manufactured-homes-in-humble-tx"))
    locations = next(item for item in entries if item[0].endswith("/locations"))
    assert city[1] == facts["content_lastmod"]
    assert locations[1] == facts["content_lastmod"]

    facts["content_lastmod"] = "2026-10-19"
    rewritten = tmp_path / "cityLocalFacts.json"
    rewritten.write_text(json.dumps(facts), encoding="utf-8")
    monkeypatch.setattr(seo_routes, "_CITY_FACTS_PATH", rewritten)
    monkeypatch.setattr(seo_routes, "_city_facts_cache", None)
    moved = _sitemap_entries(client.get("/sitemap.xml").text)
    city = next(item for item in moved if item[0].endswith("/manufactured-homes-in-humble-tx"))
    locations = next(item for item in moved if item[0].endswith("/locations"))
    home = next(item for item in moved if item[0].rstrip("/").endswith("texashomeoutlet.com"))
    assert city[1] == "2026-10-19"
    assert locations[1] == "2026-10-19"
    assert home[1] == "2026-10-18"


def test_invalid_city_content_lastmod_omits_the_date(monkeypatch, tmp_path, caplog):
    import seo_routes

    client, _ = seo_client(monkeypatch)
    facts_path = (
        Path(__file__).resolve().parent.parent
        / "frontend"
        / "src"
        / "content"
        / "cityLocalFacts.json"
    )
    facts = json.loads(facts_path.read_text(encoding="utf-8"))
    facts["content_lastmod"] = "2026-13-40"
    rewritten = tmp_path / "cityLocalFacts.json"
    rewritten.write_text(json.dumps(facts), encoding="utf-8")
    monkeypatch.setattr(seo_routes, "_CITY_FACTS_PATH", rewritten)
    monkeypatch.setattr(seo_routes, "_city_facts_cache", None)
    with caplog.at_level(logging.WARNING, logger="seo_routes"):
        response = client.get("/sitemap.xml")
    assert response.status_code == 200
    entries = _sitemap_entries(response.text)
    city = next(item for item in entries if item[0].endswith("/manufactured-homes-in-humble-tx"))
    assert city[1] is None
    assert "content_lastmod" in caplog.text
    page = client.get("/manufactured-homes-in-humble-tx")
    assert page.status_code == 200
    assert "<h2>Humble beside U.S. Highway 59</h2>" in page.text


@pytest.mark.parametrize(
    "contents",
    [None, "{", "[]"],
    ids=["missing", "invalid-json", "not-an-object"],
)
def test_city_facts_file_failure_still_renders_page_and_sitemap(
    monkeypatch, caplog, tmp_path, contents
):
    import seo_routes

    client, _ = seo_client(monkeypatch)
    facts_path = tmp_path / "cityLocalFacts.json"
    if contents is not None:
        facts_path.write_text(contents, encoding="utf-8")
    monkeypatch.setattr(seo_routes, "_CITY_FACTS_PATH", facts_path)
    monkeypatch.setattr(seo_routes, "_city_facts_cache", None)
    with caplog.at_level(logging.WARNING, logger="seo_routes"):
        page = client.get("/manufactured-homes-in-humble-tx")
        sitemap = client.get("/sitemap.xml")
    assert page.status_code == 200
    plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page.text))
    held = (
        f"{business_name()} delivers manufactured and mobile homes to Humble, TX "
        f"from the {business_city()} showroom at {business_address()}. "
        f"Call {business_phone()} for the current price."
    )
    assert "<h1>Manufactured &amp; Mobile Homes in Humble, TX</h1>" in page.text
    assert held in plain
    assert "Humble beside U.S. Highway 59" not in page.text
    assert "<h2>" not in page.text
    assert sitemap.status_code == 200
    assert "/manufactured-homes-in-humble-tx</loc>" in sitemap.text
    entries = _sitemap_entries(sitemap.text)
    city = next(item for item in entries if item[0].endswith("/manufactured-homes-in-humble-tx"))
    locations = next(item for item in entries if item[0].endswith("/locations"))
    home = next(item for item in entries if item[0].rstrip("/").endswith("texashomeoutlet.com"))
    assert city[1] is None
    assert locations[1] is None
    assert home[1] == "2026-10-18"
    assert "local section" in caplog.text


def _shingles(body: str, k: int = 5) -> set[tuple[str, ...]]:
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", body)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    words = re.findall(r"[a-z0-9$']+", html.unescape(text).lower())
    return set(tuple(words[i : i + k]) for i in range(len(words) - k + 1))


def test_city_pages_keep_the_delivery_sentence_and_add_local_facts(monkeypatch):
    """The owner-held delivery sentence stays verbatim. Each town page adds a
    distinct local section and a breadcrumb through /locations."""
    client, _ = seo_client(monkeypatch)
    cities = list(get_business()["area_served"])
    humble = client.get("/manufactured-homes-in-humble-tx").text
    plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", humble))
    held = (
        f"{business_name()} delivers manufactured and mobile homes to Humble, TX "
        f"from the {business_city()} showroom at {business_address()}. "
        f"Call {business_phone()} for the current price."
    )
    assert held in plain
    crumbs = next(
        block for block in _jsonld_blocks(humble) if block.get("@type") == "BreadcrumbList"
    )
    assert [item["name"] for item in crumbs["itemListElement"]] == [
        "Home",
        "Locations",
        "Homes in Humble, TX",
    ]
    assert 'aria-label="Breadcrumb"' in humble
    assert 'href="/locations"' in humble
    assert "<h2>Humble beside U.S. Highway 59</h2>" in humble
    assert "west-southwest" not in plain
    assert "west from the showroom" in plain
    assert not any(block.get("@type") == "Product" for block in _jsonld_blocks(humble))

    def visible(path: str) -> str:
        raw = client.get(path).text
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw))

    cleveland = visible("/manufactured-homes-in-cleveland-tx")
    assert "through the Humble area" not in cleveland
    assert "farther up that highway" not in cleveland
    crosby = visible("/manufactured-homes-in-crosby-tx")
    assert "meets FM 1960" not in crosby
    assert "which the handbook also places on Farm Road 1960." in crosby
    assert "south-southeast from the showroom" in crosby
    kingwood = visible("/manufactured-homes-in-kingwood-tx")
    assert "west from the showroom" in kingwood
    assert "of Huffman" not in kingwood
    livingston = visible("/manufactured-homes-in-livingston-tx")
    assert (
        "Cleveland and Livingston are both on U.S. Highway 59, the handbook entries say."
        in livingston
    )
    assert "north-northeast from the showroom" in livingston
    assert "continues north from the Cleveland area" not in livingston
    conroe = visible("/manufactured-homes-in-conroe-tx")
    assert "The county seat is on Interstate 45." in conroe
    assert "the highway into the county seat" not in conroe

    baytown = _shingles(client.get("/manufactured-homes-in-baytown-tx").text)
    for city in cities:
        slug = city.lower().replace(" ", "-")
        body = client.get(f"/manufactured-homes-in-{slug}-tx").text
        assert f"<h1>Manufactured &amp; Mobile Homes in {city}, TX</h1>" in body
        assert "<h2>" in body
        assert 'href="/inventory"' in body
        assert 'href="/locations"' in body
        if city != "Baytown":
            page = _shingles(body)
            union = page | baytown
            assert union
            assert len(page & baytown) / len(union) < 0.6


def test_locations_index_links_every_city_page(monkeypatch):
    client, _ = seo_client(monkeypatch)
    response = client.get("/locations")
    assert response.status_code == 200
    body = response.text
    assert "<h1>Locations</h1>" in body
    assert 'rel="canonical" href="https://www.texashomeoutlet.com/locations"' in body
    crumbs = next(block for block in _jsonld_blocks(body) if block.get("@type") == "BreadcrumbList")
    assert [item["name"] for item in crumbs["itemListElement"]] == ["Home", "Locations"]
    listed = next(block for block in _jsonld_blocks(body) if block.get("@type") == "ItemList")
    names = [item["name"] for item in listed["itemListElement"]]
    assert names == [f"Homes in {city}, TX" for city in get_business()["area_served"]]
    for city in get_business()["area_served"]:
        slug = city.lower().replace(" ", "-")
        assert f'href="/manufactured-homes-in-{slug}-tx"' in body
    assert not any(block.get("@type") == "Product" for block in _jsonld_blocks(body))
    assert not any(_jsonld_has_price_markup(block) for block in _jsonld_blocks(body))
    trailing = client.get("/locations/", follow_redirects=False)
    assert trailing.status_code == 301
    assert trailing.headers["location"] == "/locations"


def test_home_category_pages_are_indexable_and_filter_inventory(monkeypatch):
    client, _ = seo_client(monkeypatch)
    cases = {
        "/single-wide": (
            "Single Wide Manufactured Homes",
            "/plan/223034/skyliner/4732b/",
            "/inventory-detail/43372/texas-home-outlet/huffman/premier/",
        ),
        "/double-wide": (
            "Double Wide Manufactured Homes",
            "/inventory-detail/43372/texas-home-outlet/huffman/premier/",
            "/plan/223034/skyliner/4732b/",
        ),
    }

    for path, (heading, matching_path, excluded_path) in cases.items():
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 200, path
        body = response.text
        assert f"<h1>{heading}" in body
        assert f'<link rel="canonical" href="https://www.texashomeoutlet.com{path}"' in body
        assert matching_path in body
        assert excluded_path not in body
        assert "listed homes plus orderable floorplans" in body
        assert "live inventory" not in body.lower()
        assert "ready now" not in body.lower()

        item_lists = [block for block in _jsonld_blocks(body) if block.get("@type") == "ItemList"]
        assert item_lists, path
        item_urls = [item["url"] for item in item_lists[0]["itemListElement"]]
        assert any(matching_path in url for url in item_urls)
        assert not any(excluded_path in url for url in item_urls)


def test_home_category_pages_canonicalize_case_and_trailing_slashes(monkeypatch):
    client, _ = seo_client(monkeypatch)
    cases = {
        "/single-wide/": "/single-wide",
        "/Single-Wide": "/single-wide",
        "/double-wide/": "/double-wide",
        "/Double-Wide": "/double-wide",
    }
    for path, canonical_path in cases.items():
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 301, path
        assert response.headers["location"] == canonical_path, path


def test_home_category_pages_are_listed_once_in_sitemap(monkeypatch):
    client, _ = seo_client(monkeypatch)
    body = client.get("/sitemap.xml").text
    for path in ("/single-wide", "/double-wide"):
        assert body.count(f"https://www.texashomeoutlet.com{path}</loc>") == 1


def test_inventory_hub_links_to_home_category_pages(monkeypatch):
    client, _ = seo_client(monkeypatch)
    body = client.get("/inventory").text
    assert '<a href="/single-wide">Single Wide Homes</a>' in body
    assert '<a href="/double-wide">Double Wide Homes</a>' in body


def test_empty_home_category_is_not_indexable_or_listed(monkeypatch):
    import seo_routes

    client, _ = seo_client(monkeypatch)
    only_double = {**FAKE_HOMES[0], "classification": "  DOUBLE-wide  "}
    monkeypatch.setattr(seo_routes, "_get_homes", lambda: [only_double])
    monkeypatch.setattr(seo_routes, "_registry_cache", None)

    response = client.get("/single-wide", follow_redirects=False)
    assert response.status_code == 404
    assert '<meta name="robots" content="noindex"' in response.text
    assert "No single wide homes are listed right now" in response.text
    assert 'href="/inventory"' in response.text
    assert 'href="/contact"' in response.text
    assert "Clear All" not in response.text

    sitemap = client.get("/sitemap.xml").text
    assert "/single-wide</loc>" not in sitemap
    assert "/double-wide</loc>" in sitemap


def test_inventory_outage_makes_categories_temporarily_unavailable(monkeypatch):
    import seo_routes

    client, _ = seo_client(monkeypatch)

    def unavailable():
        raise RuntimeError("catalog unavailable")

    monkeypatch.setattr(seo_routes, "_get_homes", unavailable)
    monkeypatch.setattr(seo_routes, "_registry_cache", None)

    response = client.get("/single-wide", follow_redirects=False)
    assert response.status_code == 503
    assert '<meta name="robots" content="noindex"' in response.text
    assert "Inventory is temporarily unavailable" in response.text
    assert 'href="/inventory"' in response.text
    assert 'href="/contact"' in response.text

    sitemap = client.get("/sitemap.xml").text
    assert "/single-wide</loc>" not in sitemap
    assert "/double-wide</loc>" not in sitemap


def test_returned_inventory_failure_makes_categories_temporarily_unavailable(monkeypatch):
    import seo_routes

    client, _ = seo_client(monkeypatch)
    monkeypatch.setattr(
        seo_routes,
        "_get_homes",
        lambda: {"success": False, "error": "catalog unavailable", "homes": []},
    )
    monkeypatch.setattr(seo_routes, "_registry_cache", None)

    response = client.get("/single-wide", follow_redirects=False)
    assert response.status_code == 503
    assert '<meta name="robots" content="noindex"' in response.text
    assert "Inventory is temporarily unavailable" in response.text


def test_firestore_only_category_home_renders_without_false_link_or_schema(monkeypatch):
    import seo_routes

    client, _ = seo_client(monkeypatch)
    firestore_home = {
        "id": "firestore-only",
        "model_name": "Firestore Only Home",
        "manufacturer": "Current Homes",
        "classification": " single-section ",
        "specs": {"beds": 3, "baths": 2},
    }
    monkeypatch.setattr(
        seo_routes,
        "_get_homes",
        lambda: {"success": True, "homes": [firestore_home]},
    )
    monkeypatch.setattr(seo_routes, "_registry_cache", None)

    response = client.get("/single-wide", follow_redirects=False)
    assert response.status_code == 200
    assert "<ul><li>Firestore Only Home" in response.text
    assert ">Firestore Only Home</a>" not in response.text
    item_lists = [
        block for block in _jsonld_blocks(response.text) if block.get("@type") == "ItemList"
    ]
    assert item_lists == []


def test_contact_page_has_crawlable_nap_block(monkeypatch):
    # /contact must not be an empty SPA shell to non-JS crawlers — it is the
    # strongest NAP (name/address/phone) signal on the site. Server-render an
    # <h1> + the canonical NAP so search engines can read it without executing JS.
    client, _ = seo_client(monkeypatch)
    body = client.get("/contact").text
    assert "<h1>Contact Texas Home Outlet" in body
    assert "10685 FM 1960 East" in body
    assert 'href="tel:(281) 324-3020"' in body
    assert "google.com/maps/search" in body  # directions link


def test_appointments_page_has_crawlable_block(monkeypatch):
    client, _ = seo_client(monkeypatch)
    body = client.get("/appointments").text
    assert "<h1>Book a Showroom Visit" in body
    assert "10685 FM 1960 East" in body  # showroom address rendered server-side


def test_detail_page_emits_breadcrumb_jsonld(monkeypatch):
    # A BreadcrumbList (Home -> Inventory -> model) gives Google a breadcrumb
    # rich result and reinforces site hierarchy. It's independent of price, so
    # even call-for-price homes (no Product snippet) still get one.
    client, _ = seo_client(monkeypatch)
    body = client.get("/inventory-detail/43372/texas-home-outlet/huffman/premier/").text
    crumbs = [b for b in _jsonld_blocks(body) if b.get("@type") == "BreadcrumbList"]
    assert crumbs, "detail page should emit a BreadcrumbList"
    items = crumbs[0]["itemListElement"]
    assert [i["name"] for i in items] == ["Home", "Inventory", "Premier / Creole 3256H32447"]
    assert [i["position"] for i in items] == [1, 2, 3]
    assert items[1]["item"].endswith("/inventory")


def test_call_for_price_page_still_gets_breadcrumb(monkeypatch):
    client, _ = seo_client(monkeypatch)
    body = client.get("/plan/223034/skyliner/4732b/").text
    blocks = _jsonld_blocks(body)
    assert not [b for b in blocks if b.get("@type") == "Product"]  # no Product (no price)
    assert [b for b in blocks if b.get("@type") == "BreadcrumbList"]  # but breadcrumb yes


def test_gzip_middleware_registered_and_serves(monkeypatch):
    # Compression is registered as the outermost layer; the large SEO HTML is
    # exactly the payload it should shrink. Assert registration (deterministic)
    # and that a large page still round-trips correctly through it.
    from starlette.middleware.gzip import GZipMiddleware

    client, main = seo_client(monkeypatch)
    assert any(m.cls is GZipMiddleware for m in main.app.user_middleware)
    body = client.get("/", headers={"Accept-Encoding": "gzip"}).text
    assert "Texas Home Outlet" in body and len(body) > 500


def test_inventory_page_emits_itemlist_jsonld(monkeypatch):
    # /inventory (and /) emit an ItemList of homes -> a list/carousel rich-result
    # candidate. Items carry only position/url/name (no fabricated price/rating).
    client, _ = seo_client(monkeypatch)
    lists = [
        b for b in _jsonld_blocks(client.get("/inventory").text) if b.get("@type") == "ItemList"
    ]
    assert lists, "/inventory should emit an ItemList"
    items = lists[0]["itemListElement"]
    assert items and all(i["@type"] == "ListItem" for i in items)
    # positions are 1-indexed and sequential
    assert [i["position"] for i in items] == list(range(1, len(items) + 1))
    # each item links to a real detail/plan URL, with no price/offer fabrication
    assert any("/inventory-detail/43372/" in i["url"] for i in items)
    assert all("offers" not in i and "price" not in i for i in items)


def test_legal_routes_render_shared_copy_on_canonical_host(monkeypatch):
    import html

    import seo_routes

    client, _ = seo_client(monkeypatch)
    host = {"Host": "www.texashomeoutlet.com"}
    for page, content in seo_routes.LEGAL_PAGES.items():
        path = f"/{page}"
        response = client.get(path, headers=host, follow_redirects=False)
        assert response.status_code == 200
        body = response.text
        assert f"<title>{content['title']} | Texas Home Outlet</title>" in body
        assert f'<meta name="description" content="{html.escape(content["description"])}"' in body
        assert f'href="https://www.texashomeoutlet.com{path}"' in body
        assert 'name="robots" content="noindex"' not in body
        assert f"<h1>{content['title']}</h1>" in body
        assert "Effective date: October 7, 2026." in body
        assert "[EFFECTIVE DATE]" not in body
        for heading, text in content["sections"]:
            assert html.escape(heading) in body
            assert html.escape(text) in body


def test_legal_routes_canonicalize_and_appear_in_sitemap(monkeypatch):
    client, _ = seo_client(monkeypatch)
    sitemap = client.get("/sitemap.xml").text
    for path in ("/privacy", "/terms"):
        for variant in (path + "/", path.title()):
            response = client.get(variant, follow_redirects=False)
            assert response.status_code == 301
            assert response.headers["location"] == path
        assert sitemap.count(f"https://www.texashomeoutlet.com{path}</loc>") == 1
    assert "/legal</loc>" not in sitemap
    assert client.get("/privacy/unknown", follow_redirects=False).status_code == 404
    assert client.get("/terms/unknown", follow_redirects=False).status_code == 404


def test_legal_aliases_redirect_to_canonical_pages(monkeypatch):
    client, _ = seo_client(monkeypatch)
    for path, target in (
        ("/legal", "/privacy"),
        ("/legal/", "/privacy"),
        ("/Legal", "/privacy"),
        ("/privacy-policy", "/privacy"),
        ("/terms-of-use", "/terms"),
        ("/terms-of-service", "/terms"),
        ("/terms-and-conditions", "/terms"),
    ):
        response = client.get(
            path, headers={"Host": "www.texashomeoutlet.com"}, follow_redirects=False
        )
        assert response.status_code == 301, path
        assert response.headers["location"] == target
        assert client.get(path).status_code == 200
    assert client.get("/legal/unknown", follow_redirects=False).status_code == 404


def _title_h1_canonical(body):
    title = re.search(r"<title>(.*?)</title>", body, re.DOTALL).group(1)
    h1 = re.search(r"<h1>(.*?)</h1>", body, re.DOTALL).group(1)
    canonical = re.search(r'rel="canonical" href="([^"]+)"', body).group(1)
    return title, h1, canonical


def test_city_floorplan_and_home_pages_have_unique_title_h1_canonical(monkeypatch):
    client, _ = seo_client(monkeypatch)
    city = client.get("/manufactured-homes-in-humble-tx")
    plan = client.get("/plan/223034/skyliner/4732b/")
    home = client.get("/homes/44490-pre-owned-big-blue")
    assert city.status_code == plan.status_code == home.status_code == 200

    city_t, city_h, city_c = _title_h1_canonical(city.text)
    plan_t, plan_h, plan_c = _title_h1_canonical(plan.text)
    home_t, home_h, home_c = _title_h1_canonical(home.text)

    assert "Humble, TX" in city_t and "Humble, TX" in city_h
    assert city_c.endswith("/manufactured-homes-in-humble-tx")
    assert "Skyliner 4732B" in plan_t and "Skyliner 4732B" in plan_h
    assert "/plan/223034/skyliner/4732b/" in plan_c
    assert "Big Blue" in home_t and "PRE-OWNED / Big Blue" in home_h
    assert home_c.endswith("/homes/44490-pre-owned-big-blue")
    assert len({city_t, plan_t, home_t}) == 3
    assert len({city_h, plan_h, home_h}) == 3
    assert len({city_c, plan_c, home_c}) == 3
    assert "Manufactured Homes for Sale in Huffman, TX" not in city_t
    assert "Manufactured Homes for Sale in Huffman, TX" not in plan_t
    assert "Manufactured Homes for Sale in Huffman, TX" not in home_t


def test_inventory_hub_heading_differs_from_homepage(monkeypatch):
    client, _ = seo_client(monkeypatch)
    home = client.get("/").text
    inventory = client.get("/inventory").text
    assert "<h1>Manufactured and Mobile Homes for Sale in Huffman, TX</h1>" in home
    assert "<h1>Home Inventory in Huffman, TX</h1>" in inventory
    assert "<h1>Manufactured and Mobile Homes for Sale in Huffman, TX</h1>" not in inventory
    assert "Current manufactured and mobile home inventory" in inventory
    assert 'href="/plan/223034/skyliner/4732b/"' in home
    assert 'href="/plan/223034/skyliner/4732b/"' in inventory
    assert "<title>Manufactured Home Financing in Huffman, TX | Texas Home Outlet</title>" in (
        client.get("/financing").text
    )


def test_inventory_home_query_redirects_to_the_listing(monkeypatch):
    client, _ = seo_client(monkeypatch)
    instock = client.get("/inventory?home=44490&utm_source=share", follow_redirects=False)
    assert instock.status_code == 301
    assert instock.headers["location"] == ("/homes/44490-pre-owned-big-blue?utm_source=share")
    homepage = client.get("/?home=44490", follow_redirects=False)
    assert homepage.status_code == 301
    assert homepage.headers["location"] == "/homes/44490-pre-owned-big-blue"
    plan = client.get("/inventory?home=floorplan-223034", follow_redirects=False)
    assert plan.status_code == 301
    assert plan.headers["location"] == "/plan/223034/skyliner/4732b/"
    unknown = client.get("/inventory?home=not-a-home", follow_redirects=False)
    assert unknown.status_code == 200
    assert "<h1>Home Inventory in Huffman, TX</h1>" in unknown.text


def test_listing_photo_alts_use_filename_subjects(monkeypatch):
    import seo_routes

    home = {
        **FAKE_HOMES[2],
        "image_url": "https://img.example.com/lot-44490-ext-1.jpg",
        "real_photos": [
            "https://img.example.com/lot-44490-ext-1.jpg",
            "https://img.example.com/lot-44490-ext-2.jpg",
            "https://img.example.com/lot-44490-kit-1.jpg",
        ],
    }
    client, _ = seo_client(monkeypatch)
    monkeypatch.setattr(seo_routes, "_get_homes", lambda: [home])
    body = client.get("/homes/44490-pre-owned-big-blue").text
    assert (
        'alt="PRE-OWNED / Big Blue — exterior photo 1 at Texas Home Outlet in Huffman, TX"' in body
    )
    assert "exterior photo 2 at Texas Home Outlet in Huffman, TX" in body
    assert "kitchen photo 3 at Texas Home Outlet in Huffman, TX" in body
    assert 'alt="PRE-OWNED / Big Blue"' not in body
    assert '"offers"' not in body and '"aggregateRating"' not in body


def test_home_page_keeps_call_for_price_and_own_photos(monkeypatch):
    client, _ = seo_client(monkeypatch)
    body = client.get("/homes/44490-pre-owned-big-blue").text
    assert "Call for Price" in body
    assert "https://img.example.com/lot-44490.jpg" in body
    assert "catalog-should-not-appear.jpg" not in body
    assert "Heritage-kitchen-1.jpg" not in body
    assert 'href="tel:+12813243020">Call (281) 324-3020</a>' in body
    assert "Text (281) 324-3020" in body
    assert 'href="/financing">Apply for financing</a>' in body
    og = re.search(r'<meta property="og:image" content="([^"]+)"', body)
    assert og is not None
    assert og.group(1) == "https://img.example.com/lot-44490.jpg"
    assert (
        'alt="PRE-OWNED / Big Blue — manufactured home at Texas Home Outlet in Huffman, TX"' in body
    )
    assert (
        'property="og:image:alt" content="PRE-OWNED / Big Blue — manufactured home '
        'at Texas Home Outlet in Huffman, TX"'
    ) in body
    products = [
        json.loads(block)
        for block in re.findall(
            r'<script type="application/ld\+json">(.*?)</script>', body, re.DOTALL
        )
        if '"Product"' in block
    ]
    # Call-for-Price: no Product markup at all (Google needs offers on Product).
    assert not products


def test_home_page_wrong_slug_301s_and_missing_stock_is_410(monkeypatch):
    client, _ = seo_client(monkeypatch)
    redirect = client.get("/homes/44490-stale-slug", follow_redirects=False)
    assert redirect.status_code == 301
    assert redirect.headers["location"] == "/homes/44490-pre-owned-big-blue"
    gone = client.get("/homes/99999-sold-home")
    assert gone.status_code == 410
    assert "no longer listed" in gone.text.lower()
    assert "noindex" in gone.text


def test_legacy_homes_hub_still_redirects_to_inventory(monkeypatch):
    client, _ = seo_client(monkeypatch)
    response = client.get("/homes/", follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["location"] == "/inventory"


def test_sitemap_includes_home_urls_with_lastmod(monkeypatch):
    client, _ = seo_client(monkeypatch)
    body = client.get("/sitemap.xml").text
    assert body.count("https://www.texashomeoutlet.com/homes/44490-pre-owned-big-blue</loc>") == 1
    home_entry = body[body.index("/homes/44490-pre-owned-big-blue") :]
    assert "<lastmod>2026-10-18</lastmod>" in home_entry[:240]


# ── COD-153: no inline executable script (CSP script-src without 'unsafe-inline')


_INLINE_EXEC_SCRIPT_RE = re.compile(
    r"<script(?![^>]*\bsrc=)(?![^>]*\btype=\"application/(?:ld\+)?json\")[^>]*>",
    re.IGNORECASE,
)


def test_public_pages_emit_no_inline_executable_script_with_all_ids(monkeypatch):
    for var in _ANALYTICS_ENV:
        monkeypatch.delenv(var, raising=False)
    client, _ = seo_client(monkeypatch)
    monkeypatch.setenv("GA4_MEASUREMENT_ID", "G-ABC1234XYZ")
    monkeypatch.setenv("GTM_CONTAINER_ID", "GTM-ABC1234")
    monkeypatch.setenv("META_PIXEL_ID", "1234567890123456")
    monkeypatch.setenv("TIKTOK_PIXEL_ID", "CABCDEF1234567890GHIJK")
    body = client.get("/").text
    assert "tho-analytics-config" in body
    assert not _INLINE_EXEC_SCRIPT_RE.search(body), _INLINE_EXEC_SCRIPT_RE.search(body)
    # The vendor bootstrap code itself never appears inline in the page.
    assert "__THO_ENABLE_ANALYTICS__" not in body
    assert "fbq(" not in body


def test_analytics_config_block_is_inert_json(monkeypatch):
    for var in _ANALYTICS_ENV:
        monkeypatch.delenv(var, raising=False)
    client, _ = seo_client(monkeypatch)
    monkeypatch.setenv("GA4_MEASUREMENT_ID", "G-ABC1234XYZ")
    body = client.get("/").text
    m = re.search(r'<script type="application/json" id="tho-analytics-config">(.*?)</script>', body)
    assert m, "config data block missing"
    assert json.loads(m.group(1)) == {"GA4_MEASUREMENT_ID": "G-ABC1234XYZ"}


def test_analytics_bootstrap_js_is_static_first_party_and_id_free(monkeypatch):
    for var in _ANALYTICS_ENV:
        monkeypatch.delenv(var, raising=False)
    client, _ = seo_client(monkeypatch)
    monkeypatch.setenv("GA4_MEASUREMENT_ID", "G-ABC1234XYZ")
    r = client.get("/tho-analytics.js")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/javascript")
    # IDs come only from the page's data block, never baked into the script.
    assert "G-ABC1234XYZ" not in r.text
    assert "getElementById('tho-analytics-config')" in r.text
    # No-ops (no vendor fetch, no globals) when the config block is absent.
    assert r.text.index("if(!el)return;") < r.text.index("__THO_ANALYTICS_CONFIGURED__")


def test_no_analytics_markup_when_unconfigured(monkeypatch):
    for var in _ANALYTICS_ENV:
        monkeypatch.delenv(var, raising=False)
    client, _ = seo_client(monkeypatch)
    body = client.get("/").text
    assert "tho-analytics-config" not in body
    assert "/tho-analytics.js" not in body


# ── COD-153: served HTML has no inline styles (CSP style-src without 'unsafe-inline')

# <style> blocks and style attributes in the HTML response are what style-src
# blocks. React CSSOM updates and external stylesheets are not in this markup.
_INLINE_STYLE_RE = re.compile(r"<style[\s>/]|\sstyle\s*=", re.IGNORECASE)

_NO_INLINE_STYLE_PATHS = (
    "/",
    "/inventory",
    "/contact",
    "/financing",
    "/appointments",
    "/staff",
    "/studio",
    "/ad-studio",
    "/homes/44490-pre-owned-big-blue",
    "/inventory-detail/43372/texas-home-outlet/huffman/premier/",
)


def test_served_html_has_no_inline_styles(monkeypatch):
    for var in _ANALYTICS_ENV:
        monkeypatch.delenv(var, raising=False)
    client, _ = seo_client(monkeypatch)
    monkeypatch.setenv("GA4_MEASUREMENT_ID", "G-ABC1234XYZ")
    for path in _NO_INLINE_STYLE_PATHS:
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["content-type"].startswith("text/html"), path
        match = _INLINE_STYLE_RE.search(response.text)
        assert match is None, f"{path} contains inline style markup: {match.group(0)!r}"
        assert "'unsafe-inline'" not in response.headers["content-security-policy"]


def test_every_homes_page_product_has_offers(monkeypatch):
    # Search Console (Oct 11): Product without offers/review/rating is invalid.
    # Walk every /homes/ URL in the sitemap; any Product block must carry offers.
    client, _ = seo_client(monkeypatch)
    sitemap = client.get("/sitemap.xml").text
    paths = sorted(
        {
            loc.split("://", 1)[1].split("/", 1)[1]
            for loc in re.findall(r"<loc>([^<]+/homes/[^<]+)</loc>", sitemap)
        }
    )
    assert paths, "sitemap should list /homes/ pages"
    for path in paths:
        response = client.get("/" + path)
        assert response.status_code == 200, path
        for block in _jsonld_blocks(response.text):
            if block.get("@type") == "Product":
                assert block.get("offers"), f"/{path} emits Product without offers"


def test_priced_home_page_emits_one_product_and_breadcrumb(monkeypatch):
    # Fixture home 43372 (price_value 129900) is the priced /homes/ page.
    client, _ = seo_client(monkeypatch)
    response = client.get("/homes/43372-premier-creole-3256h32447")
    assert response.status_code == 200
    blocks = _jsonld_blocks(response.text)
    products = [block for block in blocks if block.get("@type") == "Product"]
    assert len(products) == 1
    offer = products[0]["offers"]
    assert offer["price"] == "129900"
    assert offer["priceCurrency"] == "USD"
    assert any(block.get("@type") == "BreadcrumbList" for block in blocks)


def test_comma_formatted_price_value_drops_product(monkeypatch):
    # A thousands-separator string is not a real Offer price. Do not parse it
    # into Product markup; omit the snippet the same way Call-for-Price does.
    import seo_routes

    priced = {**FAKE_HOMES[0], "price_value": "129,900"}
    client, _ = seo_client(monkeypatch)
    monkeypatch.setattr(seo_routes, "_get_homes", lambda: [priced, *FAKE_HOMES[1:]])
    response = client.get("/homes/43372-premier-creole-3256h32447")
    assert response.status_code == 200
    products = [block for block in _jsonld_blocks(response.text) if block.get("@type") == "Product"]
    assert not products


_NONFINITE_PRICE_VALUES = (
    "NaN",
    "nan",
    "NAN",
    " NaN ",
    float("nan"),
    "inf",
    "+inf",
    "-inf",
    "Infinity",
    "+Infinity",
    "-Infinity",
    " inf ",
    float("inf"),
    float("-inf"),
)


def test_nonfinite_price_value_emits_no_product_offer_or_price(monkeypatch):
    """A visible dollar price plus NaN or an infinity is not an Offer.

    float("NaN") is not <= 0, and abs(NaN - shown) > 0.5 is also false, so a
    comparison-only guard lets the Offer serializer emit "price": "nan".
    The crawlable price line still shows the visible dollar amount.
    """
    import seo_routes

    visible = {**FAKE_HOMES[0], "display_price": "$100,000", "price_value": 100000}
    assert seo_routes._shown_offer_price(visible) == 100000
    assert seo_routes._product_jsonld(visible, "https://x/p")["offers"]["price"] == "100000"

    for raw in _NONFINITE_PRICE_VALUES:
        home = {**FAKE_HOMES[0], "display_price": "$100,000", "price_value": raw}
        assert seo_routes._shown_offer_price(home) is None, raw
        assert seo_routes._product_jsonld(home, "https://x/p") is None, raw

    client, _ = seo_client(monkeypatch)
    for raw in _NONFINITE_PRICE_VALUES:
        poisoned = {**FAKE_HOMES[0], "display_price": "$100,000", "price_value": raw}
        monkeypatch.setattr(
            seo_routes,
            "_get_homes",
            lambda poisoned=poisoned: [poisoned, *FAKE_HOMES[1:]],
        )
        monkeypatch.setattr(seo_routes, "_registry_cache", None)
        monkeypatch.setattr(seo_routes, "_registry_built_at", 0.0)
        for path in (
            "/homes/43372-premier-creole-3256h32447",
            "/inventory-detail/43372/texas-home-outlet/huffman/premier/",
        ):
            response = client.get(path)
            assert response.status_code == 200, (raw, path)
            assert "Price: $100,000" in response.text
            assert re.search(r"\b(?:nan|inf|infinity)\b", response.text, re.I) is None, (
                raw,
                path,
            )
            blocks = _jsonld_blocks(response.text)
            assert blocks, (raw, path)
            for block in blocks:
                assert not _jsonld_has_price_markup(block), (raw, path, block)
            jsonld = "\n".join(
                re.findall(
                    r'<script type="application/ld\+json">(.*?)</script>',
                    response.text,
                    re.DOTALL,
                )
            ).lower()
            assert '"price"' not in jsonld
            assert "product" not in jsonld
            assert "offer" not in jsonld
            assert "nan" not in jsonld
            assert "infinity" not in jsonld


def test_offer_serializer_rejects_nonfinite_price_from_the_guard(monkeypatch):
    """The Offer formatter must not emit nan/inf even if the guard returns one."""
    import seo_routes

    home = {**FAKE_HOMES[0], "display_price": "$100,000", "price_value": 100000}
    for raw in (float("nan"), float("inf"), float("-inf"), "nan", "inf"):
        monkeypatch.setattr(seo_routes, "_shown_offer_price", lambda home, raw=raw: raw)
        assert seo_routes._product_jsonld(home, "https://x/p") is None, raw
        assert seo_routes._offer_price_text(raw) is None, raw


_PRICE_MARKUP_TYPES = {"Product", "Offer", "AggregateOffer", "AggregateRating"}
_PRICE_MARKUP_KEYS = {
    "price",
    "lowPrice",
    "highPrice",
    "offers",
    "review",
    "aggregateRating",
    "ratingValue",
}


def _jsonld_has_price_markup(node):
    if isinstance(node, dict):
        if node.get("@type") in _PRICE_MARKUP_TYPES:
            return True
        if any(key in node for key in _PRICE_MARKUP_KEYS):
            return True
        return any(_jsonld_has_price_markup(value) for value in node.values())
    if isinstance(node, list):
        return any(_jsonld_has_price_markup(value) for value in node)
    return False


def test_call_for_price_pages_emit_no_price_or_offer_markup(monkeypatch):
    """A page that shows Call for Price must not emit Product or Offer prices.

    A hidden price_value is not a public price (issue #398). This walks every
    sitemap URL whose visible price line is Call for Price, including a home
    whose record still carries a number.
    """
    import seo_routes

    leaked = {**FAKE_HOMES[2], "display_price": "Call for Price", "price_value": 88000}
    assert seo_routes._product_jsonld(leaked, "https://x/hidden") is None

    client, _ = seo_client(monkeypatch)
    monkeypatch.setattr(seo_routes, "_get_homes", lambda: [FAKE_HOMES[0], FAKE_HOMES[1], leaked])
    monkeypatch.setattr(seo_routes, "_registry_cache", None)
    monkeypatch.setattr(seo_routes, "_registry_built_at", 0.0)

    checked = []
    for loc, _lastmod in _sitemap_entries(client.get("/sitemap.xml").text):
        path = "/" + loc.split("://", 1)[1].split("/", 1)[1]
        response = client.get(path)
        if response.status_code != 200:
            continue
        body = response.text
        if re.search(r"Price:\s*\$\s*[0-9]", body) or "Call for Price" not in body:
            continue
        checked.append(path)
        for block in _jsonld_blocks(body):
            assert not _jsonld_has_price_markup(block), path
        assert "88000" not in body
    assert any("44490" in path for path in checked)
    assert any(path.startswith("/plan/") for path in checked)


def test_home_inventory_and_location_structured_data(monkeypatch):
    client, _ = seo_client(monkeypatch)

    home_blocks = _jsonld_blocks(client.get("/").text)
    home_types = {block.get("@type") for block in home_blocks}
    assert {"LocalBusiness", "WebSite", "CollectionPage", "ItemList"} <= home_types
    business = next(block for block in home_blocks if block.get("@type") == "LocalBusiness")
    assert business["@id"].endswith("/#dealership")
    assert business["address"]["streetAddress"] == "10685 FM 1960 East"
    assert business["address"]["addressLocality"] == "Huffman"
    assert business["address"]["postalCode"] == "77336"
    assert "aggregateRating" not in business
    assert "review" not in business
    home_items = next(
        block["itemListElement"] for block in home_blocks if block.get("@type") == "ItemList"
    )
    assert home_items
    assert all("price" not in item and "offers" not in item for item in home_items)

    inventory_blocks = _jsonld_blocks(client.get("/inventory").text)
    crumbs = next(block for block in inventory_blocks if block.get("@type") == "BreadcrumbList")
    assert [item["name"] for item in crumbs["itemListElement"]] == ["Home", "Inventory"]
    assert not any(block.get("@type") == "Product" for block in inventory_blocks)

    city_blocks = _jsonld_blocks(client.get("/manufactured-homes-in-humble-tx").text)
    city_business = next(block for block in city_blocks if block.get("@type") == "LocalBusiness")
    assert city_business["address"]["addressLocality"] == "Huffman"
    service = next(block for block in city_blocks if block.get("@type") == "Service")
    assert service["areaServed"] == {"@type": "City", "name": "Humble"}
    assert service["provider"]["@id"] == city_business["@id"]
    assert not any(block.get("@type") == "Product" for block in city_blocks)
    assert not any(_jsonld_has_price_markup(block) for block in city_blocks)


def test_storefront_pages_link_home_inventory_listings_and_locations(monkeypatch):
    client, _ = seo_client(monkeypatch)
    home = client.get("/").text
    inventory = client.get("/inventory").text
    city = client.get("/manufactured-homes-in-humble-tx").text
    listing = client.get("/homes/44490-pre-owned-big-blue").text

    assert 'href="/inventory"' in home
    assert 'href="/manufactured-homes-in-humble-tx"' in home
    assert 'href="/homes/44490-pre-owned-big-blue"' in home or 'href="/plan/' in home
    assert 'href="/"' in inventory
    assert 'href="/manufactured-homes-in-huffman-tx"' in inventory
    assert 'href="/"' in city and 'href="/inventory"' in city
    assert 'href="/homes/44490-pre-owned-big-blue"' in city or 'href="/plan/' in city
    assert 'href="/"' in listing and 'href="/inventory"' in listing
    assert 'href="/manufactured-homes-in-huffman-tx"' in listing


def test_public_titles_and_descriptions_fit_and_stay_unique(monkeypatch):
    client, _ = seo_client(monkeypatch)
    pages = (
        "/",
        "/inventory",
        "/contact",
        "/about",
        "/financing",
        "/faq",
        "/single-wide",
        "/double-wide",
        "/manufactured-homes-in-humble-tx",
        "/manufactured-homes-in-atascocita-tx",
        "/manufactured-homes-in-livingston-tx",
        "/homes/44490-pre-owned-big-blue",
    )
    titles = []
    descriptions = []
    for path in pages:
        body = client.get(path).text
        title = re.search(r"<title>(.*?)</title>", body, re.DOTALL).group(1)
        description = re.search(r'<meta name="description" content="([^"]*)"', body).group(1)
        assert 25 <= len(title) <= 65, (path, len(title), title)
        assert 70 <= len(description) <= 165, (path, len(description), description)
        titles.append(title)
        descriptions.append(description)
    assert len(set(titles)) == len(titles)
    assert len(set(descriptions)) == len(descriptions)


def test_contact_description_contains_configured_hours(monkeypatch):
    """The contact description must include the hours from config.yaml."""
    import yaml

    config = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    hours = config["business"]["hours"]
    configured = f"{hours['weekday']}, {hours['saturday']}, {hours['sunday']}"
    client, _ = seo_client(monkeypatch)
    description = re.search(
        r'<meta name="description" content="([^"]*)"',
        client.get("/contact").text,
    ).group(1)
    assert configured in description
    assert len(description) <= 165


def test_service_area_cities_match_config():
    from config_loader import get_business

    text = Path("frontend/src/constants.js").read_text(encoding="utf-8")
    block = re.search(r"export const SERVICE_AREA_CITIES = \[(.*?)\];", text, re.DOTALL)
    cities = re.findall(r'"([^"]+)"', block.group(1))
    assert cities == list(get_business()["area_served"])


@pytest.mark.parametrize(
    "path",
    ["/", "/inventory", "/financing", "/faq", "/warranty", "/delivery", "/contact"],
)
def test_named_public_pages_have_canonical_social_and_verification_slots(monkeypatch, path):
    monkeypatch.delenv("GOOGLE_SITE_VERIFICATION", raising=False)
    monkeypatch.delenv("BING_SITE_VERIFICATION", raising=False)
    client, _ = seo_client(monkeypatch)
    sitemap = client.get("/sitemap.xml").text
    assert f"https://www.texashomeoutlet.com{path}</loc>" in sitemap
    body = client.get(path).text
    assert f'<link rel="canonical" href="https://www.texashomeoutlet.com{path}"' in body
    assert 'property="og:title"' in body
    assert 'property="og:description"' in body
    assert 'property="og:image"' in body
    assert 'name="twitter:card" content="summary_large_image"' in body
    assert 'name="twitter:title"' in body
    assert 'name="twitter:description"' in body
    assert "google-site-verification" not in body
    assert "msvalidate.01" not in body
    assert "TODO_GOOGLE_SEARCH_CONSOLE_TOKEN" not in body
    assert "TODO_BING_WEBMASTER_TOKEN" not in body
    assert "TODO(owner)" not in body
    assert "abc123" not in body
    if path == "/financing":
        assert "Ask our team for current lender options." in body
        assert "lender partners" not in body.lower()


def test_site_verification_meta_absent_until_real_tokens_are_set(monkeypatch):
    monkeypatch.delenv("GOOGLE_SITE_VERIFICATION", raising=False)
    monkeypatch.setenv("BING_SITE_VERIFICATION", "TODO_BING_WEBMASTER_TOKEN")
    client, _ = seo_client(monkeypatch)
    body = client.get("/").text
    assert "google-site-verification" not in body
    assert "msvalidate.01" not in body
    assert "TODO" not in body

    monkeypatch.setenv("GOOGLE_SITE_VERIFICATION", '"><script>')
    monkeypatch.setenv("BING_SITE_VERIFICATION", "short")
    client, _ = seo_client(monkeypatch)
    rejected = client.get("/contact").text
    assert "google-site-verification" not in rejected
    assert "msvalidate.01" not in rejected
    assert '"><script>' not in rejected


def test_site_verification_meta_present_when_tokens_are_configured(monkeypatch):
    monkeypatch.setenv("GOOGLE_SITE_VERIFICATION", "googleToken1234abcd")
    monkeypatch.setenv("BING_SITE_VERIFICATION", "bingToken1234abcd")
    client, _ = seo_client(monkeypatch)
    body = client.get("/financing").text
    assert 'name="google-site-verification" content="googleToken1234abcd"' in body
    assert 'name="msvalidate.01" content="bingToken1234abcd"' in body
    assert "TODO_GOOGLE_SEARCH_CONSOLE_TOKEN" not in body
    assert "TODO_BING_WEBMASTER_TOKEN" not in body


def test_placeholder_verification_tags_in_the_shell_are_stripped(monkeypatch, tmp_path):
    import seo_routes

    shell = tmp_path / "index.html"
    shell.write_text(
        "<!doctype html><html><head>\n"
        "<!-- TODO(owner): replace TODO_GOOGLE_SEARCH_CONSOLE_TOKEN -->\n"
        '<meta name="google-site-verification" content="TODO_GOOGLE_SEARCH_CONSOLE_TOKEN" />\n'
        '<meta name="msvalidate.01" content="TODO_BING_WEBMASTER_TOKEN" />\n'
        '<title>Old</title></head><body><div id="root"></div></body></html>\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(seo_routes, "_index_html_path", str(shell))
    monkeypatch.setattr(seo_routes, "_shell_cache", None)
    monkeypatch.delenv("GOOGLE_SITE_VERIFICATION", raising=False)
    monkeypatch.delenv("BING_SITE_VERIFICATION", raising=False)
    client, _ = seo_client(monkeypatch)
    body = client.get("/warranty").text
    assert "google-site-verification" not in body
    assert "msvalidate.01" not in body
    assert "TODO" not in body


def test_apex_host_301s_to_www_and_other_methods_stay(monkeypatch):
    client, _ = seo_client(monkeypatch)
    response = client.get(
        "/contact?from=ad",
        headers={"host": "texashomeoutlet.com"},
        follow_redirects=False,
    )
    assert response.status_code == 301
    assert response.headers["location"] == "https://www.texashomeoutlet.com/contact?from=ad"

    robots = client.get(
        "/robots.txt",
        headers={"host": "texashomeoutlet.com"},
        follow_redirects=False,
    )
    assert robots.status_code == 301
    assert robots.headers["location"] == "https://www.texashomeoutlet.com/robots.txt"

    sitemap = client.get(
        "/sitemap.xml",
        headers={"host": "texashomeoutlet.com"},
        follow_redirects=False,
    )
    assert sitemap.status_code == 301
    assert sitemap.headers["location"] == "https://www.texashomeoutlet.com/sitemap.xml"

    www = client.get(
        "/contact",
        headers={"host": "www.texashomeoutlet.com"},
        follow_redirects=False,
    )
    assert www.status_code == 200
    assert "location" not in www.headers

    posted = client.post(
        "/api/contact",
        headers={"host": "texashomeoutlet.com"},
        json={},
        follow_redirects=False,
    )
    assert posted.status_code not in {301, 302, 307, 308}


@pytest.mark.parametrize("display_price", ["", "$0", "0", "$0.00", "Call for price"])
def test_blank_or_zero_display_price_is_call_for_price(monkeypatch, display_price):
    import seo_routes

    home = {**FAKE_HOMES[0], "display_price": display_price, "price_value": 129900}
    client, _ = seo_client(monkeypatch)
    monkeypatch.setattr(seo_routes, "_get_homes", lambda: [home])
    monkeypatch.setattr(seo_routes, "_registry_cache", None)
    monkeypatch.setattr(seo_routes, "_registry_built_at", 0.0)

    body = client.get("/inventory-detail/43372/texas-home-outlet/huffman/premier/").text
    assert "Price: $0" not in body
    assert "$129,900" not in body
    assert "Call for Price" in body
    assert 'href="tel:+12813243020">Call (281) 324-3020</a>' in body
    assert '"offers"' not in body
