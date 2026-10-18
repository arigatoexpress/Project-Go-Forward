"""Server-side SEO surface for the public SPA.

Search engines, social scrapers, and most AI crawlers do not execute
JavaScript, so the FastAPI layer — not React — must own per-URL titles,
descriptions, canonicals, Open Graph tags, JSON-LD structured data, a
crawlable content block, robots.txt, sitemap.xml, and HTTP status codes.

Strategy (see docs/SEO_MIGRATION.md for sources):
- Legacy texashomeoutlet.com detail URLs (/inventory-detail/<id>/...,
  /plan/<id>/...) stay alive at 200 as the canonical detail URLs — no
  redirect, no equity loss. The SPA already deep-links them client-side.
- Legacy /quote/... URLs 301 to their matching detail page.
- Unknown paths return HTTP 404 (with the SPA shell) to avoid soft-404s.
- Admin/operator routes are served with a noindex robots meta tag.

main.py wires this module via configure() and calls render_spa_response()
from the SPA catch-all. Business values come from config.yaml only.
"""

from __future__ import annotations

import html
import json
import logging
import math
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import quote_plus, unquote, urlparse

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from config_loader import (
    business_address,
    business_city,
    business_hours,
    business_name,
    business_phone,
    business_state,
    business_street,
    business_zip,
    get_business,
)
from inventory_classification import normalize_inventory_classification
from tools.inventory_dedupe import is_preowned
from tools.listing_urls import (
    attach_listing_urls,
    listing_lastmod,
    listing_page_images,
    listing_path,
    listing_photo_subject,
    match_instock_home,
    parse_home_path,
    stock_id,
)

router = APIRouter()

logger = logging.getLogger(__name__)

# ── Wiring (set by main.py at startup) ─────────────────────────────────────

_get_homes = None  # callable -> list[dict]; the merged public inventory
_get_canonical_base = None  # callable -> "https://host" (no trailing slash)
_index_html_path = "frontend/dist/index.html"


def configure(get_homes, get_canonical_base, index_html_path=None):
    global _get_homes, _get_canonical_base, _index_html_path
    _get_homes = get_homes
    _get_canonical_base = get_canonical_base
    if index_html_path:
        _index_html_path = index_html_path


def _base() -> str:
    return (_get_canonical_base() if _get_canonical_base else "").rstrip("/")


# ── Route taxonomy ──────────────────────────────────────────────────────────

# Public, indexable static routes: path -> (title, description)
_CITY_STATE = f"{business_city()}, {business_state()}"
PUBLIC_ROUTES = {
    "/": (
        f"Manufactured Homes for Sale in {_CITY_STATE} | {business_name()}",
        f"Browse manufactured and mobile homes for sale at {business_name()} in "
        f"{_CITY_STATE}. Call or text {business_phone()} to confirm price, financing, "
        f"and availability.",
    ),
    "/inventory": (
        f"Home Inventory in {_CITY_STATE} | {business_name()}",
        f"See the current manufactured and mobile home inventory at {business_name()} "
        f"in {_CITY_STATE}, including listed homes, pre-owned homes, and floorplans.",
    ),
    "/contact": (
        f"Contact {business_name()} — {_CITY_STATE}",
        f"Visit {business_name()} at {business_address()}. Call {business_phone()}. "
        f"Hours: {business_hours()}.",
    ),
    "/appointments": (
        f"Book a Showroom Visit | {business_name()}",
        f"Schedule a visit to the {business_name()} showroom in {_CITY_STATE} to "
        f"tour manufactured homes in person.",
    ),
    "/chat": (
        f"Chat with Tex — {business_name()} Home Finder",
        f"Ask Tex, the {business_name()} assistant, about manufactured homes, "
        f"pricing, and availability in {_CITY_STATE}.",
    ),
    "/about": (
        f"About Us | {business_name()}",
        f"Learn about {business_name()}, a licensed manufactured and mobile home "
        f"dealership in {_CITY_STATE} serving the Houston area.",
    ),
    "/financing": (
        f"Manufactured Home Financing in {_CITY_STATE} | {business_name()}",
        f"Manufactured home financing options at {business_name()} in {_CITY_STATE}: "
        f"chattel loans, land-and-home loans, FHA/VA/USDA, and lender partners.",
    ),
    "/faq": (
        f"Frequently Asked Questions | {business_name()}",
        f"Answers about manufactured homes, financing, delivery, warranty, and buying "
        f"from {business_name()} in {_CITY_STATE}.",
    ),
    "/warranty": (
        f"Warranty & Service | {business_name()}",
        f"Manufacturer warranties, component coverage, and service support for homes "
        f"purchased from {business_name()} in {_CITY_STATE}.",
    ),
    "/delivery": (
        f"Delivery & Setup | {business_name()}",
        f"Delivery, placement, utility connections, and site-prep guidance for "
        f"manufactured homes from {business_name()} in {_CITY_STATE}.",
    ),
}

# Shared copy keeps server-rendered and React legal pages identical.
LEGAL_PAGES = json.loads(
    (Path(__file__).parent / "frontend/src/content/legalPages.json").read_text(encoding="utf-8")
)
for _page, _content in LEGAL_PAGES.items():
    PUBLIC_ROUTES[f"/{_page}"] = (
        f"{_content['title']} | {business_name()}",
        _content["description"],
    )

# Common legal URLs people and crawlers guess. Keys are lowercase, no trailing slash.
_LEGAL_REDIRECTS: dict[str, str] = {
    "/legal": "/privacy",
    "/privacy-policy": "/privacy",
    "/terms-of-use": "/terms",
    "/terms-of-service": "/terms",
    "/terms-and-conditions": "/terms",
}


def _crawlable_legal_block(page: str) -> str:
    content = LEGAL_PAGES[page]
    sections = "".join(
        f"<section><h2>{html.escape(heading)}</h2><p>{html.escape(text)}</p></section>"
        for heading, text in content["sections"]
    )
    return f"<main><h1>{html.escape(content['title'])}</h1>{sections}</main>"


# High-intent legacy category URLs that now resolve to real, inventory-backed
# landing pages instead of collapsing into the generic /inventory hub.
HOME_CATEGORY_PAGES = {
    "/single-wide": {
        "classification": "Single Wide",
        "heading": "Single Wide Manufactured Homes",
        "title": f"Single Wide Homes in {_CITY_STATE} | {business_name()}",
        "description": (
            f"Browse single wide manufactured and mobile homes at {business_name()} in "
            f"{_CITY_STATE}. Listed homes and orderable floorplans. Call {business_phone()} "
            "for price."
        ),
    },
    "/double-wide": {
        "classification": "Double Wide",
        "heading": "Double Wide Manufactured Homes",
        "title": f"Double Wide Homes in {_CITY_STATE} | {business_name()}",
        "description": (
            f"Browse double wide manufactured and mobile homes at {business_name()} in "
            f"{_CITY_STATE}. Listed homes and orderable floorplans. Call {business_phone()} "
            "for price."
        ),
    },
}

# Operator/admin SPA routes: served 200 but with a noindex robots meta.
NOINDEX_PREFIXES = (
    "/crm",
    "/documents",
    "/document-center",
    "/analytics",
    "/studio",
    "/ad-studio",
    "/system",
    "/getting-started",
    "/staff",
    "/team",
    "/guide",
    "/chat-history",
    "/manage-inventory",
    "/photos",
    "/copilot",
    "/ops-copilot",
    "/hub/",
    "/app/",
)

_DETAIL_RE = re.compile(r"^/(inventory-detail|plan)/(\d+)(/|$)", re.IGNORECASE)
_QUOTE_RE = re.compile(r"^/quote(/|$)", re.IGNORECASE)

# Legacy vendor (WordPress/Yoast) marketing, brand, and city/local pages from the
# OLD manufacturedhomes.com-era site. These hold real search equity + backlinks but
# have no 1:1 page on the new app, so they 301 to the closest relevant destination
# instead of hard-404ing (which would drop the rankings on cutover). Source of truth:
# the old site's live page-sitemap.xml, crawled 2026-06-12. Keys are normalized to a
# lowercase, no-trailing-slash path. Detail/plan/quote/inventory URLs are intentionally
# absent — they are already handled by the registry/regex logic in _render_spa_response.
# Re-crawl the old sitemap and extend this map before flipping DNS if pages changed.
_LEGACY_VENDOR_REDIRECTS: dict[str, str] = {
    # Brand / manufacturer showcase pages -> the unified inventory.
    "/tru-homes": "/inventory",
    "/clayton-homes": "/inventory",
    "/cavco-homes": "/inventory",
    "/cavco-homes-of-texas": "/inventory",
    "/champion-homes": "/inventory",
    "/skyline-homes": "/inventory",
    "/meridian-homes": "/inventory",
    "/new-vision-manufacturing": "/inventory",
    "/jessup-housing": "/inventory",
    "/southern-energy-homes": "/inventory",
    "/schult-homes-waco": "/inventory",
    # City / local-SEO landing pages -> inventory (local intent = browse homes).
    "/manufactured-homes-in-jasper-tx": "/inventory",
    "/manufactured-homes-in-lumberton-tx": "/inventory",
    # Category pages -> inventory.
    "/used-homes": "/inventory",
    "/pre-owned-homes": "/inventory",
    "/tiny-homes-cabin": "/inventory",
    "/red-tag-sales": "/inventory",
    "/floor-plans": "/inventory",
    "/homes": "/inventory",
    # Info / contact pages -> contact or new trust pages.
    "/about-us": "/about",
    "/contact-us": "/contact",
    "/contact-modal": "/contact",
    # "/financing" is now a real public page; trailing-slash redirect handles "/financing/".
    "/trade": "/contact",
    "/moving": "/contact",
    "/brochure": "/contact",
    # Boilerplate -> home.
    "/accessibility-statement": "/",
}


# ── Inventory-backed URL registry (cached) ──────────────────────────────────

_registry_lock = threading.Lock()
_registry_cache: dict | None = None
_registry_built_at = 0.0
_REGISTRY_TTL_S = 300


def _load_homes() -> tuple[list[dict], bool]:
    if not _get_homes:
        return [], True
    try:
        result = _get_homes()
        if isinstance(result, dict):
            homes = result.get("homes")
            inventory_ok = result.get("inventory_ok")
            if inventory_ok is None:
                inventory_ok = result.get("success", True)
            return homes if isinstance(homes, list) else [], inventory_ok is not False
        return result or [], True
    except Exception as e:
        logger.warning(f"SEO inventory fetch failed: {e}")
        return [], False


