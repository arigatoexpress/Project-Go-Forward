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
``real_photos`` start with an exterior/interior photo when one exists.

Detection looks at the FILENAME, not the URL path. The manufacturer
CDN organizes every asset for a floorplan-model under
``/manufacturer/{id}/floorplan/{plan_id}/`` — that path segment is a
NAMESPACE for the model, not a content classifier. The first photo in
that folder (e.g. ``S-1672-32B-1.jpg``) is typically an exterior
shot. The actual floorplan diagram lives alongside it as
``floor-plans.jpg``, ``floorplan.pdf``, etc.
"""

from __future__ import annotations

from collections.abc import Iterable

# Filename tokens that mark an actual floorplan diagram (not just any
# asset under a model's CDN namespace). Case-insensitive substring on
# the filename only.
FLOORPLAN_FILENAME_TOKENS: tuple[str, ...] = (
    "floorplan",
    "floor-plan",
    "floor_plan",
    "floor-plans",
    "floor_plans",
)
# File extensions that almost always indicate a floorplan diagram in
# this domain (home listings).
FLOORPLAN_FILE_EXTS: tuple[str, ...] = (".pdf",)


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
    filename = lowered.rsplit("/", 1)[-1].split("?", 1)[0]
    if filename.endswith(FLOORPLAN_FILE_EXTS):
        return True
    return any(token in filename for token in FLOORPLAN_FILENAME_TOKENS)


def split_photos(urls: Iterable[str | None]) -> tuple[list[str], list[str]]:
    """Split a sequence of photo URLs into (exteriors, floorplans).

    Order within each group is preserved. Falsy / non-string entries are
    dropped silently — callers do not have to pre-filter.
    """
    exteriors: list[str] = []
    floorplans: list[str] = []
    for url in urls or []:
        if not url or not isinstance(url, str):
            continue
        if is_floorplan_url(url):
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


def reorder_for_listing(
    photos: Iterable[str | None],
    current_image_url: str | None = None,
) -> dict:
    """Return a normalized photo dict for a listing.

    Inputs:
        photos: ordered list of photo URLs (e.g. existing ``real_photos``).
        current_image_url: the listing's current ``image_url`` (typically
            the hero/card photo). Included as the first candidate so it is
            considered alongside ``photos``.

    Output dict has keys:
        image_url: first exterior URL, or ``None`` if no exteriors exist.
            Frontends should fall back to a placeholder when ``None``.
        real_photos: exteriors first (in original order), then floorplans
            appended (also in original order). Deduplicated.
        gallery_images: first 3 entries of ``real_photos``.
        floorplan_url: first floorplan URL, or empty string if none.
    """
    candidates: list[str | None] = []
    if current_image_url:
        candidates.append(current_image_url)
    candidates.extend(photos or [])

    deduped = _dedupe_preserve_order(candidates)
    exteriors, floorplans = split_photos(deduped)

    real_photos = exteriors + floorplans
    image_url = exteriors[0] if exteriors else None
    floorplan_url = floorplans[0] if floorplans else ""
    gallery_images = real_photos[:3]

    return {
        "image_url": image_url,
        "real_photos": real_photos,
        "gallery_images": gallery_images,
        "floorplan_url": floorplan_url,
    }


def apply_classifier_to_home(home: dict) -> dict:
    """Mutate and return ``home`` with classifier-corrected photo fields.

    Combines the home's existing ``image_url`` and ``real_photos`` (and
    falls back to ``gallery_images`` when ``real_photos`` is empty), runs
    them through :func:`reorder_for_listing`, then writes the result back
    onto ``home``:

    - ``home["image_url"]`` = first exterior, or ``""`` if none exist
      (kept as empty string instead of ``None`` so existing JSON
      serialization paths and frontend ``home.image_url || placeholder``
      checks keep working).
    - ``home["real_photos"]`` = exteriors followed by floorplans.
    - ``home["gallery_images"]`` = first 3 of ``real_photos``.
    - ``home["floorplan_url"]`` = first floorplan, or ``""``. Also mirrors
      to ``home["floor_plan_url"]`` so legacy callers keep working — the
      codebase currently uses both spellings.

    Returns the same ``home`` dict for chaining/comprehensions.
    """
    if not isinstance(home, dict):
        return home

    current_image_url = home.get("image_url") or ""
    real_photos = home.get("real_photos") or []
    if not real_photos:
        real_photos = home.get("gallery_images") or []

    cleaned = reorder_for_listing(real_photos, current_image_url=current_image_url)

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

    return home
