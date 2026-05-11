"""Allowlisted manufacturer-media recovery for legacy THO inventory.

The public legacy site sometimes exposes only a floorplan for a listing even
though the same ManufacturedHomes manufacturer-plan CDN folder has a full
official model gallery. This module fills only those exact, researched gaps.

It deliberately does not use generic stock photos or another dealer's
dealer-inventory uploads. Every URL below is scoped to the same manufacturer
and plan id already shown on the THO legacy listing.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from tools.photo_classifier import apply_classifier_to_home

CDN_BASE = "https://d132mt2yijm03y.cloudfront.net"


def _mfg(manufacturer_id: str, plan_id: str, filename: str) -> str:
    return f"{CDN_BASE}/manufacturer/{manufacturer_id}/floorplan/{plan_id}/{filename}"


def _category(filename: str) -> str:
    lower = filename.lower()
    if "ext" in lower or "exterior" in lower:
        return "exterior"
    if "kit" in lower or "kitchen" in lower:
        return "kitchen"
    if "bath" in lower:
        return "bathroom"
    if "bed" in lower:
        return "bedroom"
    if "uti" in lower or "utility" in lower:
        return "utility"
    return "interior"


def _categories(urls: list[str]) -> dict[str, list[str]]:
    categories: dict[str, list[str]] = {}
    for url in urls:
        filename = url.rsplit("/", 1)[-1]
        categories.setdefault(_category(filename), []).append(filename)
    return categories


MANUFACTURER_MEDIA_RECOVERY: dict[str, dict[str, Any]] = {
    "28102": {
        "label": "TRU Single Section Delight",
        "source_url": "https://www.mobilehomemasters.com/plan/222250/tru-single-section/delight-trs14602ah/",
        "manufacturer_id": "2007",
        "plan_id": "222250",
        "photos": [
            _mfg("2007", "222250", "1d159323-54bb-4839-a0f0-ad46f0e48262.jpg"),
            _mfg("2007", "222250", "67200862-7955-4e91-b6f0-de5ddc10a3db.jpg"),
            _mfg("2007", "222250", "6b7b5707-7ca7-437d-b150-4876ff65bf12.jpg"),
            _mfg("2007", "222250", "a0d01f6e-c327-4fb1-b282-d4805b190387.jpg"),
            _mfg("2007", "222250", "a6e3b896-2407-44e2-a4f9-9153ca507e12.jpg"),
            _mfg("2007", "222250", "abfdb578-1109-414c-a3b1-839f10476620.jpg"),
            _mfg("2007", "222250", "d9ea8684-a04c-4aad-b181-eedf9e16715d.jpg"),
            _mfg("2007", "222250", "db2a0d53-d5c0-468c-b56a-0cfd8f12f149.jpg"),
        ],
    },
    "42156": {
        "label": "The Jackson ELS16763D",
        "source_url": "https://www.henlyhomes.com/floor-plan-detail/234947/the-elite-series/the-jackson-els16763d/",
        "manufacturer_id": "3328",
        "plan_id": "234947",
        "photos": [
            _mfg("3328", "234947", "ELS16763D%201.jpg"),
            _mfg("3328", "234947", "ELS16763D%202.jpg"),
            _mfg("3328", "234947", "ELS16763D%203.jpg"),
            _mfg("3328", "234947", "ELS16763D%204.jpg"),
            _mfg("3328", "234947", "ELS16763D%205.jpg"),
            _mfg("3328", "234947", "ELS16763D%206.jpg"),
        ],
    },
    "43942": {
        "label": "The Pt 78 SLT28563D",
        "source_url": "https://www.mobilehomemasters.com/plan/226548/solution/the-pt-78-slt28563d/",
        "manufacturer_id": "2025",
        "plan_id": "226548",
        "photos": [
            _mfg("2025", "226548", "Pt%2078-1.jpg"),
            _mfg("2025", "226548", "Pt%2078-2.jpg"),
            _mfg("2025", "226548", "Pt%2078-3.jpg"),
            _mfg("2025", "226548", "Pt%2078-4.jpg"),
            _mfg("2025", "226548", "Pt%2078-5.jpg"),
            _mfg("2025", "226548", "Pt%2078-6.jpg"),
            _mfg("2025", "226548", "Pt%2078-7.jpg"),
            _mfg("2025", "226548", "Pt%2078-8.jpg"),
            _mfg("2025", "226548", "Pt%2078-9.jpg"),
            _mfg("2025", "226548", "Pt%2078-10.jpg"),
            _mfg("2025", "226548", "Pt%2078-11.jpg"),
            _mfg("2025", "226548", "Pt%2078-12.jpg"),
            _mfg("2025", "226548", "Pt%2078-13.webp"),
        ],
    },
    "28527": {
        "label": "Heritage 1672-32C",
        "source_url": "https://www.countrylivingmodularhomes.com/plan/1383/heritage/h-1672-32c/",
        "manufacturer_id": "1944",
        "plan_id": "1383",
        "photos": [
            _mfg("1944", "1383", "Heritage%20H-1672-32C-ext-1.jpg"),
            _mfg("1944", "1383", "Heritage%20H-1672-32C-ext-2.jpg"),
            _mfg("1944", "1383", "Heritage%20H-1672-32C-ext-3.jpg"),
            _mfg("1944", "1383", "Heritage%20H-1672-32C-kit-1.jpg"),
            _mfg("1944", "1383", "Heritage%20H-1672-32C-kit-2.jpg"),
            _mfg("1944", "1383", "Heritage%20H-1672-32C-kit-3.jpg"),
            _mfg("1944", "1383", "Heritage%20H-1672-32C-kit-4.jpg"),
            _mfg("1944", "1383", "Heritage%20H-1672-32C-kit-5.jpg"),
            _mfg("1944", "1383", "Heritage%20H-1672-32C-int-1.jpg"),
            _mfg("1944", "1383", "Heritage%20H-1672-32C-int-2.jpg"),
            _mfg("1944", "1383", "Heritage%20H-1672-32C-int-3.jpg"),
            _mfg("1944", "1383", "Heritage%20H-1672-32C-int-4.jpg"),
            _mfg("1944", "1383", "Heritage%20H-1672-32C-bed-1.jpg"),
            _mfg("1944", "1383", "Heritage%20H-1672-32C-bed-2.jpg"),
            _mfg("1944", "1383", "Heritage%20H-1672-32C-bed-3.jpg"),
            _mfg("1944", "1383", "Heritage%20H-1672-32C-bed-4.jpg"),
            _mfg("1944", "1383", "Heritage%20H-1672-32C-bath-1.jpg"),
            _mfg("1944", "1383", "Heritage%20H-1672-32C-bath-2.jpg"),
            _mfg("1944", "1383", "Heritage%20H-1672-32C-uti-1.jpg"),
            _mfg("1944", "1383", "Heritage%20H-1672-32C-uti-2.jpg"),
        ],
    },
}


def _legacy_ids(home: dict[str, Any]) -> set[str]:
    return {
        str(value).strip()
        for value in (
            home.get("id"),
            home.get("legacy_inventory_id"),
            home.get("listing_id"),
        )
        if value
    }


def recovery_for_home(home: dict[str, Any]) -> dict[str, Any] | None:
    """Return the exact recovery catalog entry for ``home`` when one exists."""
    for legacy_id in _legacy_ids(home):
        if legacy_id in MANUFACTURER_MEDIA_RECOVERY:
            return deepcopy(MANUFACTURER_MEDIA_RECOVERY[legacy_id])
    return None


def apply_manufacturer_media_recovery(home: dict[str, Any]) -> dict[str, Any]:
    """Fill known floorplan-only gaps with exact manufacturer-plan CDN media."""
    if not isinstance(home, dict):
        return home

    apply_classifier_to_home(home)
    if (home.get("media_quality") or {}).get("has_real_photo"):
        return home

    recovery = recovery_for_home(home)
    if not recovery:
        return home

    photos = list(recovery["photos"])
    existing_floorplan = home.get("floorplan_url") or home.get("floor_plan_url") or ""
    home["real_photos"] = photos
    home["photos"] = photos
    home["gallery_images"] = photos[:3]
    home["image_url"] = photos[0] if photos else ""
    home["hero_image"] = photos[0] if photos else ""
    home["image_categories"] = _categories(photos)
    if existing_floorplan:
        home["floorplan_url"] = existing_floorplan
        home["floor_plan_url"] = existing_floorplan
    apply_classifier_to_home(home)
    home["photos"] = list(home.get("real_photos") or [])
    home["hero_image"] = home.get("image_url", "")

    provenance = {
        "status": "recovered",
        "source": "exact_manufacturer_plan_cdn",
        "source_url": recovery["source_url"],
        "source_owner": "manufacturer_plan_media",
        "manufacturer_id": recovery["manufacturer_id"],
        "plan_id": recovery["plan_id"],
        "output_policy": (
            "Exact model/plan gallery from the same ManufacturedHomes CDN "
            "namespace as the THO legacy listing; no generic stock substitution."
        ),
    }
    home["media_recovery"] = provenance
    source_provenance = dict(home.get("source_provenance") or {})
    source_provenance["media_recovery"] = provenance
    home["source_provenance"] = source_provenance
    media_quality = dict(home.get("media_quality") or {})
    media_quality["recovered"] = True
    media_quality["recovery_source"] = "exact_manufacturer_plan_cdn"
    home["media_quality"] = media_quality
    return home


def apply_manufacturer_media_recovery_to_homes(
    homes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Mutate and return ``homes`` after applying known recovery entries."""
    for home in homes:
        apply_manufacturer_media_recovery(home)
    return homes
