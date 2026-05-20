"""Legacy Texas Home Outlet inventory crawler.

This module reads the public WordPress/MFH inventory on texashomeoutlet.com and
normalizes it into the same shape used by the THO public inventory page and Ad
Studio. It intentionally does not import Firestore or customer/deal data.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from caching import cache_get, cache_set
except ImportError:  # pragma: no cover - script use outside app root
    cache_get = None
    cache_set = None

try:
    from tools.manufacturer_media_recovery import apply_manufacturer_media_recovery_to_homes
    from tools.photo_classifier import apply_classifier_to_home, is_floorplan_url
except ImportError:  # pragma: no cover - script use outside package import path
    from .manufacturer_media_recovery import apply_manufacturer_media_recovery_to_homes
    from .photo_classifier import apply_classifier_to_home, is_floorplan_url

LOG = logging.getLogger(__name__)

LEGACY_BASE_URL = "https://www.texashomeoutlet.com"
LEGACY_INVENTORY_URL = f"{LEGACY_BASE_URL}/inventory/"
LEGACY_FLOORPLAN_URL = f"{LEGACY_BASE_URL}/floor-plans/"
LEGACY_DEALER_ID = "3522"
LEGACY_SOURCE_ID = "texashomeoutlet_legacy_wordpress"
LEGACY_FLOORPLAN_SOURCE_ID = "texashomeoutlet_legacy_floorplan_catalog"
LEGACY_CDN_HOST = "d132mt2yijm03y.cloudfront.net"
LEGACY_CACHE_KEY = "legacy_site_inventory_context_v2"
LEGACY_FLOORPLAN_CACHE_KEY = "legacy_site_floorplan_catalog_context_v1"
SNAPSHOT_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "legacy_site" / "legacy_inventory_context.json"
)
FLOORPLAN_SNAPSHOT_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "legacy_site"
    / "legacy_floorplan_catalog_context.json"
)

REQUEST_TIMEOUT_SECONDS = 18
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; THOInventoryParity/1.0; " "+https://tho.sapphirealpha.xyz)"
    )
}

CDN_RE = re.compile(r"https://d132mt2yijm03y\.cloudfront\.net/[^\s\"'<>),]+")
MATTERPORT_RE = re.compile(r"https://my\.matterport\.com/show/\?[^\s\"'<>]+")

PHOTO_CATEGORY_PRIORITY = {
    "exterior": 0,
    "kitchen": 1,
    "interior": 2,
    "bedroom": 3,
    "bathroom": 4,
    "utility": 5,
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    return session


def _inventory_page_url(page: int) -> str:
    if page <= 1:
        return LEGACY_INVENTORY_URL
    return f"{LEGACY_INVENTORY_URL}?pg={page}"


def _floorplan_page_url(page: int) -> str:
    if page <= 1:
        return LEGACY_FLOORPLAN_URL
    return f"{LEGACY_FLOORPLAN_URL}?pg={page}"


def _absolute_url(value: str | None, base_url: str = LEGACY_BASE_URL) -> str:
    if not value:
        return ""
    return urljoin(base_url, str(value).strip())


def normalize_media_url(url: str | None, base_url: str = LEGACY_BASE_URL) -> str:
    """Normalize legacy thumbnail variants to their full-size media URL."""
    if not url:
        return ""
    value = _absolute_url(str(url).strip().rstrip(").,;"), base_url=base_url)
    value = value.replace("&amp;", "&")
    value = re.sub(r"_thumb_xl(?=\.[a-zA-Z0-9]{3,5}(?:$|\?))", "", value)
    value = re.sub(r"_card_lg(?=\.[a-zA-Z0-9]{3,5}(?:$|\?))", "", value)
    return value


def _dedupe(values: list[str | None]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        value = normalize_media_url(raw)
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _dedupe_text(values: list[str | None]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _text(node: Any, separator: str = " ") -> str:
    if not node:
        return ""
    return node.get_text(separator, strip=True)


def _parse_int(value: str | None) -> int:
    match = re.search(r"\d[\d,]*", str(value or ""))
    return int(match.group(0).replace(",", "")) if match else 0


def _parse_float(value: str | None) -> float:
    match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    return float(match.group(0)) if match else 0.0


def _clean_dimensions(value: str | None) -> str:
    text = str(value or "").strip()
    text = text.replace("′", "'").replace("″", '"').replace("×", "x")
    return re.sub(r"\s+", " ", text)


def _parse_dimensions(value: str | None) -> tuple[str, int, int]:
    dimensions = _clean_dimensions(value)
    match = re.search(r"(\d+)(?:'\s*\d*\"?)?\s*x\s*(\d+)(?:'\s*\d*\"?)?", dimensions)
    if not match:
        return dimensions, 0, 0
    return dimensions, int(match.group(1)), int(match.group(2))


def _extract_listing_id(url: str | None) -> str:
    match = re.search(r"/inventory-detail/(\d+)/", str(url or ""))
    return match.group(1) if match else ""


def _extract_matterport_id(url: str | None) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(str(url).replace("&amp;", "&"))
        ids = parse_qs(parsed.query).get("m", [])
        return ids[0] if ids else ""
    except Exception:
        return ""


def _matterport_url(matterport_id: str | None) -> str:
    return f"https://my.matterport.com/show/?m={matterport_id}&play=1" if matterport_id else ""


def _parse_price(text: str | None) -> tuple[str, int]:
    value = str(text or "").strip()
    if not value or "call" in value.lower():
        return "Call for Price", 0
    match = re.search(r"\$?\s*(\d[\d,]*)", value)
    if not match:
        return value, 0
    price_value = int(match.group(1).replace(",", ""))
    return f"${price_value:,}", price_value


def _classification(specs: dict[str, Any], tags: list[str], model_name: str) -> str:
    text = " ".join([model_name, *tags]).lower()
    if "park model" in text:
        return "Park Model"
    width = specs.get("width") or 0
    if width >= 20:
        return "Double Wide"
    if width > 0:
        return "Single Wide"
    return "Manufactured Home"


def _status(tags: list[str], model_name: str) -> str:
    text = " ".join([model_name, *tags]).lower()
    if "pre-owned" in text or "pre owned" in text or "used" in text:
        return "Pre-Owned"
    return "Available"


def _parse_card_specs(card: Any) -> dict[str, Any]:
    specs: dict[str, Any] = {
        "beds": 0,
        "baths": 0,
        "sq_ft": 0,
        "dimensions": "",
        "width": 0,
        "length": 0,
    }

    bed_icon = card.select_one(".icon-mfh-bed")
    if bed_icon and bed_icon.parent:
        specs["beds"] = _parse_int(_text(bed_icon.parent))

    bath_icon = card.select_one(".icon-mfh-bath")
    if bath_icon and bath_icon.parent:
        specs["baths"] = _parse_float(_text(bath_icon.parent))

    sqft_icon = card.select_one(".icon-mfh-tapemeasure")
    if sqft_icon and sqft_icon.parent:
        specs["sq_ft"] = _parse_int(_text(sqft_icon.parent))

    dim_icon = card.select_one(".icon-mfh-lengthwidth")
    if dim_icon and dim_icon.parent:
        dimensions, width, length = _parse_dimensions(_text(dim_icon.parent))
        specs["dimensions"] = dimensions
        specs["width"] = width
        specs["length"] = length

    return specs


def _parse_labeled_specs(container: Any) -> dict[str, Any]:
    specs: dict[str, Any] = {
        "beds": 0,
        "baths": 0,
        "sq_ft": 0,
        "dimensions": "",
        "width": 0,
        "length": 0,
    }
    if not container:
        return specs

    text = _text(container)
    label_patterns = {
        "beds": r"Beds:\s*([0-9]+)",
        "baths": r"Baths:\s*([0-9]+(?:\.[0-9]+)?)",
        "sq_ft": r"Sq\s*Ft:\s*([0-9,]+)",
        "dimensions": r"W\s*x\s*L:\s*([^B]+?)(?:\s{2,}|$)",
    }
    if match := re.search(label_patterns["beds"], text, re.IGNORECASE):
        specs["beds"] = _parse_int(match.group(1))
    if match := re.search(label_patterns["baths"], text, re.IGNORECASE):
        specs["baths"] = _parse_float(match.group(1))
    if match := re.search(label_patterns["sq_ft"], text, re.IGNORECASE):
        specs["sq_ft"] = _parse_int(match.group(1))
    if match := re.search(label_patterns["dimensions"], text, re.IGNORECASE):
        dimensions, width, length = _parse_dimensions(match.group(1))
        specs["dimensions"] = dimensions
        specs["width"] = width
        specs["length"] = length
    return specs


def _parse_detail_info(soup: BeautifulSoup) -> dict[str, Any]:
    info = soup.select_one(".detail-info")
    if not info:
        return {}

    lines = [line.strip() for line in info.get_text("\n", strip=True).splitlines() if line.strip()]
    mapped: dict[str, str] = {}
    i = 0
    while i < len(lines):
        label = lines[i].strip().rstrip(":").lower()
        if i + 1 < len(lines) and not lines[i + 1].strip().endswith(":"):
            mapped[label] = lines[i + 1].strip()
            i += 2
        else:
            mapped[label] = ""
            i += 1

    dimensions, width, length = _parse_dimensions(mapped.get("wxl"))
    display_price, price_value = _parse_price(mapped.get("price"))

    specs = {
        "beds": _parse_int(mapped.get("beds")),
        "baths": _parse_float(mapped.get("baths")),
        "sq_ft": _parse_int(mapped.get("sq. ft.")),
        "dimensions": dimensions,
        "width": width,
        "length": length,
    }

    return {
        "manufacturer": mapped.get("built by", ""),
        "display_price": display_price,
        "price_value": price_value,
        "specs": specs,
    }


def _filename(url: str) -> str:
    return urlparse(url).path.rsplit("/", 1)[-1]


def _is_cdn_url(url: str) -> bool:
    return LEGACY_CDN_HOST in str(url or "")


def _is_dealer_inventory_url(url: str, listing_id: str | None = None) -> bool:
    marker = f"/dealer/{LEGACY_DEALER_ID}/inventory/"
    if marker not in url:
        return False
    return not listing_id or f"{marker}{listing_id}/" in url


def _manufacturer_floorplan_prefix(url: str) -> str:
    match = re.search(r"(/manufacturer/[^/]+/floorplan/[^/]+/)", urlparse(url).path)
    return match.group(1) if match else ""


def _extract_plan_id(url: str | None) -> str:
    match = re.search(r"/plan/(\d+)/", str(url or ""))
    if match:
        return match.group(1)
    match = re.search(r"/quote/floorplan/(\d+)/dealer/", str(url or ""))
    return match.group(1) if match else ""


def _extract_background_url(value: str | None) -> str:
    match = re.search(r"url\((['\"]?)(.*?)\1\)", str(value or ""))
    return match.group(2) if match else ""


def categorize_photo_url(url: str, label: str | None = None) -> str:
    source = f"{label or ''} {_filename(url)}".lower()
    if "ext" in source or "exterior" in source:
        return "exterior"
    if "kit" in source or "kitchen" in source:
        return "kitchen"
    if "bath" in source:
        return "bathroom"
    if "bed" in source:
        return "bedroom"
    if "uti" in source or "utility" in source or "laundry" in source:
        return "utility"
    if "living" in source or "interior" in source or "room" in source:
        return "interior"
    return "interior"


def _order_photos(photos: list[str], categories: dict[str, str]) -> list[str]:
    deduped = _dedupe(photos)
    return sorted(
        deduped,
        key=lambda url: (
            PHOTO_CATEGORY_PRIORITY.get(categories.get(url, categorize_photo_url(url)), 9),
            deduped.index(url),
        ),
    )


def extract_inventory_card(card: Any, base_url: str = LEGACY_BASE_URL) -> dict[str, Any] | None:
    """Extract the public listing facts from a legacy inventory card."""
    title_tag = card.select_one("h4.home-name a[href]") or card.select_one(
        "a[href*='/inventory-detail/']"
    )
    if not title_tag:
        return None

    model_name = _text(title_tag)
    detail_url = _absolute_url(title_tag.get("href"), base_url=base_url)
    listing_id = _extract_listing_id(detail_url)
    if not listing_id:
        return None

    built_by = _text(card.select_one(".fp-card-built-by")).replace("Built by:", "").strip()
    tags = [_text(tag) for tag in card.select(".fp-card-tags .label") if _text(tag)]
    specs = _parse_card_specs(card)
    display_price, price_value = _parse_price(_text(card.select_one(".fp-card-price")))

    image = ""
    image_div = card.select_one(".fp-card-image")
    if image_div:
        image = normalize_media_url(image_div.get("data-bg"), base_url=base_url)

    floorplan = ""
    floorplan_link = card.select_one("a.fp-thumb[href]")
    if floorplan_link:
        floorplan = normalize_media_url(floorplan_link.get("href"), base_url=base_url)

    matterport_id = ""
    matterport_link = card.select_one("a.tour-thumb[href]")
    if matterport_link:
        matterport_id = _extract_matterport_id(matterport_link.get("href"))

    quote_link = card.select_one("a[href*='/quote/inventory']")
    quote_url = _absolute_url(quote_link.get("href"), base_url=base_url) if quote_link else ""

    return {
        "id": listing_id,
        "legacy_inventory_id": listing_id,
        "model_name": model_name,
        "manufacturer": built_by,
        "classification": _classification(specs, tags, model_name),
        "status": _status(tags, model_name),
        "display_price": display_price,
        "price_value": price_value,
        "pricing": {"display_price": display_price, "price_value": price_value},
        "specs": specs,
        "features": tags,
        "marketing_tags": tags,
        "image_url": image,
        "gallery_images": [image] if image else [],
        "real_photos": [image] if image else [],
        "floorplan_url": floorplan,
        "floor_plan_url": floorplan,
        "floorplan_urls": [floorplan] if floorplan else [],
        "matterport_id": matterport_id,
        "matterport_url": _matterport_url(matterport_id),
        "detail_url": detail_url,
        "quote_url": quote_url,
        "source_url": detail_url,
        "source": LEGACY_SOURCE_ID,
    }


def extract_floorplan_card(card: Any, base_url: str = LEGACY_BASE_URL) -> dict[str, Any] | None:
    """Extract one orderable floorplan card from the legacy /floor-plans page."""
    title_tag = card.select_one("h4.home-name a[href*='/plan/']") or card.select_one(
        "a[href*='/plan/']"
    )
    if not title_tag:
        return None

    model_name = _text(title_tag)
    detail_url = _absolute_url(title_tag.get("href"), base_url=base_url)
    plan_id = _extract_plan_id(detail_url)
    if not plan_id:
        return None

    image = ""
    image_div = card.select_one(".fp-card-image")
    if image_div:
        image = normalize_media_url(
            image_div.get("data-bg") or _extract_background_url(image_div.get("style")),
            base_url=base_url,
        )

    floorplan = ""
    floorplan_link = card.select_one("a.fp-thumb[href]")
    if floorplan_link:
        floorplan = normalize_media_url(floorplan_link.get("href"), base_url=base_url)

    matterport_id = ""
    matterport_link = card.select_one("a.tour-thumb[href]")
    if matterport_link:
        matterport_id = _extract_matterport_id(matterport_link.get("href"))

    quote_link = card.select_one("a[href*='/quote/floorplan']")
    quote_url = _absolute_url(quote_link.get("href"), base_url=base_url) if quote_link else ""
    tags = [_text(tag) for tag in card.select(".fp-card-tags .label") if _text(tag)]
    specs = _parse_labeled_specs(card.select_one(".fp-card-specs"))
    built_by = _text(card.select_one(".fp-card-built-by")).replace("Built by:", "").strip()
    description = _text(card.select_one(".fp-card-description"))

    home = {
        "id": f"floorplan-{plan_id}",
        "legacy_plan_id": plan_id,
        "model_name": model_name,
        "manufacturer": built_by,
        "classification": _classification(specs, tags, model_name),
        "status": "Orderable",
        "availability_type": "orderable",
        "inventory_kind": "orderable_floorplan",
        "is_orderable": True,
        "is_on_lot": False,
        "display_price": "Call for Price",
        "price_value": 0,
        "pricing": {"display_price": "Call for Price", "price_value": 0},
        "specs": specs,
        "features": _dedupe_text(["Orderable Floorplan", *tags]),
        "marketing_tags": _dedupe_text(["Orderable Floorplan", *tags]),
        "description": description,
        "image_url": image,
        "hero_image": image,
        "gallery_images": [image] if image else [],
        "real_photos": [image] if image else [],
        "floorplan_url": floorplan,
        "floor_plan_url": floorplan,
        "floorplan_urls": [floorplan] if floorplan else [],
        "matterport_id": matterport_id,
        "matterport_url": _matterport_url(matterport_id),
        "detail_url": detail_url,
        "quote_url": quote_url,
        "legacy_actions": {
            "detail_url": detail_url,
            "quote_url": quote_url,
        },
        "source": LEGACY_FLOORPLAN_SOURCE_ID,
        "source_url": detail_url,
        "source_provenance": {
            "source_id": LEGACY_FLOORPLAN_SOURCE_ID,
            "source_owner": "Texas Home Outlet",
            "source_url": detail_url or LEGACY_FLOORPLAN_URL,
            "retrieval_mode": "public_legacy_floorplan_catalog_scrape",
            "rights_envelope": "client_owned_legacy_site_media_for_orderable_floorplans",
            "output_policy": "THO-owned app and marketing context only; no generic stock substitution",
        },
    }
    apply_classifier_to_home(home)
    home["hero_image"] = home.get("image_url", "")
    home["photos"] = list(home.get("real_photos") or [])
    return home


def extract_detail_media(html: str, listing_id: str, detail_url: str) -> dict[str, Any]:
    """Extract full-size photos, floorplan, quote URL, and detail specs."""
    soup = BeautifulSoup(html, "html.parser")
    detail = _parse_detail_info(soup)

    title_node = soup.find("h1") or soup.find("h2")
    model_name = _text(title_node)
    meta = soup.find("meta", attrs={"name": "description"})
    description = str(meta.get("content") or "").strip() if meta else ""

    quote_link = soup.select_one("a[href*='/quote/inventory']")
    quote_url = _absolute_url(quote_link.get("href"), base_url=detail_url) if quote_link else ""

    raw_urls: list[str] = []
    photos: list[str] = []
    figure_floorplans: list[str] = []
    category_by_url: dict[str, str] = {}

    for figure in soup.select("figure"):
        label = _text(figure.find("figcaption"))
        figure_urls: list[str] = []
        for tag in figure.find_all(["a", "img", "source"]):
            for attr in ("href", "src", "data-src", "data-original"):
                url = normalize_media_url(tag.get(attr), base_url=detail_url)
                if _is_cdn_url(url):
                    figure_urls.append(url)
        for url in _dedupe(figure_urls):
            raw_urls.append(url)
            is_layout = label.strip().lower() in {"layout", "floorplan", "floor plan"}
            if is_layout or is_floorplan_url(url):
                figure_floorplans.append(url)
                continue
            if _is_dealer_inventory_url(url, listing_id) or "/manufacturer/" in url:
                photos.append(url)
                category_by_url[url] = categorize_photo_url(url, label)

    for tag in soup.find_all(["a", "img", "source"]):
        for attr in ("href", "src", "data-src", "data-original"):
            url = normalize_media_url(tag.get(attr), base_url=detail_url)
            if _is_cdn_url(url):
                raw_urls.append(url)

    raw_urls.extend(normalize_media_url(url, base_url=detail_url) for url in CDN_RE.findall(html))

    photo_prefixes = {_manufacturer_floorplan_prefix(url) for url in photos}
    photo_prefixes.discard("")
    raw_floorplans: list[str] = []
    for url in _dedupe(raw_urls):
        if is_floorplan_url(url):
            prefix = _manufacturer_floorplan_prefix(url)
            if not figure_floorplans and (not photo_prefixes or prefix in photo_prefixes):
                raw_floorplans.append(url)
        elif _is_dealer_inventory_url(url, listing_id):
            photos.append(url)
            category_by_url.setdefault(url, categorize_photo_url(url))

    ordered_photos = _order_photos(photos, category_by_url)
    floorplans = _dedupe([*figure_floorplans, *raw_floorplans])
    image_categories: dict[str, list[str]] = {}
    for url in ordered_photos:
        category = category_by_url.get(url, categorize_photo_url(url))
        image_categories.setdefault(category, []).append(url)

    matterport_id = ""
    for url in MATTERPORT_RE.findall(html):
        matterport_id = _extract_matterport_id(url)
        if matterport_id:
            break
    if not matterport_id:
        for link in soup.select("a[href*='my.matterport.com/show']"):
            matterport_id = _extract_matterport_id(link.get("href"))
            if matterport_id:
                break

    return {
        "listing_id": listing_id,
        "detail_url": detail_url,
        "model_name": model_name,
        "description": description,
        "quote_url": quote_url,
        "photos": ordered_photos,
        "floorplan_url": floorplans[0] if floorplans else "",
        "floorplan_urls": floorplans,
        "matterport_id": matterport_id,
        "matterport_url": _matterport_url(matterport_id),
        "image_categories": image_categories,
        **detail,
    }


def _merge_card_and_detail(
    card_home: dict[str, Any], detail: dict[str, Any] | None
) -> dict[str, Any]:
    detail = detail or {}
    specs = {**(card_home.get("specs") or {})}
    detail_specs = detail.get("specs") or {}
    for key, value in detail_specs.items():
        if value not in ("", 0, 0.0, None):
            specs[key] = value

    display_price = (
        detail.get("display_price") or card_home.get("display_price") or "Call for Price"
    )
    price_value = detail.get("price_value")
    if price_value is None:
        price_value = card_home.get("price_value") or 0

    photos = [
        *(detail.get("photos") or []),
        *(card_home.get("real_photos") or []),
        *(card_home.get("gallery_images") or []),
    ]
    floorplans = [
        detail.get("floorplan_url"),
        *(detail.get("floorplan_urls") or []),
        card_home.get("floorplan_url"),
        *(card_home.get("floorplan_urls") or []),
    ]

    matterport_id = detail.get("matterport_id") or card_home.get("matterport_id") or ""
    detail_url = detail.get("detail_url") or card_home.get("detail_url") or ""
    quote_url = detail.get("quote_url") or card_home.get("quote_url") or ""

    home = {
        **card_home,
        "model_name": detail.get("model_name") or card_home.get("model_name") or "Unknown Home",
        "manufacturer": detail.get("manufacturer")
        or card_home.get("manufacturer")
        or "Texas Home Outlet",
        "classification": _classification(
            specs, card_home.get("features") or [], card_home.get("model_name", "")
        ),
        "display_price": display_price,
        "price_value": int(price_value or 0),
        "pricing": {"display_price": display_price, "price_value": int(price_value or 0)},
        "specs": specs,
        "description": detail.get("description") or card_home.get("description") or "",
        "image_url": photos[0] if photos else card_home.get("image_url", ""),
        "hero_image": photos[0] if photos else card_home.get("image_url", ""),
        "photos": _dedupe(photos),
        "real_photos": _dedupe(photos),
        "gallery_images": _dedupe(photos)[:3],
        "floorplan_url": _dedupe(floorplans)[0] if _dedupe(floorplans) else "",
        "floor_plan_url": _dedupe(floorplans)[0] if _dedupe(floorplans) else "",
        "floorplan_urls": _dedupe(floorplans),
        "matterport_id": matterport_id,
        "matterport_url": _matterport_url(matterport_id),
        "detail_url": detail_url,
        "quote_url": quote_url,
        "legacy_actions": {
            "detail_url": detail_url,
            "quote_url": quote_url,
        },
        "image_categories": detail.get("image_categories") or {},
        "source": LEGACY_SOURCE_ID,
        "source_url": detail_url or card_home.get("source_url") or LEGACY_INVENTORY_URL,
        "source_provenance": {
            "source_id": LEGACY_SOURCE_ID,
            "source_owner": "Texas Home Outlet",
            "source_url": detail_url or card_home.get("source_url") or LEGACY_INVENTORY_URL,
            "retrieval_mode": "public_legacy_site_scrape",
            "rights_envelope": "client_owned_legacy_site_media_for_tho_properties",
            "output_policy": "THO-owned app and marketing context only; no generic stock substitution",
        },
    }

    apply_classifier_to_home(home)
    home["hero_image"] = home.get("image_url", "")
    home["photos"] = list(home.get("real_photos") or [])
    home["pricing"] = {
        "display_price": home.get("display_price") or "Call for Price",
        "price_value": home.get("price_value") or 0,
    }
    return home


def scrape_legacy_inventory(
    max_pages: int = 25, include_details: bool = True
) -> list[dict[str, Any]]:
    """Scrape public legacy inventory cards, optionally enriching detail pages."""
    session = _session()
    homes: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for page in range(1, max_pages + 1):
        url = _inventory_page_url(page)
        response = session.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        cards = soup.select(".fp-card-container")
        if not cards:
            break

        new_on_page = 0
        for card in cards:
            home = extract_inventory_card(card, base_url=response.url)
            if not home or home["id"] in seen_ids:
                continue
            seen_ids.add(home["id"])
            homes.append(home)
            new_on_page += 1

        LOG.info("legacy inventory page=%s homes=%s total=%s", page, new_on_page, len(homes))
        if new_on_page == 0:
            break

    if not include_details:
        return [apply_classifier_to_home(home) for home in homes]

    enriched: list[dict[str, Any]] = []
    for home in homes:
        detail: dict[str, Any] | None = None
        detail_url = home.get("detail_url")
        if detail_url:
            try:
                response = session.get(detail_url, timeout=REQUEST_TIMEOUT_SECONDS)
                response.raise_for_status()
                detail = extract_detail_media(response.text, home["id"], response.url)
            except Exception as exc:
                LOG.warning(
                    "legacy detail scrape failed id=%s url=%s error=%s", home["id"], detail_url, exc
                )
        enriched.append(_merge_card_and_detail(home, detail))

    return enriched


def scrape_legacy_floorplan_catalog(max_pages: int = 40) -> list[dict[str, Any]]:
    """Scrape the legacy orderable floorplan catalog from /floor-plans."""
    session = _session()
    homes: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for page in range(1, max_pages + 1):
        url = _floorplan_page_url(page)
        response = session.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        cards = soup.select(".elementor-widget-FloorplanSearchResults .fp-card-container")
        if not cards:
            cards = soup.select(".fp-card-container")
        if not cards:
            break

        new_on_page = 0
        for card in cards:
            home = extract_floorplan_card(card, base_url=response.url)
            if not home or home["id"] in seen_ids:
                continue
            seen_ids.add(home["id"])
            homes.append(home)
            new_on_page += 1

        LOG.info("legacy floorplan page=%s homes=%s total=%s", page, new_on_page, len(homes))
        if new_on_page == 0:
            break

    return homes


def _media_summary(homes: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "photo_ready": 0,
        "limited_photos": 0,
        "floorplan_only": 0,
        "missing_photos": 0,
    }
    for home in homes:
        status = (home.get("media_quality") or {}).get("status")
        if status in counts:
            counts[status] += 1
    return counts


def _apply_media_recovery_to_context(context: dict[str, Any]) -> dict[str, Any]:
    homes = list(context.get("homes") or [])
    apply_manufacturer_media_recovery_to_homes(homes)
    context["homes"] = homes
    context["media_summary"] = _media_summary(homes)
    context["total_inventory"] = len(homes)
    context.setdefault("website_homes", len(homes))
    context["returned_inventory"] = min(
        len(homes),
        int(context.get("returned_inventory") or len(homes)),
    )
    return context


MEDIA_SNAPSHOT_FIELDS = (
    "image_url",
    "hero_image",
    "real_photos",
    "photos",
    "gallery_images",
    "image_categories",
    "floorplan_url",
    "floor_plan_url",
    "matterport_id",
    "matterport_url",
    "media_recovery",
)


def _legacy_home_ids(home: dict[str, Any]) -> set[str]:
    return {
        str(value).strip()
        for value in (
            home.get("id"),
            home.get("legacy_inventory_id"),
            home.get("listing_id"),
        )
        if value
    }


def _snapshot_media_index() -> dict[str, dict[str, Any]]:
    snapshot = _load_snapshot_context()
    if not snapshot:
        return {}
    index: dict[str, dict[str, Any]] = {}
    for home in snapshot.get("homes") or []:
        if not isinstance(home, dict):
            continue
        apply_classifier_to_home(home)
        for home_id in _legacy_home_ids(home):
            index[home_id] = home
    return index


def _merge_snapshot_media(homes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Use the local manifest as a per-listing media fallback for flaky detail pages."""
    snapshot_index = _snapshot_media_index()
    if not snapshot_index:
        return homes

    for home in homes:
        apply_classifier_to_home(home)
        live_quality = home.get("media_quality") or {}
        snapshot_home = next(
            (
                snapshot_index[home_id]
                for home_id in _legacy_home_ids(home)
                if home_id in snapshot_index
            ),
            None,
        )
        if not snapshot_home:
            continue
        snapshot_quality = snapshot_home.get("media_quality") or {}
        if not snapshot_quality.get("has_real_photo"):
            continue
        existing_recovery = home.get("media_recovery") or {}
        if existing_recovery.get("source") == "exact_manufacturer_plan_cdn" and live_quality.get(
            "has_real_photo"
        ):
            continue
        if live_quality.get("has_real_photo") and int(live_quality.get("photo_count") or 0) >= int(
            snapshot_quality.get("photo_count") or 0
        ):
            continue

        for field in MEDIA_SNAPSHOT_FIELDS:
            value = snapshot_home.get(field)
            if value:
                home[field] = value

        provenance = dict(home.get("source_provenance") or {})
        provenance["media_snapshot_fallback"] = {
            "source": "legacy_inventory_snapshot",
            "reason": "live_detail_media_unavailable_or_incomplete",
            "snapshot_path": str(SNAPSHOT_PATH),
            "snapshot_retrieved_at": snapshot_home.get("retrieved_at"),
        }
        home["source_provenance"] = provenance
        apply_classifier_to_home(home)
    return homes


