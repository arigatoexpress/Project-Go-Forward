"""
Photo classifier for inventory listings.

URL-pattern-based detection of floorplan vs. exterior/interior photos.

Source-of-truth observation (2026-04-30 production audit):
- Floorplan URLs always contain ``/floorplan/`` in the CDN path. Example::

    https://d132mt2yijm03y.cloudfront.net/manufacturer/3327/floorplan/224354/S-1672-32B-1.jpg

- Real (exterior/interior) listing photos are served from the dealer
  inventory path. Example::

    https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/30643/photo_card.jpg

The audit found that 35 of 44 homes had ``image_url`` pointing at a
floorplan URL (i.e. the floorplan was being shown as the hero photo) and
all 44 had an empty ``floorplan_url`` field. This module reorders photos
so the dedicated floorplan field is populated, and ``image_url`` /
``real_photos`` only expose exterior/interior photos when one exists.

Detection looks at the FILENAME, not the URL path. The manufacturer
CDN organizes every asset for a floorplan-model under
``/manufacturer/{id}/floorplan/{plan_id}/`` — that path segment is a
NAMESPACE for the model, not a content classifier. The first photo in
that folder (e.g. ``S-1672-32B-1.jpg``) is typically an exterior
shot. The actual floorplan diagram lives alongside it as
``floor-plans.jpg``, ``floorplan.pdf``, or sometimes a bare model/plan
filename such as ``jackson.jpg``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import unquote, urlparse

# Filename tokens that mark an actual floorplan diagram (not just any
# asset under a model's CDN namespace). Case-insensitive substring on
# the filename only.
FLOORPLAN_FILENAME_TOKENS: tuple[str, ...] = (
    "floorplan",
    "floor-plan",
    "floor_plan",
    "floor-plans",
    "floor_plans",
    "floor plans",
)
# File extensions that almost always indicate a floorplan diagram in
# this domain (home listings).
FLOORPLAN_FILE_EXTS: tuple[str, ...] = (".pdf",)
MANUFACTURER_FLOORPLAN_NAMESPACE_RE = re.compile(
    r"/manufacturer/[^/]+/floorplan/[^/]+/",
    re.IGNORECASE,
)
PHOTO_FILENAME_TOKENS: tuple[str, ...] = (
    "bath",
    "bed",
    "coffee",
    "exterior",
    "front",
    "interior",
    "island",
    "kitchen",
    "living",
    "porch",
    "room",
    "utility",
)
SHORT_PHOTO_FILENAME_TOKENS: tuple[str, ...] = ("3d", "ext", "int", "kit", "lvg", "lvgrm")
PLACEHOLDER_FILENAME_TOKENS: tuple[str, ...] = ("placeholder", "tex-icon", "vite")
UUID_FILENAME_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _is_manufacturer_floorplan_namespace(url: str) -> bool:
    path = urlparse(url).path
    return bool(MANUFACTURER_FLOORPLAN_NAMESPACE_RE.search(path))


def _looks_like_photo_filename(filename: str) -> bool:
    stem = filename.rsplit(".", 1)[0]
    if not stem:
        return False
    if stem.isdigit():
        return True
    if UUID_FILENAME_RE.fullmatch(stem):
        return True
    if re.search(r"[-_\s]\d+$", stem):
        return True
    parts = [part for part in re.split(r"[^a-z0-9]+", stem) if part]
    if any(part in SHORT_PHOTO_FILENAME_TOKENS for part in parts):
        return True
    return any(token in stem for token in PHOTO_FILENAME_TOKENS)


def _looks_like_bare_model_floorplan(url: str, filename: str) -> bool:
    """Detect plan diagrams whose filename is just the model/plan name.

    Manufacturer CDN URLs are stored under a ``/floorplan/{plan_id}/``
    namespace even when they are photos, so the path alone is not enough.
    But the plan diagram itself often has a bare model filename such as
    ``jackson.jpg`` or ``de_Vaca_S64F.jpg`` while actual photos are named
    ``1.jpg``, ``Kit-1.jpg``, ``S-1672-32B-1.jpg``, UUIDs, or room names.
    """
    if not _is_manufacturer_floorplan_namespace(url):
        return False
    if not filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
        return False
    if _looks_like_photo_filename(filename):
        return False
    return any(char.isalpha() for char in filename)


def _is_placeholder_url(url: str | None) -> bool:
    if not url or not isinstance(url, str):
        return False
    filename = unquote(url.lower().rsplit("/", 1)[-1].split("?", 1)[0])
    return any(token in filename for token in PLACEHOLDER_FILENAME_TOKENS)


def is_floorplan_url(url: str | None) -> bool:
    """Return True if ``url`` is a floorplan image/PDF URL.

    Detection is filename-based, not path-based. A URL whose PATH has
    ``/floorplan/`` but whose FILENAME is just an indexed photo
    (e.g. ``S-1672-32B-1.jpg``) is NOT a floorplan — it's an asset
    stored under that model's CDN namespace. A URL with
    ``floorplan``/``floor-plan``/``floor-plans`` in the filename, or
    ending in ``.pdf``, IS a floorplan. Case-insensitive. Empty / None
    inputs return False.
    """
    if not url or not isinstance(url, str):
        return False
    lowered = url.lower()
    filename = unquote(lowered.rsplit("/", 1)[-1].split("?", 1)[0])
    if filename.endswith(FLOORPLAN_FILE_EXTS):
        return True
    if any(token in filename for token in FLOORPLAN_FILENAME_TOKENS):
        return True
    return _looks_like_bare_model_floorplan(url, filename)


def _normalize_url(value: str | None) -> str:
    """Return a comparison-safe URL key while preserving CDN path casing."""
    if not value or not isinstance(value, str):
        return ""
    return value.strip().rstrip("/")


def split_photos(
    urls: Iterable[str | None],
    floorplan_hints: Iterable[str | None] | None = None,
) -> tuple[list[str], list[str]]:
    """Split a sequence of photo URLs into (exteriors, floorplans).

    Order within each group is preserved. Falsy / non-string entries are
    dropped silently — callers do not have to pre-filter. URLs already
    present in ``floorplan_hints`` are treated as floorplans even when the
    filename is ambiguous, because an explicit Firestore/asset-catalog
    floorplan field is stronger evidence than the filename.
    """
    hint_set = {_normalize_url(url) for url in floorplan_hints or [] if _normalize_url(url)}
    exteriors: list[str] = []
    floorplans: list[str] = []
    for url in urls or []:
        if not url or not isinstance(url, str):
            continue
        if _is_placeholder_url(url):
            continue
        normalized = _normalize_url(url)
        if normalized in hint_set or is_floorplan_url(url):
            floorplans.append(url)
        else:
            exteriors.append(url)
    return exteriors, floorplans


def _dedupe_preserve_order(items: Iterable[str | None]) -> list[str]:
    """Return ``items`` deduplicated while preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items or []:
        if not item or not isinstance(item, str):
            continue
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _as_url_list(value: object) -> list[str | None]:
    """Normalize a scalar/list media field into a list for classifier input."""
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return value
    return []