def _safe_homes() -> list[dict]:
    """Best-effort inventory for non-status-sensitive SEO helpers."""
    return _load_homes()[0]


def _legacy_path(url: str | None) -> str | None:
    if not url:
        return None
    path = urlparse(str(url)).path
    return path if path and path != "/" else None


def canonical_listing_path_for_id(home_id: str) -> str | None:
    """Public path for an inventory ``?home=`` id, when that home is listed.

    Shared links used to point at ``/inventory?home=<id>``, which serves the
    catalog page. The id matches the home's ``id`` field (the value the
    share button copies), then prefers the in-stock ``/homes/`` path.
    """
    token = str(home_id or "").strip()
    if not token or len(token) > 80 or any(char in token for char in "/\\?#&"):
        return None
    registry = _registry()
    if not registry.get("inventory_ok"):
        return None
    for home in registry["homes"]:
        if str(home.get("id") or "").strip() != token:
            continue
        for candidate in (listing_path(home), _legacy_path(home.get("detail_url"))):
            if candidate and candidate.startswith("/") and not candidate.startswith("//"):
                return candidate
        return None
    return None


def _detail_route_key(path: str | None) -> tuple[str, str] | None:
    match = _DETAIL_RE.match(path or "")
    return (match.group(1).lower(), match.group(2)) if match else None


def _build_registry() -> dict:
    """Map legacy routes to homes, canonical paths, and safe redirects."""
    detail_by_route: dict[tuple[str, str], dict] = {}
    detail_path_by_route: dict[tuple[str, str], str] = {}
    detail_alias_redirects: dict[tuple[str, str], str] = {}
    quote_redirects: dict[str, str] = {}
    quote_alias_redirects: dict[str, str] = {}
    homes, inventory_ok = _load_homes()
    attach_listing_urls(homes)
    instock_by_stock: dict[str, dict] = {}
    instock_path_by_stock: dict[str, str] = {}
    for home in homes:
        path_value = listing_path(home)
        identifier = stock_id(home)
        if path_value and identifier:
            instock_by_stock[identifier] = home
            instock_path_by_stock[identifier] = path_value
    for home in homes:
        dpath = _legacy_path(home.get("detail_url"))
        if not dpath:
            continue
        route_key = _detail_route_key(dpath)
        if not route_key:
            continue
        detail_by_route[route_key] = home
        detail_path_by_route[route_key] = dpath
        qpath = _legacy_path(home.get("quote_url"))
        if qpath:
            quote_redirects[qpath.rstrip("/")] = dpath

    # Catalog de-duplication keeps one canonical home but preserves legacy URLs
    # from the suppressed exact-model rows. Canonical route keys always win on a
    # collision; aliases exist only to recover otherwise-dead product URLs.
    for home in homes:
        dpath = _legacy_path(home.get("detail_url"))
        canonical_key = _detail_route_key(dpath)
        if not canonical_key or detail_by_route.get(canonical_key) is not home:
            continue
        canonical_path = detail_path_by_route[canonical_key]
        detail_aliases = home.get("legacy_detail_aliases")
        if isinstance(detail_aliases, list):
            for value in detail_aliases:
                alias_path = _legacy_path(value) if isinstance(value, str) else None
                alias_key = _detail_route_key(alias_path)
                if alias_key and alias_key not in detail_by_route:
                    detail_alias_redirects.setdefault(alias_key, canonical_path)
        quote_aliases = home.get("legacy_quote_aliases")
        if isinstance(quote_aliases, list):
            for value in quote_aliases:
                alias_path = _legacy_path(value) if isinstance(value, str) else None
                if alias_path and _QUOTE_RE.match(alias_path):
                    alias_key = alias_path.rstrip("/")
                    if alias_key not in quote_redirects:
                        quote_alias_redirects.setdefault(alias_key, canonical_path)

    return {
        "homes": homes,
        "inventory_ok": inventory_ok,
        "detail_by_route": detail_by_route,
        "detail_path_by_route": detail_path_by_route,
        "detail_alias_redirects": detail_alias_redirects,
        "quote_redirects": quote_redirects,
        "quote_alias_redirects": quote_alias_redirects,
        "instock_by_stock": instock_by_stock,
        "instock_path_by_stock": instock_path_by_stock,
    }


def _registry() -> dict:
    global _registry_cache, _registry_built_at
    now = time.time()
    if _registry_cache is not None and now - _registry_built_at < _REGISTRY_TTL_S:
        return _registry_cache
    with _registry_lock:
        if _registry_cache is None or now - _registry_built_at >= _REGISTRY_TTL_S:
            _registry_cache = _build_registry()
            _registry_built_at = now
    return _registry_cache


# ── JSON-LD builders ────────────────────────────────────────────────────────


def _business_id() -> str:
    return _base() + "/#dealership"


def _website_id() -> str:
    return _base() + "/#website"


def _local_business_jsonld() -> dict:
    data = {
        "@context": "https://schema.org",
        "@type": "LocalBusiness",
        "@id": _business_id(),
        "name": business_name(),
        "description": "Manufactured and mobile home dealership",
        "url": _base() + "/",
        "telephone": business_phone(),
        "address": {
            "@type": "PostalAddress",
            "streetAddress": business_street(),
            "addressLocality": business_city(),
            "addressRegion": business_state(),
            "postalCode": business_zip(),
            "addressCountry": "US",
        },
    }
    hours = (get_business() or {}).get("hours_structured")
    if hours:
        data["openingHoursSpecification"] = [
            {
                "@type": "OpeningHoursSpecification",
                "dayOfWeek": spec.get("days", []),
                "opens": spec.get("opens"),
                "closes": spec.get("closes"),
            }
            for spec in hours
        ]
    biz = get_business() or {}
    geo = biz.get("geo")
    if geo and geo.get("latitude") and geo.get("longitude"):
        data["geo"] = {
            "@type": "GeoCoordinates",
            "latitude": geo["latitude"],
            "longitude": geo["longitude"],
        }
    # Recommended LocalBusiness fields Google uses for local-pack/maps placement.
    # All config-driven (config.yaml business.*); each only emitted when present.
    if biz.get("email"):
        data["email"] = biz["email"]
    if biz.get("price_range"):
        data["priceRange"] = biz["price_range"]
    if biz.get("area_served"):
        data["areaServed"] = [{"@type": "City", "name": c} for c in biz["area_served"]]
    same_as = [s for s in (biz.get("same_as") or []) if s]
    if same_as:
        data["sameAs"] = same_as
    image = os.environ.get("OG_IMAGE_URL") or biz.get("image")
    if image:
        data["image"] = image if str(image).startswith("http") else _base() + str(image)
    # Organization-level entity signals (LocalBusiness inherits from Organization)
    # — richer knowledge-graph understanding for Google. All accurate + additive;
    # none are price/review data, so no structured-data policy risk.
    logo = biz.get("logo_url")
    if logo:
        data["logo"] = logo if str(logo).startswith("http") else _base() + str(logo)
    data["contactPoint"] = {
        "@type": "ContactPoint",
        "telephone": business_phone(),
        "contactType": "sales",
        "areaServed": "US-TX",
        "availableLanguage": ["English"],
    }
    data["knowsAbout"] = [
        "Manufactured homes",
        "Mobile homes",
        "Modular homes",
        "Manufactured home financing",
    ]
    return data


def _website_jsonld() -> dict:
    """One site entity. Search is on-page, so there is no SearchAction URL."""
    return {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "@id": _website_id(),
        "name": business_name(),
        "url": _base() + "/",
        "publisher": {"@id": _business_id()},
    }


def _webpage_jsonld(name: str, description: str, path: str, *, page_type: str = "WebPage") -> dict:
    url = _base() + (path if path != "/" else "/")
    return {
        "@context": "https://schema.org",
        "@type": page_type,
        "@id": url + "#webpage",
        "url": url,
        "name": name,
        "description": description,
        "isPartOf": {"@id": _website_id()},
        "about": {"@id": _business_id()},
    }


def _service_area_jsonld(city: str, path: str) -> dict:
    """The page is about delivery to this city. The showroom stays in Huffman."""
    return {
        "@context": "https://schema.org",
        "@type": "Service",
        "name": f"Manufactured and mobile home sales in {city}, TX",
        "serviceType": "Manufactured home sales",
        "url": _base() + path,
        "provider": {"@id": _business_id()},
        "areaServed": {"@type": "City", "name": city},
    }


def _breadcrumb_trail(items: list[tuple[str, str]]) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": index, "name": name, "item": url}
            for index, (name, url) in enumerate(items, start=1)
        ],
    }


def _first_image(home: dict) -> str | None:
    """Return the first public photo for OG / JSON-LD.

    Used homes use the same own-photo picker as ``/homes/`` pages so social
    cards never show a manufacturer catalog shot. New homes keep catalog
    heroes.
    """
    if is_preowned(home):
        images = listing_page_images(home)
        return images[0] if images else None
    for key in ("hero_image", "image_url"):
        if home.get(key):
            return str(home[key])
    photos = home.get("real_photos") or home.get("photos") or home.get("gallery_images")
    if photos:
        first = photos[0]
        return str(first.get("url") if isinstance(first, dict) else first)
    return None


def _inventory_hero_image() -> str | None:
    """Return the image React will prioritize on the public inventory hero."""
    homes = _safe_homes()
    for home in homes:
        availability = home.get("inventory_kind")
        is_orderable = availability == "orderable_floorplan" or home.get("status") == "Orderable"
        image = _first_image(home)
        if image and not is_orderable:
            return image
    for home in homes:
        image = _first_image(home)
        if image:
            return image
    return None


def _phone_digits() -> str:
    digits = re.sub(r"\D", "", business_phone() or "")
    if len(digits) == 10:
        digits = "1" + digits
    return digits


def _tel_href() -> str:
    digits = _phone_digits()
    return f"tel:+{digits}" if digits else "tel:"


def _sms_href(home: dict | None = None) -> str:
    digits = _phone_digits()
    name = ""
    if isinstance(home, dict) and home.get("model_name"):
        name = f" the {home.get('model_name')}"
    body = quote_plus(f"Hi, I'm interested in{name} at {business_name()}.")
    base = f"sms:+{digits}" if digits else "sms:"
    return f"{base}?&body={body}"


