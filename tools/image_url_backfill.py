"""
tools/image_url_backfill.py

Dry-run script: finds Firestore inventory rows with no image_url and proposes
a CloudFront CDN URL for each by cross-referencing the static asset catalog
(tools/asset_scraper.py) and known CDN patterns.

CDN patterns (from asset_scraper.py):
  New homes:  https://d132mt2yijm03y.cloudfront.net/manufacturer/{manufacturer_id}/floorplan/{plan_id}/{filename}
  Pre-owned:  https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/{inventory_id}/{filename}

Usage:
    python tools/image_url_backfill.py                   # dry-run → JSONL on stdout
    python tools/image_url_backfill.py --out results.jsonl
    python tools/image_url_backfill.py --apply --yes     # write verified URLs to Firestore
"""

import argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

try:
    from tools.asset_scraper import CDN_BASE, get_assets_for_home
except ImportError:
    from .asset_scraper import CDN_BASE, get_assets_for_home  # type: ignore

logger = logging.getLogger(__name__)

DEALER_ID = "3522"
HEAD_TIMEOUT = 5  # seconds per attempt
MAX_WORKERS = 10  # <<< well below the 50-request fan-out limit


# ─── URL construction helpers ────────────────────────────────────────────────


def build_new_home_url(manufacturer_id: str, plan_id: str, filename: str = "1.jpg") -> str:
    """CDN URL for a new manufactured home's first gallery image."""
    return f"{CDN_BASE}/manufacturer/{manufacturer_id}/floorplan/{plan_id}/{filename}"


def build_preowned_url(inventory_id: str, filename: str = "1.jpg") -> str:
    """CDN URL for a pre-owned home's first gallery image (dealer/3522 bucket)."""
    return f"{CDN_BASE}/dealer/{DEALER_ID}/inventory/{inventory_id}/{filename}"


# ─── URL resolution ──────────────────────────────────────────────────────────


def resolve_candidate_url(row: dict) -> str | None:
    """
    Derive the most likely CDN image URL for a Firestore inventory row.

    Resolution order:
      1. Match model_name against static asset catalog → use first known image.
      2. Asset matched but no images → construct from catalog manufacturer_id/plan_id.
      3. Row has manufacturer_id + plan_id Firestore fields → construct directly.
      4. Pre-owned row whose Firestore doc ID is a numeric dealer inventory ID.

    Returns None when no candidate can be derived (caller logs + skips).
    Rows without manufacturer_id are skipped as specified in requirements.
    """
    model_name = row.get("model_name") or ""
    inventory_id = row.get("id") or ""
    is_new = row.get("is_new", True)

    # 1 + 2. Static asset catalog lookup (highest confidence)
    # Guard: empty string matches every asset entry ("" in any_str is True)
    asset = get_assets_for_home(model_name) if model_name else None
    if asset:
        images = asset.get("images", [])
        if images:
            return images[0]
        # Catalog entry exists but no images listed; try constructing from its IDs
        mid = asset.get("manufacturer_id")
        pid = asset.get("plan_id")
        if mid and pid:
            return build_new_home_url(str(mid), str(pid))

    # 3. Firestore row carries manufacturer_id + plan_id directly
    mid = row.get("manufacturer_id")
    pid = row.get("plan_id")
    if mid and pid:
        return build_new_home_url(str(mid), str(pid))

    # 4. Pre-owned with numeric dealer inventory ID
    if not is_new and inventory_id and str(inventory_id).isdigit():
        return build_preowned_url(str(inventory_id))

    # Cannot derive a URL — skip (requirement: never construct without manufacturer_id
    # unless it's a known dealer inventory ID)
    return None


# ─── HEAD probe ──────────────────────────────────────────────────────────────


def probe_url(url: str, timeout: int = HEAD_TIMEOUT) -> dict:
    """
    HEAD-request a URL, retry once on transient failure.
    Returns a dict with head_status (int or str), content_length, content_type.
    """
    last_result: dict = {}
    for attempt in range(2):
        try:
            resp = requests.head(url, timeout=timeout, allow_redirects=True)
            return {
                "head_status": resp.status_code,
                "content_length": resp.headers.get("Content-Length"),
                "content_type": resp.headers.get("Content-Type"),
            }
        except requests.Timeout:
            last_result = {"head_status": "timeout", "content_length": None, "content_type": None}
        except requests.ConnectionError as exc:
            last_result = {
                "head_status": f"connection_error:{exc}",
                "content_length": None,
                "content_type": None,
            }
        except Exception as exc:
            last_result = {
                "head_status": f"error:{exc}",
                "content_length": None,
                "content_type": None,
            }
    return last_result


# ─── Per-row processing ──────────────────────────────────────────────────────


