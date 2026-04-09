"""Tests for Firestore customer persistence layer.

Verifies CRUD operations, search, sanitization, and edge cases
against the LIVE Firestore database (tho-ai-agent project).

Run: python -m pytest tests/test_firestore_customers.py -v
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from database.firestore_client import get_database

_db = get_database()
_TEST_PREFIX = "__test_"  # All test customers use this prefix for cleanup


@pytest.fixture(autouse=True)
def cleanup_test_customers():
    """Delete any test customers after each test."""
    yield
    for doc in _db.db.collection("customers").where(
        "_name_lower", ">=", _TEST_PREFIX
    ).where("_name_lower", "<", _TEST_PREFIX + "\uf8ff").stream():
        doc.reference.delete()


class TestCustomerCRUD:
    """Test create, read, update, delete cycle."""

    def test_create_and_get(self):
        name = f"{_TEST_PREFIX}Create Test"
        doc_id = _db.create_customer({"full_name": name, "phone": "555-000-0001"}, doc_id=f"{_TEST_PREFIX}crud1")
        customer = _db.get_customer(doc_id)
        assert customer is not None
        assert customer["full_name"] == name
        assert customer["phone"] == "555-000-0001"
        assert customer.get("_name_lower") == name.lower()

    def test_update_customer(self):
        doc_id = _db.create_customer({"full_name": f"{_TEST_PREFIX}Update Test"}, doc_id=f"{_TEST_PREFIX}upd1")
        _db.update_customer(doc_id, {"phone": "555-111-2222", "city": "Austin"})
        updated = _db.get_customer(doc_id)
        assert updated["phone"] == "555-111-2222"
        assert updated["city"] == "Austin"

    def test_delete_customer(self):
        doc_id = _db.create_customer({"full_name": f"{_TEST_PREFIX}Delete Test"}, doc_id=f"{_TEST_PREFIX}del1")
        assert _db.get_customer(doc_id) is not None
        _db.delete_customer(doc_id)
        assert _db.get_customer(doc_id) is None

    def test_get_nonexistent_returns_none(self):
        assert _db.get_customer("nonexistent-id-99999") is None


class TestCustomerSearch:
    """Test search functionality."""

    def test_search_by_name(self):
        _db.create_customer({"full_name": f"{_TEST_PREFIX}Searchable Person", "phone": "555-999-8888"}, doc_id=f"{_TEST_PREFIX}srch1")
        results = _db.search_customers(query_text=f"{_TEST_PREFIX}Searchable")
        assert len(results) >= 1
        assert any(r["full_name"] == f"{_TEST_PREFIX}Searchable Person" for r in results)

    def test_search_by_phone(self):
        _db.create_customer({"full_name": f"{_TEST_PREFIX}Phone Test", "phone": "555-777-3333"}, doc_id=f"{_TEST_PREFIX}srch2")
        results = _db.search_customers(query_text="5557773333")
        assert any("Phone Test" in r.get("full_name", "") for r in results)

    def test_search_by_status(self):
        _db.create_customer({"full_name": f"{_TEST_PREFIX}Status Enrolled", "status": "ENROLLED"}, doc_id=f"{_TEST_PREFIX}srch3")
        _db.create_customer({"full_name": f"{_TEST_PREFIX}Status Lead", "status": "LEAD"}, doc_id=f"{_TEST_PREFIX}srch4")
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
        assert counts["total"] >= 1963  # Migrated records
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

        # Verify all exist
        for cid in [f"{_TEST_PREFIX}batch1", f"{_TEST_PREFIX}batch2", f"{_TEST_PREFIX}batch3"]:
            c = _db.get_customer(cid)
            assert c is not None

        # Cleanup
        for cid in [f"{_TEST_PREFIX}batch1", f"{_TEST_PREFIX}batch2", f"{_TEST_PREFIX}batch3"]:
            _db.delete_customer(cid)