_CALL_FOR_PRICE_RE = re.compile(r"^call\s+for\s+pric(?:e|ing)$", re.IGNORECASE)
_DOLLAR_AMOUNT_RE = re.compile(r"\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)")


def _is_call_for_price_label(value) -> bool:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return not text or bool(_CALL_FOR_PRICE_RE.match(text))


def _visible_dollar_amount(display: str) -> float | None:
    match = _DOLLAR_AMOUNT_RE.search(str(display or ""))
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


_NONFINITE_PRICE_TEXT = frozenset({"nan", "inf", "-inf", "+inf", "infinity", "-infinity"})


def _is_finite_positive(price: float | None) -> bool:
    """True only for a real number greater than zero.

    NaN and infinities must be rejected before ``<=`` or ``abs`` comparisons.
    ``float("NaN") <= 0`` and ``abs(float("NaN") - shown) > 0.5`` are both
    false, so a comparison-only guard treats NaN as a valid Offer price.
    """
    return isinstance(price, int | float) and math.isfinite(price) and price > 0


def _offer_price_text(price: float | None) -> str | None:
    """Whole-dollar Offer price text, or None when it is not a real price."""
    if isinstance(price, bool) or not _is_finite_positive(price):
        return None
    text = f"{price:.0f}"
    if text.lower() in _NONFINITE_PRICE_TEXT or not text.isdecimal():
        return None
    return text


def _shown_offer_price(home: dict) -> float | None:
    """Numeric Offer price only when the page shows that same dollar amount.

    Call for Price, a blank price, a non-finite price (NaN or infinity,
    including the strings "nan" and "inf"), or a price_value that does not
    match the visible price never becomes Product or Offer markup (issue #398).
    """
    visible = str(home.get("display_price") or "").strip()
    if _is_call_for_price_label(visible):
        return None
    shown = _visible_dollar_amount(visible)
    if not _is_finite_positive(shown):
        return None
    raw = home.get("price_value")
    try:
        price = float(raw) if raw is not None else None
    except (TypeError, ValueError) as e:
        logger.warning(
            f"SEO _product_jsonld: unparseable price_value {raw!r}, omitting Product JSON-LD: {e}"
        )
        return None
    if price is not None and not math.isfinite(price):
        logger.warning(
            f"SEO _product_jsonld: non-finite price_value {raw!r}, omitting Product JSON-LD"
        )
        return None
    if not _is_finite_positive(price) or abs(price - shown) > 0.5:
        return None
    return price


def _product_jsonld(home: dict, canonical_url: str) -> dict | None:
    # Google's Product snippet requires one of offers/review/aggregateRating, and
    # a valid Offer REQUIRES a real price. A Call for Price page has no public
    # price and no per-home ratings, so it gets no Product markup. A hidden
    # price_value must not leak into an Offer the page does not show.
    price = _shown_offer_price(home)
    price_text = _offer_price_text(price)
    if price_text is None:
        return None

    specs = home.get("specs") or {}
    bits = []
    if specs.get("beds"):
        bits.append(f"{specs['beds']} bed")
    if specs.get("baths"):
        bits.append(f"{specs['baths']} bath")
    if specs.get("dimensions"):
        bits.append(str(specs["dimensions"]))
    description = home.get("description") or (
        f"{home.get('model_name', 'Manufactured home')} ({', '.join(bits)}) at "
        f"{business_name()} in {_CITY_STATE}."
    )
    data = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": home.get("model_name") or "Manufactured Home",
        "description": str(description)[:300],
        "url": canonical_url,
    }
    if home.get("manufacturer"):
        data["brand"] = {"@type": "Brand", "name": home["manufacturer"]}
    image = _first_image(home)
    if image:
        data["image"] = image
    offer = {
        "@type": "Offer",
        "price": price_text,
        "priceCurrency": "USD",
        "url": canonical_url,
    }
    status = str(home.get("status", "")).lower()
    if "order" in status:
        offer["availability"] = "https://schema.org/PreOrder"
    elif any(token in status for token in ("sold", "unavailable")):
        offer["availability"] = "https://schema.org/OutOfStock"
    elif home.get("availability_verified") is True:
        offer["availability"] = "https://schema.org/InStock"
    data["offers"] = offer
    return data


# ── Head + crawlable-body rendering ────────────────────────────────────────

_TITLE_RE = re.compile(r"<title>.*?</title>", re.DOTALL)
_DESC_RE = re.compile(r'<meta\s+name="description"[^>]*/?>')

_shell_cache: tuple[float, str] | None = None


def _shell() -> str:
    """dist/index.html, cached on mtime so deploys pick up new bundles."""
    global _shell_cache
    try:
        mtime = os.path.getmtime(_index_html_path)
        if _shell_cache and _shell_cache[0] == mtime:
            return _shell_cache[1]
        with open(_index_html_path, encoding="utf-8") as f:
            content = f.read()
        _shell_cache = (mtime, content)
        return content
    except OSError as e:
        logger.warning(
            f"SEO _shell: cannot read index shell {_index_html_path!r}, using minimal fallback: {e}"
        )
        return '<!doctype html><html><head><title></title></head><body><div id="root"></div></body></html>'


# ── Analytics / ad-pixel injection (opt-in, runtime env-gated) ──────────────
#
# Snippets are emitted server-side here (this module already owns <head>
# injection for every SPA navigation) so an operator activates a tag at Cloud
# Run RUNTIME with `--update-env-vars` and ZERO rebuild. Each tag is a strict
# no-op until its ID env var is set AND matches a strict format: absent or
# malformed -> no markup, no CSP change, head byte-identical to today. IDs are
# NEVER hardcoded. Pixels load only on indexable pages (see _head_block: the
# snippet is appended only when noindex is False), so they never fire on
# operator/admin/CRM/404 surfaces.
#
# OPERATOR/LEGAL GATE: IDs remain runtime configuration, but the browser now
# defaults every storage category to denied and does not fetch a third-party
# tag until the visitor explicitly grants the shared first-party consent key.
# Activation still requires operator review of the public privacy copy and the
# vendor account settings; code alone must never turn on a paid campaign.

_GA4_RE = re.compile(r"^G-[A-Z0-9]{4,15}$")
_GTM_RE = re.compile(r"^GTM-[A-Z0-9]{4,10}$")
_META_PIXEL_RE = re.compile(r"^\d{8,20}$")
_TIKTOK_PIXEL_RE = re.compile(r"^[A-Za-z0-9]{16,30}$")

_ANALYTICS_IDS = (
    ("GA4_MEASUREMENT_ID", _GA4_RE),
    ("GTM_CONTAINER_ID", _GTM_RE),
    ("META_PIXEL_ID", _META_PIXEL_RE),
    ("TIKTOK_PIXEL_ID", _TIKTOK_PIXEL_RE),
)

# Extra CSP hosts each tag needs, added ONLY when that ID is validly set.
# img-src already allows `https:` so pixel images need no addition.
_ANALYTICS_CSP = {
    "GA4_MEASUREMENT_ID": {
        "script-src": ("https://www.googletagmanager.com",),
        "connect-src": (
            "https://www.google-analytics.com",
            "https://*.google-analytics.com",
            "https://*.analytics.google.com",
        ),
    },
    "GTM_CONTAINER_ID": {
        "script-src": ("https://www.googletagmanager.com",),
        "connect-src": ("https://www.googletagmanager.com",),
    },
    "META_PIXEL_ID": {
        "script-src": ("https://connect.facebook.net",),
        "connect-src": ("https://www.facebook.com",),
    },
    "TIKTOK_PIXEL_ID": {
        "script-src": ("https://analytics.tiktok.com",),
        "connect-src": ("https://analytics.tiktok.com",),
    },
}


def _clean_id(env_name: str, pattern: re.Pattern) -> str | None:
    """Return the env value only if present AND format-valid, else None.

    A malformed / leftover value is treated as unset: no markup is emitted and
    the CSP is not widened for it.
    """
    raw = (os.environ.get(env_name) or "").strip()
    return raw if raw and pattern.match(raw) else None


def _analytics_ids() -> dict:
    """Validated analytics IDs that are currently active (empty when none)."""
    out = {}
    for env_name, pattern in _ANALYTICS_IDS:
        val = _clean_id(env_name, pattern)
        if val:
            out[env_name] = val
    return out


def analytics_csp_sources() -> dict:
    """Extra CSP hosts for the validly-configured tags (uses the SAME _clean_id
    gate as the snippet). Returns {} when none are set, so main.py's CSP header
    is byte-identical to today until an operator sets a valid ID."""
    extra: dict[str, set] = {}
    for env_name in _analytics_ids():
        for directive, hosts in _ANALYTICS_CSP.get(env_name, {}).items():
            extra.setdefault(directive, set()).update(hosts)
    return {k: sorted(v) for k, v in extra.items()}