def _filename(value: str) -> str:
    """Extract a decoded filename-ish token from a URL or catalog filename."""
    return unquote(str(value or "").rsplit("/", 1)[-1].split("?", 1)[0])


def _normalize_image_categories(categories: dict | None, photo_urls: Iterable[str]) -> dict:
    """Keep image category labels aligned with actual photo URLs only."""
    if not isinstance(categories, dict):
        return {}

    photos = [url for url in photo_urls or [] if isinstance(url, str)]
    allowed_urls = {_normalize_url(url) for url in photos}
    allowed_filenames = {_filename(url) for url in photos}
    normalized: dict = {}

    for category, values in categories.items():
        if not isinstance(values, list):
            continue
        kept: list[str] = []
        for value in values:
            if not value or not isinstance(value, str):
                continue
            if is_floorplan_url(value):
                continue
            if value.startswith("http"):
                if _normalize_url(value) in allowed_urls:
                    kept.append(value)
                continue
            if value in allowed_filenames:
                kept.append(value)
        if kept:
            normalized[category] = kept
    return normalized


def _media_quality(photo_count: int, floorplan_count: int) -> dict:
    """Build a small, stable quality contract for UI/audit/smoke checks."""
    issues: list[str] = []
    if photo_count == 0 and floorplan_count > 0:
        status = "floorplan_only"
        issues.append("floorplan_only")
    elif photo_count == 0:
        status = "missing_photos"
        issues.append("missing_real_photos")
    elif photo_count < 3:
        status = "limited_photos"
        issues.append("limited_real_photos")
    else:
        status = "photo_ready"

    return {
        "status": status,
        "has_real_photo": photo_count > 0,
        "photo_count": photo_count,
        "floorplan_count": floorplan_count,
        "issues": issues,
    }


