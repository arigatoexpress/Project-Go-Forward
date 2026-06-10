"""
Firestore Client for THO Database
Handles all database operations for the AI agents
"""

import os
import re
from datetime import datetime
from typing import Any

from google.cloud import firestore


class THODatabase:
    """Firestore database client for Texas Home Outlet"""

    def __init__(self, project_id: str | None = None):
        self.project_id = (
            project_id
            or os.getenv("GCP_PROJECT_ID")
            or os.getenv("GOOGLE_CLOUD_PROJECT", "tho-ai-agent")
        )
        self._db = None

    @property
    def db(self) -> firestore.Client:
        """Lazy-load Firestore client"""
        if self._db is None:
            self._db = firestore.Client(project=self.project_id)
        return self._db

    # ============ CUSTOMERS ============

    def get_customer(self, customer_id: str) -> dict | None:
        """Get customer by document ID or legacy_id."""
        # Try direct document lookup first
        doc = self.db.collection("customers").document(customer_id).get()
        if doc.exists:
            data = doc.to_dict()
            data["id"] = doc.id
            return data
        # Fall back to legacy_id search
        docs = (
            self.db.collection("customers").where("legacy_id", "==", customer_id).limit(1).stream()
        )
        for doc in docs:
            data = doc.to_dict()
            data["id"] = doc.id
            return data
        return None

    def search_customers(
        self,
        query_text: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """
        Search customers by free-text query across name, phone, email, legacy_id, salesrep.

        Optimization: if the query looks like a name (alphabetic), use Firestore's native
        range query on the `_name_lower` indexed field for prefix matching. This avoids
        scanning all documents. Falls back to client-side filtering for phone/email/id queries.
        """
        query = self.db.collection("customers")

        if status:
            query = query.where("status", "==", status.upper())

        q_lower = (query_text or "").lower().strip()
        q_digits = re.sub(r"\D", "", q_lower)

        # Fast path: name prefix search using Firestore index
        if q_lower and q_lower.isalpha() and len(q_lower) >= 2:
            name_query = (
                query.where("_name_lower", ">=", q_lower)
                .where("_name_lower", "<", q_lower + "\uf8ff")
                .limit(limit)
            )
            results = []
            for doc in name_query.stream():
                data = doc.to_dict()
                data["id"] = doc.id
                results.append(data)
            if results:
                return results
            # If no results from prefix index, fall through to full scan
            # (handles cases where _name_lower hasn't been backfilled yet)

        # Slow path: full scan with client-side filtering
        scan_limit = 6000 if q_lower else limit
        results = []

        for doc in query.limit(scan_limit).stream():
            data = doc.to_dict()
            data["id"] = doc.id

            if q_lower:
                phone_digits = re.sub(r"\D", "", data.get("phone") or "")
                searchable = " ".join(
                    [
                        (data.get("full_name") or ""),
                        (data.get("email") or ""),
                        (data.get("phone") or ""),
                        phone_digits,
                        (data.get("legacy_id") or ""),
                        (data.get("salesrep") or ""),
                    ]
                ).lower()
                if q_lower not in searchable and (not q_digits or q_digits not in phone_digits):
                    continue

            results.append(data)
            if len(results) >= limit:
                break

        return results

    def create_customer(self, data: dict, doc_id: str | None = None) -> str:
        """Create new customer. Use doc_id to set a specific document ID (e.g. for migration)."""
        data["created_at"] = data.get("created_at") or datetime.utcnow()
        data["updated_at"] = data.get("updated_at") or datetime.utcnow()
        # Indexed field for fast prefix search
        if data.get("full_name"):
            data["_name_lower"] = data["full_name"].lower().strip()
        if doc_id:
            doc_ref = self.db.collection("customers").document(doc_id)
        else:
            doc_ref = self.db.collection("customers").document()
        doc_ref.set(data)
        return doc_ref.id

    def update_customer(self, customer_id: str, data: dict) -> bool:
        """Update customer record."""
        data["updated_at"] = datetime.utcnow()
        self.db.collection("customers").document(customer_id).update(data)
        return True

    def delete_customer(self, customer_id: str) -> bool:
        """Delete a customer document."""
        self.db.collection("customers").document(customer_id).delete()
        return True

    def count_customers(self) -> dict[str, Any]:
        """Get total customer count and breakdown by status."""
        totals: dict[str, int] = {}
        count = 0
        for doc in self.db.collection("customers").stream():
            count += 1
            data = doc.to_dict()
            s = data.get("status", "UNKNOWN")
            totals[s] = totals.get(s, 0) + 1
        return {"total": count, "by_status": totals}

    def batch_create_customers(self, customers: list[dict], batch_size: int = 400) -> int:
        """
        Bulk-import customers using Firestore batch writes (max 500 per batch).
        Each customer dict should have an 'id' key used as the document ID.
        Returns the number of records written.
        """
        written = 0
        batch = self.db.batch()
        pending = 0

        for cust in customers:
            doc_id = cust.pop("id", None) or cust.get("legacy_id")
            doc_ref = self.db.collection("customers").document(doc_id or None)
            batch.set(doc_ref, cust)
            pending += 1

            if pending >= batch_size:
                batch.commit()
                written += pending
                batch = self.db.batch()
                pending = 0

        if pending:
            batch.commit()
            written += pending

        return written

    # ============ INVENTORY ============

    def get_inventory(self, inventory_id: str) -> dict | None:
        """Get inventory item by ID"""
        doc = self.db.collection("inventory").document(inventory_id).get()
        return doc.to_dict() if doc.exists else None

    def get_inventory_by_id(self, inventory_id: str) -> dict | None:
        """Alias for get_inventory"""
        return self.get_inventory(inventory_id)

    def get_inventory_by_serial(self, serial_number: str) -> dict | None:
        """Get inventory by serial number"""
        docs = (
            self.db.collection("inventory")
            .where("serial_number", "==", serial_number)
            .limit(1)
            .stream()
        )

        for doc in docs:
            data = doc.to_dict()
            data["id"] = doc.id
            return data
        return None

    def get_inventory_by_serials(self, serial_numbers: list[str]) -> dict[str, dict]:
        """Batch lookup of inventory by serial numbers.

        Returns a dict keyed by serial_number. Firestore's `in` query supports
        up to 30 values per call (as of client 2.22.0), so we chunk the input.
        Empty/None serials are skipped. Duplicate serials in input are
        deduplicated; if multiple inventory docs share a serial, the first
        one wins (matches the single-lookup variant's behaviour).
        """
        unique = list({s for s in serial_numbers if s})
        if not unique:
            return {}

        result: dict[str, dict] = {}
        # Firestore `in` queries cap at 30 values
        chunk_size = 30
        for i in range(0, len(unique), chunk_size):
            chunk = unique[i : i + chunk_size]
            docs = self.db.collection("inventory").where("serial_number", "in", chunk).stream()
            for doc in docs:
                data = doc.to_dict()
                data["id"] = doc.id
                serial = data.get("serial_number")
                if serial and serial not in result:
                    result[serial] = data
        return result

    def search_inventory(
        self,
        min_beds: int | None = None,
        max_beds: int | None = None,
        min_baths: int | None = None,
        max_baths: int | None = None,
        min_budget: float | None = None,
        max_budget: float | None = None,
        status: str = "AVAILABLE",
        manufacturer: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Search available inventory"""
        query = self.db.collection("inventory")

        if status:
            query = query.where("status", "==", status)

        if manufacturer:
            query = query.where("manufacturer", "==", manufacturer)

        results = []
        for doc in query.limit(limit * 3).stream():
            data = doc.to_dict()
            data["id"] = doc.id

            # Client-side filtering for ranges
            beds = data.get("bedrooms", 0)
            baths = data.get("bathrooms", 0)
            price = data.get("msrp", 0)

            if min_beds and beds < min_beds:
                continue
            if max_beds and beds > max_beds:
                continue
            if min_baths and baths < min_baths:
                continue
            if max_baths and baths > max_baths:
                continue
            if min_budget and price < min_budget:
                continue
            if max_budget and price > max_budget:
                continue

            results.append(data)
            if len(results) >= limit:
                break

        return results

    def create_inventory(self, data: dict) -> str:
        """Add new inventory item"""
        doc_ref = self.db.collection("inventory").document()
        doc_ref.set(data)
        return doc_ref.id

    def update_inventory(self, inventory_id: str, data: dict) -> bool:
        """Update inventory record"""
        self.db.collection("inventory").document(inventory_id).update(data)
        return True

    # ============ PROPERTIES ============

    def get_property(self, property_id: str) -> dict | None:
        """Get property by ID"""
        doc = self.db.collection("properties").document(property_id).get()
        return doc.to_dict() if doc.exists else None

    def search_properties_by_address(self, address: str) -> list[dict]:
        """Search properties by address (partial match)"""
        results = []
        for doc in self.db.collection("properties").limit(100).stream():
            data = doc.to_dict()
            data["id"] = doc.id
            if address.lower() in data.get("address", "").lower():
                results.append(data)
        return results

    def get_properties_by_customer(self, customer_id: str) -> list[dict]:
        """Get all properties owned by a customer"""
        results = []
        docs = self.db.collection("properties").where("customer_id", "==", customer_id).stream()

        for doc in docs:
            data = doc.to_dict()
            data["id"] = doc.id
            results.append(data)

        return results

    # ============ LEASES ============

    def get_active_leases(self, customer_id: str | None = None) -> list[dict]:
        """Get active leases, optionally filtered by customer"""
        query = self.db.collection("leases").where("status", "==", "active")

        if customer_id:
            query = query.where("customer_id", "==", customer_id)

        results = []
        for doc in query.stream():
            data = doc.to_dict()
            data["id"] = doc.id
            results.append(data)

        return results

    def get_lease_by_property(self, property_id: str) -> dict | None:
        """Get active lease for a property"""
        docs = (
            self.db.collection("leases")
            .where("property_id", "==", property_id)
            .where("status", "==", "active")
            .limit(1)
            .stream()
        )

        for doc in docs:
            data = doc.to_dict()
            data["id"] = doc.id
            return data
        return None

    # ============ SERVICE REQUESTS ============

    def create_service_request(self, data: dict) -> str:
        """Create new service request"""
        data["created_at"] = datetime.utcnow()
        data["updated_at"] = datetime.utcnow()
        data["status"] = data.get("status", "open")
        doc_ref = self.db.collection("service_requests").document()
        doc_ref.set(data)
        return doc_ref.id

    def get_service_requests(
        self, customer_id: str | None = None, status: str | None = None, limit: int = 20
    ) -> list[dict]:
        """Get service requests with optional filters"""
        query = self.db.collection("service_requests")

        if customer_id:
            query = query.where("customer_id", "==", customer_id)
        if status:
            query = query.where("status", "==", status)

        results = []
        for doc in (
            query.order_by("created_at", direction=firestore.Query.DESCENDING).limit(limit).stream()
        ):
            data = doc.to_dict()
            data["id"] = doc.id
            results.append(data)

        return results

    def update_service_request(self, request_id: str, data: dict) -> bool:
        """Update service request"""
        data["updated_at"] = datetime.utcnow()
        self.db.collection("service_requests").document(request_id).update(data)
        return True

    # ============ DEALS (replaces fastcontractdocs.com) ============

    def create_deal(self, data: dict) -> str:
        """Create new deal/application, returns deal ID"""
        data["created_at"] = datetime.utcnow()
        data["updated_at"] = datetime.utcnow()
        data.setdefault("status", "pending")
        deal_id = data.pop("id", None)
        if deal_id:
            doc_ref = self.db.collection("deals").document(deal_id)
            doc_ref.set(data)
            return deal_id
        else:
            doc_ref = self.db.collection("deals").document()
            doc_ref.set(data)
            return doc_ref.id

    def get_deal(self, deal_id: str) -> dict | None:
        """Get deal by ID"""
        doc = self.db.collection("deals").document(deal_id).get()
        if doc.exists:
            data = doc.to_dict()
            data["id"] = doc.id
            return data
        return None

    def update_deal(self, deal_id: str, data: dict) -> bool:
        """Update deal record"""
        data["updated_at"] = datetime.utcnow()
        self.db.collection("deals").document(deal_id).update(data)
        return True

    def search_deals(
        self,
        status: str | None = None,
        salesrep: str | None = None,
        buyer_name: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Search deals by status, salesrep, or buyer name.
        When text search is active, scans more records since Firestore has no LIKE operator."""
        query = self.db.collection("deals")

        if status:
            query = query.where("status", "==", status)

        # When doing text search, scan more records to find matches across the full dataset
        needs_text_filter = bool(buyer_name or salesrep)
        scan_limit = 3000 if needs_text_filter else limit * 3

        results = []
        for doc in (
            query.order_by("updated_at", direction=firestore.Query.DESCENDING)
            .limit(scan_limit)
            .stream()
        ):
            data = doc.to_dict()
            data["id"] = doc.id

            # Client-side filtering for name and salesrep (Firestore doesn't support LIKE)
            if salesrep and salesrep.lower() not in (data.get("salesrep") or "").lower():
                continue

            if buyer_name:
                full_name = f"{data.get('buyer_first_name', '')} {data.get('buyer_last_name', '')}".strip().lower()
                if buyer_name.lower() not in full_name:
                    continue

            results.append(data)
            if len(results) >= limit:
                break

        return results

    def archive_deal(self, deal_id: str) -> bool:
        """Archive a deal (soft delete — sets status to archived)"""
        self.db.collection("deals").document(deal_id).update(
            {"status": "archived", "updated_at": datetime.utcnow()}
        )
        return True

    # ============ TAX PAYMENTS ============

    def get_tax_history(self, property_id: str) -> list[dict]:
        """Get tax payment history for a property"""
        results = []
        docs = (
            self.db.collection("tax_payments")
            .where("property_id", "==", property_id)
            .order_by("tax_year", direction=firestore.Query.DESCENDING)
            .stream()
        )

        for doc in docs:
            data = doc.to_dict()
            data["id"] = doc.id
            results.append(data)

        return results


# Singleton instance
_db_instance = None


def get_database() -> THODatabase:
    """Get singleton database instance"""
    global _db_instance
    if _db_instance is None:
        _db_instance = THODatabase()
    return _db_instance
