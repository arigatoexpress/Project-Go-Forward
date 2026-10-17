"""Tests for database/firestore_client.py — the core data-access layer.

THODatabase had no dedicated coverage: every method talks to Firestore through
the bounded FIRESTORE_RPC_TIMEOUT (PR #296 hardening). The fake client below
implements the slice of the google.cloud.firestore surface the module uses —
document get/set/update/delete, where/order_by/limit queries, collection
stream, and batch writes — and asserts every RPC carries the timeout.

SSN envelope encryption (tools.field_crypto) is inert without SSN_KMS_KEY and
passes plaintext through, so deal tests exercise the encrypt/decrypt call
sites without KMS.
"""

from __future__ import annotations

import inspect
import sys
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from google.cloud import firestore  # noqa: E402

from database.firestore_client import THODatabase  # noqa: E402


def _require_timeout(timeout):
    """Pin PR #296: RPCs issued by database/firestore_client.py must carry
    FIRESTORE_RPC_TIMEOUT. Seeding/read-back calls made directly from this
    test module are allowed to skip it."""
    if timeout is not None:
        return
    for frame in inspect.stack():
        if frame.filename.endswith("database/firestore_client.py"):
            pytest.fail(
                "Firestore RPC from database/firestore_client.py is missing "
                "the bounded FIRESTORE_RPC_TIMEOUT"
            )


# ---------------------------------------------------------------------------
# Fake Firestore client
# ---------------------------------------------------------------------------


class FakeDocSnap:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class FakeDocRef:
    def __init__(self, collection, doc_id):
        self._collection = collection
        self.id = doc_id

    def _assert_timeout(self, timeout):
        _require_timeout(timeout)

    def set(self, data, merge=False, timeout=None):
        self._assert_timeout(timeout)
        existing = self._collection._docs.get(self.id) or {}
        self._collection._docs[self.id] = {**existing, **data} if merge else dict(data)

    def get(self, timeout=None):
        self._assert_timeout(timeout)
        data = self._collection._docs.get(self.id)
        return FakeDocSnap(self.id, dict(data) if data is not None else None)

    def update(self, fields, timeout=None):
        self._assert_timeout(timeout)
        existing = self._collection._docs.setdefault(self.id, {})
        existing.update(fields)

    def delete(self, timeout=None):
        self._assert_timeout(timeout)
        self._collection._docs.pop(self.id, None)


class FakeQuery:
    def __init__(self, collection, filters=(), orderings=(), limit_count=None):
        self._collection = collection
        self._filters = filters
        self._orderings = orderings
        self._limit = limit_count

    def where(self, field, op, value):
        return FakeQuery(
            self._collection, self._filters + ((field, op, value),), self._orderings, self._limit
        )

    def order_by(self, field, direction=None):
        return FakeQuery(
            self._collection, self._filters, self._orderings + ((field, direction),), self._limit
        )

    def limit(self, count):
        return FakeQuery(self._collection, self._filters, self._orderings, count)

    def _matches(self, data):
        for field, op, value in self._filters:
            current = data.get(field)
            if op == "==" and current != value:
                return False
            if op == "in" and current not in value:
                return False
            if op == ">=" and not (current is not None and current >= value):
                return False
            if op == "<" and not (current is not None and current < value):
                return False
        return True

    def _sorted(self, rows):
        for field, direction in reversed(self._orderings):
            rows.sort(key=lambda row: (row[1].get(field) is None, row[1].get(field)))
            if direction == firestore.Query.DESCENDING:
                rows.reverse()
        return rows

    def stream(self, timeout=None):
        _require_timeout(timeout)
        rows = [
            (doc_id, data)
            for doc_id, data in self._collection._docs.items()
            if self._matches(data)
        ]
        rows = self._sorted(rows)
        if self._limit is not None:
            rows = rows[: self._limit]
        for doc_id, data in rows:
            yield FakeDocSnap(doc_id, dict(data))


class FakeCollection:
    def __init__(self, name):
        self.name = name
        self._docs = {}
        self._auto = 0

    def document(self, doc_id=None):
        if doc_id is None:
            self._auto += 1
            doc_id = f"auto-{self._auto}"
        return FakeDocRef(self, doc_id)

    def where(self, field, op, value):
        return FakeQuery(self).where(field, op, value)

    def order_by(self, field, direction=None):
        return FakeQuery(self).order_by(field, direction)

    def limit(self, count):
        return FakeQuery(self).limit(count)

    def stream(self, timeout=None):
        _require_timeout(timeout)
        for doc_id, data in self._docs.items():
            yield FakeDocSnap(doc_id, dict(data))


