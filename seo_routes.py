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
        f"Browse manufactured & mobile homes for sale at {business_name()} in "
        f"{_CITY_STATE} — on-lot homes ready now plus orderable factory floorplans.",
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
    "/manufactured-homes-in-baytown-tx": "/inventory",
    "/manufactured-homes-in-beaumont-tx": "/inventory",
    "/manufactured-homes-in-cleveland-tx": "/inventory",
    "/manufactured-homes-in-conroe-tx": "/inventory",
    "/manufactured-homes-in-jasper-tx": "/inventory",
    "/manufactured-homes-in-livingston-tx": "/inventory",
    "/manufactured-homes-in-lumberton-tx": "/inventory",
    # Category pages -> inventory.
    "/single-wide": "/inventory",
    "/double-wide": "/inventory",
    "/used-homes": "/inventory",
    "/pre-owned-homes": "/inventory",
    "/tiny-homes-cabin": "/inventory",
    "/red-tag-sales": "/inventory",
    "/floor-plans": "/inventory",
    # Info / contact / financing pages -> contact.
    "/about-us": "/contact",
    "/contact-us": "/contact",
    "/contact-modal": "/contact",
    "/financing": "/contact",
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


def _product_jsonld(home: dict, canonical_url: str) -> dict | None:
    # Google's Product snippet requires one of offers/review/aggregateRating, and
    # a valid Offer REQUIRES a real price. THO lists every home "call for price"
    # (no online price) with no per-home ratings/reviews, so a Product snippet
    # cannot be made valid honestly: a $0/placeholder price violates Google's
    # "data must match the page" policy, and fabricating ratings/reviews is barred.
    # So emit Product structured data ONLY when a real positive price exists (a
    # priced home then becomes rich-result eligible with a valid Product+Offer);
    # otherwise return None, so Search Console is not flagged for an
    # unsatisfiable Product. Price-less pages still rank on their
    # title/description/OG tags/crawlable body.
    price = home.get("price_value")
    try:
        price = float(price) if price is not None else None
    except (TypeError, ValueError):
        price = None
    if not (price and price > 0):
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
    availability = (
        "https://schema.org/PreOrder"
        if "order" in str(home.get("status", "")).lower()
        else "https://schema.org/InStock"
    )
    data["offers"] = {
        "@type": "Offer",
        "price": f"{price:.0f}",
        "priceCurrency": "USD",
        "availability": availability,
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
# OPERATOR/LEGAL GATE: activating analytics or ad pixels sets third-party
# cookies / sends visitor data to Google/Meta/TikTok. No consent-management
# platform exists yet — make a privacy-policy / cookie-consent decision before
# setting any *_PIXEL_ID. The code ships inert; turning it on is your call.

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


def _clean_id(env_name: str, pattern: "re.Pattern") -> str | None:
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


def _analytics_head() -> str:
    """Inline analytics/pixel <script> snippets for whichever IDs are validly
    set. Returns '' when none are configured (head unchanged from today)."""
    ids = _analytics_ids()
    if not ids:
        return ""
    e = html.escape
    out = []
    ga4 = ids.get("GA4_MEASUREMENT_ID")
    if ga4:
        out.append(
            '<script async src="https://www.googletagmanager.com/gtag/js?id='
            + e(ga4) + '"></script>'
            "<script>window.dataLayer=window.dataLayer||[];"
            "function gtag(){dataLayer.push(arguments);}"
            "gtag('js',new Date());gtag('config','" + e(ga4) + "');</script>"
        )
    gtm = ids.get("GTM_CONTAINER_ID")
    if gtm:
        out.append(
            "<script>(function(w,d,s,l,i){w[l]=w[l]||[];"
            "w[l].push({'gtm.start':new Date().getTime(),event:'gtm.js'});"
            "var f=d.getElementsByTagName(s)[0],j=d.createElement(s),"
            "dl=l!='dataLayer'?'&l='+l:'';j.async=true;"
            "j.src='https://www.googletagmanager.com/gtm.js?id='+i+dl;"
            "f.parentNode.insertBefore(j,f);})"
            "(window,document,'script','dataLayer','" + e(gtm) + "');</script>"
        )
    meta = ids.get("META_PIXEL_ID")
    if meta:
        out.append(
            "<script>!function(f,b,e,v,n,t,s){if(f.fbq)return;"
            "n=f.fbq=function(){n.callMethod?n.callMethod.apply(n,arguments):"
            "n.queue.push(arguments)};if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;"
            "n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;"
            "t.src=v;s=b.getElementsByTagName(e)[0];"
            "s.parentNode.insertBefore(t,s)}(window,document,'script',"
            "'https://connect.facebook.net/en_US/fbevents.js');"
            "fbq('init','" + e(meta) + "');fbq('track','PageView');</script>"
        )
    tiktok = ids.get("TIKTOK_PIXEL_ID")
    if tiktok:
        out.append(
            "<script>!function(w,d,t){w.TiktokAnalyticsObject=t;"
            "var ttq=w[t]=w[t]||[];ttq.methods=['page','track','identify',"
            "'instances','debug','on','off','once','ready','alias','group',"
            "'enableCookie','disableCookie'];ttq.setAndDefer=function(t,e){"
            "t[e]=function(){t.push([e].concat(Array.prototype.slice.call("
            "arguments,0)))}};for(var i=0;i<ttq.methods.length;i++)"
            "ttq.setAndDefer(ttq,ttq.methods[i]);ttq.load=function(e,n){"
            "var i='https://analytics.tiktok.com/i18n/pixel/events.js';"
            "ttq._i=ttq._i||{},ttq._i[e]=[],ttq._i[e]._u=i,ttq._t=ttq._t||{},"
            "ttq._t[e]=+new Date,ttq._o=ttq._o||{},ttq._o[e]=n||{};"
            "var o=d.createElement('script');o.type='text/javascript',"
            "o.async=!0,o.src=i+'?sdkid='+e+'&lib='+t;"
            "var a=d.getElementsByTagName('script')[0];"
            "a.parentNode.insertBefore(o,a)};ttq.load('" + e(tiktok) + "');"
            "ttq.page();}(window,document,'ttq');</script>"
        )
    return "\n    ".join(out)


def _default_og_image() -> str | None:
    """Brand social-share image (og:image / twitter:image) from OG_IMAGE_URL.

    No-op when unset: pages emit no og:image (today's behavior). A relative
    value (starts with "/") is resolved against the canonical base so the
    emitted URL is absolute, as social scrapers require. Set this ONLY once a
    real 1200x630 raster exists at that URL — scrapers reject SVG, and a
    missing asset would make og:image a broken link (worse than none).
    """
    raw = (os.environ.get("OG_IMAGE_URL") or "").strip()
    if not raw:
        return None
    return _base() + raw if raw.startswith("/") else raw


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
        if og_image is None:
            og_image = _default_og_image()
        if og_image:
            parts.append(f'<meta property="og:image" content="{e(og_image)}" />')
            parts.append(f'<meta name="twitter:image" content="{e(og_image)}" />')
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


def _breadcrumb_jsonld(name: str, canonical_url: str) -> dict:
    b = _base()
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": b + "/"},
            {"@type": "ListItem", "position": 2, "name": "Inventory", "item": b + "/inventory"},
            {"@type": "ListItem", "position": 3, "name": name, "item": canonical_url},
        ],
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