# First-party consent-gated analytics bootstrap, served as an EXTERNAL script
# from /tho-analytics.js so the CSP can drop 'unsafe-inline' from script-src
# (COD-153). The per-deploy config (validated IDs only) rides in an inert
# <script type="application/json"> data block, which CSP does not execute.
# Behavior is unchanged: consent defaults to denied and no vendor JavaScript
# is fetched until tho_analytics_consent_v1 is explicitly "granted".
ANALYTICS_BOOTSTRAP_PATH = "/tho-analytics.js"
_ANALYTICS_BOOTSTRAP_JS = """(function(w,d){
var el=d.getElementById('tho-analytics-config');if(!el)return;
var c;try{c=JSON.parse(el.textContent||'{}')}catch(e){return;}
if(!c||typeof c!=='object')return;

var KEY='tho_analytics_consent_v1',loaded=false;
w.__THO_ANALYTICS_CONFIGURED__=true;
w.dataLayer=w.dataLayer||[];
w.gtag=w.gtag||function(){w.dataLayer.push(arguments);};
var denied={analytics_storage:'denied',ad_storage:'denied',ad_user_data:'denied',ad_personalization:'denied'};
// Measurement consent never implies permission for personalized advertising.
var granted={analytics_storage:'granted',ad_storage:'granted',ad_user_data:'granted',ad_personalization:'denied'};
w.gtag('consent','default',denied);
function add(src){var s=d.createElement('script');s.async=true;s.src=src;(d.head||d.documentElement).appendChild(s);}
function loadGoogle(){
 var ga=c.GA4_MEASUREMENT_ID,gtm=c.GTM_CONTAINER_ID;
 if(ga){add('https://www.googletagmanager.com/gtag/js?id='+ga);w.gtag('js',new Date());w.gtag('config',ga,{send_page_view:false});}
 if(gtm){w.dataLayer.push({'gtm.start':new Date().getTime(),event:'gtm.js'});add('https://www.googletagmanager.com/gtm.js?id='+gtm);}
}
function loadMeta(){var id=c.META_PIXEL_ID;if(!id)return;
 !function(f,b,e,v,n,t,s){if(f.fbq)return;n=f.fbq=function(){n.callMethod?n.callMethod.apply(n,arguments):n.queue.push(arguments)};
 if(!f._fbq)f._fbq=n;n.push=n;n.loaded=true;n.version='2.0';n.queue=[];t=b.createElement(e);t.async=true;t.src=v;
 s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}(w,d,'script','https://connect.facebook.net/en_US/fbevents.js');
 w.fbq('consent','grant');w.fbq('init',id);w.fbq('track','PageView');
}
function loadTikTok(){var id=c.TIKTOK_PIXEL_ID;if(!id)return;
 !function(w,d,t){w.TiktokAnalyticsObject=t;var ttq=w[t]=w[t]||[];ttq.methods=['page','track','identify','instances','debug','on','off','once','ready','alias','group','enableCookie','disableCookie'];
 ttq.setAndDefer=function(t,e){t[e]=function(){t.push([e].concat(Array.prototype.slice.call(arguments,0)))}};
 for(var i=0;i<ttq.methods.length;i++)ttq.setAndDefer(ttq,ttq.methods[i]);ttq.load=function(e){var i='https://analytics.tiktok.com/i18n/pixel/events.js';
 var o=d.createElement('script');o.type='text/javascript';o.async=true;o.src=i+'?sdkid='+e+'&lib='+t;(d.head||d.documentElement).appendChild(o)};
 ttq.load(id);ttq.enableCookie();ttq.page()}(w,d,'ttq');
}
w.__THO_ENABLE_ANALYTICS__=function(){w.__THO_ANALYTICS_CONSENT__='granted';w.gtag('consent','update',granted);if(loaded)return;loaded=true;loadGoogle();loadMeta();loadTikTok();};
w.__THO_DISABLE_ANALYTICS__=function(){w.__THO_ANALYTICS_CONSENT__='denied';w.gtag('consent','update',denied);if(w.fbq)w.fbq('consent','revoke');if(w.ttq&&w.ttq.disableCookie)w.ttq.disableCookie();};
var pref=null;try{pref=w.localStorage.getItem(KEY)}catch(e){}
w.__THO_ANALYTICS_CONSENT__=pref==='granted'?'granted':pref==='denied'?'denied':null;
if(pref==='granted')w.__THO_ENABLE_ANALYTICS__();
})(window,document);
"""


def _analytics_head() -> str:
    """Consent-gated loader for whichever analytics IDs are validly set.

    Emits an inert JSON config block plus a first-party external bootstrap
    (``/tho-analytics.js``); no inline executable script. The bootstrap
    defaults Google's consent signals to denied and does not fetch Google,
    Meta, or TikTok JavaScript until ``tho_analytics_consent_v1`` is explicitly
    ``granted``. This is intentionally stricter than cookieless/advanced mode.
    """
    ids = _analytics_ids()
    if not ids:
        return ""
    # Values have already passed strict alphanumeric ID regexes. JSON encoding
    # with "<" escaped means the data block can never break out of <script>.
    config = json.dumps(ids, separators=(",", ":")).replace("<", "\\u003c")
    return (
        '<script type="application/json" id="tho-analytics-config">'
        + config
        + "</script>\n    "
        + f'<script src="{ANALYTICS_BOOTSTRAP_PATH}"></script>'
    )


def _default_og_image() -> str | None:
    """Brand social-share image (og:image / twitter:image) from OG_IMAGE_URL.

    Defaults to the bundled /og-card.png (a real 1200x630 raster shipped in
    the frontend build, so code + asset deploy atomically and the URL cannot
    dangle). OG_IMAGE_URL overrides it; setting it to an empty string opts
    out entirely. A relative value (starts with "/") is resolved against the
    canonical base so the emitted URL is absolute, as social scrapers
    require — scrapers reject SVG, keep this a raster.
    """
    raw = os.environ.get("OG_IMAGE_URL", "/og-card.png").strip()
    if not raw:
        return None
    return _base() + raw if raw.startswith("/") else raw


def _head_block(
    title,
    description,
    canonical_url,
    og_image=None,
    jsonld=None,
    noindex=False,
    preload_image=None,
    og_image_alt=None,
):
    e = html.escape
    parts = [
        f"<title>{e(title)}</title>",
        f'<meta name="description" content="{e(description)}" />',
    ]
    if noindex:
        parts.append('<meta name="robots" content="noindex" />')
    else:
        parts.append(f'<link rel="canonical" href="{e(canonical_url)}" />')
        if preload_image:
            # Preload the file once. Width query params are not honored by the
            # image host, and a fake srcset makes the browser fetch it again.
            parts.append(
                '<link rel="preload" as="image" '
                f'href="{e(preload_image)}" fetchpriority="high" />'
            )
        parts.extend(
            [
                f'<meta property="og:title" content="{e(title)}" />',
                f'<meta property="og:description" content="{e(description)}" />',
                f'<meta property="og:url" content="{e(canonical_url)}" />',
                '<meta property="og:type" content="website" />',
                '<meta property="og:locale" content="en_US" />',
                f'<meta property="og:site_name" content="{e(business_name())}" />',
                '<meta name="twitter:card" content="summary_large_image" />',
                f'<meta name="twitter:title" content="{e(title)}" />',
                f'<meta name="twitter:description" content="{e(description)}" />',
            ]
        )
        if og_image is None:
            og_image = _default_og_image()
        if og_image:
            parts.append(f'<meta property="og:image" content="{e(og_image)}" />')
            parts.append(f'<meta name="twitter:image" content="{e(og_image)}" />')
            if og_image_alt:
                parts.append(f'<meta property="og:image:alt" content="{e(og_image_alt)}" />')
    for block in jsonld or []:
        # Escape "<" so crawled values containing "</script>" cannot break
        # out of the JSON-LD block (< is valid JSON, inert in HTML).
        payload = json.dumps(block, ensure_ascii=False).replace("<", "\\u003c")
        parts.append('<script type="application/ld+json">' + payload + "</script>")
    # Analytics/ad pixels: indexable pages only (never noindex admin/CRM/404).
    if not noindex:
        snippet = _analytics_head()
        if snippet:
            parts.append(snippet)
    return "\n    ".join(parts)


def _storefront_links(current_path: str) -> str:
    """Real links among the home page, inventory, listings' neighbors, and cities.

    City names come from config.yaml area_served. The current page is named,
    not linked to itself.
    """
    e = html.escape
    parts = []
    if current_path != "/":
        parts.append('<a href="/">Home</a>')
    if current_path != "/inventory":
        parts.append('<a href="/inventory">All homes</a>')
    for path, city in _city_pages().items():
        label = f"Homes in {city}, TX"
        if path == current_path:
            parts.append(f'<span aria-current="page">{e(label)}</span>')
        else:
            parts.append(f'<a href="{e(path)}">{e(label)}</a>')
    return (
        '<nav aria-label="Home, inventory, and areas we serve"><p>'
        + " · ".join(parts)
        + "</p></nav>"
    )


def _crawlable_inventory_block(*, hub: bool = False) -> str:
    """Semantic HTML (replaced by React on mount) so non-JS crawlers see
    the inventory and have <a href> paths to every detail page.

    ``hub=True`` is the /inventory catalog. The homepage keeps its own
    heading so the two URLs are not copies of each other.
    """
    e = html.escape
    reg = _registry()
    items = []
    for route_key, home in list(reg["detail_by_route"].items()):
        path = reg["detail_path_by_route"][route_key]
        specs = home.get("specs") or {}
        label = home.get("model_name") or "Manufactured home"
        extra = " · ".join(
            str(x)
            for x in (
                f"{specs.get('beds')} bed" if specs.get("beds") else None,
                f"{specs.get('baths')} bath" if specs.get("baths") else None,
                home.get("manufacturer"),
            )
            if x
        )
        items.append(
            f'<li><a href="{e(path)}">{e(label)}</a>{" — " + e(extra) if extra else ""}</li>'
        )
    seen_listing_paths = {reg["detail_path_by_route"].get(key) for key in reg["detail_by_route"]}
    for home in reg["homes"]:
        path = listing_path(home)
        if not path or path in seen_listing_paths:
            continue
        seen_listing_paths.add(path)
        specs = home.get("specs") or {}
        label = home.get("model_name") or "Manufactured home"
        extra = " · ".join(
            str(x)
            for x in (
                f"{specs.get('beds')} bed" if specs.get("beds") else None,
                f"{specs.get('baths')} bath" if specs.get("baths") else None,
                home.get("manufacturer"),
            )
            if x
        )
        items.append(
            f'<li><a href="{e(path)}">{e(label)}</a>{" — " + e(extra) if extra else ""}</li>'
        )
    if hub:
        heading = f"Home Inventory in {e(_CITY_STATE)}"
        intro = (
            f"<p>Current manufactured and mobile home inventory at {e(business_name())} "
            f"in {e(_CITY_STATE)}. Listed homes, pre-owned homes, and orderable floorplans "
            f"are linked below. Confirm current price and availability with our team.</p>"
        )
        current = "/inventory"
    else:
        heading = f"Manufactured and Mobile Homes for Sale in {e(_CITY_STATE)}"
        intro = (
            f"<p>{e(business_name())}, {e(business_address())}. "
            f"{e(business_phone())}. {e(business_hours())}.</p>"
        )
        current = "/"
    return (
        f"<h1>{heading}</h1>"
        f"{intro}"
        '<p>Browse by home type: <a href="/single-wide">Single Wide Homes</a> · '
        '<a href="/double-wide">Double Wide Homes</a></p>'
        f"{_storefront_links(current)}"
        f"<ul>{''.join(items)}</ul>"
    )


def _home_matches_classification(home: dict, classification: str | None) -> bool:
    if not classification:
        return True
    return normalize_inventory_classification(
        home.get("classification")
    ) == normalize_inventory_classification(classification)