class FakeBatch:
    def __init__(self, client):
        self._client = client
        self._ops = []

    def set(self, doc_ref, data):
        self._ops.append((doc_ref, dict(data)))

    def commit(self, timeout=None):
        _require_timeout(timeout)
        for doc_ref, data in self._ops:
            doc_ref._collection._docs[doc_ref.id] = data
        self._ops = []


class FakeFirestoreClient:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        return self.collections.setdefault(name, FakeCollection(name))

    def batch(self):
        return FakeBatch(self)


@pytest.fixture
def db():
    database = THODatabase("test-project")
    fake = FakeFirestoreClient()
    database._db = fake
    return database, fake


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------


class TestCustomers:
    def test_get_customer_by_doc_id(self, db):
        database, fake = db
        fake.collection("customers").document("c1").set({"full_name": "Ada Lovelace"})
        customer = database.get_customer("c1")
        assert customer["id"] == "c1"
        assert customer["full_name"] == "Ada Lovelace"

    def test_get_customer_falls_back_to_legacy_id(self, db):
        database, fake = db
        fake.collection("customers").document("doc-9").set(
            {"full_name": "Legacy User", "legacy_id": "OLD-42"}
        )
        customer = database.get_customer("OLD-42")
        assert customer["id"] == "doc-9"

    def test_get_customer_missing_returns_none(self, db):
        database, _fake = db
        assert database.get_customer("nope") is None

    def test_search_customers_by_status(self, db):
        database, fake = db
        database.create_customer({"full_name": "Active One", "status": "ACTIVE"})
        database.create_customer({"full_name": "Prospect Two", "status": "PROSPECT"})
        results = database.search_customers(status="active")
        assert [r["full_name"] for r in results] == ["Active One"]

    def test_search_customers_name_prefix_fast_path(self, db):
        database, fake = db
        database.create_customer({"full_name": "Grace Hopper"})
        database.create_customer({"full_name": "Alan Turing"})
        # Alphabetic queries hit the _name_lower prefix index.
        results = database.search_customers(query_text="gra")
        assert [r["full_name"] for r in results] == ["Grace Hopper"]

    def test_search_customers_name_prefix_falls_back_to_scan(self, db):
        """Unbackfilled _name_lower must not hide customers from name search."""
        database, fake = db
        fake.collection("customers").document("c1").set({"full_name": "Grace Hopper"})
        results = database.search_customers(query_text="gra")
        assert [r["full_name"] for r in results] == ["Grace Hopper"]

    def test_search_customers_phone_digit_match(self, db):
        database, _fake = db
        database.create_customer({"full_name": "Phony", "phone": "(281) 555-0100"})
        results = database.search_customers(query_text="2815550100")
        assert [r["full_name"] for r in results] == ["Phony"]

    def test_search_customers_no_match(self, db):
        database, _fake = db
        database.create_customer({"full_name": "Someone", "phone": "5550001"})
        assert database.search_customers(query_text="zzzz") == []

    def test_create_customer_stamps_dates_and_name_index(self, db):
        database, fake = db
        doc_id = database.create_customer({"full_name": "  New Person  "})
        stored = fake.collection("customers").document(doc_id).get().to_dict()
        assert isinstance(stored["created_at"], datetime)
        assert isinstance(stored["updated_at"], datetime)
        assert stored["_name_lower"] == "new person"

    def test_create_customer_with_explicit_doc_id(self, db):
        database, fake = db
        doc_id = database.create_customer({"full_name": "Migrated"}, doc_id="mig-1")
        assert doc_id == "mig-1"
        assert fake.collection("customers").document("mig-1").get().exists

    def test_update_customer_stamps_updated_at(self, db):
        database, fake = db
        doc_id = database.create_customer({"full_name": "Before"})
        assert database.update_customer(doc_id, {"full_name": "After"}) is True
        stored = fake.collection("customers").document(doc_id).get().to_dict()
        assert stored["full_name"] == "After"
        assert isinstance(stored["updated_at"], datetime)

    def test_delete_customer(self, db):
        database, fake = db
        doc_id = database.create_customer({"full_name": "Gone"})
        assert database.delete_customer(doc_id) is True
        assert not fake.collection("customers").document(doc_id).get().exists

    def test_count_customers_by_status(self, db):
        database, _fake = db
        database.create_customer({"full_name": "A", "status": "ACTIVE"})
        database.create_customer({"full_name": "B", "status": "ACTIVE"})
        database.create_customer({"full_name": "C"})  # no status -> UNKNOWN
        counts = database.count_customers()
        assert counts["total"] == 3
        assert counts["by_status"] == {"ACTIVE": 2, "UNKNOWN": 1}

    def test_batch_create_customers_chunks(self, db):
        database, fake = db
        customers = [{"id": f"c{i}", "full_name": f"Bulk {i}"} for i in range(5)]
        written = database.batch_create_customers(customers, batch_size=2)
        assert written == 5
        collection = fake.collection("customers")
        for i in range(5):
            assert collection.document(f"c{i}").get().exists

    def test_batch_create_customers_uses_legacy_id_when_no_id(self, db):
        database, fake = db
        written = database.batch_create_customers([{"legacy_id": "L-1", "full_name": "Legacy"}])
        assert written == 1
        assert fake.collection("customers").document("L-1").get().exists

    def test_batch_create_customers_empty(self, db):
        database, _fake = db
        assert database.batch_create_customers([]) == 0


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


