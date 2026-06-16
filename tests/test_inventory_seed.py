"""Tests for the legacy-snapshot -> Firestore inventory seeder.

The seeder is the first step of moving THO inventory in-app: it populates the
Firestore ``inventory`` collection from the frozen legacy snapshot so the public
site can serve staff-managed homes instead of a dead scrape. The mapping is the
inverse of ``tools.inventory_tools._load_inventory_from_firestore``, so these
tests pin that the seed survives the round trip and is idempotent.
"""

from tools import inventory_seed


def _snapshot_home(**overrides):
    home = {
        "id": "43372",
        "legacy_inventory_id": "43372",
        "model_name": "Premier / Creole 3256H32447",
        "manufacturer": "Champion Homes",
        "classification": "Double Wide",
        "status": "Available",
        "marketing_tags": ["Manufactured", "Lot Model", "New"],
        "features": ["Manufactured", "Lot Model", "Island Kitchen"],
        "price_value": 0,
        "display_price": "Call for Price",
        "specs": {"beds": 3, "baths": 2.0, "sq_ft": 1699, "width": 30, "length": 56},
        "image_url": "https://cdn.example.com/43372-ext-1.jpg",
        "hero_image": "https://cdn.example.com/43372-ext-1.jpg",
        "gallery_images": [
            "https://cdn.example.com/43372-ext-1.jpg",
            "https://cdn.example.com/43372-ext-2.jpg",
        ],
        "real_photos": [
            "https://cdn.example.com/43372-ext-1.jpg",
            "https://cdn.example.com/43372-ext-2.jpg",
            "https://cdn.example.com/43372-int-1.jpg",
        ],
        "floorplan_url": "https://cdn.example.com/43372-floorplan.jpg",
        "floor_plan_url": "https://cdn.example.com/43372-floorplan.jpg",
        "matterport_id": "mTvc6YoSRTx",
        "image_categories": {"exterior": ["https://cdn.example.com/43372-ext-1.jpg"]},
        "description": "The Creole is a 3 bed, 2 bath home.",
        "detail_url": "https://www.texashomeoutlet.com/inventory-detail/43372/...",
    }
    home.update(overrides)
    return home


class FakeDatabase:
    """Records upsert_inventory calls so we can assert idempotent doc-id writes."""

    def __init__(self):
        self.docs: dict[str, dict] = {}

    def upsert_inventory(self, inventory_id: str, data: dict) -> str:
        self.docs[inventory_id] = data
        return inventory_id


def test_mapper_produces_firestore_read_shape():
    doc = inventory_seed.snapshot_home_to_inventory_doc(_snapshot_home())

    # Keys the public Firestore read path (_load_inventory_from_firestore) needs.
    assert doc["legacy_inventory_id"] == "43372"
    assert doc["model_name"] == "Premier / Creole 3256H32447"
    assert doc["manufacturer"] == "Champion Homes"
    assert doc["classification"] == "Double Wide"
    assert doc["status"] == "AVAILABLE"  # uppercase enum search_inventory filters on
    assert doc["is_new"] is True
    assert doc["bedrooms"] == 3
    assert doc["bathrooms"] == 2  # 2.0 -> 2 (int), halves preserved elsewhere
    assert doc["sqft"] == 1699
    assert doc["width"] == 30
    assert doc["length"] == 56
    assert doc["sale_price"] == 0 and doc["msrp"] == 0  # "Call for Price"
    assert doc["floorplan_url"] == "https://cdn.example.com/43372-floorplan.jpg"
    assert doc["matterport_id"] == "mTvc6YoSRTx"
    assert doc["photos"] == doc["gallery_images"]
    assert doc["photos"][0] == "https://cdn.example.com/43372-ext-1.jpg"
    assert doc["source"] == inventory_seed.SEED_SOURCE


def test_mapper_preserves_half_baths():
    doc = inventory_seed.snapshot_home_to_inventory_doc(_snapshot_home(specs={"beds": 4, "baths": 2.5}))
    assert doc["bathrooms"] == 2.5


def test_mapper_marks_preowned_homes():
    doc = inventory_seed.snapshot_home_to_inventory_doc(
        _snapshot_home(status="Pre-Owned", marketing_tags=["Pre-Owned"])
    )
    assert doc["is_new"] is False


def test_seed_dry_run_writes_nothing_but_plans_everything():
    db = FakeDatabase()
    homes = [_snapshot_home(), _snapshot_home(id="44490", legacy_inventory_id="44490", model_name="Big Blue")]

    stats = inventory_seed.seed_inventory_from_snapshot(db, homes, dry_run=True)

    assert stats["total"] == 2
    assert stats["written"] == 0
    assert db.docs == {}  # no writes on a dry run
    assert ("43372", "Premier / Creole 3256H32447") in stats["planned"]
    assert ("44490", "Big Blue") in stats["planned"]


def test_seed_apply_upserts_by_legacy_id_and_is_idempotent():
    db = FakeDatabase()
    homes = [_snapshot_home()]

    first = inventory_seed.seed_inventory_from_snapshot(db, homes, dry_run=False)
    assert first["written"] == 1
    assert set(db.docs) == {"43372"}  # legacy id is the doc id

    # Re-running must not duplicate — same doc id is overwritten in place.
    second = inventory_seed.seed_inventory_from_snapshot(db, homes, dry_run=False)
    assert second["written"] == 1
    assert set(db.docs) == {"43372"}


def test_seed_skips_homes_with_no_id():
    db = FakeDatabase()
    homes = [_snapshot_home(id="", legacy_inventory_id="")]

    stats = inventory_seed.seed_inventory_from_snapshot(db, homes, dry_run=False)

    assert stats["total"] == 1
    assert stats["written"] == 0
    assert stats["skipped_no_id"] == 1
    assert db.docs == {}