def _category_homes(category: dict, registry: dict | None = None) -> list[dict]:
    reg = registry or _registry()
    return [
        home
        for home in reg["homes"]
        if _home_matches_classification(home, category["classification"])
    ]


def _crawlable_category_recovery_block(category: dict, *, outage: bool) -> str:
    e = html.escape
    if outage:
        heading = "Inventory is temporarily unavailable"
        message = "Please browse all homes again shortly or contact our team for current options."
    else:
        heading = f"No {category['classification'].lower()} homes are listed right now"
        message = "Browse all homes or contact our team about upcoming and orderable options."
    return (
        f"<h1>{e(heading)}</h1><p>{e(message)}</p>"
        '<p><a href="/inventory">Browse all homes</a> · '
        '<a href="/contact">Contact us</a></p>'
    )


def _crawlable_category_block(category: dict) -> str:
    """Server-render a category-specific link graph for non-JS crawlers."""
    e = html.escape
    reg = _registry()
    classification = category["classification"]
    items = []
    for home in _category_homes(category, reg):
        route_key = _detail_route_key(_legacy_path(home.get("detail_url")))
        path = (
            reg["detail_path_by_route"].get(route_key)
            if route_key and reg["detail_by_route"].get(route_key) is home
            else listing_path(home)
        )
        specs = home.get("specs") or {}
        label = home.get("model_name") or f"{classification} manufactured home"
        extra = " · ".join(
            str(x)
            for x in (
                f"{specs.get('beds')} bed" if specs.get("beds") else None,
                f"{specs.get('baths')} bath" if specs.get("baths") else None,
                home.get("manufacturer"),
            )
            if x
        )
        item_label = f'<a href="{e(path)}">{e(label)}</a>' if path else e(label)
        items.append(f'<li>{item_label}{" — " + e(extra) if extra else ""}</li>')

    return (
        f"<h1>{e(category['heading'])} in {e(_CITY_STATE)}</h1>"
        f"<p>Browse {e(classification.lower())} listed homes plus orderable floorplans from "
        f"{e(business_name())}. Confirm current price and availability before planning a visit.</p>"
        f"<ul>{''.join(items)}</ul>"
        f'<p><a href="/inventory">Browse all manufactured homes</a> · '
        f'<a href="/appointments">Book a showroom visit</a> · '
        f'<a href="tel:{e(business_phone())}">Call {e(business_phone())}</a></p>'
        f"{_storefront_links(_category_path(category))}"
        f"<p>{e(business_name())} · {e(business_address())} · {e(business_hours())}</p>"
    )


def _public_home_path(home: dict) -> str:
    return listing_path(home) or _legacy_path(home.get("detail_url")) or "/inventory"


def _category_path(category: dict) -> str:
    for path, item in HOME_CATEGORY_PAGES.items():
        if item["heading"] == category.get("heading"):
            return path
    return "/inventory"


def _crawlable_detail_block(home: dict) -> str:
    e = html.escape
    specs = home.get("specs") or {}
    rows = []
    for label, key in (
        ("Beds", "beds"),
        ("Baths", "baths"),
        ("Dimensions", "dimensions"),
        ("Square feet", "sqft"),
    ):
        if specs.get(key):
            rows.append(f"<li>{label}: {e(str(specs[key]))}</li>")
    if home.get("manufacturer"):
        rows.append(f"<li>Manufacturer: {e(str(home['manufacturer']))}</li>")
    rows.append(f"<li>Price: {e(_listing_display_price(home))}</li>")
    description = home.get("description") or ""
    return (
        f"<h1>{e(home.get('model_name') or 'Manufactured Home')}</h1>"
        f"<p>{e(str(description)[:500])}</p>"
        f"<ul>{''.join(rows)}</ul>"
        f'<p><a href="/inventory">All homes for sale at {e(business_name())}</a>. '
        f"{e(business_address())}. {e(business_phone())}.</p>"
        f"{_storefront_links(_public_home_path(home))}"
    )


def _listing_image_alt(
    home: dict, url: str, index: int, total: int, *, floorplan: bool = False
) -> str:
    """Alt text for one listing photo. Filename subjects are used only when
    the file already names the view (ext, kit, kitchen, ...)."""
    name = str(home.get("model_name") or "Manufactured home").strip() or "Manufactured home"
    if floorplan:
        return f"Floor plan for {name}"
    subject = listing_photo_subject(url)
    if subject and total > 1:
        photo = f"{subject} photo {index}"
    elif subject:
        photo = f"{subject} photo"
    elif total > 1:
        photo = f"manufactured home photo {index}"
    else:
        photo = "manufactured home"
    return f"{name} — {photo} at {business_name()} in {_CITY_STATE}"


def _listing_display_price(home: dict) -> str:
    price = str(home.get("display_price") or "").strip()
    return price if price else "Call for Price"


def _listing_product_jsonld(home: dict, canonical_url: str) -> dict | None:
    """Product JSON-LD for /homes/ pages. Never invents a price.

    Google requires Product rich results to carry offers/review/aggregateRating;
    "Call for Price" homes have no offer, so they get no Product markup at all
    (Search Console "Either offers, review, or aggregateRating" error, Oct 11).
    """
    priced = _product_jsonld(home, canonical_url)
    if not priced:
        return None
    images = listing_page_images(home)
    if images:
        priced["image"] = images[0]
    return priced


def _crawlable_home_listing_block(home: dict) -> str:
    e = html.escape
    specs = home.get("specs") or {}
    rows = []
    identifier = stock_id(home)
    if identifier:
        rows.append(f"<li>Stock #{e(identifier)}</li>")
    for label, key in (
        ("Beds", "beds"),
        ("Baths", "baths"),
        ("Dimensions", "dimensions"),
        ("Square feet", "sqft"),
        ("Square feet", "sq_ft"),
    ):
        if specs.get(key) and f"{label}:" not in "".join(rows):
            rows.append(f"<li>{label}: {e(str(specs[key]))}</li>")
    if home.get("manufacturer"):
        rows.append(f"<li>Manufacturer: {e(str(home['manufacturer']))}</li>")
    rows.append(f"<li>Price: {e(_listing_display_price(home))}</li>")
    images = listing_page_images(home)
    plan_urls = {
        str(home.get("floorplan_url") or "").strip(),
        str(home.get("floor_plan_url") or "").strip(),
        *[str(u).strip() for u in (home.get("floorplan_urls") or []) if u],
    }
    figures = []
    for index, url in enumerate(images, start=1):
        is_plan = url in plan_urls or (
            len(images) == 1 and not any(item not in plan_urls for item in images)
        )
        alt = _listing_image_alt(home, url, index, len(images), floorplan=is_plan)
        if is_plan:
            figures.append(
                f'<figure><img src="{e(url)}" alt="{e(alt)}" />'
                f"<figcaption>Floor plan</figcaption></figure>"
            )
        else:
            figures.append(f'<img src="{e(url)}" alt="{e(alt)}" />')
    description = home.get("description") or (
        f"{home.get('model_name') or 'Manufactured home'} at {business_name()} in {_CITY_STATE}."
    )
    return (
        f"<article>"
        f"<h1>{e(home.get('model_name') or 'Manufactured Home')}</h1>"
        f"<p>{e(str(description)[:500])}</p>"
        f"{''.join(figures)}"
        f"<ul>{''.join(rows)}</ul>"
        f'<p><a href="{e(_tel_href())}">Call {e(business_phone())}</a>'
        f' · <a href="{e(_sms_href(home))}">Text {e(business_phone())}</a>'
        f' · <a href="/financing">Apply for financing</a></p>'
        f'<p><a href="/">Home</a> · '
        f'<a href="/inventory">All homes for sale at {e(business_name())}</a>. '
        f"{e(business_address())}. {e(business_phone())}.</p>"
        f"{_storefront_links(_public_home_path(home))}"
        f"</article>"
    )


def _crawlable_home_gone_block() -> str:
    e = html.escape
    return (
        f"<h1>This home is no longer listed</h1>"
        f"<p>The manufactured home you requested is sold or has been removed from "
        f"{e(business_name())} inventory.</p>"
        f'<p><a href="/inventory">Browse current homes for sale</a> · '
        f'<a href="/contact">Contact us</a></p>'
    )


def _crawlable_contact_block() -> str:
    # Server-rendered NAP + h1 so /contact is not an empty page to non-JS crawlers
    # (the strongest local-relevance/NAP signal on the site).
    e = html.escape
    email = (get_business() or {}).get("email", "")
    maps = "https://www.google.com/maps/search/?api=1&query=" + quote_plus(
        f"{business_name()} {business_address()}"
    )
    return (
        f"<h1>Contact {e(business_name())} — {e(_CITY_STATE)}</h1>"
        f"<p>{e(business_name())} is a manufactured &amp; mobile home dealership in "
        f"{e(_CITY_STATE)}. Call, text, or visit our showroom.</p>"
        "<ul>"
        f"<li>Address: {e(business_address())}</li>"
        f'<li>Phone: <a href="tel:{e(business_phone())}">{e(business_phone())}</a></li>'
        + (f'<li>Email: <a href="mailto:{e(email)}">{e(email)}</a></li>' if email else "")
        + f"<li>Hours: {e(business_hours())}</li>"
        "</ul>"
        f'<p><a href="{e(maps)}">Get directions</a></p>'
    )


def _crawlable_appointments_block() -> str:
    e = html.escape
    return (
        f"<h1>Book a Showroom Visit at {e(business_name())}</h1>"
        f"<p>Schedule a visit to tour manufactured &amp; mobile homes in person at "
        f"{e(business_name())} in {e(_CITY_STATE)}.</p>"
        "<ul>"
        f"<li>Showroom: {e(business_address())}</li>"
        f'<li>Phone: <a href="tel:{e(business_phone())}">{e(business_phone())}</a></li>'
        f"<li>Hours: {e(business_hours())}</li>"
        "</ul>"
    )


def _crawlable_about_block() -> str:
    e = html.escape
    return (
        f"<h1>About {e(business_name())}</h1>"
        f"<p>{e(business_name())} is a licensed manufactured and mobile home dealership "
        f"in {e(_CITY_STATE)}. We help buyers find quality factory-built homes, from "
        f"budget-friendly pre-owned units to new single-section and multi-section floorplans.</p>"
        "<ul>"
        f"<li>Showroom: {e(business_address())}</li>"
        f'<li>Phone: <a href="tel:{e(business_phone())}">{e(business_phone())}</a></li>'
        f"<li>Hours: {e(business_hours())}</li>"
        "</ul>"
        '<p><a href="/inventory">Browse homes for sale</a> · <a href="/contact">Contact us</a></p>'
    )