def _seed_inventory(database, fake, items):
    for item in items:
        doc_id = item.pop("id")
        fake.collection("inventory").document(doc_id).set(item)


class TestInventory:
    def test_get_inventory(self, db):
        database, fake = db
        _seed_inventory(database, fake, [{"id": "inv-1", "model_name": "The Oak"}])
        item = database.get_inventory("inv-1")
        assert item["model_name"] == "The Oak"
        assert database.get_inventory_by_id("inv-1") == item

    def test_get_inventory_missing(self, db):
        database, _fake = db
        assert database.get_inventory("missing") is None

    def test_get_inventory_by_serial(self, db):
        database, fake = db
        _seed_inventory(database, fake, [{"id": "inv-1", "serial_number": "SN-123"}])
        item = database.get_inventory_by_serial("SN-123")
        assert item["id"] == "inv-1"
        assert database.get_inventory_by_serial("SN-999") is None

    def test_get_inventory_by_serials_chunks_and_dedupes(self, db):
        database, fake = db
        _seed_inventory(
            database,
            fake,
            [
                {"id": f"inv-{i}", "serial_number": f"SN-{i}"}
                for i in range(35)  # forces two chunks (30 + 5)
            ],
        )
        serials = [f"SN-{i}" for i in range(35)] + [None, "", "SN-0"]  # dups/empties skipped
        result = database.get_inventory_by_serials(serials)
        assert len(result) == 35
        assert result["SN-0"]["id"] == "inv-0"

    def test_get_inventory_by_serials_empty_input(self, db):
        database, _fake = db
        assert database.get_inventory_by_serials([]) == {}
        assert database.get_inventory_by_serials([None, ""]) == {}

    def test_search_inventory_filters(self, db):
        database, fake = db
        _seed_inventory(
            database,
            fake,
            [
                {"id": "i1", "status": "AVAILABLE", "bedrooms": 3, "bathrooms": 2, "msrp": 80000},
                {"id": "i2", "status": "AVAILABLE", "bedrooms": 4, "bathrooms": 2, "msrp": 95000},
                {"id": "i3", "status": "SOLD", "bedrooms": 3, "bathrooms": 2, "msrp": 70000},
            ],
        )
        results = database.search_inventory(min_beds=3, max_beds=3, max_budget=75000)
        assert [r["id"] for r in results] == []  # i3 is SOLD
        results = database.search_inventory(min_beds=3, max_budget=90000, status="AVAILABLE")
        assert [r["id"] for r in results] == ["i1"]

    def test_search_inventory_manufacturer_filter(self, db):
        database, fake = db
        _seed_inventory(
            database,
            fake,
            [
                {"id": "i1", "status": "AVAILABLE", "manufacturer": "TRU"},
                {"id": "i2", "status": "AVAILABLE", "manufacturer": "Clayton"},
            ],
        )
        results = database.search_inventory(manufacturer="TRU")
        assert [r["id"] for r in results] == ["i1"]

    def test_create_update_upsert_delete_inventory(self, db):
        database, fake = db
        doc_id = database.create_inventory({"model_name": "New"})
        assert fake.collection("inventory").document(doc_id).get().exists

        assert database.update_inventory(doc_id, {"model_name": "Renamed"}) is True
        assert fake.collection("inventory").document(doc_id).get().to_dict()["model_name"] == "Renamed"

        # Upsert merges: staff-edited fields not in the payload survive.
        database.update_inventory(doc_id, {"staff_note": "keep me"})
        assert database.upsert_inventory(doc_id, {"model_name": "Re-seeded"}) == doc_id
        stored = fake.collection("inventory").document(doc_id).get().to_dict()
        assert stored["model_name"] == "Re-seeded"
        assert stored["staff_note"] == "keep me"

        assert database.delete_inventory(doc_id) is True
        assert not fake.collection("inventory").document(doc_id).get().exists