def render_spa_response(full_path: str) -> Response | None:
    """SEO-aware response for an SPA path; None means caller falls through
    to its default file handling. Never raises."""
    try:
        return _render_spa_response(full_path)
    except Exception:
        return None


def _render_spa_response(full_path: str) -> Response | None:
    raw_path = "/" + full_path if full_path else "/"
    path = "/" + full_path.strip("/") if full_path else "/"
    base = _base()

    # 1. Legacy quote URLs -> 301 to the matching detail page.
    if _QUOTE_RE.match(path):
        target = _registry()["quote_redirects"].get(path.rstrip("/"))
        return RedirectResponse(target or "/inventory", status_code=301)

    # 2. Legacy aliases for the listing hub -> new /inventory.
    if path.lower() in ("/home", "/index.html"):
        return RedirectResponse("/inventory", status_code=301)

    # 2b. Legacy vendor marketing/brand/city pages -> closest relevant page (301).
    #     Preserves the old site's search equity instead of hard-404ing on cutover.
    vendor_target = _LEGACY_VENDOR_REDIRECTS.get(path.lower())
    if vendor_target:
        return RedirectResponse(vendor_target, status_code=301)

    # 3. One URL per page: trailing-slash (or doubled-slash) variants of
    #    public routes 301 to the canonical no-slash form.
    if path in PUBLIC_ROUTES and raw_path != path:
        return RedirectResponse(path, status_code=301)

    no_cache = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }

    # 4. Live legacy detail/plan URLs: 200 + per-home head + crawlable body.
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

    # 5. Known public static routes: 200 + route meta (+ inventory block).
    if path in PUBLIC_ROUTES:
        title, description = PUBLIC_ROUTES[path]
        jsonld = [_local_business_jsonld()] if path in ("/", "/inventory", "/contact") else []
        head = _head_block(title, description, base + (path if path != "/" else "/"), jsonld=jsonld)
        if path in ("/", "/inventory"):
            body = _crawlable_inventory_block()
        elif path == "/contact":
            body = _crawlable_contact_block()
        elif path == "/appointments":
            body = _crawlable_appointments_block()
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
