"""
Firestore Client for THO Database
Handles all database operations for the AI agents
"""

import os
from typing import Optional, List, Dict, Any
from datetime import datetime
from google.cloud import firestore


class THODatabase:
    """Firestore database client for Texas Home Outlet"""
    
    def __init__(self, project_id: Optional[str] = None):
        self.project_id = project_id or os.getenv("GCP_PROJECT_ID", "texas-home-outlet")
        self._db = None
    
    @property
    def db(self) -> firestore.Client:
        """Lazy-load Firestore client"""
        if self._db is None:
            self._db = firestore.Client(project=self.project_id)
        return self._db
    
    # ============ CUSTOMERS ============
    
    def get_customer(self, customer_id: str) -> Optional[Dict]:
        """Get customer by ID"""
        doc = self.db.collection("customers").document(customer_id).get()
        return doc.to_dict() if doc.exists else None
    
    def search_customers(
        self,
        name: Optional[str] = None,
        phone: Optional[str] = None,
        email: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20
    ) -> List[Dict]:
        """Search customers by various criteria"""
        query = self.db.collection("customers")
        
        if status:
            query = query.where("status", "==", status)
        
        # Note: Firestore doesn't support LIKE queries
        # For name/phone/email search, we use client-side filtering
        results = []
        for doc in query.limit(limit * 3).stream():
            data = doc.to_dict()
            data["id"] = doc.id
            
            # Client-side filtering
            if name and name.lower() not in data.get("full_name", "").lower():
                continue
            if phone and phone not in data.get("phone", ""):
                continue
            if email and email.lower() not in data.get("email", "").lower():
                continue
            
            results.append(data)
            if len(results) >= limit:
                break
        
        return results
    
    def create_customer(self, data: Dict) -> str:
        """Create new customer, returns ID"""
        data["created_at"] = datetime.utcnow()
        data["updated_at"] = datetime.utcnow()
        doc_ref = self.db.collection("customers").document()
        doc_ref.set(data)
        return doc_ref.id
    
    def update_customer(self, customer_id: str, data: Dict) -> bool:
        """Update customer record"""
        data["updated_at"] = datetime.utcnow()
        self.db.collection("customers").document(customer_id).update(data)
        return True
    
    # ============ INVENTORY ============
    
    def get_inventory(self, inventory_id: str) -> Optional[Dict]:
        """Get inventory item by ID"""
        doc = self.db.collection("inventory").document(inventory_id).get()
        return doc.to_dict() if doc.exists else None
    
    def get_inventory_by_serial(self, serial_number: str) -> Optional[Dict]:
        """Get inventory by serial number"""
        docs = self.db.collection("inventory").where(
            "serial_number", "==", serial_number
        ).limit(1).stream()
        
        for doc in docs:
            data = doc.to_dict()
            data["id"] = doc.id
            return data
        return None
    
    def search_inventory(
        self,
        min_beds: Optional[int] = None,
        max_beds: Optional[int] = None,
        min_baths: Optional[int] = None,
        max_baths: Optional[int] = None,
        min_budget: Optional[float] = None,
        max_budget: Optional[float] = None,
        status: str = "AVAILABLE",
        manufacturer: Optional[str] = None,
        limit: int = 10
    ) -> List[Dict]:
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
    
    def create_inventory(self, data: Dict) -> str:
        """Add new inventory item"""
        doc_ref = self.db.collection("inventory").document()
        doc_ref.set(data)
        return doc_ref.id
    
    def update_inventory(self, inventory_id: str, data: Dict) -> bool:
        """Update inventory record"""
        self.db.collection("inventory").document(inventory_id).update(data)
        return True
    
    # ============ PROPERTIES ============
    
    def get_property(self, property_id: str) -> Optional[Dict]:
        """Get property by ID"""
        doc = self.db.collection("properties").document(property_id).get()
        return doc.to_dict() if doc.exists else None
    
    def search_properties_by_address(self, address: str) -> List[Dict]:
        """Search properties by address (partial match)"""
        results = []
        for doc in self.db.collection("properties").limit(100).stream():
            data = doc.to_dict()
            data["id"] = doc.id
            if address.lower() in data.get("address", "").lower():
                results.append(data)
        return results
    
    def get_properties_by_customer(self, customer_id: str) -> List[Dict]:
        """Get all properties owned by a customer"""
        results = []
        docs = self.db.collection("properties").where(
            "customer_id", "==", customer_id
        ).stream()
        
        for doc in docs:
            data = doc.to_dict()
            data["id"] = doc.id
            results.append(data)
        
        return results
    
    # ============ LEASES ============
    
    def get_active_leases(self, customer_id: Optional[str] = None) -> List[Dict]:
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
    
    def get_lease_by_property(self, property_id: str) -> Optional[Dict]:
        """Get active lease for a property"""
        docs = self.db.collection("leases").where(
            "property_id", "==", property_id
        ).where("status", "==", "active").limit(1).stream()
        
        for doc in docs:
            data = doc.to_dict()
            data["id"] = doc.id
            return data
        return None
    
    # ============ SERVICE REQUESTS ============
    
    def create_service_request(self, data: Dict) -> str:
        """Create new service request"""
        data["created_at"] = datetime.utcnow()
        data["updated_at"] = datetime.utcnow()
        data["status"] = data.get("status", "open")
        doc_ref = self.db.collection("service_requests").document()
        doc_ref.set(data)
        return doc_ref.id
    
    def get_service_requests(
        self,
        customer_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20
    ) -> List[Dict]:
        """Get service requests with optional filters"""
        query = self.db.collection("service_requests")
        
        if customer_id:
            query = query.where("customer_id", "==", customer_id)
        if status:
            query = query.where("status", "==", status)
        
        results = []
        for doc in query.order_by("created_at", direction=firestore.Query.DESCENDING).limit(limit).stream():
            data = doc.to_dict()
            data["id"] = doc.id
            results.append(data)
        
        return results
    
    def update_service_request(self, request_id: str, data: Dict) -> bool:
        """Update service request"""
        data["updated_at"] = datetime.utcnow()
        self.db.collection("service_requests").document(request_id).update(data)
        return True
    
    # ============ TAX PAYMENTS ============
    
    def get_tax_history(self, property_id: str) -> List[Dict]:
        """Get tax payment history for a property"""
        results = []
        docs = self.db.collection("tax_payments").where(
            "property_id", "==", property_id
        ).order_by("tax_year", direction=firestore.Query.DESCENDING).stream()
        
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