def _crawlable_financing_block() -> str:
    # TODO(owner): confirm which lenders finance manufactured homes, and whether any in-house program exists, before naming a lender or program.
    e = html.escape
    return (
        f"<h1>Manufactured Home Financing in {e(_CITY_STATE)}</h1>"
        "<p>Lenders look at your credit, income, and land situation. "
        "We can help you find out which loan types may fit.</p>"
        "<ul>"
        "<li>Chattel (home-only) loans</li>"
        "<li>Land-and-home / mortgage loans</li>"
        "<li>Some buyers qualify for FHA, VA, or USDA loans through approved lenders.</li>"
        "<li>Loans through lenders that finance manufactured homes</li>"
        "</ul>"
        f'<p>Call <a href="tel:{e(business_phone())}">{e(business_phone())}</a> or '
        '<a href="/appointments">book a showroom visit</a> to discuss your options.</p>'
    )


def _faqpage_jsonld() -> dict:
    """FAQPage structured data for /faq.

    The questions and answers match the visible content on the FAQ page so the
    schema complies with Google's requirement that FAQPage markup reflect
    page-visible Q&A.
    """
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": "What's the difference between a manufactured home, a mobile home, and a modular home?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": (
                        '"Mobile home" is the older term for factory-built homes made before June 15, 1976. '
                        "Homes built after that date to the federal HUD Code are called manufactured homes. "
                        "Modular homes are also factory-built but assembled to local/state building codes. "
                        f"{business_name()} sells new manufactured homes in single- and multi-section floor plans."
                    ),
                },
            },
            {
                "@type": "Question",
                "name": 'Why do the listings say "Call for Price"?',
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": (
                        "Manufactured-home pricing depends on options, delivery distance, site prep, and "
                        "current factory promotions, so we provide real, itemized quotes rather than a "
                        f"misleading sticker number. Call {business_phone()} or request a quote on any home."
                    ),
                },
            },
            {
                "@type": "Question",
                "name": "How does financing work for a manufactured home?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": (
                        "Several options are commonly available depending on whether you own or are buying land. "
                        "Chattel (home-only) loans are for the home itself when it's on leased land or in a community. "
                        "Land-and-home loans, when the home and land are financed together, may qualify for "
                        "FHA Title I/II, VA, or USDA programs. "
                        "Ask us about lenders that finance manufactured homes."
                    ),
                },
            },
            {
                "@type": "Question",
                "name": "Do I need land to buy a home from you?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": (
                        "No. You can place a home on land you own, on family land, or in a manufactured-home "
                        "community or leased lot. We can discuss the trade-offs of each option."
                    ),
                },
            },
            {
                "@type": "Question",
                "name": "What does delivery and setup include?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": (
                        "A typical new-home delivery includes transport, placement and leveling on your "
                        "prepared site, joining multi-section homes, and connecting to utilities per local "
                        "code. Site prep is usually arranged separately."
                    ),
                },
            },
            {
                "@type": "Question",
                "name": "What areas do you serve / deliver to?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": (
                        f"{business_name()} is located at {business_address()} and serves the greater "
                        "Houston area including Huffman, Humble, Atascocita, Crosby, Kingwood, New Caney, "
                        "Baytown, Beaumont, Cleveland, Conroe, and Livingston."
                    ),
                },
            },
            {
                "@type": "Question",
                "name": "What warranty comes with a new manufactured home?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": (
                        "New manufactured homes carry a manufacturer's warranty on the home and major "
                        "systems, and many components carry their own manufacturer warranties. Texas "
                        "manufactured housing is regulated by the Texas Department of Housing and Community "
                        "Affairs (TDHCA)."
                    ),
                },
            },
            {
                "@type": "Question",
                "name": "How long does the whole process take?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": (
                        "After on-lot availability is confirmed, delivery can often happen within weeks once "
                        "financing and site prep are in place. Factory-ordered floor plans vary by "
                        "manufacturer and season."
                    ),
                },
            },
            {
                "@type": "Question",
                "name": "Can I tour homes in person?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": (
                        f"Yes. Visit our Huffman showroom during business hours ({business_hours()}) or "
                        "book a showroom visit online. Many listings also include photos and 3D virtual tours."
                    ),
                },
            },
            {
                "@type": "Question",
                "name": "How do I get started?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": (
                        f"Call or text {business_phone()}, request a price quote on any home, or book a "
                        "showroom visit. Tell us your budget, land situation, and size needs."
                    ),
                },
            },
        ],
    }


def _crawlable_faq_block() -> str:
    e = html.escape
    questions = [
        (
            "What's the difference between a manufactured home, a mobile home, and a modular home?",
            '"Mobile home" is the older term for factory-built homes made before June 15, 1976. '
            "Homes built after that date to the federal HUD Code are called manufactured homes. "
            "Modular homes are also factory-built but assembled to local/state building codes.",
        ),
        (
            'Why do the listings say "Call for Price"?',
            "Manufactured-home pricing depends on options, delivery distance, site prep, and current "
            "factory promotions, so we provide itemized quotes rather than misleading sticker numbers.",
        ),
        (
            "How does financing work for a manufactured home?",
            "Several options are commonly available depending on whether you own or are buying land. "
            "Chattel (home-only) loans are for the home itself when it's on leased land or in a community. "
            "Land-and-home loans, when the home and land are financed together, may qualify for "
            "FHA Title I/II, VA, or USDA programs. "
            "Ask us about lenders that finance manufactured homes.",
        ),
        (
            "Do I need land to buy a home from you?",
            "No. You can place a home on land you own, family land, or in a manufactured-home community.",
        ),
        (
            "What does delivery and setup include?",
            "Transport, placement and leveling, joining multi-section homes, and utility connections "
            "per local code. Site prep is usually arranged separately.",
        ),
        (
            "What areas do you serve / deliver to?",
            f"{business_name()} serves the greater Houston area from {business_address()}.",
        ),
        (
            "What warranty comes with a new manufactured home?",
            "A manufacturer's warranty on the home and major systems, plus component warranties. "
            "Texas manufactured housing is regulated by TDHCA.",
        ),
        (
            "How long does the whole process take?",
            "After on-lot availability is confirmed, delivery can often happen within weeks once "
            "financing and site prep are in place. Factory-ordered floor plans vary by manufacturer "
            "and season.",
        ),
        (
            "Can I tour homes in person?",
            f"Yes — visit our showroom during business hours ({business_hours()}) or book a visit online.",
        ),
        (
            "How do I get started?",
            f"Call {business_phone()}, request a quote, or book a showroom visit.",
        ),
    ]
    items = "".join(f"<li><h3>{e(q)}</h3><p>{e(a)}</p></li>" for q, a in questions)
    return (
        f"<h1>Frequently Asked Questions — {e(business_name())}</h1>"
        f"<p>Answers about manufactured homes, financing, delivery, warranty, and buying from "
        f"{e(business_name())} in {e(_CITY_STATE)}.</p>"
        f"<ul>{items}</ul>"
        f'<p>Call <a href="tel:{e(business_phone())}">{e(business_phone())}</a> or '
        '<a href="/contact">contact us</a> for more help.</p>'
    )


def _crawlable_warranty_block() -> str:
    e = html.escape
    return (
        f"<h1>Warranty &amp; Service — {e(business_name())}</h1>"
        f"<p>Every new manufactured home we sell is backed by warranties designed to protect your "
        f"investment in {e(_CITY_STATE)}.</p>"
        "<ul>"
        "<li>Manufacturer's warranty on the home and major systems</li>"
        "<li>Component warranties for appliances, HVAC, roofing, and other items</li>"
        "<li>Texas TDHCA oversight for installation and safety requirements</li>"
        "<li>Service coordination through our team</li>"
        "</ul>"
        f'<p>For warranty questions, call <a href="tel:{e(business_phone())}">{e(business_phone())}</a> '
        'or <a href="/contact">contact us</a>.</p>'
    )


def _crawlable_delivery_block() -> str:
    e = html.escape
    return (
        f"<h1>Delivery &amp; Setup — {e(business_name())}</h1>"
        f"<p>{e(business_name())} coordinates transport, placement, and setup for manufactured "
        f"homes delivered throughout the greater Houston area from {e(_CITY_STATE)}.</p>"
        "<ul>"
        "<li>Transport from the factory or lot to your prepared site</li>"
        "<li>Placement, leveling, and joining of multi-section homes</li>"
        "<li>Utility connections per local code</li>"
        "<li>Final walkthrough before handover</li>"
        "</ul>"
        f"<p>Service area includes Huffman, Humble, Atascocita, Crosby, Kingwood, New Caney, "
        f"Baytown, Beaumont, Cleveland, Conroe, and Livingston.</p>"
        f'<p>Call <a href="tel:{e(business_phone())}">{e(business_phone())}</a> to confirm delivery '
        "to your address.</p>"
    )


def _breadcrumb_jsonld(name: str, canonical_url: str) -> dict:
    b = _base()
    return _breadcrumb_trail(
        [
            ("Home", b + "/"),
            ("Inventory", b + "/inventory"),
            (name, canonical_url),
        ]
    )


def _inventory_itemlist_jsonld(limit: int = 25, classification: str | None = None) -> dict | None:
    """ItemList of homes for the listing pages — a carousel/list rich-result
    candidate for "manufactured homes in <city>" queries. Each item is only
    position + url + name; no price/rating (homes are "Call for Price", so a
    priced ListItem would fabricate data Google penalizes)."""
    reg = _registry()
    base = _base()
    elements = []
    for route_key, home in list(reg["detail_by_route"].items()):
        if not _home_matches_classification(home, classification):
            continue
        path = reg["detail_path_by_route"].get(route_key)
        if not path:
            continue
        elements.append(
            {
                "@type": "ListItem",
                "position": len(elements) + 1,
                "url": base + path,
                "name": home.get("model_name") or "Manufactured home",
            }
        )
        if len(elements) >= limit:
            break
    if len(elements) < limit:
        seen_urls = {item["url"] for item in elements}
        for home in reg["homes"]:
            if not _home_matches_classification(home, classification):
                continue
            path = listing_path(home)
            if not path:
                continue
            url = base + path
            if url in seen_urls:
                continue
            elements.append(
                {
                    "@type": "ListItem",
                    "position": len(elements) + 1,
                    "url": url,
                    "name": home.get("model_name") or "Manufactured home",
                }
            )
            seen_urls.add(url)
            if len(elements) >= limit:
                break
    if not elements:
        return None
    return {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "itemListElement": elements,
    }