def process_row(row: dict) -> dict:
    """Build a JSONL record for one inventory row: resolve URL → probe → return dict."""
    inventory_id = row.get("id", "")
    model_name = row.get("model_name", "")
    manufacturer = row.get("manufacturer", "")

    candidate_url = resolve_candidate_url(row)

    if candidate_url is None:
        return {
            "inventory_id": inventory_id,
            "model_name": model_name,
            "manufacturer": manufacturer,
            "candidate_url": None,
            "head_status": "skipped_no_ids",
            "content_length": None,
            "content_type": None,
        }

    probe = probe_url(candidate_url)
    return {
        "inventory_id": inventory_id,
        "model_name": model_name,
        "manufacturer": manufacturer,
        "candidate_url": candidate_url,
        **probe,
    }


# ─── Firestore read ──────────────────────────────────────────────────────────


def load_inventory_missing_image() -> list:
    """
    Stream all Firestore inventory docs and return those with null/empty image_url.
    Read-only: no writes.
    """
    try:
        from database.firestore_client import get_database

        db = get_database()
    except Exception as exc:
        logger.error("Cannot connect to Firestore: %s", exc)
        return []

    rows = []
    try:
        for doc in db.db.collection("inventory").stream():
            data = doc.to_dict()
            data["id"] = doc.id
            if not data.get("image_url"):
                rows.append(data)
    except Exception as exc:
        logger.error("Error streaming inventory: %s", exc)
    return rows


# ─── Apply mode ──────────────────────────────────────────────────────────────


def _apply_verified(results: list) -> None:
    """
    Write confirmed image_url values (head_status == 200) back to Firestore.
    Only called when --apply --yes is given. Never called in dry-run.
    """
    try:
        from database.firestore_client import get_database

        db = get_database()
    except Exception as exc:
        logger.error("Cannot connect to Firestore for apply: %s", exc)
        return

    written = 0
    for record in results:
        if record.get("head_status") == 200 and record.get("candidate_url"):
            inv_id = record["inventory_id"]
            url = record["candidate_url"]
            try:
                db.update_inventory(inv_id, {"image_url": url})
                logger.info("[APPLY] %s → %s", inv_id, url)
                written += 1
            except Exception as exc:
                logger.error("[APPLY] Failed to update %s: %s", inv_id, exc)

    logger.info("[APPLY] Wrote image_url to %d inventory record(s).", written)


# ─── Main orchestration ──────────────────────────────────────────────────────


def run(args) -> list:
    """
    Core logic: load → probe → report → optionally apply.
    Returns the list of result dicts for testing.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )

    logger.info("Loading Firestore inventory with missing image_url…")
    rows = load_inventory_missing_image()
    logger.info("Found %d row(s) with missing image_url.", len(rows))

    if not rows:
        logger.info("Nothing to do.")
        return []

    results: list = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(process_row, row): row for row in rows}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                row = futures[future]
                logger.error("Unhandled error for %s: %s", row.get("id"), exc)

    # ── Stats summary ──
    total = len(results)
    derivable = sum(1 for r in results if r["candidate_url"])
    verified = sum(1 for r in results if r.get("head_status") == 200)
    skipped = sum(1 for r in results if r["candidate_url"] is None)
    pct = derivable / total * 100 if total else 0

    logger.info("─" * 50)
    logger.info("Total missing image_url : %d", total)
    logger.info("Derivable URL candidates: %d (%.0f%%)", derivable, pct)
    logger.info("Verified HTTP 200       : %d", verified)
    logger.info("Skipped (no IDs)        : %d", skipped)
    logger.info("─" * 50)

    # ── JSONL output ──
    out_path = getattr(args, "out", None)
    out = open(out_path, "w") if out_path else sys.stdout
    try:
        for record in results:
            print(json.dumps(record), file=out)
    finally:
        if out_path:
            out.close()
            logger.info("Results written to %s", out_path)

    # ── Apply mode ──
    if getattr(args, "apply", False):
        if getattr(args, "yes", False):
            _apply_verified(results)
        else:
            logger.warning("--apply requires --yes to confirm. No writes performed.")

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run: propose CDN image_url for Firestore inventory rows missing one.\n"
            "Outputs JSONL: {inventory_id, model_name, manufacturer, "
            "candidate_url, head_status, content_length, content_type}\n\n"
            "To write verified URLs back to Firestore:\n"
            "    python tools/image_url_backfill.py --apply --yes"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--out", metavar="FILE", help="Write JSONL to FILE instead of stdout")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write verified (HTTP 200) URLs back to Firestore (requires --yes)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm Firestore writes (required with --apply)",
    )
    run(parser.parse_args())


if __name__ == "__main__":
    main()
