"""
THO Database Models - Firestore Data Layer
Pydantic models matching the database schema for Texas Home Outlet
"""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field
import uuid


class CustomerStatus(str, Enum):
    ENROLLED = "ENROLLED"
    NON_ENROLLED = "NON_ENROLLED"
    LEAD = "LEAD"
    SOLD = "SOLD"


class InventoryStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    SOLD = "SOLD"
    PENDING = "PENDING"
    RESERVED = "RESERVED"


class Customer(BaseModel):
    """Customer/Tenant record"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    full_name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    status: CustomerStatus = CustomerStatus.LEAD
    
    # Billing info
    billing_account: Optional[str] = None  # e.g., "Prosperity Acquisitions LLC - 15th"
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        use_enum_values = True


class Property(BaseModel):
    """Physical property/land location"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    address: str
    city: Optional[str] = None
    state: str = "TX"
    zip_code: Optional[str] = None
    
    # Tax info
    county: Optional[str] = None
    school_district: Optional[str] = None
    county_account_number: Optional[str] = None
    county_tax_link: Optional[str] = None
    isd_tax_link: Optional[str] = None
    
    # Owner reference
    customer_id: Optional[str] = None
    
    class Config:
        use_enum_values = True


class Inventory(BaseModel):
    """Manufactured home inventory"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    serial_number: str
    model_name: str
    manufacturer: Optional[str] = None  # e.g., "Tru Belton", "Champion LA"
    
    # Pricing
    invoice_date: Optional[date] = None
    invoice_amount: Optional[float] = None
    msrp: Optional[float] = None
    
    # Specs (parsed from model name)
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    sqft: Optional[int] = None
    width: Optional[int] = None  # e.g., 14, 28, 32
    length: Optional[int] = None  # e.g., 60, 66, 76
    
    # Status
    status: InventoryStatus = InventoryStatus.AVAILABLE
    notes: Optional[str] = None
    
    # Media
    photos: List[str] = Field(default_factory=list)
    video_tour_url: Optional[str] = None
    
    class Config:
        use_enum_values = True


class Sale(BaseModel):
    """Links customer to purchased inventory"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    customer_id: str
    inventory_id: str
    
    # Sale details
    salesman: Optional[str] = None
    customer_number: Optional[str] = None  # 21st Mortgage customer #
    sale_date: Optional[date] = None
    sale_price: Optional[float] = None
    
    # Financing
    down_payment: Optional[float] = None
    financed_amount: Optional[float] = None
    monthly_payment: Optional[float] = None
    loan_term_months: Optional[int] = None
    interest_rate: Optional[float] = None
    
    # Status
    contract_status: str = "pending"  # pending, approved, funded, complete
    notes: Optional[str] = None


class Lease(BaseModel):
    """Active lease/rent-to-own agreement"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    customer_id: str
    property_id: str
    
    # Terms
    monthly_payment: float
    lease_start_date: Optional[date] = None
    lease_end_date: Optional[date] = None
    
    # Billing
    billing_account: Optional[str] = None
    payment_day: int = 1  # Day of month payment is due
    
    # Status
    status: str = "active"  # active, delinquent, paid_off, terminated


class TaxPayment(BaseModel):
    """Annual property tax records"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    property_id: str
    tax_year: int
    
    # Tax amounts
    county_taxes: Optional[float] = None
    school_taxes: Optional[float] = None
    total_taxes: Optional[float] = None
    
    # Escrow tracking
    escrow_balance: Optional[float] = None
    payment_date: Optional[date] = None
    
    notes: Optional[str] = None


class ServiceRequest(BaseModel):
    """Service/warranty requests from customers"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    customer_id: str
    property_id: Optional[str] = None
    inventory_id: Optional[str] = None  # For warranty lookup
    
    # Issue details
    issue_type: str  # structural, plumbing, electrical, hvac, cosmetic, appliance
    description: str
    photos: List[str] = Field(default_factory=list)
    
    # Warranty
    is_warranty_claim: bool = False
    warranty_status: Optional[str] = None  # covered, not_covered, pending
    warranty_notes: Optional[str] = None
    
    # Resolution
    status: str = "open"  # open, in_progress, scheduled, resolved, closed
    assigned_contractor: Optional[str] = None
    resolution_notes: Optional[str] = None
    resolved_date: Optional[date] = None
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