def _limit_context(context: dict[str, Any], limit: int) -> dict[str, Any]:
    homes = list(context.get("homes") or [])
    limited_homes = homes[:limit] if limit else homes
    limited = {**context, "homes": limited_homes}
    limited["total_inventory"] = len(homes)
    limited["returned_inventory"] = len(limited_homes)
    return limited


def build_legacy_inventory_context(
    limit: int = 100,
    max_pages: int = 25,
    include_details: bool = True,
) -> dict[str, Any]:
    homes = scrape_legacy_inventory(max_pages=max_pages, include_details=include_details)
    if include_details:
        apply_manufacturer_media_recovery_to_homes(homes)
        homes = _merge_snapshot_media(homes)
    available = sum(1 for home in homes if home.get("status") == "Available")
    preowned = sum(1 for home in homes if home.get("status") == "Pre-Owned")
    context = {
        "success": True,
        "source": "legacy_site_live",
        "source_id": LEGACY_SOURCE_ID,
        "source_url": LEGACY_INVENTORY_URL,
        "source_owner": "Texas Home Outlet",
        "retrieved_at": _now_iso(),
        "rights": {
            "owner": "Texas Home Outlet",
            "retrieval_mode": "public_legacy_site_scrape",
            "rights_envelope": "client_owned_legacy_site_media_for_tho_properties",
            "output_policy": "Use only inside THO-owned application and marketing workflows.",
        },
        "homes": homes,
        "total_inventory": len(homes),
        "returned_inventory": min(len(homes), limit),
        "new_homes": available,
        "preowned_homes": preowned,
        "website_homes": len(homes),
        "media_summary": _media_summary(homes),
        "message": f"Loaded {len(homes)} current listings from texashomeoutlet.com.",
    }
    return _limit_context(_apply_media_recovery_to_context(context), limit)


