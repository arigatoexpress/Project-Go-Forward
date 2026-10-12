"""GET /api/inventory?include_staff_photos=true overlays staff uploads.

The Photos picker uses this so "Needs photos" matches what customers already see.
Default stays off so document autofill is unchanged.

Run: python -m pytest tests/test_inventory_staff_photos.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.test_api_v1 import create_client  # noqa: E402


def test_include_staff_photos_overlays_uploads_and_default_does_not(monkeypatch):
    client, main, fake_db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    fake_db.collections["inventory"]["home-1"] = {
        "id": "home-1",
        "model_name": "Big Blue",
        "status": "AVAILABLE",
        "image_url": "",
    }

    overlayed = []

    def fake_overlay(homes):
        overlayed.append(list(homes))
        for home in homes:
            home["real_photos"] = ["/api/inventory/photos/home-1/lot.jpg"]
            home["has_staff_photos"] = True
            home["image_placeholder"] = True

    monkeypatch.setattr(main, "_overlay_staff_photos", fake_overlay)
    token = main._create_admin_token()
    headers = {"X-Admin-Token": token}

    default = client.get("/api/inventory?status=&limit=50", headers=headers)
    assert default.status_code == 200
    assert overlayed == []
    assert default.json()["inventory"][0].get("has_staff_photos") is not True

    with_photos = client.get(
        "/api/inventory?status=&limit=50&include_staff_photos=true", headers=headers
    )
    assert with_photos.status_code == 200
    assert len(overlayed) == 1
    row = with_photos.json()["inventory"][0]
    assert row["has_staff_photos"] is True
    assert row["real_photos"] == ["/api/inventory/photos/home-1/lot.jpg"]
    assert "image_placeholder" not in row