def reorder_for_listing(
    photos: Iterable[str | None],
    current_image_url: str | None = None,
    floorplan_urls: Iterable[str | None] | None = None,
) -> dict:
    """Return a normalized photo dict for a listing.

    Inputs:
        photos: ordered list of photo URLs (e.g. existing ``real_photos``).
        current_image_url: the listing's current ``image_url`` (typically
            the hero/card photo). Included as the first candidate so it is
            considered alongside ``photos``.
        floorplan_urls: URLs from explicit floorplan fields. These are
            preserved as floorplans even when their filenames are ambiguous.

    Output dict has keys:
        image_url: first exterior URL, or ``None`` if no exteriors exist.
            Frontends should fall back to a placeholder when ``None``.
        real_photos: deduplicated exterior/interior URLs only.
        gallery_images: first 3 entries of ``real_photos``.
        floorplan_url: first floorplan URL, or empty string if none.
        floorplan_urls: all floorplan URLs, deduplicated.
        media_quality: status/counts for audit and UI gating.
    """
    candidates: list[str | None] = []
    if current_image_url:
        candidates.append(current_image_url)
    candidates.extend(photos or [])
    candidates.extend(floorplan_urls or [])

    deduped = _dedupe_preserve_order(candidates)
    floorplan_hints = _dedupe_preserve_order(floorplan_urls or [])
    exteriors, floorplans = split_photos(deduped, floorplan_hints=floorplan_hints)

    image_url = exteriors[0] if exteriors else None
    floorplan_url = floorplans[0] if floorplans else ""
    gallery_images = exteriors[:3]

    return {
        "image_url": image_url,
        "real_photos": exteriors,
        "gallery_images": gallery_images,
        "floorplan_url": floorplan_url,
        "floorplan_urls": floorplans,
        "media_quality": _media_quality(len(exteriors), len(floorplans)),
    }


def apply_classifier_to_home(home: dict) -> dict:
    """Mutate and return ``home`` with classifier-corrected photo fields.

    Combines the home's existing hero/list/gallery fields, runs them through
    :func:`reorder_for_listing`, then writes the result back onto ``home``:

    - ``home["image_url"]`` = first exterior, or ``""`` if none exist
      (kept as empty string instead of ``None`` so existing JSON
      serialization paths and frontend ``home.image_url || placeholder``
      checks keep working).
    - ``home["real_photos"]`` = exterior/interior photos only.
    - ``home["gallery_images"]`` = first 3 of ``real_photos``.
    - ``home["floorplan_url"]`` = first floorplan, or ``""``. Also mirrors
      to ``home["floor_plan_url"]`` so legacy callers keep working — the
      codebase currently uses both spellings.
    - ``home["floorplan_urls"]`` = all floorplan URLs.
    - ``home["media_quality"]`` = stable status/counts.

    Returns the same ``home`` dict for chaining/comprehensions.
    """
    if not isinstance(home, dict):
        return home

    current_image_url = home.get("image_url") or home.get("hero_image") or ""
    photo_candidates: list[str | None] = []
    for field in ("real_photos", "gallery_images", "photos"):
        photo_candidates.extend(_as_url_list(home.get(field)))

    floorplan_candidates: list[str | None] = []
    for field in ("floorplan_url", "floor_plan_url"):
        value = home.get(field)
        if value:
            floorplan_candidates.append(value)
    for field in ("floorplan_urls", "floor_plan_urls"):
        floorplan_candidates.extend(_as_url_list(home.get(field)))

    cleaned = reorder_for_listing(
        photo_candidates,
        current_image_url=current_image_url,
        floorplan_urls=floorplan_candidates,
    )

    home["image_url"] = cleaned["image_url"] or ""
    home["real_photos"] = cleaned["real_photos"]
    home["gallery_images"] = cleaned["gallery_images"]

    # Preserve any pre-existing floorplan URL the home already had if the
    # classifier didn't find one in the photo list (e.g. a Firestore doc
    # with floorplan_url set but no floorplan in real_photos).
    existing_floorplan = home.get("floorplan_url") or home.get("floor_plan_url") or ""
    home["floorplan_url"] = cleaned["floorplan_url"] or existing_floorplan
    # Keep both spellings in sync — existing callers read either name.
    home["floor_plan_url"] = home["floorplan_url"]
    home["floorplan_urls"] = cleaned["floorplan_urls"]
    home["media_quality"] = cleaned["media_quality"]
    home["image_categories"] = _normalize_image_categories(
        home.get("image_categories"),
        cleaned["real_photos"],
    )

    # Block unverified photos for specific legacy inventory records that need provenance
    model_name_lower = str(home.get("model_name") or "").lower()
    needs_provenance = any(
        target in model_name_lower 
        for target in ["mountain delight", "the aspen", "the cottage"]
    )
    if needs_provenance:
        home["image_url"] = ""
        home["real_photos"] = []
        home["gallery_images"] = []
        home["media_quality"]["has_real_photo"] = False
        home["media_quality"]["status"] = "missing_photos"

    return home


def get_real_photo_urls(home: dict | None) -> list[str]:
    """Return deduplicated non-floorplan photo URLs from a home-like dict."""
    if not isinstance(home, dict):
        return []
    cleaned = reorder_for_listing(
        [*_as_url_list(home.get("real_photos")), *_as_url_list(home.get("gallery_images"))],
        current_image_url=home.get("image_url") or home.get("hero_image") or None,
        floorplan_urls=[
            home.get("floorplan_url"),
            home.get("floor_plan_url"),
            *_as_url_list(home.get("floorplan_urls")),
        ],
    )
    return cleaned["real_photos"]


def has_real_photo(home: dict | None) -> bool:
    """Return True only when a home has at least one non-floorplan photo."""
    return bool(get_real_photo_urls(home))
