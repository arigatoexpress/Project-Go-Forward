"""Orderable manufacturer floorplan catalog helpers.

The public inventory page has two different customer promises:

* current homes THO can discuss as available/pre-owned inventory; and
* manufacturer floorplans customers can order or customize.

The legacy ManufacturedHomes.com site showed both concepts in practice. This
module keeps that distinction explicit so the replacement site can preserve the
wide floorplan offering without implying that every floorplan is on the lot.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import unquote, urlparse

from tools.photo_classifier import apply_classifier_to_home

ORDERABLE_STATUS = "Orderable"
ORDERABLE_KIND = "orderable_floorplan"
AVAILABLE_KIND = "available_now"
PREOWNED_KIND = "pre_owned"


def _sanitize_public_description(value: Any) -> str:
    """Remove time-sensitive stock claims inherited from catalog snapshots."""
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(
        r"take a 3d home tour, check out photos, and get a price quote on this floor plan today!?",
        "Explore photos and any 3D tour, then request current price and availability.",
        text,
        flags=re.IGNORECASE,
    )
    for pattern, replacement in (
        (r"\bavailable for sale now\b", "listed in our catalog"),
        (r"\bavailable now\b", "listed in our catalog"),
        (r"\bin stock\b", "listed in our catalog"),
        (r"\bready now\b", "listed in our catalog"),
        (r"\bis sold by\b", "is offered by"),
        (
            r"\bget a price quote on this floor plan today\b",
            "request current price and availability",
        ),
    ):
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _normalize_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _clean_tag(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.netloc.endswith("texashomeoutlet.com"):
        text = unquote(parsed.path.rsplit("/", 1)[-1]).replace("-", " ").strip()
    if text.startswith("http"):
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _sanitize_tags(values: Any) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    if not isinstance(values, list):
        return out
    for raw in values:
        value = _clean_tag(raw)
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _catalog_key(home: Mapping[str, Any]) -> str:
    """Return a stable de-dupe key for catalog/listing homes."""
    explicit = home.get("source_catalog_slug") or home.get("catalog_slug")
    if explicit:
        return f"catalog:{_normalize_text(explicit)}"

    plan_id = home.get("legacy_plan_id") or home.get("plan_id")
    if plan_id:
        return f"plan:{_normalize_text(plan_id)}"

    home_id = home.get("legacy_inventory_id") or home.get("id")
    if home_id:
        return f"id:{_normalize_text(home_id)}"

    manufacturer = _normalize_text(home.get("manufacturer"))
    model = _normalize_text(home.get("model_name"))
    return f"model:{manufacturer}:{model}"


def _identity_keys(home: Mapping[str, Any]) -> set[str]:
    keys = {_catalog_key(home)}
    for field in ("legacy_plan_id", "plan_id", "legacy_inventory_id", "id"):
        if home.get(field):
            keys.add(f"{field}:{_normalize_text(home.get(field))}")
    for field in ("floorplan_url", "floor_plan_url"):
        value = home.get(field)
        if value:
            keys.add(f"floorplan:{str(value).strip().rstrip('/').lower()}")
    for value in home.get("floorplan_urls") or []:
        if value:
            keys.add(f"floorplan:{str(value).strip().rstrip('/').lower()}")
    return keys


def _model_key(home: Mapping[str, Any]) -> str:
    words = [
        word
        for word in _normalize_text(home.get("model_name")).split()
        if word not in {"the", "series", "floorplan", "floorplans", "home", "homes"}
    ]
    return " ".join(words)


def _manufacturer_key(home: Mapping[str, Any]) -> str:
    words = [
        word
        for word in _normalize_text(home.get("manufacturer")).split()
        if word not in {"homes", "home", "housing", "built"}
    ]
    return " ".join(words)


def _has_strong_identity(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Return whether two rows have evidence strong enough to share URLs."""
    if _identity_keys(left) & _identity_keys(right):
        return True

    left_model = _normalize_text(left.get("model_name"))
    right_model = _normalize_text(right.get("model_name"))
    left_mfr = _normalize_text(left.get("manufacturer"))
    right_mfr = _normalize_text(right.get("manufacturer"))
    return bool(left_model and left_model == right_model and left_mfr and left_mfr == right_mfr)