# ---------------------------------------------------------------------------
# Properties / leases
# ---------------------------------------------------------------------------


class TestPropertiesAndLeases:
    def test_get_property(self, db):
        database, fake = db
        fake.collection("properties").document("p1").set({"address": "1 Main St"})
        assert database.get_property("p1")["address"] == "1 Main St"
        assert database.get_property("missing") is None

    def test_search_properties_by_address(self, db):
        database, fake = db
        fake.collection("properties").document("p1").set({"address": "100 FM 1960 Rd"})
        fake.collection("properties").document("p2").set({"address": "9 Other Ln"})
        results = database.search_properties_by_address("fm 1960")
        assert [r["id"] for r in results] == ["p1"]

    def test_get_properties_by_customer(self, db):
        database, fake = db
        fake.collection("properties").document("p1").set({"customer_id": "c1"})
        fake.collection("properties").document("p2").set({"customer_id": "c2"})
        results = database.get_properties_by_customer("c1")
        assert [r["id"] for r in results] == ["p1"]

    def test_get_active_leases(self, db):
        database, fake = db
        fake.collection("leases").document("l1").set({"status": "active", "customer_id": "c1"})
        fake.collection("leases").document("l2").set({"status": "ended", "customer_id": "c1"})
        fake.collection("leases").document("l3").set({"status": "active", "customer_id": "c2"})
        assert [r["id"] for r in database.get_active_leases()] == ["l1", "l3"]
        assert [r["id"] for r in database.get_active_leases(customer_id="c1")] == ["l1"]

    def test_get_lease_by_property(self, db):
        database, fake = db
        fake.collection("leases").document("l1").set({"property_id": "p1", "status": "active"})
        fake.collection("leases").document("l2").set({"property_id": "p1", "status": "ended"})
        lease = database.get_lease_by_property("p1")
        assert lease["id"] == "l1"
        assert database.get_lease_by_property("p2") is None


# ---------------------------------------------------------------------------
# Service requests
# ---------------------------------------------------------------------------


class TestServiceRequests:
    def test_create_service_request_defaults(self, db):
        database, fake = db
        doc_id = database.create_service_request({"issue": "AC leak"})
        stored = fake.collection("service_requests").document(doc_id).get().to_dict()
        assert stored["status"] == "open"
        assert isinstance(stored["created_at"], datetime)

    def test_create_service_request_respects_explicit_status(self, db):
        database, fake = db
        doc_id = database.create_service_request({"issue": "x", "status": "in_progress"})
        assert fake.collection("service_requests").document(doc_id).get().to_dict()["status"] == "in_progress"

    def test_get_service_requests_ordered_newest_first(self, db):
        database, fake = db
        fake.collection("service_requests").document("old").set(
            {"customer_id": "c1", "created_at": "2026-01-01T00:00:00"}
        )
        fake.collection("service_requests").document("new").set(
            {"customer_id": "c1", "created_at": "2026-06-01T00:00:00"}
        )
        results = database.get_service_requests(customer_id="c1")
        assert [r["id"] for r in results] == ["new", "old"]

    def test_get_service_requests_status_filter(self, db):
        database, fake = db
        fake.collection("service_requests").document("a").set(
            {"status": "open", "created_at": "2026-01-01T00:00:00"}
        )
        fake.collection("service_requests").document("b").set(
            {"status": "closed", "created_at": "2026-01-02T00:00:00"}
        )
        results = database.get_service_requests(status="closed")
        assert [r["id"] for r in results] == ["b"]

    def test_update_service_request_stamps_updated_at(self, db):
        database, fake = db
        fake.collection("service_requests").document("sr-1").set({"issue": "x"})
        assert database.update_service_request("sr-1", {"status": "closed"}) is True
        stored = fake.collection("service_requests").document("sr-1").get().to_dict()
        assert stored["status"] == "closed"
        assert isinstance(stored["updated_at"], datetime)