def build_legacy_floorplan_catalog_context(
    limit: int = 500,
    max_pages: int = 40,
) -> dict[str, Any]:
    homes = scrape_legacy_floorplan_catalog(max_pages=max_pages)
    context = {
        "success": True,
        "source": "legacy_floorplan_catalog_live",
        "source_id": LEGACY_FLOORPLAN_SOURCE_ID,
        "source_url": LEGACY_FLOORPLAN_URL,
        "source_owner": "Texas Home Outlet",
        "retrieved_at": _now_iso(),
        "rights": {
            "owner": "Texas Home Outlet",
            "retrieval_mode": "public_legacy_floorplan_catalog_scrape",
            "rights_envelope": "client_owned_legacy_site_media_for_orderable_floorplans",
            "output_policy": "Use only inside THO-owned application and marketing workflows.",
        },
        "homes": homes,
        "total_inventory": len(homes),
        "returned_inventory": min(len(homes), limit),
        "orderable_floorplans": len(homes),
        "media_summary": _media_summary(homes),
        "message": f"Loaded {len(homes)} orderable floorplans from texashomeoutlet.com.",
    }
    return _limit_context(context, limit)


def _load_snapshot_context() -> dict[str, Any] | None:
    if not SNAPSHOT_PATH.exists():
        return None
    try:
        context = json.loads(SNAPSHOT_PATH.read_text())
    except Exception as exc:
        LOG.warning("failed to load legacy inventory snapshot path=%s error=%s", SNAPSHOT_PATH, exc)
        return None
    context = {**context, "source": "legacy_site_snapshot", "snapshot_path": str(SNAPSHOT_PATH)}
    context.setdefault("success", True)
    context.setdefault("message", "Loaded cached legacy-site inventory snapshot.")
    return context


