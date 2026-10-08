"""Public listing URLs for in-stock homes (special deals + pre-owned).

Orderable floorplans keep their legacy ``/plan/<id>/...`` URLs. Lot homes
that actually sell — special deals and pre-owned — get a stable
``/homes/<stock-id>-<slug>`` path so cards, the sitemap, and the SEO
renderer share one identifier.

This module never writes data. Stock ids come from the home record
(``stock_number``, ``legacy_inventory_id``, or a numeric ``id``).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from tools.catalog_floorplans import (
    AVAILABLE_KIND,
    ORDERABLE_KIND,
    PREOWNED_KIND,
    classify_inventory_kind,
)
from tools.inventory_dedupe import is_active_status, is_orderable_new, is_preowned
from tools.photo_classifier import is_floorplan_url

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_HOME_PATH_RE = re.compile(r"^/homes/([^/]+)/?$", re.IGNORECASE)


def listing_slug(value: Any) -> str:
    slug = _SLUG_RE.sub("-", str(value or "").strip().lower()).strip("-")
    return slug or "home"


def stock_id(home: dict | None) -> str | None:
    """Prefer a real lot/stock id over a catalog slug."""
    if not isinstance(home, dict):
        return None
    candidates = [
        home.get("stock_number"),
        home.get("legacy_inventory_id"),
        home.get("id"),
        home.get("home_id"),
    ]
    numeric = [str(raw).strip() for raw in candidates if str(raw or "").strip().isdigit()]
    if numeric:
        return numeric[0]
    for raw in candidates:
        value = str(raw or "").strip()
        if value:
            return value
    return None


def is_instock_home(home: dict | None) -> bool:
    """True for a currently listed special-deal or pre-owned unit."""
    if not isinstance(home, dict):
        return False
    if is_orderable_new(home):
        return False
    kind = classify_inventory_kind(home)
    if kind == ORDERABLE_KIND:
        return False
    if kind not in {AVAILABLE_KIND, PREOWNED_KIND}:
        return False
    return is_active_status(home.get("status"))


def listing_path(home: dict | None) -> str | None:
    """Relative ``/homes/<stock-id>-<slug>`` path for an in-stock home."""
    if not is_instock_home(home):
        return None
    identifier = stock_id(home)
    if not identifier or not str(identifier).isdigit():
        return None
    return f"/homes/{identifier}-{listing_slug(home.get('model_name'))}"


def parse_home_path(path: str | None) -> str | None:
    match = _HOME_PATH_RE.match(str(path or ""))
    return match.group(1) if match else None


def match_instock_home(path: str | None, homes: list[dict]) -> dict | None:
    """Return the in-stock home whose listing path or stock id matches ``path``."""
    token = parse_home_path(path)
    if not token:
        return None
    by_path: dict[str, dict] = {}
    by_stock: dict[str, dict] = {}
    for home in homes:
        path_value = listing_path(home)
        identifier = stock_id(home)
        if path_value:
            by_path[path_value.rstrip("/")] = home
        if identifier:
            by_stock[identifier.lower()] = home
    exact = by_path.get("/homes/" + token)
    if exact:
        return exact
    token_l = token.lower()
    if token_l in by_stock:
        return by_stock[token_l]
    for identifier, home in by_stock.items():
        if token_l == identifier or token_l.startswith(identifier + "-"):
            return home
    return None


def listing_lastmod(home: dict | None, *, today: str | None = None) -> str:
    """YYYY-MM-DD lastmod for a live listing.

    Prefer a record timestamp when one exists. Otherwise use today — the
    home is still in the public feed, so that date is truthful.
    """
    if isinstance(home, dict):
        for key in ("updated_at", "last_modified", "created_at"):
            parsed = _parse_date(home.get(key))
            if parsed:
                return parsed
    return today or datetime.now(UTC).date().isoformat()


def listing_page_images(home: dict | None) -> list[str]:
    """Photos a public listing page may show.

    Used / pre-owned homes keep only their own ``real_photos``. Homes
    without a real photo fall back to the floor-plan drawing only —
    never a catalog or showroom shot.
    """
    if not isinstance(home, dict):
        return []
    own_photos: list[str] = []
    seen: set[str] = set()
    if is_preowned(home):
        candidates = _url_list(home.get("real_photos"))
    else:
        candidates = [
            home.get("image_url") or home.get("hero_image"),
            *_url_list(home.get("real_photos")),
        ]
    for raw in candidates:
        url = str(raw or "").strip()
        if not url or url in seen or is_floorplan_url(url):
            continue
        seen.add(url)
        own_photos.append(url)
    if own_photos:
        return own_photos
    for key in ("floorplan_url", "floor_plan_url"):
        plan = str(home.get(key) or "").strip()
        if plan:
            return [plan]
    plans = home.get("floorplan_urls")
    if isinstance(plans, list):
        for plan in plans:
            value = str(plan or "").strip()
            if value:
                return [value]
    return []


def attach_listing_urls(homes: list[dict] | None) -> list[dict]:
    """Set ``listing_url`` / ``stock_id`` on in-stock homes. Mutates in place."""
    for home in homes or []:
        if not isinstance(home, dict):
            continue
        identifier = stock_id(home)
        if identifier and not str(home.get("stock_id") or "").strip():
            home["stock_id"] = identifier
        path = listing_path(home)
        if path:
            home["listing_url"] = path
    return homes or []


def _parse_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if hasattr(value, "date"):
        try:
            return value.date().isoformat()
        except Exception:
            pass
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        pass
    match = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    return match.group(1) if match else None


def _url_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    return []


def path_from_url(url: str | None) -> str | None:
    if not url:
        return None
    path = urlparse(str(url)).path
    return path if path and path != "/" else None
