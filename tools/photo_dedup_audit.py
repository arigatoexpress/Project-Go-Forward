"""
Photo Deduplication Audit Tool

Read-only audit of inventory photo URLs to surface duplicates across
multiple inventory items and media-quality gaps. Aggregates `image_url`,
`hero_image`, `gallery_images`/`photos`, and `floorplan_url` fields and
reports any URL that appears in 2+ inventory documents. It also normalizes
each listing through the production photo classifier so missing/floorplan-only
media cannot be counted as usable listing photos.

Pure reporter — no mutations. The user reviews the report and decides
which dedupes to apply.

Output formats:
  - dict (default, suitable for the admin endpoint)
  - JSONL (one record per duplicate URL group)
  - CSV (flat: url, count, inventory_ids, model_names)

Usage:
    # Programmatic
    from tools.photo_dedup_audit import run_photo_dedup_audit
    report = run_photo_dedup_audit(inventory)

    # CLI
    python tools/photo_dedup_audit.py --output csv > photo_dups.csv
    python tools/photo_dedup_audit.py --output jsonl > photo_dups.jsonl
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import sys
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)

try:
    from tools.photo_classifier import apply_classifier_to_home
except ImportError:  # pragma: no cover - script/package execution fallback
    from .photo_classifier import apply_classifier_to_home

# Fields on inventory docs that may carry a photo URL. The set is a
# deliberate snapshot of fields used by tools/inventory_tools.py and
# main.py:list_inventory; if new image fields are added, extend here.
SINGLE_URL_FIELDS = ("image_url", "hero_image", "floorplan_url")
LIST_URL_FIELDS = ("gallery_images", "photos", "real_photos")


def _normalize_url(value: Any) -> str | None:
    """Strip whitespace + trailing slash; ignore non-string / empty values."""
    if not value or not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    # Treat http(s) URLs case-insensitively for the scheme/host portion
    # but preserve path casing — most CDNs are case-sensitive on path.
    return cleaned.rstrip("/")


def _iter_photo_urls(item: dict[str, Any]) -> Iterable[tuple[str, str]]:
    """Yield (field_name, normalized_url) tuples for one inventory item."""
    for field in SINGLE_URL_FIELDS:
        url = _normalize_url(item.get(field))
        if url:
            yield field, url

    for field in LIST_URL_FIELDS:
        values = item.get(field)
        if not values:
            continue
        if isinstance(values, str):
            # Defensive: some legacy rows store comma-separated strings
            values = [v.strip() for v in values.split(",")]
        if not isinstance(values, list):
            continue
        for v in values:
            url = _normalize_url(v)
            if url:
                yield field, url


def build_url_index(inventory: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Build {normalized_url: [usage_record, ...]}.

    Each usage_record carries inventory_id, model_name, manufacturer, and
    the field name where the URL was found. A single inventory item that
    repeats the same URL across multiple fields produces multiple records
    for that URL — useful for spotting in-doc redundancy too.
    """
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in inventory:
        if not isinstance(item, dict):
            continue
        inventory_id = item.get("id") or item.get("inventory_id") or item.get("serial_number")
        if not inventory_id:
            continue
        model_name = item.get("model_name") or item.get("model") or ""
        manufacturer = item.get("manufacturer") or ""
        status = item.get("status") or ""
        for field, url in _iter_photo_urls(item):
            index[url].append(
                {
                    "inventory_id": str(inventory_id),
                    "model_name": model_name,
                    "manufacturer": manufacturer,
                    "status": status,
                    "field": field,
                }
            )
    return index


