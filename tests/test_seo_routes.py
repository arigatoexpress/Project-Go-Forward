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
        "/single-wide/": "/inventory",  # category
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
        "/financing": "Financing Options at Texas Home Outlet",
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
    assert "https://www.googletagmanager.com/gtag/js?id=" in body
    assert '"GA4_MEASUREMENT_ID":"G-ABC1234XYZ"' in body
    assert "analytics_storage:'denied'" in body
    assert "ad_storage:'denied'" in body
    assert "ad_user_data:'granted',ad_personalization:'denied'" in body
    assert "__THO_ENABLE_ANALYTICS__" in body
    assert "__THO_ANALYTICS_CONFIGURED__=true" in body
    assert "send_page_view:false" in body
    assert '<script async src="https://www.googletagmanager.com/gtag/js' not in body


def test_meta_and_tiktok_pixels_emitted_with_valid_ids(monkeypatch):
    for var in _ANALYTICS_ENV:
        monkeypatch.delenv(var, raising=False)
    client, _ = seo_client(monkeypatch)
    monkeypatch.setenv("META_PIXEL_ID", "1234567890123456")
    monkeypatch.setenv("TIKTOK_PIXEL_ID", "CABCDEF1234567890GHIJK")
    body = client.get("/").text
    assert '"META_PIXEL_ID":"1234567890123456"' in body
    assert '"TIKTOK_PIXEL_ID":"CABCDEF1234567890GHIJK"' in body
    assert "w.fbq('init',id)" in body
    assert "ttq.load(id)" in body


def test_analytics_bootloader_uses_one_first_party_consent_key(monkeypatch):
    for var in _ANALYTICS_ENV:
        monkeypatch.delenv(var, raising=False)
    client, _ = seo_client(monkeypatch)
    monkeypatch.setenv("GTM_CONTAINER_ID", "GTM-ABC1234")
    body = client.get("/").text
    assert "tho_analytics_consent_v1" in body
    assert "localStorage.getItem" in body
    assert "pref==='granted'" in body


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


def test_city_landing_page_serves_200_with_local_content(monkeypatch):
    # A served city (config area_served) gets a real local-SEO landing page.
    client, _ = seo_client(monkeypatch)
    r = client.get("/manufactured-homes-in-humble-tx")
    assert r.status_code == 200
    body = r.text
    assert "<h1>Manufactured &amp; Mobile Homes in Humble, TX</h1>" in body
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
