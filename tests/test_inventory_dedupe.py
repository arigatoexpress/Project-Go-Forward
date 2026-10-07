"""Manage Inventory showed 4 homes twice ("PRE-OWNED / Big Blue" + "Big Blue",
Heritage 1684-32A, Select S-1256-21A, Select S-1272-32A).

The legacy-snapshot seeder keeps the website title with its "PRE-OWNED /"
prefix under the legacy listing id, while other writers store the bare model
name under a different doc id. ``/api/inventory`` listed every document as-is.
The fix is display-only: records are annotated, Firestore is never written.

Run: python -m pytest tests/test_inventory_dedupe.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.inventory_dedupe import (  # noqa: E402
    annotate_possible_duplicates,
    is_active_status,
    normalize_model_key,
)


def test_normalize_model_key_matches_website_and_bare_titles():
    assert normalize_model_key("PRE-OWNED / Big Blue") == normalize_model_key("Big Blue")
    assert normalize_model_key("PRE-OWNED / Heritage 1684-32A") == normalize_model_key(
        "Heritage 1684-32A"
    )
    assert normalize_model_key("PRE-OWNED / Select S-1256-21A") == normalize_model_key(
        "Select S-1256-21A"
    )
    assert normalize_model_key("The Promotional Series / The Nassau FAC28483A") == "nassau"
    assert normalize_model_key("") == ""


def test_reported_live_duplicates_collapse_to_one_each():
    homes = [
        {"id": "44490", "model_name": "PRE-OWNED / Big Blue", "status": "AVAILABLE"},
        {"id": "fs-big-blue", "model_name": "Big Blue", "status": "AVAILABLE"},
        {"id": "43945", "model_name": "PRE-OWNED / Heritage 1684-32A", "status": "AVAILABLE"},
        {"id": "fs-heritage", "model_name": "Heritage 1684-32A", "status": "AVAILABLE"},
        {"id": "43944", "model_name": "PRE-OWNED / Select S-1256-21A", "status": "AVAILABLE"},
        {"id": "fs-1256", "model_name": "Select S-1256-21A", "status": "AVAILABLE"},
        {"id": "43943", "model_name": "PRE-OWNED / Select S-1272-32A", "status": "AVAILABLE"},
        {"id": "fs-1272", "model_name": "Select S-1272-32A", "status": "AVAILABLE"},
        {"id": "solo", "model_name": "The Nassau", "status": "AVAILABLE"},
    ]
    annotate_possible_duplicates(homes)

    shown = [h for h in homes if not h.get("duplicate_of")]
    assert len(shown) == 5
    # The clean-named record wins when nothing else distinguishes them.
    by_id = {h["id"]: h for h in homes}
    assert by_id["fs-big-blue"]["possible_duplicate_ids"] == ["44490"]
    assert by_id["44490"]["duplicate_of"] == "fs-big-blue"
    assert "possible_duplicate_ids" not in by_id["solo"]
    assert "duplicate_of" not in by_id["solo"]


def test_same_model_without_serials_stays_two_homes():
    homes = [
        {"id": "lot-a", "model_name": "The Nassau"},
        {"id": "lot-b", "model_name": "The Nassau"},
    ]
    annotate_possible_duplicates(homes)
    assert all("duplicate_of" not in h for h in homes)


def test_bare_model_with_serial_does_not_hide_unserialized_twin():
    homes = [
        {"id": "with-serial", "model_name": "The Nassau", "serial_number": "TXL111"},
        {"id": "no-serial", "model_name": "The Nassau"},
    ]
    annotate_possible_duplicates(homes)
    assert all("duplicate_of" not in h for h in homes)


def test_different_serials_are_different_homes():
    homes = [
        {"id": "a", "model_name": "The Nassau", "serial_number": "TXL111"},
        {"id": "b", "model_name": "The Nassau", "serial_number": "TXL222"},
    ]
    annotate_possible_duplicates(homes)
    assert all("duplicate_of" not in h for h in homes)


def test_same_serial_is_flagged_and_unserialed_left_alone_when_ambiguous():
    homes = [
        {"id": "a", "model_name": "The Nassau", "serial_number": "TXL111"},
        {"id": "b", "model_name": "PRE-OWNED / The Nassau", "serial_number": "txl111"},
        {"id": "c", "model_name": "The Nassau", "serial_number": "TXL222"},
        {"id": "d", "model_name": "The Nassau"},
    ]
    annotate_possible_duplicates(homes)
    by_id = {h["id"]: h for h in homes}
    assert by_id["a"]["possible_duplicate_ids"] == ["b"]
    assert by_id["b"]["duplicate_of"] == "a"
    assert "duplicate_of" not in by_id["c"]
    assert "duplicate_of" not in by_id["d"]


def test_active_record_with_serial_and_photos_is_preferred():
    homes = [
        {"id": "retired", "model_name": "Big Blue", "status": "RETIRED", "serial_number": "S1"},
        {"id": "photos", "model_name": "PRE-OWNED / Big Blue", "real_photos": ["x.jpg"]},
        {"id": "serial", "model_name": "PRE-OWNED / Big Blue", "serial_number": "S1"},
    ]
    annotate_possible_duplicates(homes)
    by_id = {h["id"]: h for h in homes}
    assert sorted(by_id["serial"]["possible_duplicate_ids"]) == ["photos", "retired"]


def test_is_active_status():
    assert is_active_status("AVAILABLE")
    assert is_active_status("pending")
    assert is_active_status(None)
    assert not is_active_status("SOLD")
    assert not is_active_status("retired")


def test_admin_inventory_endpoint_annotates_without_writing(monkeypatch):
    from tests.test_api_v1 import create_client

    client, main, fake_db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    fake_db.collections["inventory"].update(
        {
            "44490": {"id": "44490", "model_name": "PRE-OWNED / Big Blue", "status": "AVAILABLE",
                      "legacy_inventory_id": "44490"},
            "fs-big-blue": {"id": "fs-big-blue", "model_name": "Big Blue", "status": "AVAILABLE"},
        }
    )
    before = {k: dict(v) for k, v in fake_db.collections["inventory"].items()}

    token = main._create_admin_token()
    resp = client.get("/api/inventory?status=&limit=500", headers={"X-Admin-Token": token})
    assert resp.status_code == 200, resp.text
    rows = {r["id"]: r for r in resp.json()["inventory"]}
    assert rows["fs-big-blue"]["possible_duplicate_ids"] == ["44490"]
    assert rows["44490"]["duplicate_of"] == "fs-big-blue"
    assert rows["44490"]["stock_number"] == "44490"

    assert fake_db.collections["inventory"] == before
