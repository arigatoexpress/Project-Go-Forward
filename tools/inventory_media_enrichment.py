"""
Safely enrich THO production inventory media fields.

This operator script reads current Firestore inventory docs, scrapes the
public legacy inventory detail pages for richer media, writes a local backup,
and optionally updates only allow-listed inventory media fields.

It intentionally does not import customer/deal data.

Usage:
    python tools/inventory_media_enrichment.py --dry-run
    python tools/inventory_media_enrichment.py --apply
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.inventory_sync import InventorySync  # noqa: E402

try:
    from tools.asset_scraper import get_matterport_url
except ImportError:  # pragma: no cover
    from .asset_scraper import get_matterport_url

try:
    from tools.photo_classifier import apply_classifier_to_home, is_floorplan_url
except ImportError:  # pragma: no cover
    from .photo_classifier import apply_classifier_to_home, is_floorplan_url

try:
    from google.cloud import firestore
except ImportError as exc:  # pragma: no cover
    raise SystemExit("google-cloud-firestore is required for production reads/writes") from exc

LOG = logging.getLogger("inventory_media_enrichment")

CDN_RE = re.compile(r"https://d132mt2yijm03y\.cloudfront\.net/[^\s\"'<>),]+")
MATTERPORT_RE = re.compile(r"https://my\.matterport\.com/show/\?[^\s\"'<>]+")
MEDIA_FIELDS = {
    "image_url",
    "hero_image",
    "photos",
    "gallery_images",
    "real_photos",
    "image_categories",
    "floorplan_url",
    "floor_plan_url",
    "matterport_id",
    "matterport_url",
    "detail_url",
    "source_url",
    "media_source",
    "floorplan_urls",
    "media_quality",
    "last_media_synced",
}
CLEARABLE_MEDIA_FIELDS = {
    "image_url",
    "hero_image",
    "photos",
    "gallery_images",
    "real_photos",
    "image_categories",
}


@dataclass
class DetailMedia:
    listing_id: str
    detail_url: str
    photos: list[str] = field(default_factory=list)
    floorplan_url: str | None = None
    matterport_id: str | None = None
    image_categories: dict[str, list[str]] = field(default_factory=dict)

    @property
    def matterport_url(self) -> str | None:
        return get_matterport_url(self.matterport_id) if self.matterport_id else None


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        value = normalize_media_url(raw)
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def normalize_media_url(url: str | None) -> str:
    """Normalize harmless thumbnail URL variants to their full-size sibling."""
    if not url:
        return ""
    value = str(url).strip().rstrip(").,")
    value = value.replace("&amp;", "&")
    value = re.sub(r"_thumb_xl(?=\.[a-zA-Z]{3,4}(?:$|\?))", "", value)
    return value


def _filename(url: str) -> str:
    return urlparse(url).path.rsplit("/", 1)[-1]


def _is_dealer_inventory_url(url: str, listing_id: str | None = None) -> bool:
    marker = "/dealer/3522/inventory/"
    if marker not in url:
        return False
    return not listing_id or f"{marker}{listing_id}/" in url


def _is_floorplan_url(url: str) -> bool:
    return is_floorplan_url(url)


def _manufacturer_floorplan_prefix(url: str) -> str | None:
    match = re.search(r"(/manufacturer/[^/]+/floorplan/[^/]+/)", urlparse(url).path)
    if not match:
        return None
    return match.group(1)


def _model_match_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"\b(pre[-\s]?owned|new|model|manufactured)\b", " ", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def categorize_photo_url(url: str) -> str:
    lower = _filename(url).lower()
    if "ext" in lower or "exterior" in lower:
        return "exterior"
    if "kit" in lower or "kitchen" in lower:
        return "kitchen"
    if "bath" in lower:
        return "bathroom"
    if "bed" in lower:
        return "bedroom"
    if "uti" in lower or "utility" in lower or "laundry" in lower:
        return "utility"
    return "interior"


def order_photo_urls(urls: list[str]) -> list[str]:
    priority = {
        "exterior": 0,
        "kitchen": 1,
        "interior": 2,
        "bedroom": 3,
        "bathroom": 4,
        "utility": 5,
    }
    deduped = _dedupe(urls)
    return sorted(
        deduped,
        key=lambda value: (priority.get(categorize_photo_url(value), 9), deduped.index(value)),
    )


def _categories_for_photos(photos: list[str]) -> dict[str, list[str]]:
    categories: dict[str, list[str]] = {}
    for url in photos:
        categories.setdefault(categorize_photo_url(url), []).append(_filename(url))
    return categories


def _extract_matterport_id(url: str) -> str | None:
    parsed = urlparse(url)
    ids = parse_qs(parsed.query).get("m", [])
    return ids[0] if ids else None


def extract_detail_media(html: str, listing_id: str, detail_url: str) -> DetailMedia:
    """Extract full-size property photos, floorplan, and Matterport id from a detail page."""
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []

    for tag in soup.find_all(["a", "img", "source"]):
        for attr in ("href", "src", "data-src", "data-original"):
            value = tag.get(attr)
            if value and "d132mt2yijm03y.cloudfront.net" in value:
                urls.append(value)

    urls.extend(CDN_RE.findall(html))
    urls = _dedupe(urls)

    floorplans = [url for url in urls if _is_floorplan_url(url)]
    dealer_photos = [
        url
        for url in urls
        if _is_dealer_inventory_url(url, listing_id) and not _is_floorplan_url(url)
    ]
    manufacturer_photos = [
        url for url in urls if "/manufacturer/" in url and not _is_floorplan_url(url)
    ]
    if floorplans and manufacturer_photos:
        allowed_prefixes = {
            prefix
            for prefix in (_manufacturer_floorplan_prefix(url) for url in floorplans)
            if prefix
        }
        if allowed_prefixes:
            manufacturer_photos = [
                url
                for url in manufacturer_photos
                if any(prefix in urlparse(url).path for prefix in allowed_prefixes)
            ]
    if not floorplans and dealer_photos:
        # Legacy detail pages often link the floorplan as a manufacturer CDN
        # image whose filename is just the model name, while dealer photos live
        # under /dealer/3522/inventory/<id>/.
        floorplans = [url for url in urls if "/manufacturer/" in url]

    photos = order_photo_urls(dealer_photos or manufacturer_photos)

    matterport_id = None
    for url in MATTERPORT_RE.findall(html):
        matterport_id = _extract_matterport_id(url)
        if matterport_id:
            break

    return DetailMedia(
        listing_id=listing_id,
        detail_url=detail_url,
        photos=photos,
        floorplan_url=floorplans[0] if floorplans else None,
        matterport_id=matterport_id,
        image_categories=_categories_for_photos(photos),
    )


def scrape_detail_media(raw_homes: list[dict[str, Any]]) -> dict[str, DetailMedia]:
    session = requests.Session()
    session.headers.update(
        {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    )
    media: dict[str, DetailMedia] = {}
    for home in raw_homes:
        listing_id = str(home.get("id") or "").strip()
        detail_url = str(home.get("detail_url") or "").strip()
        if not listing_id or not detail_url:
            continue
        try:
            response = session.get(detail_url, timeout=20)
            response.raise_for_status()
            media[listing_id] = extract_detail_media(response.text, listing_id, response.url)
            LOG.info(
                "scraped detail media id=%s photos=%d floorplan=%s matterport=%s",
                listing_id,
                len(media[listing_id].photos),
                bool(media[listing_id].floorplan_url),
                bool(media[listing_id].matterport_id),
            )
        except Exception as exc:
            LOG.warning("failed detail scrape id=%s url=%s error=%s", listing_id, detail_url, exc)
    return media


def _detail_media_by_model(
    raw_homes: list[dict[str, Any]],
    detail_media: dict[str, DetailMedia],
) -> dict[str, DetailMedia]:
    candidates: dict[str, list[DetailMedia]] = {}
    for home in raw_homes:
        listing_id = str(home.get("id") or "").strip()
        detail = detail_media.get(listing_id)
        if not detail:
            continue
        key = _model_match_key(home.get("model_name"))
        if key:
            candidates.setdefault(key, []).append(detail)
    return {
        key: values[0]
        for key, values in candidates.items()
        if len({value.listing_id for value in values}) == 1
    }


def _select_detail_media(
    doc_id: str,
    current: dict[str, Any],
    detail_media: dict[str, DetailMedia],
    detail_by_model: dict[str, DetailMedia],
) -> DetailMedia | None:
    for value in (doc_id, current.get("legacy_inventory_id")):
        key = str(value or "").strip()
        if key and key in detail_media:
            return detail_media[key]
    for value in (current.get("model_name"), current.get("name"), current.get("title")):
        key = _model_match_key(value)
        if key and key in detail_by_model:
            return detail_by_model[key]
    return None


def _canonical_matterport_id(*values: Any) -> str | None:
    for value in values:
        if not value:
            continue
        text = str(value)
        if "my.matterport.com" in text:
            extracted = _extract_matterport_id(text)
            if extracted:
                return extracted
        return text
    return None


def build_update(
    doc_id: str, current: dict[str, Any], detail: DetailMedia | None
) -> dict[str, Any]:
    classified_current = apply_classifier_to_home(dict(current))
    existing_photos = classified_current.get("real_photos") or []

    detail_photos = detail.photos if detail else []
    photos = detail_photos if len(detail_photos) >= len(existing_photos) else existing_photos
    photos = order_photo_urls([str(url) for url in photos if url])

    floorplan_url = (
        (detail.floorplan_url if detail else None)
        or classified_current.get("floorplan_url")
        or classified_current.get("floor_plan_url")
    )
    matterport_id = _canonical_matterport_id(
        detail.matterport_id if detail else None,
        current.get("matterport_id"),
        current.get("matterport_url"),
    )
    matterport_url = (
        get_matterport_url(matterport_id) if matterport_id else current.get("matterport_url")
    )
    hero = photos[0] if photos else classified_current.get("image_url", "")
    image_categories = (
        _categories_for_photos(photos) or classified_current.get("image_categories") or {}
    )

    classified_update = apply_classifier_to_home(
        {
            "image_url": hero,
            "hero_image": hero,
            "real_photos": photos,
            "gallery_images": photos[:3],
            "image_categories": image_categories,
            "floorplan_url": floorplan_url,
            "floor_plan_url": floorplan_url,
        }
    )

    update = {
        "photos": classified_update["real_photos"],
        "real_photos": classified_update["real_photos"],
        "gallery_images": classified_update["gallery_images"],
        "image_categories": classified_update["image_categories"],
        "image_url": classified_update["image_url"],
        "hero_image": classified_update["image_url"],
        "floorplan_url": classified_update["floorplan_url"],
        "floor_plan_url": classified_update["floor_plan_url"],
        "floorplan_urls": classified_update["floorplan_urls"],
        "media_quality": classified_update["media_quality"],
        "matterport_id": matterport_id,
        "matterport_url": matterport_url,
        "detail_url": (detail.detail_url if detail else current.get("detail_url")),
        "source_url": (detail.detail_url if detail else current.get("source_url")),
        "media_source": "texashomeoutlet.com media enrichment",
        "last_media_synced": datetime.now(UTC).isoformat(),
    }
    return {
        key: value
        for key, value in update.items()
        if key in MEDIA_FIELDS and (value not in (None, "") or key in CLEARABLE_MEDIA_FIELDS)
    }


def _changed_fields(current: dict[str, Any], update: dict[str, Any]) -> list[str]:
    changed = []
    for key, value in update.items():
        if current.get(key) != value:
            changed.append(key)
    return changed


def _prune_timestamp_only_update(
    current: dict[str, Any], update: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    changed = _changed_fields(current, update)
    if changed == ["last_media_synced"]:
        update = {key: value for key, value in update.items() if key != "last_media_synced"}
        changed = _changed_fields(current, update)
    return update, changed


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str) + "\n")


def _write_plan_csv(path: Path, plan: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "id",
                "model_name",
                "photo_count_before",
                "photo_count_after",
                "matched_listing_id",
                "dealer_detail_scraped",
                "detail_url",
                "matterport_after",
                "changed_fields",
            ],
        )
        writer.writeheader()
        for row in plan:
            writer.writerow(row)


def run(
    project: str,
    output_dir: Path,
    apply: bool,
    only_ids: set[str] | None = None,
) -> dict[str, Any]:
    mode = "APPLY" if apply else "DRY_RUN"
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir.mkdir(parents=True, exist_ok=True)

    LOG.info("scraping public inventory cards")
    raw_homes = InventorySync(dry_run=True).scrape_website_inventory()
    detail_media = scrape_detail_media(raw_homes)
    detail_by_model = _detail_media_by_model(raw_homes, detail_media)

    client = firestore.Client(project=project)
    docs = list(client.collection("inventory").limit(1000).stream())
    backup = []
    plan: list[dict[str, Any]] = []
    updates: list[tuple[str, dict[str, Any], list[str]]] = []

    for doc in docs:
        current = doc.to_dict() or {}
        current["id"] = doc.id
        backup.append(current)
        if only_ids and doc.id not in only_ids:
            continue
        detail = _select_detail_media(doc.id, current, detail_media, detail_by_model)
        update = build_update(doc.id, current, detail)
        update, changed = _prune_timestamp_only_update(current, update)
        before_photos = apply_classifier_to_home(dict(current)).get("real_photos") or []
        after_photos = update.get("photos") or []
        if changed:
            updates.append((doc.id, update, changed))
        plan.append(
            {
                "id": doc.id,
                "model_name": current.get("model_name", ""),
                "photo_count_before": len(before_photos) if isinstance(before_photos, list) else 1,
                "photo_count_after": len(after_photos),
                "matched_listing_id": detail.listing_id if detail else "",
                "dealer_detail_scraped": bool(
                    detail
                    and detail.photos
                    and any(
                        _is_dealer_inventory_url(url, detail.listing_id) for url in detail.photos
                    )
                ),
                "detail_url": detail.detail_url if detail else "",
                "matterport_after": bool(
                    update.get("matterport_id") or update.get("matterport_url")
                ),
                "changed_fields": ", ".join(changed),
            }
        )

    backup_path = output_dir / f"firestore_inventory_media_backup_{timestamp}.json"
    plan_path = output_dir / f"inventory_media_enrichment_plan_{timestamp}.json"
    csv_path = output_dir / f"inventory_media_enrichment_plan_{timestamp}.csv"
    summary_path = output_dir / f"inventory_media_enrichment_summary_{timestamp}.json"

    _write_json(backup_path, backup)
    _write_json(
        plan_path,
        {
            "mode": mode,
            "project": project,
            "generated_at": timestamp,
            "doc_count": len(docs),
            "scraped_detail_count": len(detail_media),
            "update_count": len(updates),
            "plan": plan,
        },
    )
    _write_plan_csv(csv_path, plan)

    applied = 0
    if apply:
        for doc_id, update, _changed in updates:
            client.collection("inventory").document(doc_id).update(update)
            applied += 1

    summary = {
        "mode": mode,
        "project": project,
        "doc_count": len(docs),
        "scraped_detail_count": len(detail_media),
        "update_count": len(updates),
        "applied": applied,
        "backup_path": str(backup_path),
        "plan_path": str(plan_path),
        "csv_path": str(csv_path),
        "summary_path": str(summary_path),
    }
    _write_json(summary_path, summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safely enrich THO inventory media fields.")
    parser.add_argument("--project", default=os.environ.get("GOOGLE_CLOUD_PROJECT", "tho-ai-agent"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/Users/aribs/Documents/Cowork/tho-cutover-2026-05-06/private"),
    )
    parser.add_argument("--dry-run", action="store_true", help="Plan only; do not write Firestore")
    parser.add_argument("--apply", action="store_true", help="Apply allow-listed media updates")
    parser.add_argument(
        "--only-id",
        action="append",
        default=[],
        help="Limit the plan/apply pass to one Firestore inventory document id; repeatable.",
    )
    parser.add_argument("-v", "--verbose", action="count", default=0)
    args = parser.parse_args(argv)

    if args.dry_run and args.apply:
        parser.error("Choose either --dry-run or --apply, not both.")
    if not args.dry_run and not args.apply:
        args.dry_run = True

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    summary = run(
        project=args.project,
        output_dir=args.output_dir,
        apply=args.apply,
        only_ids=set(args.only_id) if args.only_id else None,
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
