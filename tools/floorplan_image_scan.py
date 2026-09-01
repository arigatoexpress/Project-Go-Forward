"""Content-based floorplan detection for listing photos.

``tools.photo_classifier`` decides floorplan-vs-photo from the URL: filename
tokens plus the manufacturer CDN namespace. That is fast, pure, and right most
of the time — but it can only reject what it RECOGNIZES, so anything unfamiliar
defaults to "this is a photo". Two real sources exploit that gap:

* seeded heroes at ``tho-inventory-assets/inventory/<id>/hero.jpg`` — outside
  the manufacturer floorplan namespace and named ``hero``; and
* bare names like ``1.jpg`` inside that namespace, which
  ``_looks_like_photo_filename`` accepts because ``/^\\d+$/`` reads as a photo
  index.

Verified on production 2026-08-31: the site's FEATURED home (28102) and the card
image for 28527 (``PRE-OWNED / Heritage 1672-32C``) were both floorplan line
drawings shown as the home's picture.

The pixels settle what the filename cannot. A floorplan diagram is a line
drawing: mostly white, and effectively grayscale. A photograph of a home is
neither. This module measures that offline and writes a manifest of URLs known
to be drawings; ``photo_classifier`` consults the manifest so the request path
stays pure and never touches the network.

Scan (writes ``data/floorplan_image_manifest.json``)::

    python3 -m tools.floorplan_image_scan --source https://texashomeoutlet.com
    python3 -m tools.floorplan_image_scan --source ./inventory.json --dry-run

Re-runnable and idempotent: rescanning rewrites the manifest from scratch.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# A pixel counts as "white" above this on every channel. Floorplan diagrams are
# drawn on paper-white; photographs rarely are for most of the frame.
WHITE_LEVEL = 235
# Fraction of the frame that must be white, and the maximum mean saturation, for
# an image to read as a line drawing. Calibrated against the live catalog: the
# 33 confirmed drawings measured saturation <= 0.002 and white >= 0.58, while
# real interior photos measured saturation >= 0.13.  The gap is wide, so these
# thresholds sit comfortably between the two populations rather than on a knife
# edge.
MIN_WHITE_RATIO = 0.45
MAX_SATURATION = 0.10
# Downscale before measuring: the statistics are scale-invariant and this keeps
# a full-catalog scan cheap.
SAMPLE_BOX = (160, 160)

DEFAULT_MANIFEST = Path(__file__).resolve().parent.parent / "data" / "floorplan_image_manifest.json"
INVENTORY_PATH = "/api/marketing/inventory-context"
PHOTO_FIELDS = ("image_url", "hero_image", "real_photos", "gallery_images", "photos")


def analyze_image_bytes(data: bytes | None) -> dict[str, float] | None:
    """Return white-ratio/saturation stats, or ``None`` if unreadable.

    ``None`` is the fail-OPEN signal: a fetch error or corrupt payload must
    never let us hide a real photo. Only a positively measured drawing is
    ever flagged.
    """
    if not data:
        return None
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow is a hard dep in prod
        log.warning("Pillow unavailable; content-based floorplan scan skipped")
        return None
    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
        image.thumbnail(SAMPLE_BOX)
        pixels = list(image.getdata())
    except Exception as exc:
        log.debug("unreadable image: %s", exc)
        return None
    if not pixels:
        return None
    total = len(pixels)
    white = sum(1 for p in pixels if p[0] > WHITE_LEVEL and p[1] > WHITE_LEVEL and p[2] > WHITE_LEVEL)
    saturation = sum((max(p) - min(p)) / 255 for p in pixels) / total
    return {"white_ratio": white / total, "saturation": saturation}


def is_drawing(stats: dict[str, float] | None) -> bool:
    """True only when the pixels positively say "line drawing"."""
    if not stats:
        return False
    return stats["white_ratio"] > MIN_WHITE_RATIO and stats["saturation"] < MAX_SATURATION


def _fetch(url: str, timeout: int = 20) -> bytes | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as response:
            return response.read()
    except Exception as exc:
        log.debug("fetch failed %s: %s", url, exc)
        return None


def collect_photo_urls(homes: Iterable[dict[str, Any]]) -> list[str]:
    """Every candidate listing photo URL, de-duplicated, order preserved.

    Known floorplan fields are deliberately NOT scanned — they are already
    classified, and re-flagging them adds nothing.
    """
    seen: dict[str, None] = {}
    for home in homes:
        if not isinstance(home, dict):
            continue
        for field in PHOTO_FIELDS:
            value = home.get(field)
            values = value if isinstance(value, list) else [value]
            for url in values:
                if isinstance(url, str) and url.strip():
                    seen.setdefault(url.strip(), None)
    return list(seen)


def build_manifest(
    urls: Sequence[str],
    fetch: Callable[[str], bytes | None] = _fetch,
    out_path: Path | None = None,
    max_workers: int = 12,
) -> dict[str, Any]:
    """Measure every URL and record the ones that are line drawings."""
    drawings: list[str] = []
    unreadable = 0

    def classify(url: str) -> tuple[str, dict[str, float] | None]:
        return url, analyze_image_bytes(fetch(url))

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for url, stats in pool.map(classify, urls):
            if stats is None:
                unreadable += 1
            elif is_drawing(stats):
                drawings.append(url)

    manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "scanned": len(urls),
        "unreadable": unreadable,
        "urls": sorted(drawings),
    }
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def _load_homes(source: str) -> list[dict[str, Any]]:
    if source.startswith(("http://", "https://")):
        url = source.rstrip("/") + INVENTORY_PATH
        with urllib.request.urlopen(url, timeout=60) as response:
            payload = json.load(response)
    else:
        payload = json.loads(Path(source).read_text())
    homes = payload.get("homes") if isinstance(payload, dict) else payload
    return [h for h in (homes or []) if isinstance(h, dict)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--source",
        default="https://texashomeoutlet.com",
        help="Site origin to read inventory from, or a local JSON file.",
    )
    parser.add_argument("--out", default=str(DEFAULT_MANIFEST), help="Manifest path to write.")
    parser.add_argument("--dry-run", action="store_true", help="Report findings without writing.")
    parser.add_argument("--limit", type=int, default=None, help="Only scan the first N URLs.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    homes = _load_homes(args.source)
    urls = collect_photo_urls(homes)
    if args.limit:
        urls = urls[: args.limit]

    print(f"homes: {len(homes)} | candidate photo URLs: {len(urls)}")
    manifest = build_manifest(urls, out_path=None if args.dry_run else Path(args.out))
    print(
        f"scanned={manifest['scanned']} "
        f"unreadable={manifest['unreadable']} "
        f"floorplan_drawings={len(manifest['urls'])}"
    )
    for url in manifest["urls"][:15]:
        print(f"  drawing: {url}")
    if args.dry_run:
        print("\nDry run — nothing written. Re-run without --dry-run to write the manifest.")
    else:
        print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
