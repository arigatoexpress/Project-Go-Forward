"""Tests for the photo deduplication audit tool.

Run: python -m pytest tests/test_photo_dedup_audit.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.photo_dedup_audit import (  # noqa: E402
    _normalize_url,
    build_url_index,
    run_photo_dedup_audit,
    to_csv,
    to_jsonl,
)


# Fixtures ───────────────────────────────────────────────────────────────────

UNIQUE_INVENTORY = [
    {
        "id": "inv-A",
        "model_name": "Alpha",
        "manufacturer": "Champion",
        "image_url": "https://cdn.example.com/A/hero.jpg",
        "gallery_images": [
            "https://cdn.example.com/A/g1.jpg",
            "https://cdn.example.com/A/g2.jpg",
        ],
    },
    {
        "id": "inv-B",
        "model_name": "Beta",
        "manufacturer": "Clayton",
        "image_url": "https://cdn.example.com/B/hero.jpg",
        "gallery_images": ["https://cdn.example.com/B/g1.jpg"],
        "floorplan_url": "https://cdn.example.com/B/fp.jpg",
    },
]

DUPED_INVENTORY = [
    {
        "id": "inv-1",
        "model_name": "The Steve",
        "manufacturer": "Champion",
        "status": "AVAILABLE",
        "image_url": "https://cdn.example.com/shared/hero.jpg",
        "gallery_images": [
            "https://cdn.example.com/shared/photo-1.jpg",
            "https://cdn.example.com/inv-1-only.jpg",
        ],
    },
    {
        "id": "inv-2",
        "model_name": "The Steve Twin",
        "manufacturer": "Champion",
        "status": "AVAILABLE",
        # Same hero URL as inv-1 — the canonical dup case
        "image_url": "https://cdn.example.com/shared/hero.jpg",
        "photos": ["https://cdn.example.com/shared/photo-1.jpg"],
    },
    {
        "id": "inv-3",
        "model_name": "Different Home",
        "manufacturer": "Clayton",
        "status": "SOLD",
        # Trailing slash should normalize to match inv-1/inv-2
        "image_url": "https://cdn.example.com/shared/hero.jpg/",
    },
]


# Normalization ──────────────────────────────────────────────────────────────

def test_normalize_url_strips_whitespace_and_trailing_slash():
    assert _normalize_url("  https://x.com/a/  ") == "https://x.com/a"


def test_normalize_url_rejects_empty_and_non_strings():
    assert _normalize_url("") is None
    assert _normalize_url("   ") is None
    assert _normalize_url(None) is None
    assert _normalize_url(42) is None
    assert _normalize_url(["http://x"]) is None


# Index construction ─────────────────────────────────────────────────────────

def test_build_url_index_collects_single_and_list_fields():
    index = build_url_index(UNIQUE_INVENTORY)
    # Each unique URL should appear exactly once
    assert all(len(usages) == 1 for usages in index.values())
    # Sanity: 5 distinct URLs across 2 items (2 hero + 3 gallery)
    assert len(index) == 6  # +1 floorplan_url on inv-B


def test_build_url_index_handles_missing_id_gracefully():
    items = [{"model_name": "ghost", "image_url": "https://x.com/a.jpg"}]
    index = build_url_index(items)
    assert index == {}


def test_build_url_index_skips_non_dict_items():
    index = build_url_index([None, "not-a-dict", 42, {"id": "x", "image_url": "https://y.com/a"}])
    assert len(index) == 1


# Audit reporting ────────────────────────────────────────────────────────────

def test_run_photo_dedup_audit_no_duplicates():
    report = run_photo_dedup_audit(UNIQUE_INVENTORY)
    assert report["summary"]["duplicate_urls"] == 0
    assert report["summary"]["items_affected"] == 0
    assert report["duplicates"] == []
    assert report["summary"]["inventory_scanned"] == 2


def test_run_photo_dedup_audit_finds_cross_doc_duplicates():
    report = run_photo_dedup_audit(DUPED_INVENTORY)
    summary = report["summary"]

    # Two URLs are shared:
    #   1. shared/hero.jpg → inv-1, inv-2, inv-3 (3 docs)
    #   2. shared/photo-1.jpg → inv-1, inv-2 (2 docs)
    assert summary["inventory_scanned"] == 3
    assert summary["duplicate_urls"] == 2
    assert summary["items_affected"] == 3

    # Duplicates sorted by descending count
    hero_group = report["duplicates"][0]
    assert hero_group["url"] == "https://cdn.example.com/shared/hero.jpg"
    assert hero_group["duplicate_count"] == 3
    assert hero_group["inventory_ids"] == ["inv-1", "inv-2", "inv-3"]

    photo_group = report["duplicates"][1]
    assert photo_group["url"] == "https://cdn.example.com/shared/photo-1.jpg"
    assert photo_group["duplicate_count"] == 2
    assert photo_group["inventory_ids"] == ["inv-1", "inv-2"]


def test_run_photo_dedup_audit_does_not_flag_intra_doc_only():
    """A single doc that uses the same URL across image_url + gallery_images
    should NOT show up as a 'duplicate' — that's intra-doc redundancy, not
    cross-doc dedup."""
    inventory = [{
        "id": "inv-only",
        "model_name": "Solo",
        "image_url": "https://cdn.example.com/same.jpg",
        "hero_image": "https://cdn.example.com/same.jpg",
        "gallery_images": ["https://cdn.example.com/same.jpg"],
    }]
    report = run_photo_dedup_audit(inventory)
    assert report["summary"]["duplicate_urls"] == 0
    assert report["duplicates"] == []


def test_run_photo_dedup_audit_handles_legacy_string_lists():
    """Some legacy rows store comma-separated photo URLs as strings."""
    inventory = [
        {"id": "inv-x", "photos": "https://cdn.example.com/a.jpg, https://cdn.example.com/b.jpg"},
        {"id": "inv-y", "photos": "https://cdn.example.com/a.jpg"},
    ]
    report = run_photo_dedup_audit(inventory)
    assert report["summary"]["duplicate_urls"] == 1
    assert report["duplicates"][0]["url"] == "https://cdn.example.com/a.jpg"


# Output formatting ──────────────────────────────────────────────────────────

def test_to_csv_renders_header_and_rows():
    report = run_photo_dedup_audit(DUPED_INVENTORY)
    csv_text = to_csv(report)
    # csv writer uses \r\n line terminators by default — splitlines normalizes
    lines = [ln for ln in csv_text.splitlines() if ln]
    assert lines[0] == "url,duplicate_count,total_references,inventory_ids,model_names"
    # 2 duplicate groups → 2 data rows
    assert len(lines) == 3
    assert "shared/hero.jpg" in lines[1]
    # Inventory IDs joined with pipe
    assert "inv-1|inv-2|inv-3" in lines[1]


def test_to_jsonl_one_record_per_duplicate_group():
    report = run_photo_dedup_audit(DUPED_INVENTORY)
    jsonl_text = to_jsonl(report)
    records = [json.loads(line) for line in jsonl_text.strip().split("\n")]
    assert len(records) == 2
    assert {r["url"] for r in records} == {
        "https://cdn.example.com/shared/hero.jpg",
        "https://cdn.example.com/shared/photo-1.jpg",
    }


def test_to_jsonl_empty_when_no_dups():
    report = run_photo_dedup_audit(UNIQUE_INVENTORY)
    assert to_jsonl(report) == ""