def _load_floorplan_snapshot_context() -> dict[str, Any] | None:
    if not FLOORPLAN_SNAPSHOT_PATH.exists():
        return None
    try:
        context = json.loads(FLOORPLAN_SNAPSHOT_PATH.read_text())
    except Exception as exc:
        LOG.warning(
            "failed to load floorplan catalog snapshot path=%s error=%s",
            FLOORPLAN_SNAPSHOT_PATH,
            exc,
        )
        return None
    context = {
        **context,
        "source": "legacy_floorplan_catalog_snapshot",
        "snapshot_path": str(FLOORPLAN_SNAPSHOT_PATH),
    }
    context.setdefault("success", True)
    context.setdefault("message", "Loaded cached legacy floorplan catalog snapshot.")
    return context


def load_legacy_inventory_context(
    limit: int = 100,
    ttl_seconds: int = 300,
    force_refresh: bool = False,
    snapshot_fallback: bool = True,
    prefer_snapshot: bool = True,
) -> dict[str, Any]:
    """Load cached/archived legacy inventory context with live refresh fallback."""
    if cache_get and not force_refresh:
        cached = cache_get(LEGACY_CACHE_KEY)
        if cached:
            return _limit_context(_apply_media_recovery_to_context(cached), limit)

    if prefer_snapshot and not force_refresh and snapshot_fallback:
        snapshot = _load_snapshot_context()
        if snapshot and snapshot.get("homes"):
            limited = _limit_context(_apply_media_recovery_to_context(snapshot), limit)
            if cache_set:
                cache_set(LEGACY_CACHE_KEY, limited, ttl_seconds=ttl_seconds)
            return limited

    try:
        context = build_legacy_inventory_context(limit=100)
        if cache_set:
            cache_set(LEGACY_CACHE_KEY, context, ttl_seconds=ttl_seconds)
        return _limit_context(context, limit)
    except Exception as exc:
        LOG.warning("legacy inventory live crawl failed: %s", exc)
        if snapshot_fallback:
            snapshot = _load_snapshot_context()
            if snapshot and snapshot.get("homes"):
                return _limit_context(_apply_media_recovery_to_context(snapshot), limit)
        return {
            "success": False,
            "homes": [],
            "error": str(exc),
            "message": "Legacy site inventory unavailable.",
        }


