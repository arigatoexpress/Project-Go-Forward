"""Flag inventory records that look like the same physical home (read-only).

The Firestore ``inventory`` collection is written by several importers that key
documents differently: the legacy-snapshot seeder uses the legacy listing id and
keeps the website title (``"PRE-OWNED / Big Blue"``), while other writers store
the bare model name (``"Big Blue"``) under a different doc id. The staff list
read every document as-is, so one home showed twice.

This module never writes. It annotates a list of homes so the staff UI can show
each home once and surface a "possible duplicate" hint with the record ids, and
the owner decides what (if anything) to clean up in the data.
"""

from __future__ import annotations

import re
from typing import Any

INACTIVE_STATUSES = frozenset({"SOLD", "RETIRED", "ARCHIVED"})

_WEBSITE_PREFIXES = (
    "new year clearance sale",
    "pre-owned",
    "preowned",
    "tru single section",
    "tru multi section",
)


def normalize_model_key(model_name: Any) -> str:
    """Reduce a model title to the part that identifies the floor plan.

    Mirrors ``tools.inventory_sync.InventorySync._normalize_model_key`` so the
    staff list and the importer agree on what counts as the same model.
    """
    value = str(model_name or "").lower()
    if "/" in value:
        value = value.split("/")[-1]
    for prefix in _WEBSITE_PREFIXES:
        value = value.replace(prefix, "")
    value = re.sub(r"\b(fac|els|slt|cee|tru|sap)\d+[a-z0-9]*\b", "", value)
    value = re.sub(r"\b\d{4}h\d+\b", "", value)
    value = re.sub(r"[^a-z0-9]+", " ", value).strip()
    if value.startswith("the "):
        value = value[4:]
    return value


def is_active_status(status: Any) -> bool:
    return str(status or "AVAILABLE").strip().upper() not in INACTIVE_STATUSES


def _serial(home: dict) -> str:
    return str(home.get("serial_number") or "").strip().upper()


def _photo_count(home: dict) -> int:
    photos = home.get("real_photos")
    return len(photos) if isinstance(photos, list) else 0


def _primary_rank(home: dict) -> tuple:
    """Sort key: the record staff should see first for a duplicated home."""
    name = str(home.get("model_name") or "")
    return (
        0 if is_active_status(home.get("status")) else 1,
        0 if _serial(home) else 1,
        -_photo_count(home),
        1 if "/" in name else 0,
        str(home.get("id") or ""),
    )


def _clusters(group: list[dict]) -> list[list[dict]]:
    """Split one same-model group into likely-same-home clusters.

    Two different serial numbers mean two different homes, so with more than
    one distinct serial only records sharing a serial are clustered; records
    without a serial are then left alone rather than guessed at.
    """
    serials = {_serial(h) for h in group if _serial(h)}
    if len(serials) <= 1:
        return [group]
    by_serial: dict[str, list[dict]] = {}
    singles: list[list[dict]] = []
    for home in group:
        serial = _serial(home)
        if serial:
            by_serial.setdefault(serial, []).append(home)
        else:
            singles.append([home])
    return list(by_serial.values()) + singles


def annotate_possible_duplicates(homes: list[dict]) -> list[dict]:
    """Mark likely-duplicate records in place and return the same list.

    The chosen record of each cluster gets ``possible_duplicate_ids`` (the other
    record ids); every other record gets ``duplicate_of`` (the chosen id).
    Records that are not duplicated are left untouched.
    """
    groups: dict[str, list[dict]] = {}
    for home in homes:
        key = normalize_model_key(home.get("model_name"))
        if key and home.get("id") not in (None, ""):
            groups.setdefault(key, []).append(home)

    for group in groups.values():
        if len(group) < 2:
            continue
        for cluster in _clusters(group):
            if len(cluster) < 2:
                continue
            ordered = sorted(cluster, key=_primary_rank)
            primary, others = ordered[0], ordered[1:]
            primary["possible_duplicate_ids"] = [str(h["id"]) for h in others]
            for other in others:
                other["duplicate_of"] = str(primary["id"])
    return homes