# ---------------------------------------------------------------------------
# Deals (SSN envelope encryption call sites)
# ---------------------------------------------------------------------------


class TestDeals:
    def test_create_deal_generates_id_and_stamps(self, db):
        database, fake = db
        doc_id = database.create_deal({"buyer_first_name": "Bo"})
        stored = fake.collection("deals").document(doc_id).get().to_dict()
        assert stored["status"] == "pending"
        assert isinstance(stored["created_at"], datetime)

    def test_create_deal_with_explicit_id(self, db):
        database, fake = db
        doc_id = database.create_deal({"id": "deal-9", "buyer_first_name": "Bo"})
        assert doc_id == "deal-9"
        assert fake.collection("deals").document("deal-9").get().exists

    def test_get_deal_missing(self, db):
        database, _fake = db
        assert database.get_deal("missing") is None

    def test_get_deal_stamps_id(self, db):
        database, _fake = db
        doc_id = database.create_deal({"buyer_first_name": "Bo"})
        deal = database.get_deal(doc_id)
        assert deal["id"] == doc_id

    def test_update_deal(self, db):
        database, fake = db
        doc_id = database.create_deal({"buyer_first_name": "Bo"})
        assert database.update_deal(doc_id, {"status": "funded"}) is True
        stored = fake.collection("deals").document(doc_id).get().to_dict()
        assert stored["status"] == "funded"
        assert isinstance(stored["updated_at"], datetime)

    def test_search_deals_by_status(self, db):
        database, _fake = db
        database.create_deal({"id": "d1", "status": "pending"})
        database.create_deal({"id": "d2", "status": "funded"})
        results = database.search_deals(status="funded")
        assert [r["id"] for r in results] == ["d2"]

    def test_search_deals_by_buyer_name_client_side(self, db):
        database, _fake = db
        database.create_deal({"id": "d1", "buyer_first_name": "Betty", "buyer_last_name": "Buyer"})
        database.create_deal({"id": "d2", "buyer_first_name": "Sam", "buyer_last_name": "Seller"})
        results = database.search_deals(buyer_name="betty buy")
        assert [r["id"] for r in results] == ["d1"]

    def test_search_deals_by_salesrep_client_side(self, db):
        database, _fake = db
        database.create_deal({"id": "d1", "salesrep": "Tanya Torres"})
        database.create_deal({"id": "d2", "salesrep": "Other Agent"})
        results = database.search_deals(salesrep="tanya")
        assert [r["id"] for r in results] == ["d1"]

    def test_archive_deal_soft_delete(self, db):
        database, fake = db
        database.create_deal({"id": "d1"})
        assert database.archive_deal("d1") is True
        stored = fake.collection("deals").document("d1").get().to_dict()
        assert stored["status"] == "archived"


# ---------------------------------------------------------------------------
# Tax history
# ---------------------------------------------------------------------------


class TestTaxHistory:
    def test_get_tax_history_ordered_desc(self, db):
        database, fake = db
        fake.collection("tax_payments").document("t2024").set(
            {"property_id": "p1", "tax_year": 2024}
        )
        fake.collection("tax_payments").document("t2025").set(
            {"property_id": "p1", "tax_year": 2025}
        )
        fake.collection("tax_payments").document("other").set(
            {"property_id": "p2", "tax_year": 2025}
        )
        results = database.get_tax_history("p1")
        assert [r["tax_year"] for r in results] == [2025, 2024]


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestGetDatabaseSingleton:
    def test_returns_same_instance(self, monkeypatch):
        import database.firestore_client as module

        monkeypatch.setattr(module, "_db_instance", None)
        first = module.get_database()
        assert module.get_database() is first
