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
import os
import re
import threading
import time
from urllib.parse import urlparse

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

router = APIRouter()

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
        f"Mobile & Manufactured Homes for Sale in {_CITY_STATE} | {business_name()}",
        f"Browse manufactured and mobile homes for sale at {business_name()} in "
        f"{_CITY_STATE}. On-lot homes ready now plus orderable floorplans. "
        f"Visit our showroom at {business_address()} or call {business_phone()}.",
    ),
    "/inventory": (
        f"Home Inventory — Mobile & Manufactured Homes in {_CITY_STATE} | {business_name()}",
        f"Live inventory of manufactured homes at {business_name()} in {_CITY_STATE}: "
        f"single and double section homes, 3D tours, photos, and orderable floorplans.",
    ),
    "/contact": (
        f"Contact {business_name()} — {_CITY_STATE}",
        f"Visit {business_name()} at {business_address()}, call {business_phone()}, "
        f"or send us a message. Hours: {business_hours()}.",
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
}

# Operator/admin SPA routes: served 200 but with a noindex robots meta.
NOINDEX_PREFIXES = (
    "/crm",
    "/documents",
    "/document-center",
    "/analytics",
    "/studio",
    "/system",
    "/getting-started",
    "/guide",
    "/chat-history",
    "/hub/",
    "/app/",
)

_DETAIL_RE = re.compile(r"^/(inventory-detail|plan)/(\d+)(/|$)", re.IGNORECASE)
_QUOTE_RE = re.compile(r"^/quote(/|$)", re.IGNORECASE)


# ── Inventory-backed URL registry (cached) ──────────────────────────────────

_registry_lock = threading.Lock()
_registry_cache: dict | None = None
_registry_built_at = 0.0
_REGISTRY_TTL_S = 300


def _safe_homes() -> list[dict]:
    if not _get_homes:
        return []
    try:
        return _get_homes() or []
    except Exception:
        # SEO surface must never take the page down with it.
        return []


def _legacy_path(url: str | None) -> str | None:
    if not url:
        return None
    path = urlparse(str(url)).path
    return path if path and path != "/" else None


def _home_key(home: dict) -> str | None:
    m = _DETAIL_RE.match(_legacy_path(home.get("detail_url")) or "")
    return m.group(2) if m else None