def load_legacy_floorplan_catalog_context(
    limit: int = 500,
    ttl_seconds: int = 86400,
    force_refresh: bool = False,
    snapshot_fallback: bool = True,
) -> dict[str, Any]:
    """Load the broad orderable catalog, preferring the local cutover snapshot."""
    if cache_get and not force_refresh:
        cached = cache_get(LEGACY_FLOORPLAN_CACHE_KEY)
        if cached:
            return _limit_context(cached, limit)

    if not force_refresh and snapshot_fallback:
        snapshot = _load_floorplan_snapshot_context()
        if snapshot and snapshot.get("homes"):
            limited = _limit_context(snapshot, limit)
            if cache_set:
                cache_set(LEGACY_FLOORPLAN_CACHE_KEY, limited, ttl_seconds=ttl_seconds)
            return limited

    try:
        context = build_legacy_floorplan_catalog_context(limit=500)
        if cache_set:
            cache_set(LEGACY_FLOORPLAN_CACHE_KEY, context, ttl_seconds=ttl_seconds)
        return _limit_context(context, limit)
    except Exception as exc:
        LOG.warning("legacy floorplan catalog live crawl failed: %s", exc)
        if snapshot_fallback:
            snapshot = _load_floorplan_snapshot_context()
            if snapshot and snapshot.get("homes"):
                return _limit_context(snapshot, limit)
        return {
            "success": False,
            "homes": [],
            "error": str(exc),
            "message": "Legacy floorplan catalog unavailable.",
        }


