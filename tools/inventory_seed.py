"""Seed the in-app (Firestore) inventory store from the legacy snapshot.

THO's public inventory page historically rendered the *legacy snapshot*
(``data/legacy_site/legacy_inventory_context.json``), captured by scraping
``texashomeoutlet.com/inventory``. The 2026 domain cutover repointed that URL
to the new app, so the live refresh can no longer reach a real source and the
snapshot is frozen (see ``tools/legacy_site_crawler.py`` + PR #192).

The fix is to make inventory *staff-managed in-app*: homes live in the Firestore
``inventory`` collection (which ``THODatabase`` already reads via
``search_inventory``) and the public endpoint serves them. This module seeds
that collection from the existing snapshot so staff inherit the current 279
homes to edit/retire rather than starting from an empty store.

The mapping here is the deliberate inverse of
``tools.inventory_tools._load_inventory_from_firestore`` — a home seeded by this
module renders identically when read back through the public Firestore path, so
seeding is display-preserving.

Idempotent: the legacy id is used as the Firestore document id, so re-running
updates in place instead of duplicating. Dry-run by default; pass ``--apply``
(or ``dry_run=False``) to write.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

# Marks docs created by this seeder so they can be told apart from homes a
# staff member created directly in-app.
SEED_SOURCE = "legacy_snapshot_seed"


def _to_int(value: Any) -> int | None:
    """Coerce to a positive int, or None for missing/zero/non-numeric."""
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return int(number)


def _to_number(value: Any) -> float | int | None:
    """Coerce to a positive number (keeps halves like 2.5 baths), else None."""
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return int(number) if number.is_integer() else number


def _is_new(home: dict) -> bool:
    """Infer new vs pre-owned from the snapshot's status / marketing tags.

    Defaults to new unless the home is explicitly flagged pre-owned, mirroring
    how the read path derives "Pre-Owned" status.
    """
    status = str(home.get("status") or "").lower()
    if "pre-owned" in status or "preowned" in status or "used" in status:
        return False
    tags = [str(t).lower() for t in (home.get("marketing_tags") or [])]
    if any("pre-owned" in t or "preowned" in t or "used" in t for t in tags):
        return False
    return True


def _legacy_id(home: dict) -> str:
    """Stable id for the home (used as the Firestore doc id for idempotency)."""
    return str(home.get("legacy_inventory_id") or home.get("id") or "").strip()


def snapshot_home_to_inventory_doc(home: dict) -> dict:
    """Map one legacy-snapshot home to a Firestore ``inventory`` document.

    The output keys match what ``_load_inventory_from_firestore`` reads
    (``bedrooms``/``bathrooms``/``sqft``/``width``/``length``/``sale_price``/
    ``photos``/``floorplan_url``/...), so seeded homes survive the round trip to
    the public API unchanged.
    """
    specs = home.get("specs") or {}
    pricing = home.get("pricing") or {}
    price_value = _to_int(home.get("price_value") or pricing.get("price_value")) or 0
    photos = home.get("real_photos") or home.get("gallery_images") or home.get("photos") or []

    return {
        "legacy_inventory_id": _legacy_id(home),
        "serial_number": str(home.get("serial_number") or ""),
        "manufacturer": home.get("manufacturer") or "",
        "model_name": home.get("model_name") or "",
        "classification": home.get("classification") or "",
        # Firestore inventory uses an uppercase status enum; search_inventory
        # filters status == "AVAILABLE".
        "status": "AVAILABLE",
        "is_new": _is_new(home),
        "bedrooms": _to_int(specs.get("beds") or specs.get("bedrooms")),
        "bathrooms": _to_number(specs.get("baths") or specs.get("bathrooms")),
        "sqft": _to_int(specs.get("sq_ft") or specs.get("sqft")),
        "width": _to_int(specs.get("width")),
        "length": _to_int(specs.get("length")),
        "sale_price": price_value,
        "msrp": price_value,
        "features": list(home.get("features") or []),
        "marketing_tags": list(home.get("marketing_tags") or []),
        "image_url": home.get("image_url") or home.get("hero_image") or "",
        "gallery_images": list(photos),
        "photos": list(photos),
        "image_categories": dict(home.get("image_categories") or {}),
        "floorplan_url": home.get("floorplan_url") or home.get("floor_plan_url"),
        "matterport_id": home.get("matterport_id"),
        "description": home.get("description") or "",
        "detail_url": home.get("detail_url") or "",
        "source": SEED_SOURCE,
    }


def load_snapshot_homes(loader: Callable[..., dict] | None = None, *, limit: int = 1000) -> list[dict]:
    """Load homes from the legacy snapshot via the crawler's cached loader."""
    if loader is None:
        from tools.legacy_site_crawler import load_legacy_inventory_context

        loader = load_legacy_inventory_context
    context = loader(limit=limit) or {}
    return list(context.get("homes") or [])


def seed_inventory_from_snapshot(
    db: Any,
    homes: list[dict],
    *,
    dry_run: bool = True,
    limit: int | None = None,
) -> dict:
    """Upsert snapshot homes into the Firestore ``inventory`` collection.

    Uses the legacy id as the doc id (idempotent). ``db`` must provide
    ``upsert_inventory(doc_id, data)``. Returns a stats dict; on ``dry_run`` no
    writes happen and ``planned`` lists the (id, model_name) that would be
    written.
    """
    stats: dict[str, Any] = {"total": 0, "written": 0, "skipped_no_id": 0, "planned": []}
    for home in homes[: limit if limit else None]:
        stats["total"] += 1
        doc_id = _legacy_id(home)
        if not doc_id:
            stats["skipped_no_id"] += 1
            log.warning("Skipping snapshot home with no legacy id: model=%s", home.get("model_name"))
            continue
        doc = snapshot_home_to_inventory_doc(home)
        stats["planned"].append((doc_id, doc["model_name"]))
        if dry_run:
            continue
        db.upsert_inventory(doc_id, doc)
        stats["written"] += 1
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed Firestore inventory from the legacy snapshot.")
    parser.add_argument("--apply", action="store_true", help="Write to Firestore (default: dry run).")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N homes.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    homes = load_snapshot_homes()
    db = None
    if args.apply:
        from database.firestore_client import get_database

        db = get_database()

    stats = seed_inventory_from_snapshot(db, homes, dry_run=not args.apply, limit=args.limit)
    mode = "APPLIED" if args.apply else "DRY-RUN"
    print(f"[{mode}] snapshot homes: {stats['total']} | written: {stats['written']} "
          f"| skipped (no id): {stats['skipped_no_id']}")
    if not args.apply:
        for doc_id, model in stats["planned"][:25]:
            print(f"  would upsert {doc_id}: {model}")
        if len(stats["planned"]) > 25:
            print(f"  ... and {len(stats['planned']) - 25} more")
        print("\nRe-run with --apply to write to Firestore.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