def _looks_like_duplicate(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    if _identity_keys(left) & _identity_keys(right):
        return True

    left_model = _model_key(left)
    right_model = _model_key(right)
    if len(left_model) < 6 or len(right_model) < 6:
        return False

    left_mfr = _manufacturer_key(left)
    right_mfr = _manufacturer_key(right)
    manufacturer_matches = bool(
        left_mfr and right_mfr and (left_mfr in right_mfr or right_mfr in left_mfr)
    )
    model_matches = left_model in right_model or right_model in left_model
    return manufacturer_matches and model_matches


def _preserve_legacy_url_aliases(survivor: dict[str, Any], duplicate: Mapping[str, Any]) -> None:
    """Keep public URLs from a catalog row suppressed as a duplicate.

    The current-inventory row remains the one public home, but legacy plan and
    quote URLs may already carry search equity. Store those URLs on the
    survivor so the SEO registry can redirect them to its canonical detail
    page instead of turning an exact-model de-duplication into a 404.
    """
    if not _has_strong_identity(survivor, duplicate):
        return

    for url_field, aliases_field in (
        ("detail_url", "legacy_detail_aliases"),
        ("quote_url", "legacy_quote_aliases"),
    ):
        canonical = str(survivor.get(url_field) or "").strip()
        candidates: list[str] = []
        for owner in (survivor, duplicate):
            existing = owner.get(aliases_field)
            if isinstance(existing, list):
                candidates.extend(value for value in existing if isinstance(value, str))
        duplicate_url = duplicate.get(url_field)
        if isinstance(duplicate_url, str):
            candidates.append(duplicate_url)

        aliases: list[str] = []
        for value in candidates:
            cleaned = value.strip()
            if not cleaned or cleaned == canonical or cleaned in aliases:
                continue
            aliases.append(cleaned)
        if aliases:
            survivor[aliases_field] = aliases


def classify_inventory_kind(home: Mapping[str, Any]) -> str:
    """Classify a home for public inventory filters."""
    raw_kind = str(home.get("inventory_kind") or "").strip()
    if raw_kind:
        return raw_kind

    status = str(home.get("status") or "").strip().lower()
    if status == "orderable":
        return ORDERABLE_KIND
    if "pre" in status and "owned" in status:
        return PREOWNED_KIND
    return AVAILABLE_KIND


def apply_inventory_kind(home: dict[str, Any]) -> dict[str, Any]:
    """Attach availability flags while preserving the existing status field."""
    home_id = str(home.get("home_id") or "").strip()
    if not home_id:
        home_id = str(home.get("id") or "").strip()
    if home_id:
        home["home_id"] = home_id
    if home.get("description"):
        home["description"] = _sanitize_public_description(home["description"])

    kind = classify_inventory_kind(home)
    home["inventory_kind"] = kind
    availability_verified = home.get("availability_verified") is True

    if kind == ORDERABLE_KIND:
        home["status"] = ORDERABLE_STATUS
        home["availability_label"] = "Orderable floorplan"
        home["is_orderable"] = True
        home["is_in_stock"] = False
    elif kind == PREOWNED_KIND:
        home["availability_label"] = "Pre-owned"
        home["is_orderable"] = False
        home["is_in_stock"] = availability_verified
    else:
        home["availability_label"] = "Check availability"
        home["is_orderable"] = False
        home["is_in_stock"] = availability_verified

    return home


def build_orderable_home(slug: str, asset: Mapping[str, Any]) -> dict[str, Any] | None:
    """Convert one asset-catalog entry into a public orderable floorplan."""
    if asset.get("is_new") is not True:
        return None

    manufacturer = str(asset.get("manufacturer") or "").strip()
    if manufacturer.lower() == "pre-owned":
        return None

    width = _to_int(str(asset.get("dims") or "").split("x", 1)[0])
    classification = "Double Wide" if width and width >= 24 else "Single Wide"
    images = [url for url in (asset.get("images") or []) if isinstance(url, str) and url]
    matterport_id = asset.get("matterport_id") or ""

    home: dict[str, Any] = {
        "id": f"catalog-{slug}",
        "source_catalog_slug": slug,
        "model_name": asset.get("name") or slug.replace("-", " ").title(),
        "manufacturer": manufacturer or "Texas Home Outlet",
        "manufacturer_id": asset.get("manufacturer_id"),
        "plan_id": asset.get("plan_id"),
        "classification": classification,
        "status": ORDERABLE_STATUS,
        "inventory_kind": ORDERABLE_KIND,
        "specs": {
            "beds": _to_int(asset.get("beds")),
            "baths": _to_int(asset.get("baths")),
            "sq_ft": _to_int(asset.get("sqft")),
            "dimensions": asset.get("dims") or "",
        },
        "pricing": {
            "price_value": 0,
            "display_price": "Call for Price",
            "price_tier": "Call for Price",
        },
        "price_value": 0,
        "display_price": "Call for Price",
        "features": ["Custom order available"],
        "marketing_tags": ["Orderable Floorplan"],
        "image_url": images[0] if images else "",
        "gallery_images": images[:3],
        "real_photos": images,
        "image_categories": asset.get("image_categories") or {},
        "floor_plan_url": asset.get("floor_plan") or "",
        "floorplan_url": asset.get("floor_plan") or "",
        "matterport_id": matterport_id or None,
        "matterport_url": f"https://my.matterport.com/show/?m={matterport_id}&play=1"
        if matterport_id
        else None,
        "source": "manufacturer_catalog",
        "source_owner": "Texas Home Outlet",
    }
    apply_classifier_to_home(home)
    return apply_inventory_kind(home)


def build_orderable_catalog(
    assets: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Build orderable floorplans from the approved local asset catalog."""
    homes: list[dict[str, Any]] = []
    for slug, asset in sorted(assets.items()):
        home = build_orderable_home(str(slug), asset)
        if home:
            homes.append(home)
    return homes


def build_orderable_catalog_from_context(
    floorplan_context: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return orderable homes from a legacy floorplan catalog context."""
    homes: list[dict[str, Any]] = []
    for raw_home in (floorplan_context or {}).get("homes") or []:
        if not isinstance(raw_home, dict):
            continue
        home = copy.deepcopy(raw_home)
        home["inventory_kind"] = ORDERABLE_KIND
        home["status"] = ORDERABLE_STATUS
        features = _sanitize_tags(home.get("features"))
        marketing_tags = _sanitize_tags(home.get("marketing_tags"))
        if "orderable floorplan" not in {tag.lower() for tag in features}:
            features = ["Orderable Floorplan", *features]
        if "orderable floorplan" not in {tag.lower() for tag in marketing_tags}:
            marketing_tags = ["Orderable Floorplan", *marketing_tags]
        home["features"] = features
        home["marketing_tags"] = marketing_tags
        apply_classifier_to_home(home)
        homes.append(apply_inventory_kind(home))
    return homes


def merge_orderable_floorplan_catalog(
    result: Mapping[str, Any],
    assets: Mapping[str, Mapping[str, Any]] | None = None,
    floorplan_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return inventory context with live homes plus orderable floorplans.

    Existing live homes are copied, classified, and preserved first. Orderable
    floorplans are then appended unless the source already contains the same
    catalog slug/id/model. The broad legacy /floor-plans snapshot wins when
    available; the smaller local asset catalog remains a fallback for tests and
    offline development.
    """
    if assets is None:
        from tools.asset_scraper import PROPERTY_ASSETS

        assets = PROPERTY_ASSETS

    merged = copy.deepcopy(dict(result or {}))
    homes = [copy.deepcopy(home) for home in merged.get("homes") or [] if isinstance(home, dict)]

    classified_homes: list[dict[str, Any]] = []
    for home in homes:
        apply_inventory_kind(home)
        apply_classifier_to_home(home)
        duplicate = next(
            (existing for existing in classified_homes if _looks_like_duplicate(home, existing)),
            None,
        )
        if duplicate is not None:
            _preserve_legacy_url_aliases(duplicate, home)
            continue
        classified_homes.append(home)

    orderable_added = 0
    if floorplan_context and floorplan_context.get("homes"):
        orderable_candidates = build_orderable_catalog_from_context(floorplan_context)
    else:
        orderable_candidates = build_orderable_catalog(assets)

    for home in orderable_candidates:
        duplicate = next(
            (existing for existing in classified_homes if _looks_like_duplicate(home, existing)),
            None,
        )
        if duplicate is not None:
            _preserve_legacy_url_aliases(duplicate, home)
            continue
        classified_homes.append(home)
        orderable_added += 1

    available_now = sum(
        1 for home in classified_homes if home.get("inventory_kind") == AVAILABLE_KIND
    )
    preowned = sum(1 for home in classified_homes if home.get("inventory_kind") == PREOWNED_KIND)
    orderable = sum(1 for home in classified_homes if home.get("inventory_kind") == ORDERABLE_KIND)

    merged["homes"] = classified_homes
    merged["total_inventory"] = len(classified_homes)
    merged["returned_inventory"] = len(classified_homes)
    merged["current_inventory_count"] = available_now + preowned
    merged["available_now"] = available_now
    merged["preowned_homes"] = preowned
    merged["orderable_floorplans"] = orderable
    merged["orderable_floorplans_added"] = orderable_added
    if floorplan_context and floorplan_context.get("homes"):
        merged["catalog_source"] = floorplan_context.get("source")
        merged["catalog_source_url"] = floorplan_context.get("source_url")
        merged["catalog_floorplan_source_count"] = len(floorplan_context.get("homes") or [])
    merged["catalog_summary"] = {
        "available_now": available_now,
        "pre_owned": preowned,
        "orderable_floorplans": orderable,
        "total_public_homes": len(classified_homes),
    }
    if orderable:
        merged["message"] = "Includes listed homes plus orderable manufacturer floorplans."
    return merged
