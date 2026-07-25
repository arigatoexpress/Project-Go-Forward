"""Tests for Firestore customer persistence behavior.

These tests run against an in-memory Firestore-shaped fake. They must not touch
the live THO `/customers` collection or require ADC in CI.
"""

import sys
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from database.firestore_client import get_database

_db = get_database()
_TEST_PREFIX = "__test_"


class FakeDocumentSnapshot:
    def __init__(self, ref: "FakeDocumentReference", data: dict | None):
        self.reference = ref
        self.id = ref.id
        self.exists = data is not None
        self._data = deepcopy(data) if data is not None else None

    def to_dict(self) -> dict:
        return deepcopy(self._data or {})


class FakeDocumentReference:
    def __init__(self, collection: "FakeCollectionReference", doc_id: str):
        self._collection = collection
        self.id = doc_id

    def get(self, timeout: float | None = None) -> FakeDocumentSnapshot:
        return FakeDocumentSnapshot(self, self._collection._docs.get(self.id))

    def set(self, data: dict, timeout: float | None = None) -> None:
        self._collection._docs[self.id] = deepcopy(data)

    def update(self, data: dict, timeout: float | None = None) -> None:
        if self.id not in self._collection._docs:
            raise KeyError(self.id)
        self._collection._docs[self.id].update(deepcopy(data))

    def delete(self, timeout: float | None = None) -> None:
        self._collection._docs.pop(self.id, None)


class FakeQuery:
    def __init__(
        self,
        collection: "FakeCollectionReference",
        filters: list[tuple[str, str, object]] | None = None,
        limit_value: int | None = None,
    ):
        self._collection = collection
        self._filters = filters or []
        self._limit = limit_value

    def where(self, field: str, op: str, value: object) -> "FakeQuery":
        return FakeQuery(self._collection, [*self._filters, (field, op, value)], self._limit)

    def limit(self, limit_value: int) -> "FakeQuery":
        return FakeQuery(self._collection, list(self._filters), limit_value)

    def stream(self, timeout: float | None = None):
        emitted = 0
        for doc_id, data in self._collection._docs.items():
            if not self._matches(data):
                continue
            yield FakeDocumentSnapshot(FakeDocumentReference(self._collection, doc_id), data)
            emitted += 1
            if self._limit is not None and emitted >= self._limit:
                return

    def _matches(self, data: dict) -> bool:
        for field, op, value in self._filters:
            current = data.get(field)
            if op == "==" and current != value:
                return False
            if op == ">=" and not (current is not None and current >= value):
                return False
            if op == "<" and not (current is not None and current < value):
                return False
        return True


class FakeCollectionReference(FakeQuery):
    def __init__(self, docs: dict[str, dict]):
        self._docs = docs
        super().__init__(self)

    def document(self, doc_id: str | None = None) -> FakeDocumentReference:
        return FakeDocumentReference(self, doc_id or str(uuid4()))


class FakeBatch:
    def __init__(self):
        self._writes: list[tuple[FakeDocumentReference, dict]] = []

    def set(self, doc_ref: FakeDocumentReference, data: dict) -> None:
        self._writes.append((doc_ref, deepcopy(data)))

    def commit(self, timeout: float | None = None) -> None:
        for doc_ref, data in self._writes:
            doc_ref.set(data)
        self._writes.clear()


class FakeFirestore:
    def __init__(self):
        self._collections = {"customers": FakeCollectionReference(_seed_customers())}

    def collection(self, name: str) -> FakeCollectionReference:
        return self._collections.setdefault(name, FakeCollectionReference({}))

    def batch(self) -> FakeBatch:
        return FakeBatch()


def _seed_customers() -> dict[str, dict]:
    docs: dict[str, dict] = {}
    for i in range(1326):
        docs[f"enrolled-{i}"] = {
            "full_name": f"Enrolled Customer {i}",
            "_name_lower": f"enrolled customer {i}",
            "phone": f"555-100-{i:04d}",
            "status": "ENROLLED",
        }
    for i in range(629):
        docs[f"lead-{i}"] = {
            "full_name": f"Lead Customer {i}",
            "_name_lower": f"lead customer {i}",
            "phone": f"555-200-{i:04d}",
            "status": "LEAD",
        }
    for i in range(8):
        docs[f"closed-{i}"] = {
            "full_name": f"Closed Customer {i}",
            "_name_lower": f"closed customer {i}",
            "phone": f"555-300-{i:04d}",
            "status": "CLOSED",
        }
    return docs


@pytest.fixture(autouse=True)
def fake_firestore():
    """Use isolated in-memory data for every test."""
    _db._db = FakeFirestore()
    yield
    _db._db = None


