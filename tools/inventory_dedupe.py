"""Flag inventory records that look like the same physical home (read-only).

The Firestore ``inventory`` collection is written by several importers that key
documents differently: the legacy-snapshot seeder uses the legacy listing id and
keeps the website title (``"PRE-OWNED / Big Blue"``), while other writers store
the bare model name (``"Big Blue"``) under a different doc id. Website-title
pairs like ``The Razor`` next to ``New Vision / The Razor`` (floorplan-227314)
are the same physical plan under two ids.

This module never writes. ``annotate_possible_duplicates`` marks copies in
place so the staff UI can show each home once and surface a hint. The public
feed calls ``collapse_duplicate_homes`` (same rule, then drop the copies) so
visitors see one card. The surviving card keeps the PRE-OWNED / stocked unit's
identity and only borrows extra photos or a missing floorplan from the catalog
twin. The owner still decides what, if anything, to clean up in the data.
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


def _photo_list(home: dict, key: str = "real_photos") -> list[str]:
    photos = home.get(key)
    if not isinstance(photos, list):
        return []
    return [url for url in photos if isinstance(url, str) and url]


def _photo_count(home: dict) -> int:
    return len(_photo_list(home) or _photo_list(home, "photos"))


def _price_value(home: dict) -> float:
    candidates: list[Any] = [home.get("price_value"), home.get("sale_price")]
    pricing = home.get("pricing")
    if isinstance(pricing, dict):
        candidates.append(pricing.get("price_value"))
    for raw in candidates:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0.0


def is_preowned(home: dict) -> bool:
    if home.get("is_new") is False:
        return True
    kind = str(home.get("inventory_kind") or "").strip().lower()
    if kind == "pre_owned":
        return True
    status = str(home.get("status") or "").strip().lower()
    if "pre" in status and "owned" in status:
        return True
    return bool(re.search(r"pre-?owned", str(home.get("model_name") or ""), re.I))


def is_orderable_new(home: dict) -> bool:
    kind = str(home.get("inventory_kind") or "").strip().lower()
    if kind == "orderable_floorplan":
        return True
    if home.get("is_orderable") is True:
        return True
    return str(home.get("status") or "").strip().lower() == "orderable"


def is_stocked_listing(home: dict) -> bool:
    """True for a real lot/listing id, not a catalog slug."""
    for raw in (
        home.get("id"),
        home.get("stock_number"),
        home.get("legacy_inventory_id"),
        home.get("home_id"),
    ):
        value = str(raw or "").strip()
        if value.isdigit():
            return True
    return False


def offerings_conflict(left: dict, right: dict) -> bool:
    """True when two same-model rows look like different products.

    A stocked PRE-OWNED unit next to an orderable new floorplan is two
    offerings. Two priced listings with different sale prices are too.
    """
    left_price, right_price = _price_value(left), _price_value(right)
    if left_price and right_price and left_price != right_price:
        return True
    if is_preowned(left) and is_orderable_new(right):
        return True
    if is_preowned(right) and is_orderable_new(left):
        return True
    return False


def _join_blocked(left: dict, right: dict) -> bool:
    """True when these two rows must not share a cluster.

    Different serials are different homes. Price and new-vs-used conflicts
    use the same rule as ``offerings_conflict``.
    """
    left_serial, right_serial = _serial(left), _serial(right)
    if left_serial and right_serial and left_serial != right_serial:
        return True
    return offerings_conflict(left, right)


def _has_public_link(home: dict) -> bool:
    return any(
        str(home.get(field) or "").strip() for field in ("detail_url", "quote_url", "source_url")
    )


def _identity_rank(home: dict) -> tuple:
    """Stocked / PRE-OWNED identity wins. Photos are borrowed, not ranked."""
    return (
        0 if is_active_status(home.get("status")) else 1,
        0 if is_stocked_listing(home) else 1,
        0 if is_preowned(home) else 1,
        0 if _serial(home) else 1,
        0 if _has_public_link(home) else 1,
        str(home.get("id") or ""),
    )


def _primary_rank(home: dict) -> tuple:
    """Alias kept so older tests and callers share the identity sort."""
    return _identity_rank(home)


def _is_website_title(model_name: Any) -> bool:
    """True when the stored name still carries a website series / sale prefix."""
    value = str(model_name or "")
    if "/" in value:
        return True
    lowered = value.lower()
    return any(prefix in lowered for prefix in _WEBSITE_PREFIXES)


def _borrow_media(survivor: dict, donor: dict) -> None:
    """Copy extra photos / a missing floorplan onto the stocked unit.

    Never changes title, id, price, condition, status, or listing links.
    """
    survivor_photos = _photo_list(survivor) or _photo_list(survivor, "photos")
    donor_photos = _photo_list(donor) or _photo_list(donor, "photos")
    seen = set(survivor_photos)
    extras: list[str] = []
    for url in donor_photos:
        if url in seen:
            continue
        seen.add(url)
        extras.append(url)
    # Length is not the test: a shorter donor can still hold a photo the
    # survivor does not. Survivor URLs stay first and are never reordered.
    if extras:
        merged = survivor_photos + extras
        survivor["real_photos"] = merged
        if "photos" in survivor or "photos" in donor:
            survivor["photos"] = merged
        survivor["gallery_images"] = merged[:3]
        if not str(survivor.get("image_url") or "").strip() and merged:
            survivor["image_url"] = merged[0]

    for key in ("floor_plan_url", "floorplan_url"):
        if not str(survivor.get(key) or "").strip() and donor.get(key):
            survivor[key] = donor[key]
    if not survivor.get("floorplan_urls"):
        donor_plans = donor.get("floorplan_urls")
        if isinstance(donor_plans, list) and donor_plans:
            survivor["floorplan_urls"] = list(donor_plans)


def _choose_primary(cluster: list[dict]) -> tuple[dict, list[dict]]:
    ordered = sorted(cluster, key=_identity_rank)
    primary, others = ordered[0], ordered[1:]
    for other in others:
        _borrow_media(primary, other)
    return primary, others


def _clusters(group: list[dict]) -> list[list[dict]]:
    """Split one same-model group into likely-same-home clusters.

    A shared model name is not enough: two Nassaus on the lot are two homes.
    We join records that share a serial, or a prefixed website title
    (``PRE-OWNED / Big Blue``) with the single bare name, but only when every
    row in the prospective cluster agrees: no conflicting serials, sale
    prices, or used-vs-orderable mismatch.
    """
    n = len(group)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    by_serial: dict[str, int] = {}
    for i, home in enumerate(group):
        serial = _serial(home)
        if not serial:
            continue
        if serial in by_serial:
            union(by_serial[serial], i)
        else:
            by_serial[serial] = i

    website_idx = [i for i, home in enumerate(group) if _is_website_title(home.get("model_name"))]
    bare_idx = [i for i in range(n) if i not in set(website_idx)]
    # Only attach prefixed titles to a single bare name. Two bare records of
    # the same model stay visible — they may be two physical homes.
    # Each prefixed row has to agree with the whole cluster it would join.
    # Checking only the bare overlay lets two real PRE-OWNED units (different
    # serials or sale prices) both pass an empty overlay and collapse together.
    if len(bare_idx) == 1:
        bare_i = bare_idx[0]
        for web_i in website_idx:
            bare_root, web_root = find(bare_i), find(web_i)
            if bare_root == web_root:
                continue
            bare_members = [i for i in range(n) if find(i) == bare_root]
            web_members = [i for i in range(n) if find(i) == web_root]
            if any(
                _join_blocked(group[left_i], group[right_i])
                for left_i in bare_members
                for right_i in web_members
            ):
                continue
            union(bare_i, web_i)

    buckets: dict[int, list[dict]] = {}
    for i, home in enumerate(group):
        buckets.setdefault(find(i), []).append(home)
    return list(buckets.values())


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
            primary, others = _choose_primary(cluster)
            primary["possible_duplicate_ids"] = [str(h["id"]) for h in others]
            for other in others:
                other["duplicate_of"] = str(primary["id"])
    return homes


def collapse_duplicate_homes(
    homes: list[dict],
    *,
    strip_annotations: bool = False,
) -> list[dict]:
    """Apply the shared rule, then keep one record per duplicated home.

    Staff lists call ``annotate_possible_duplicates`` and hide copies in the
    UI. The public feed calls this so visitors never see the twin. The stocked
    PRE-OWNED unit keeps its identity; extra catalog photos are borrowed.
    """
    annotate_possible_duplicates(homes)
    visible = [home for home in homes if not home.get("duplicate_of")]
    if strip_annotations:
        for home in visible:
            home.pop("possible_duplicate_ids", None)
            home.pop("duplicate_of", None)
    return visible