def _build_registry() -> dict:
    """Map legacy ids -> homes, detail paths, and quote->detail redirects."""
    detail_by_id: dict[str, dict] = {}
    detail_path_by_id: dict[str, str] = {}
    quote_redirects: dict[str, str] = {}

    for home in _safe_homes():
        dpath = _legacy_path(home.get("detail_url"))
        if not dpath:
            continue
        m = _DETAIL_RE.match(dpath)
        if not m:
            continue
        legacy_id = m.group(2)
        detail_by_id[legacy_id] = home
        detail_path_by_id[legacy_id] = dpath
        qpath = _legacy_path(home.get("quote_url"))
        if qpath:
            quote_redirects[qpath.rstrip("/")] = dpath

    return {
        "detail_by_id": detail_by_id,
        "detail_path_by_id": detail_path_by_id,
        "quote_redirects": quote_redirects,
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


def _local_business_jsonld() -> dict:
    data = {
        "@context": "https://schema.org",
        "@type": "LocalBusiness",
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
    geo = (get_business() or {}).get("geo")
    if geo and geo.get("latitude") and geo.get("longitude"):
        data["geo"] = {
            "@type": "GeoCoordinates",
            "latitude": geo["latitude"],
            "longitude": geo["longitude"],
        }
    return data


def _first_image(home: dict) -> str | None:
    for key in ("hero_image", "image_url"):
        if home.get(key):
            return str(home[key])
    photos = home.get("real_photos") or home.get("photos") or home.get("gallery_images")
    if photos:
        first = photos[0]
        return str(first.get("url") if isinstance(first, dict) else first)
    return None


def _product_jsonld(home: dict, canonical_url: str) -> dict:
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
    # Honest pricing only: a placeholder/zero price violates Google's
    # "data must match the page" policy for call-for-price listings.
    price = home.get("price_value")
    try:
        price = float(price) if price is not None else None
    except (TypeError, ValueError):
        price = None
    if price and price > 0:
        data["offers"] = {
            "@type": "Offer",
            "price": f"{price:.0f}",
            "priceCurrency": "USD",
            "availability": "https://schema.org/InStock",
            "url": canonical_url,
        }
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
    except OSError:
        return '<!doctype html><html><head><title></title></head><body><div id="root"></div></body></html>'


def _head_block(title, description, canonical_url, og_image=None, jsonld=None, noindex=False):
    e = html.escape
    parts = [
        f"<title>{e(title)}</title>",
        f'<meta name="description" content="{e(description)}" />',
    ]
    if noindex:
        parts.append('<meta name="robots" content="noindex" />')
    else:
        parts.append(f'<link rel="canonical" href="{e(canonical_url)}" />')
        parts.extend(
            [
                f'<meta property="og:title" content="{e(title)}" />',
                f'<meta property="og:description" content="{e(description)}" />',
                f'<meta property="og:url" content="{e(canonical_url)}" />',
                '<meta property="og:type" content="website" />',
                f'<meta property="og:site_name" content="{e(business_name())}" />',
                '<meta name="twitter:card" content="summary_large_image" />',
            ]
        )
        if og_image:
            parts.append(f'<meta property="og:image" content="{e(og_image)}" />')
    for block in jsonld or []:
        parts.append(
            '<script type="application/ld+json">'
            + json.dumps(block, ensure_ascii=False)
            + "</script>"
        )
    return "\n    ".join(parts)


def _crawlable_inventory_block() -> str:
    """Semantic HTML (replaced by React on mount) so non-JS crawlers see
    the inventory and have <a href> paths to every detail page."""
    e = html.escape
    reg = _registry()
    items = []
    for legacy_id, home in list(reg["detail_by_id"].items()):
        path = reg["detail_path_by_id"][legacy_id]
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
    return (
        f"<h1>Mobile &amp; Manufactured Homes for Sale in {html.escape(_CITY_STATE)}</h1>"
        f"<p>{html.escape(business_name())} — {html.escape(business_address())} · "
        f"{html.escape(business_phone())} · {html.escape(business_hours())}</p>"
        f"<ul>{''.join(items)}</ul>"
    )


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
    if home.get("display_price"):
        rows.append(f"<li>Price: {e(str(home['display_price']))}</li>")
    description = home.get("description") or ""
    return (
        f"<h1>{e(home.get('model_name') or 'Manufactured Home')}</h1>"
        f"<p>{e(str(description)[:500])}</p>"
        f"<ul>{''.join(rows)}</ul>"
        f'<p><a href="/inventory">All homes for sale at {e(business_name())}</a> — '
        f"{e(business_address())} · {e(business_phone())}</p>"
    )


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


def render_spa_response(full_path: str) -> Response | None:
    """SEO-aware response for an SPA path; None means caller falls through
    to its default file handling. Never raises."""
    try:
        return _render_spa_response(full_path)
    except Exception:
        return None


def _render_spa_response(full_path: str) -> Response | None:
    path = "/" + full_path.strip("/") if full_path else "/"
    base = _base()

    # 1. Legacy quote URLs -> 301 to the matching detail page.
    if _QUOTE_RE.match(path):
        target = _registry()["quote_redirects"].get(path.rstrip("/"))
        return RedirectResponse(target or "/inventory", status_code=301)

    # 2. Legacy listing path: /inventory/ was the old hub -> new /inventory.
    if path.lower() in ("/inventory/", "/home", "/index.html") or (
        path != "/inventory" and path.lower().rstrip("/") == "/inventory"
    ):
        return RedirectResponse("/inventory", status_code=301)

    no_cache = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }

    # 3. Live legacy detail/plan URLs: 200 + per-home head + crawlable body.
    m = _DETAIL_RE.match(path)
    if m:
        reg = _registry()
        home = reg["detail_by_id"].get(m.group(2))
        if home is None:
            head = _head_block(
                "Home not found | " + business_name(),
                "This home is no longer listed.",
                base + "/inventory",
                noindex=True,
            )
            return HTMLResponse(_inject(_shell(), head, None), status_code=404, headers=no_cache)
        canonical_path = reg["detail_path_by_id"][m.group(2)]
        if path != canonical_path and path.rstrip("/") == canonical_path.rstrip("/"):
            canonical_path = path  # tolerate trailing-slash variants
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
            jsonld=[_product_jsonld(home, canonical_url)],
        )
        return HTMLResponse(
            _inject(_shell(), head, _crawlable_detail_block(home)), headers=no_cache
        )

    # 4. Known public static routes: 200 + route meta (+ inventory block).
    if path in PUBLIC_ROUTES:
        title, description = PUBLIC_ROUTES[path]
        jsonld = [_local_business_jsonld()] if path in ("/", "/inventory", "/contact") else []
        head = _head_block(title, description, base + (path if path != "/" else "/"), jsonld=jsonld)
        body = _crawlable_inventory_block() if path in ("/", "/inventory") else None
        return HTMLResponse(_inject(_shell(), head, body), headers=no_cache)

    # 5. Operator/admin routes: 200, noindex.
    if any(path.startswith(p) or path == p.rstrip("/") for p in NOINDEX_PREFIXES):
        head = _head_block(business_name(), "Operator tools.", base + path, noindex=True)
        return HTMLResponse(_inject(_shell(), head, None), headers=no_cache)

    # 6. Anything else the caller can't match to a real file: defer to
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


# ── robots.txt and sitemap.xml ──────────────────────────────────────────────


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
            "/system",
            "/chat-history",
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
    urls = [base + p for p in ("/", "/inventory", "/contact", "/appointments")]
    urls += [base + p for p in sorted(_registry()["detail_path_by_id"].values())]
    # lastmod intentionally omitted: Google only trusts it when verifiably
    # accurate, and inventory records carry no reliable update timestamps.
    entries = "\n".join(f"  <url><loc>{html.escape(u)}</loc></url>" for u in urls)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}\n</urlset>\n"
    )
    return Response(content=xml, media_type="application/xml")
