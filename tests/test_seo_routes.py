"""Tests for the server-side SEO surface (seo_routes.py).

Covers: robots.txt, sitemap.xml, per-route head injection (title/canonical/
OG/JSON-LD), legacy detail URLs served 200 with crawlable content, quote-URL
301s, soft-404 avoidance, and noindex on admin routes.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_api_v1 import create_client

FAKE_HOMES = [
    {
        "id": "43372",
        "legacy_inventory_id": "43372",
        "model_name": "Premier / Creole 3256H32447",
        "manufacturer": "Champion Homes",
        "status": "Available",
        "detail_url": "https://www.texashomeoutlet.com/inventory-detail/43372/texas-home-outlet/huffman/premier/",
        "quote_url": "https://www.texashomeoutlet.com/quote/inventory/123/43372",
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
        "status": "Orderable",
        "detail_url": "https://www.texashomeoutlet.com/plan/223034/skyliner/4732b/",
        "quote_url": "https://www.texashomeoutlet.com/quote/plan/223034",
        "price_value": None,
        "specs": {"beds": 3, "baths": 2},
    },
]


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
    assert "Disallow: /api/admin/" in body
    assert "Sitemap: " in body and "/sitemap.xml" in body
    # Never block assets or the public inventory API the renderer depends on
    assert "Disallow: /assets" not in body
    assert "Disallow: /api/marketing" not in body
    # We WANT AI visibility: no AI-crawler blocks
    assert "GPTBot" not in body and "ClaudeBot" not in body


def test_sitemap_lists_static_and_detail_urls(monkeypatch):
    client, _ = seo_client(monkeypatch)
    response = client.get("/sitemap.xml")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    body = response.text
    assert "/inventory</loc>" in body
    assert "/inventory-detail/43372/texas-home-outlet/huffman/premier/</loc>" in body
    assert "/plan/223034/skyliner/4732b/</loc>" in body
    # Google ignores priority/changefreq; lastmod only when truthful — omitted
    assert "<priority>" not in body and "<changefreq>" not in body and "<lastmod>" not in body


def test_homepage_head_is_injected(monkeypatch):
    client, _ = seo_client(monkeypatch)
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert "Mobile &amp; Manufactured Homes for Sale in Huffman, TX" in body
    assert 'rel="canonical"' in body
    assert body.count("<title>") == 1  # static template title replaced, not duplicated
    assert '"@type": "LocalBusiness"' in body
    assert '"postalCode": "77336"' in body


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


def test_call_for_price_home_has_no_offer_block(monkeypatch):
    client, _ = seo_client(monkeypatch)
    body = client.get("/plan/223034/skyliner/4732b/").text
    jsonld_blocks = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', body, re.DOTALL
    )
    products = [json.loads(b) for b in jsonld_blocks if '"Product"' in b]
    assert products and "offers" not in products[0]  # no fabricated $0 price


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


def test_legacy_vendor_pages_301_to_relevant_targets(monkeypatch):
    # Old WordPress/Yoast marketing, brand, and city pages must 301 (not hard-404)
    # to preserve search equity on cutover. Source: old site page-sitemap.xml.
    client, _ = seo_client(monkeypatch)
    cases = {
        "/tru-homes/": "/inventory",                        # brand
        "/manufactured-homes-in-beaumont-tx/": "/inventory",  # city / local SEO
        "/single-wide/": "/inventory",                       # category
        "/financing/": "/contact",                           # info
        "/about-us/": "/contact",
        "/accessibility-statement/": "/",                    # boilerplate
    }
    for path, target in cases.items():
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 301, path
        assert response.headers["location"] == target, path
    # Case-insensitive + slashless variants resolve the same.
    response = client.get("/Financing", follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["location"] == "/contact"
    # A genuinely unknown marketing path is NOT over-broadly caught — still 404.
    response = client.get("/totally-made-up-page/", follow_redirects=False)
    assert response.status_code == 404


def test_unknown_detail_id_is_404(monkeypatch):
    client, _ = seo_client(monkeypatch)
    response = client.get("/inventory-detail/99999/whatever/")
    assert response.status_code == 404


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


def test_unknown_route_returns_real_404_not_soft_404(monkeypatch):
    client, _ = seo_client(monkeypatch)
    response = client.get("/this-page-does-not-exist")
    assert response.status_code == 404
    assert "noindex" in response.text


def test_admin_routes_are_noindex_but_200(monkeypatch):
    client, _ = seo_client(monkeypatch)
    for path in ("/crm", "/documents", "/studio"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert '<meta name="robots" content="noindex" />' in response.text, path
        assert 'rel="canonical"' not in response.text, path


def test_public_routes_have_unique_titles_and_canonicals(monkeypatch):
    client, _ = seo_client(monkeypatch)
    titles = {}
    for path in ("/", "/inventory", "/contact", "/appointments"):
        body = client.get(path).text
        title = re.search(r"<title>(.*?)</title>", body, re.DOTALL).group(1)
        titles[path] = title
        canonical = re.search(r'rel="canonical" href="([^"]+)"', body).group(1)
        assert canonical.endswith(path if path != "/" else "/"), path
    assert len(set(titles.values())) == len(titles), "titles must be unique per route"


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