def _inject(shell: str, head_block: str, body_block: str | None) -> str:
    out = _TITLE_RE.sub("", shell, count=1)
    out = _DESC_RE.sub("", out, count=1)
    out = out.replace("</head>", f"    {head_block}\n  </head>", 1)
    if body_block:
        out = out.replace('<div id="root">', f'<div id="root">{body_block}', 1)
        # vite shells emit `<div id="root"></div>`; handle both spellings
        if body_block not in out:
            out = out.replace('<div id="root"></div>', f'<div id="root">{body_block}</div>', 1)
    return out


def _city_slug(city: str) -> str:
    return city.strip().lower().replace(" ", "-").replace(".", "")


def _city_pages() -> dict[str, str]:
    """Map /manufactured-homes-in-{slug}-tx -> City for each served city
    (config.yaml business.area_served) — local-SEO landing pages."""
    return {
        f"/manufactured-homes-in-{_city_slug(c)}-tx": c
        for c in ((get_business() or {}).get("area_served") or [])
    }


def _crawlable_city_block(city: str) -> str:
    e = html.escape
    reg = _registry()
    path = next((item for item, name in _city_pages().items() if name == city), "")
    # Reuse the same home links as the inventory page so this city page points
    # at real listings. The homes are at the Huffman showroom, not in this city.
    items = []
    seen: set[str] = set()
    for route_key, home in list(reg["detail_by_route"].items()):
        home_path = reg["detail_path_by_route"].get(route_key)
        if not home_path or home_path in seen:
            continue
        seen.add(home_path)
        label = home.get("model_name") or "Manufactured home"
        items.append(f'<li><a href="{e(home_path)}">{e(label)}</a></li>')
    for home in reg["homes"]:
        home_path = listing_path(home)
        if not home_path or home_path in seen:
            continue
        seen.add(home_path)
        label = home.get("model_name") or "Manufactured home"
        items.append(f'<li><a href="{e(home_path)}">{e(label)}</a></li>')
    return (
        f"<h1>Manufactured &amp; Mobile Homes in {e(city)}, TX</h1>"
        f"<p>{e(business_name())} delivers manufactured and mobile homes to "
        f"{e(city)}, TX from the {e(business_city())} showroom at "
        f"{e(business_address())}. Call "
        f'<a href="tel:{e(business_phone())}">{e(business_phone())}</a> '
        f"for the current price. The homes below are listed at that showroom.</p>"
        f"{_storefront_links(path)}"
        f"<ul>{''.join(items)}</ul>"
        f'<p><a href="/appointments">Book a showroom visit</a></p>'
        f"<p>{e(business_name())}. {e(business_address())}. {e(business_hours())}.</p>"
    )


def render_spa_response(full_path: str) -> Response | None:
    """SEO-aware response for an SPA path; None means caller falls through
    to its default file handling. Never raises."""
    try:
        return _render_spa_response(full_path)
    except Exception as e:
        logger.warning(
            f"SEO render_spa_response: rendering failed for {full_path!r}, falling through: {e}"
        )
        return None


def _render_spa_response(full_path: str) -> Response | None:
    raw_path = "/" + full_path if full_path else "/"
    path = "/" + full_path.strip("/") if full_path else "/"
    base = _base()

    # 1. Legacy quote URLs -> matching detail page. Canonical quote routes are
    # permanent; de-duplication aliases are temporary because their survivor
    # can disappear and let the original plan become canonical again.
    if _QUOTE_RE.match(path):
        reg = _registry()
        quote_key = path.rstrip("/")
        target = reg["quote_redirects"].get(quote_key)
        if target:
            return RedirectResponse(target, status_code=301)
        alias_target = reg["quote_alias_redirects"].get(quote_key)
        if alias_target:
            return RedirectResponse(
                alias_target,
                status_code=302,
                headers={"Cache-Control": "no-store"},
            )
        return RedirectResponse("/inventory", status_code=301)

    # 2. Legacy aliases for the listing hub -> new /inventory.
    if path.lower() in ("/home", "/index.html"):
        return RedirectResponse("/inventory", status_code=301)

    # 2b. Legacy vendor marketing/brand/city pages -> closest relevant page (301).
    #     Preserves the old site's search equity instead of hard-404ing on cutover.
    vendor_target = _LEGACY_VENDOR_REDIRECTS.get(path.lower()) or _LEGAL_REDIRECTS.get(path.lower())
    if vendor_target:
        return RedirectResponse(vendor_target, status_code=301)

    # 3. One URL per page: trailing-slash (or doubled-slash) and case variants of
    #    public routes 301 to the canonical no-slash, lowercase form.
    if path.lower() in PUBLIC_ROUTES and path != path.lower():
        return RedirectResponse(path.lower(), status_code=301)
    if path in PUBLIC_ROUTES and raw_path != path:
        return RedirectResponse(path, status_code=301)

    no_cache = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }

    # 3b. City landing pages: 200 + city-targeted head/body. A local-SEO page per
    #     served city — recovers the legacy vendor city pages (which used to 301
    #     to /inventory and bleed their backlink equity) and targets high-intent
    #     "manufactured homes in {city}" queries.
    city = _city_pages().get(path.lower())
    if city:
        if raw_path != path:
            return RedirectResponse(path, status_code=301)
        title = f"Manufactured Homes in {city}, TX | {business_name()}"
        desc = (
            f"{business_name()} delivers manufactured and mobile homes to {city}, TX from the "
            f"{business_city()} showroom at {business_street()}. Call {business_phone()} for price."
        )
        jsonld = [
            _local_business_jsonld(),
            _webpage_jsonld(title, desc, path),
            _service_area_jsonld(city, path),
            _breadcrumb_trail(
                [
                    ("Home", base + "/"),
                    (f"Homes in {city}, TX", base + path),
                ]
            ),
        ]
        itemlist = _inventory_itemlist_jsonld()
        if itemlist:
            jsonld.append(itemlist)
        head = _head_block(title, desc, base + path, jsonld=jsonld)
        return HTMLResponse(_inject(_shell(), head, _crawlable_city_block(city)), headers=no_cache)

    # 3c. High-intent home-type pages: preserve the legacy category URLs while
    #     serving a truthful, filtered link graph instead of generic inventory.
    category_path = path.lower()
    category = HOME_CATEGORY_PAGES.get(category_path)
    if category:
        if raw_path != category_path:
            return RedirectResponse(category_path, status_code=301)
        reg = _registry()
        inventory_ok = reg["inventory_ok"]
        category_homes = _category_homes(category, reg)
        if not inventory_ok or not category_homes:
            outage = not inventory_ok
            title = (
                "Inventory temporarily unavailable"
                if outage
                else f"No {category['classification']} homes listed"
            )
            head = _head_block(
                f"{title} | {business_name()}",
                "Browse all manufactured homes or contact our team for current options.",
                base + "/inventory",
                noindex=True,
            )
            return HTMLResponse(
                _inject(
                    _shell(),
                    head,
                    _crawlable_category_recovery_block(category, outage=outage),
                ),
                status_code=503 if outage else 404,
                headers=no_cache,
            )
        itemlist = _inventory_itemlist_jsonld(classification=category["classification"])
        jsonld = [
            _local_business_jsonld(),
            _webpage_jsonld(category["title"], category["description"], category_path),
            _breadcrumb_trail(
                [
                    ("Home", base + "/"),
                    ("Inventory", base + "/inventory"),
                    (category["heading"], base + category_path),
                ]
            ),
        ]
        if itemlist:
            jsonld.append(itemlist)
        head = _head_block(
            category["title"],
            category["description"],
            base + category_path,
            jsonld=jsonld,
        )
        return HTMLResponse(
            _inject(_shell(), head, _crawlable_category_block(category)), headers=no_cache
        )

    # 3d. In-stock listing pages: /homes/<stock-id>-<slug> for special deals
    #     and pre-owned units. Sold or removed stock ids return 410.
    if parse_home_path(path):
        if raw_path != path:
            return RedirectResponse(path, status_code=301)
        reg = _registry()
        if not reg["inventory_ok"]:
            head = _head_block(
                f"Inventory temporarily unavailable | {business_name()}",
                "Browse all manufactured homes or contact our team for current options.",
                base + "/inventory",
                noindex=True,
            )
            return HTMLResponse(
                _inject(_shell(), head, _crawlable_home_gone_block()),
                status_code=503,
                headers=no_cache,
            )
        home = match_instock_home(path, reg["homes"])
        if home is None:
            head = _head_block(
                f"Home no longer listed | {business_name()}",
                "This home is sold or has been removed from inventory.",
                base + "/inventory",
                noindex=True,
            )
            return HTMLResponse(
                _inject(_shell(), head, _crawlable_home_gone_block()),
                status_code=410,
                headers=no_cache,
            )
        canonical_path = listing_path(home)
        if canonical_path and unquote(path) != unquote(canonical_path):
            return RedirectResponse(canonical_path, status_code=301)
        canonical_url = base + (canonical_path or path)
        title = (
            f"{home.get('model_name') or 'Manufactured Home'} — {business_name()}, {_CITY_STATE}"
        )
        specs = home.get("specs") or {}
        desc_bits = ", ".join(
            str(x)
            for x in (
                f"{specs.get('beds')} bed" if specs.get("beds") else None,
                f"{specs.get('baths')} bath" if specs.get("baths") else None,
                specs.get("dimensions"),
                home.get("manufacturer"),
                _listing_display_price(home),
            )
            if x
        )
        description = (
            f"{home.get('model_name', 'Manufactured home')} ({desc_bits}) at "
            f"{business_name()} in {_CITY_STATE}. Call {business_phone()}."
        )[:300]
        images = listing_page_images(home)
        image_alt = _listing_image_alt(home, images[0], 1, len(images)) if images else None
        head = _head_block(
            title,
            description,
            canonical_url,
            og_image=images[0] if images else None,
            og_image_alt=image_alt,
            jsonld=[
                x
                for x in (
                    _listing_product_jsonld(home, canonical_url),
                    _breadcrumb_jsonld(
                        home.get("model_name") or "Manufactured Home", canonical_url
                    ),
                )
                if x
            ],
        )
        return HTMLResponse(
            _inject(_shell(), head, _crawlable_home_listing_block(home)), headers=no_cache
        )

    # 4. Live legacy detail/plan URLs: 200 + per-home head + crawlable body.
    m = _DETAIL_RE.match(path)
    if m:
        reg = _registry()
        route_key = (m.group(1).lower(), m.group(2))
        home = reg["detail_by_route"].get(route_key)
        if home is None:
            alias_target = reg["detail_alias_redirects"].get(route_key)
            if alias_target:
                return RedirectResponse(
                    alias_target,
                    status_code=302,
                    headers={"Cache-Control": "no-store"},
                )
            # Lot homes (special deals / pre-owned) have no legacy detail_url;
            # their canonical page is /homes/<stock>-<slug>. A bare
            # /inventory-detail/<stock id> deep link (old shares, the SPA's
            # legacy-intent route) must reach that page instead of 404ing.
            # Only the inventory-detail family maps to stock ids; /plan/ ids
            # are catalog plan ids and must never resolve to a lot home.
            # Temporary + no-store because lot stock sells and disappears.
            if route_key[0] == "inventory-detail":
                stock_target = reg["instock_path_by_stock"].get(route_key[1])
                if stock_target:
                    return RedirectResponse(
                        stock_target,
                        status_code=302,
                        headers={"Cache-Control": "no-store"},
                    )
            head = _head_block(
                "Home not found | " + business_name(),
                "This home is no longer listed.",
                base + "/inventory",
                noindex=True,
            )
            return HTMLResponse(_inject(_shell(), head, None), status_code=404, headers=no_cache)
        canonical_path = reg["detail_path_by_route"][route_key]
        # One URL per home: slash/no-slash and slug variants 301 to the
        # exact legacy path that carries the indexed equity. Compare on the
        # percent-DECODED form: the registry stores the ENCODED legacy path
        # (urlparse does not decode) while Starlette DECODES the request path,
        # so a raw `!=` on a %-encoded slug (e.g. "é" == "%C3%A9") made the
        # canonical 301 to itself forever (ERR_TOO_MANY_REDIRECTS). unquote()
        # on both sides makes the canonical reachable (200) while still 301ing
        # genuinely different slug variants for the same id.
        if unquote(raw_path) != unquote(canonical_path):
            return RedirectResponse(canonical_path, status_code=301)
        canonical_url = base + canonical_path
        title = (
            f"{home.get('model_name') or 'Manufactured Home'} — {business_name()}, {_CITY_STATE}"
        )
        specs = home.get("specs") or {}
        desc_bits = ", ".join(
            str(x)
            for x in (
                f"{specs.get('beds')} bed" if specs.get("beds") else None,
                f"{specs.get('baths')} bath" if specs.get("baths") else None,
                specs.get("dimensions"),
                home.get("manufacturer"),
            )
            if x
        )
        description = (
            f"{home.get('model_name', 'Manufactured home')} ({desc_bits}) for sale at "
            f"{business_name()} in {_CITY_STATE}. Call {business_phone()}."
        )[:300]
        head = _head_block(
            title,
            description,
            canonical_url,
            og_image=_first_image(home),
            jsonld=[x for x in (_product_jsonld(home, canonical_url),) if x]
            + [_breadcrumb_jsonld(home.get("model_name") or "Manufactured Home", canonical_url)],
        )
        return HTMLResponse(
            _inject(_shell(), head, _crawlable_detail_block(home)), headers=no_cache
        )

    # 5. Known public static routes: 200 + route meta (+ crawlable body block).
    if path in PUBLIC_ROUTES:
        title, description = PUBLIC_ROUTES[path]
        jsonld = (
            [_local_business_jsonld()]
            if path
            in (
                "/",
                "/inventory",
                "/contact",
                "/about",
                "/financing",
                "/faq",
                "/warranty",
                "/delivery",
            )
            else []
        )
        if path == "/":
            jsonld.append(_website_jsonld())
            jsonld.append(_webpage_jsonld(title, description, path, page_type="CollectionPage"))
        elif path == "/inventory":
            jsonld.append(_webpage_jsonld(title, description, path, page_type="CollectionPage"))
            jsonld.append(
                _breadcrumb_trail(
                    [
                        ("Home", base + "/"),
                        ("Inventory", base + "/inventory"),
                    ]
                )
            )
        if path in ("/", "/inventory"):
            itemlist = _inventory_itemlist_jsonld()
            if itemlist:
                jsonld.append(itemlist)
        if path == "/faq":
            jsonld.append(_faqpage_jsonld())
        head = _head_block(
            title,
            description,
            base + (path if path != "/" else "/"),
            jsonld=jsonld,
            preload_image=_inventory_hero_image() if path in ("/", "/inventory") else None,
        )
        if path in ("/", "/inventory"):
            body = _crawlable_inventory_block(hub=path == "/inventory")
        elif path == "/contact":
            body = _crawlable_contact_block()
        elif path == "/appointments":
            body = _crawlable_appointments_block()
        elif path == "/about":
            body = _crawlable_about_block()
        elif path == "/financing":
            body = _crawlable_financing_block()
        elif path == "/faq":
            body = _crawlable_faq_block()
        elif path == "/warranty":
            body = _crawlable_warranty_block()
        elif path == "/delivery":
            body = _crawlable_delivery_block()
        elif path in ("/privacy", "/terms"):
            body = _crawlable_legal_block(path.lstrip("/"))
        else:
            body = None
        return HTMLResponse(_inject(_shell(), head, body), headers=no_cache)

    # 6. Operator/admin routes: 200, noindex.
    if any(path.startswith(p) or path == p.rstrip("/") for p in NOINDEX_PREFIXES):
        head = _head_block(business_name(), "Operator tools.", base + path, noindex=True)
        return HTMLResponse(_inject(_shell(), head, None), headers=no_cache)

    # 7. Anything else the caller can't match to a real file: defer to
    #    caller for file checks; caller asks us again via render_not_found.
    return None