def run_photo_dedup_audit(inventory: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate duplicate-URL groups + summary stats.

    A "duplicate" is any URL referenced by 2 or more *distinct* inventory_ids.
    URLs that only repeat across fields within a single inventory_id are
    not flagged as duplicates here (low-signal noise).
    """
    inventory_list = list(inventory) if not isinstance(inventory, list) else inventory
    index = build_url_index(inventory_list)

    duplicates: list[dict[str, Any]] = []
    cross_doc_dup_urls = 0
    for url, usages in index.items():
        distinct_ids = {u["inventory_id"] for u in usages}
        if len(distinct_ids) < 2:
            continue
        cross_doc_dup_urls += 1
        duplicates.append(
            {
                "url": url,
                "duplicate_count": len(distinct_ids),
                "total_references": len(usages),
                "inventory_ids": sorted(distinct_ids),
                "usages": usages,
            }
        )

    duplicates.sort(key=lambda d: (-d["duplicate_count"], d["url"]))

    quality_gaps: list[dict[str, Any]] = []
    quality_counts = {
        "photo_ready": 0,
        "limited_photos": 0,
        "floorplan_only": 0,
        "missing_photos": 0,
    }
    for item in inventory_list:
        if not isinstance(item, dict):
            continue
        normalized = apply_classifier_to_home(dict(item))
        quality = normalized.get("media_quality") or {}
        status = quality.get("status") or "missing_photos"
        if status not in quality_counts:
            quality_counts[status] = 0
        quality_counts[status] += 1
        if status in {"missing_photos", "floorplan_only", "limited_photos"}:
            quality_gaps.append(
                {
                    "inventory_id": str(
                        item.get("id")
                        or item.get("inventory_id")
                        or item.get("serial_number")
                        or ""
                    ),
                    "model_name": item.get("model_name") or item.get("model") or "",
                    "manufacturer": item.get("manufacturer") or "",
                    "status": item.get("status") or "",
                    "media_status": status,
                    "photo_count": quality.get("photo_count", 0),
                    "floorplan_count": quality.get("floorplan_count", 0),
                    "has_matterport": bool(item.get("matterport_url") or item.get("matterport_id")),
                    "issues": quality.get("issues", []),
                }
            )

    summary = {
        "inventory_scanned": len(inventory_list),
        "unique_urls": len(index),
        "duplicate_urls": cross_doc_dup_urls,
        # Items that appear in at least one duplicate group
        "items_affected": len({u["inventory_id"] for group in duplicates for u in group["usages"]}),
        "photo_ready": quality_counts.get("photo_ready", 0),
        "limited_photos": quality_counts.get("limited_photos", 0),
        "floorplan_only": quality_counts.get("floorplan_only", 0),
        "missing_photos": quality_counts.get("missing_photos", 0),
    }
    quality_gaps.sort(key=lambda item: (item["media_status"], item["model_name"]))
    return {"summary": summary, "duplicates": duplicates, "media_quality_gaps": quality_gaps}


def to_csv(report: dict[str, Any]) -> str:
    """Render the audit report as flat CSV for spreadsheet review."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "url",
            "duplicate_count",
            "total_references",
            "inventory_ids",
            "model_names",
        ]
    )
    for group in report["duplicates"]:
        model_names = sorted({u["model_name"] for u in group["usages"] if u["model_name"]})
        writer.writerow(
            [
                group["url"],
                group["duplicate_count"],
                group["total_references"],
                "|".join(group["inventory_ids"]),
                "|".join(model_names),
            ]
        )
    return buf.getvalue()


def to_jsonl(report: dict[str, Any]) -> str:
    """Render the audit report as JSONL — one record per duplicate group."""
    lines = [json.dumps(group, sort_keys=True) for group in report["duplicates"]]
    return "\n".join(lines) + ("\n" if lines else "")


def _fetch_inventory_from_firestore(limit: int = 5000) -> list[dict[str, Any]]:
    """Pull inventory documents from Firestore. Lazy import for testability."""
    from database.firestore_client import get_database  # noqa: WPS433

    db = get_database()
    docs = db.db.collection("inventory").limit(limit).stream()
    items: list[dict[str, Any]] = []
    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        items.append(data)
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description="Photo dedup audit (read-only)")
    parser.add_argument(
        "--output",
        choices=("dict", "json", "jsonl", "csv"),
        default="json",
        help="Output format",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="Max inventory docs to scan",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    inventory = _fetch_inventory_from_firestore(limit=args.limit)
    logger.info("Scanning %d inventory items", len(inventory))
    report = run_photo_dedup_audit(inventory)

    if args.output == "csv":
        sys.stdout.write(to_csv(report))
    elif args.output == "jsonl":
        sys.stdout.write(to_jsonl(report))
    else:
        sys.stdout.write(json.dumps(report, indent=2, sort_keys=True))
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