class TestCustomerCRUD:
    """Test create, read, update, delete cycle."""

    def test_create_and_get(self):
        name = f"{_TEST_PREFIX}Create Test"
        doc_id = _db.create_customer(
            {"full_name": name, "phone": "555-000-0001"},
            doc_id=f"{_TEST_PREFIX}crud1",
        )
        customer = _db.get_customer(doc_id)
        assert customer is not None
        assert customer["full_name"] == name
        assert customer["phone"] == "555-000-0001"
        assert customer.get("_name_lower") == name.lower()

    def test_update_customer(self):
        doc_id = _db.create_customer(
            {"full_name": f"{_TEST_PREFIX}Update Test"},
            doc_id=f"{_TEST_PREFIX}upd1",
        )
        _db.update_customer(doc_id, {"phone": "555-111-2222", "city": "Austin"})
        updated = _db.get_customer(doc_id)
        assert updated["phone"] == "555-111-2222"
        assert updated["city"] == "Austin"

    def test_delete_customer(self):
        doc_id = _db.create_customer(
            {"full_name": f"{_TEST_PREFIX}Delete Test"},
            doc_id=f"{_TEST_PREFIX}del1",
        )
        assert _db.get_customer(doc_id) is not None
        _db.delete_customer(doc_id)
        assert _db.get_customer(doc_id) is None

    def test_get_nonexistent_returns_none(self):
        assert _db.get_customer("nonexistent-id-99999") is None


class TestCustomerSearch:
    """Test search functionality."""

    def test_search_by_name(self):
        _db.create_customer(
            {"full_name": f"{_TEST_PREFIX}Searchable Person", "phone": "555-999-8888"},
            doc_id=f"{_TEST_PREFIX}srch1",
        )
        results = _db.search_customers(query_text=f"{_TEST_PREFIX}Searchable")
        assert len(results) >= 1
        assert any(r["full_name"] == f"{_TEST_PREFIX}Searchable Person" for r in results)

    def test_search_by_phone(self):
        _db.create_customer(
            {"full_name": f"{_TEST_PREFIX}Phone Test", "phone": "555-777-3333"},
            doc_id=f"{_TEST_PREFIX}srch2",
        )
        results = _db.search_customers(query_text="5557773333")
        assert any("Phone Test" in r.get("full_name", "") for r in results)

    def test_search_by_phone_with_common_formatting(self):
        _db.create_customer(
            {"full_name": f"{_TEST_PREFIX}Formatted Phone Test", "phone": "555-777-3333"},
            doc_id=f"{_TEST_PREFIX}srch2formatted",
        )

        for query in ("555-777-3333", "(555) 777-3333", "555 777 3333"):
            results = _db.search_customers(query_text=query)
            assert any("Formatted Phone Test" in r.get("full_name", "") for r in results)

    def test_search_by_status(self):
        _db.create_customer(
            {"full_name": f"{_TEST_PREFIX}Status Enrolled", "status": "ENROLLED"},
            doc_id=f"{_TEST_PREFIX}srch3",
        )
        _db.create_customer(
            {"full_name": f"{_TEST_PREFIX}Status Lead", "status": "LEAD"},
            doc_id=f"{_TEST_PREFIX}srch4",
        )
        enrolled = _db.search_customers(query_text=_TEST_PREFIX, status="ENROLLED")
        assert all(r.get("status") == "ENROLLED" for r in enrolled)

    def test_empty_search_returns_results(self):
        results = _db.search_customers(limit=5)
        assert len(results) > 0

    def test_search_nonexistent_returns_empty(self):
        results = _db.search_customers(query_text="zzz_nonexistent_99999")
        assert len(results) == 0


class TestCustomerCount:
    """Test counting and statistics."""

    def test_count_returns_total(self):
        counts = _db.count_customers()
        assert counts["total"] >= 1963
        assert "by_status" in counts
        assert isinstance(counts["by_status"], dict)

    def test_count_has_status_breakdown(self):
        counts = _db.count_customers()
        assert "ENROLLED" in counts["by_status"]
        assert counts["by_status"]["ENROLLED"] > 0


class TestBatchCreate:
    """Test bulk import."""

    def test_batch_create_multiple(self):
        customers = [
            {"id": f"{_TEST_PREFIX}batch1", "full_name": f"{_TEST_PREFIX}Batch One"},
            {"id": f"{_TEST_PREFIX}batch2", "full_name": f"{_TEST_PREFIX}Batch Two"},
            {"id": f"{_TEST_PREFIX}batch3", "full_name": f"{_TEST_PREFIX}Batch Three"},
        ]
        written = _db.batch_create_customers(customers)
        assert written == 3

        for cid in [f"{_TEST_PREFIX}batch1", f"{_TEST_PREFIX}batch2", f"{_TEST_PREFIX}batch3"]:
            assert _db.get_customer(cid) is not None

        for cid in [f"{_TEST_PREFIX}batch1", f"{_TEST_PREFIX}batch2", f"{_TEST_PREFIX}batch3"]:
            _db.delete_customer(cid)
