"""Canonical public inventory classifications shared by API and SEO surfaces."""

from __future__ import annotations

import re

_ALIASES = {
    "single wide": "Single Wide",
    "singlewide": "Single Wide",
    "single section": "Single Wide",
    "singlesection": "Single Wide",
    "double wide": "Double Wide",
    "doublewide": "Double Wide",
    "double section": "Double Wide",
    "doublesection": "Double Wide",
    "multi section": "Double Wide",
    "multisection": "Double Wide",
    "park model": "Park Model",
    "parkmodel": "Park Model",
    "manufactured home": "Manufactured Home",
    "manufacturedhome": "Manufactured Home",
}


def normalize_inventory_classification(value: object) -> str:
    """Normalize casing, whitespace, and common separator/name aliases.

    Unknown values remain intact after trimming so the API does not erase a
    future classification merely because this alias table predates it.
    """
    trimmed = str(value or "").strip()
    if not trimmed:
        return ""
    alias_key = re.sub(r"[\s_-]+", " ", trimmed).strip().casefold()
    return _ALIASES.get(alias_key, trimmed)