def write_manifest(context: dict[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "legacy_inventory_context.json"
    csv_path = output_dir / "inventory_manifest.csv"
    report_path = output_dir / "legacy_site_asset_report.md"

    json_path.write_text(json.dumps(context, indent=2, sort_keys=True))

    rows = []
    for home in context.get("homes") or []:
        rows.append(
            {
                "id": home.get("id", ""),
                "model_name": home.get("model_name", ""),
                "manufacturer": home.get("manufacturer", ""),
                "status": home.get("status", ""),
                "detail_url": home.get("detail_url", ""),
                "quote_url": home.get("quote_url", ""),
                "photo_count": (home.get("media_quality") or {}).get("photo_count", 0),
                "floorplan_count": (home.get("media_quality") or {}).get("floorplan_count", 0),
                "media_status": (home.get("media_quality") or {}).get("status", ""),
                "matterport": bool(home.get("matterport_id")),
            }
        )

    with csv_path.open("w", newline="") as fh:
        fieldnames = (
            list(rows[0].keys())
            if rows
            else [
                "id",
                "model_name",
                "manufacturer",
                "status",
                "detail_url",
                "quote_url",
                "photo_count",
                "floorplan_count",
                "media_status",
                "matterport",
            ]
        )
        writer = csv.DictWriter(fh, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# THO Legacy Site Inventory Asset Report",
        "",
        f"- Source: {context.get('source_url', LEGACY_INVENTORY_URL)}",
        f"- Retrieved: {context.get('retrieved_at', '')}",
        f"- Listings: {context.get('total_inventory', 0)}",
        f"- Media summary: `{json.dumps(context.get('media_summary', {}), sort_keys=True)}`",
        "- Recovered manufacturer-plan galleries: "
        f"{sum(1 for home in context.get('homes') or [] if home.get('media_recovery'))}",
        "",
        "This manifest stores URL provenance only. It does not download or rehost media.",
    ]
    report_path.write_text("\n".join(lines) + "\n")

    return {
        "json": str(json_path),
        "csv": str(csv_path),
        "report": str(report_path),
    }


def write_floorplan_catalog_manifest(context: dict[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "legacy_floorplan_catalog_context.json"
    csv_path = output_dir / "floorplan_catalog_manifest.csv"
    report_path = output_dir / "legacy_floorplan_catalog_report.md"

    json_path.write_text(json.dumps(context, indent=2, sort_keys=True))

    rows = []
    for home in context.get("homes") or []:
        rows.append(
            {
                "id": home.get("id", ""),
                "legacy_plan_id": home.get("legacy_plan_id", ""),
                "model_name": home.get("model_name", ""),
                "manufacturer": home.get("manufacturer", ""),
                "classification": home.get("classification", ""),
                "detail_url": home.get("detail_url", ""),
                "quote_url": home.get("quote_url", ""),
                "floorplan_url": home.get("floorplan_url", ""),
                "photo_count": (home.get("media_quality") or {}).get("photo_count", 0),
                "floorplan_count": (home.get("media_quality") or {}).get("floorplan_count", 0),
                "matterport": bool(home.get("matterport_id")),
            }
        )

    with csv_path.open("w", newline="") as fh:
        fieldnames = (
            list(rows[0].keys())
            if rows
            else [
                "id",
                "legacy_plan_id",
                "model_name",
                "manufacturer",
                "classification",
                "detail_url",
                "quote_url",
                "floorplan_url",
                "photo_count",
                "floorplan_count",
                "matterport",
            ]
        )
        writer = csv.DictWriter(fh, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# THO Legacy Floorplan Catalog Report",
        "",
        f"- Source: {context.get('source_url', LEGACY_FLOORPLAN_URL)}",
        f"- Retrieved: {context.get('retrieved_at', '')}",
        f"- Orderable floorplans: {context.get('total_inventory', 0)}",
        f"- Media summary: `{json.dumps(context.get('media_summary', {}), sort_keys=True)}`",
        "",
        "This manifest stores URL provenance only. It does not download or rehost media.",
    ]
    report_path.write_text("\n".join(lines) + "\n")

    return {
        "json": str(json_path),
        "csv": str(csv_path),
        "report": str(report_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape THO legacy public inventory.")
    parser.add_argument(
        "--output-dir", default="data/legacy_site", help="Directory for JSON/CSV/report output"
    )
    parser.add_argument("--max-pages", type=int, default=25)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument(
        "--no-details", action="store_true", help="Skip per-listing detail page enrichment"
    )
    parser.add_argument(
        "--floorplans",
        action="store_true",
        help="Scrape the broad orderable floorplan catalog instead of current inventory",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.floorplans:
        context = build_legacy_floorplan_catalog_context(
            limit=args.limit,
            max_pages=args.max_pages,
        )
        paths = write_floorplan_catalog_manifest(context, Path(args.output_dir))
    else:
        context = build_legacy_inventory_context(
            limit=args.limit,
            max_pages=args.max_pages,
            include_details=not args.no_details,
        )
        paths = write_manifest(context, Path(args.output_dir))
    print(
        json.dumps(
            {"success": True, "paths": paths, "total_inventory": context["total_inventory"]},
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