def render_not_found() -> Response:
    """404 (real status) with the SPA shell — kills the soft-404 pattern."""
    head = _head_block(
        f"Page not found | {business_name()}",
        f"The page you requested does not exist. Browse homes for sale at {business_name()}.",
        _base() + "/inventory",
        noindex=True,
    )
    return HTMLResponse(
        _inject(_shell(), head, None),
        status_code=404,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


# Date the public home, inventory, location, and listing templates last changed.
# Listing records keep a later timestamp when the home itself was updated after this.
_SEO_TEMPLATE_LASTMOD = "2026-10-18"


def _template_lastmod_paths() -> set[str]:
    return {"/", "/inventory", "/contact", "/about", *HOME_CATEGORY_PAGES, *_city_pages()}


def _url_entry(loc: str, lastmod: str | None = None) -> str:
    loc_xml = f"  <url><loc>{html.escape(loc)}</loc>"
    if lastmod:
        return loc_xml + f"<lastmod>{html.escape(lastmod)}</lastmod></url>"
    return loc_xml + "</url>"


def _listing_sitemap_lastmod(home: dict) -> str:
    recorded = listing_lastmod(home)
    if recorded >= _SEO_TEMPLATE_LASTMOD:
        return recorded
    return _SEO_TEMPLATE_LASTMOD


# ── robots.txt and sitemap.xml ──────────────────────────────────────────────


@router.get(ANALYTICS_BOOTSTRAP_PATH)
def analytics_bootstrap_js() -> Response:
    """Static first-party analytics bootstrap (no secrets, no IDs inside; it
    reads the inert JSON config block and no-ops when that block is absent)."""
    return Response(
        content=_ANALYTICS_BOOTSTRAP_JS,
        media_type="application/javascript; charset=utf-8",
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt() -> PlainTextResponse:
    # Crawling policy, not access control (auth covers that). Assets and the
    # public GET APIs the SPA renders from stay crawlable on purpose — and we
    # deliberately do NOT block AI crawlers: the business wants AI visibility.
    disallows = "\n".join(
        f"Disallow: {p}"
        for p in (
            "/crm",
            "/documents",
            "/document-center",
            "/analytics",
            "/studio",
            "/ad-studio",
            "/system",
            "/staff",
            "/team",
            "/chat-history",
            "/manage-inventory",
            "/photos",
            "/copilot",
            "/ops-copilot",
            "/hub/",
            "/app/",
            "/api/admin/",
            "/api/deals",
            "/api/leads",
            "/api/email/",
            "/api/documents/",
        )
    )
    body = f"User-agent: *\n{disallows}\n\nSitemap: {_base()}/sitemap.xml\n"
    return PlainTextResponse(body)


@router.get("/sitemap.xml")
def sitemap_xml() -> Response:
    base = _base()
    static_paths = [
        "/",
        "/inventory",
        "/contact",
        "/appointments",
        "/chat",
        "/about",
        "/financing",
        "/faq",
        "/warranty",
        "/delivery",
        "/privacy",
        "/terms",
    ]
    dated = _template_lastmod_paths()
    entries_xml = [
        _url_entry(base + path, _SEO_TEMPLATE_LASTMOD if path in dated else None)
        for path in static_paths
    ]
    entries_xml += [
        _url_entry(base + path, _SEO_TEMPLATE_LASTMOD) for path in sorted(_city_pages())
    ]
    reg = _registry()
    if reg["inventory_ok"]:
        entries_xml += [
            _url_entry(base + path, _SEO_TEMPLATE_LASTMOD)
            for path, category in sorted(HOME_CATEGORY_PAGES.items())
            if _category_homes(category, reg)
        ]
    # Legacy detail and plan pages changed with this template (links and price line).
    entries_xml += [
        _url_entry(base + path, _SEO_TEMPLATE_LASTMOD)
        for path in sorted(reg["detail_path_by_route"].values())
    ]
    home_entries: list[tuple[str, str]] = []
    if reg["inventory_ok"]:
        seen_home_paths: set[str] = set()
        for home in reg["homes"]:
            path_value = listing_path(home)
            if not path_value or path_value in seen_home_paths:
                continue
            seen_home_paths.add(path_value)
            home_entries.append((base + path_value, _listing_sitemap_lastmod(home)))
        home_entries.sort(key=lambda item: item[0])
    entries_xml += [_url_entry(loc, lastmod) for loc, lastmod in home_entries]
    entries = "\n".join(entries_xml)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}\n</urlset>\n"
    )
    return Response(content=xml, media_type="application/xml")
