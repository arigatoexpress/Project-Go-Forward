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


class DealStatus(str, Enum):
    """Deal/application lifecycle status — mirrors fastcontractdocs.com workflow"""
    PENDING = "pending"
    APPROVED = "approved"
    CONTRACT = "contract"
    FUNDED = "funded"
    COMPLETE = "complete"
    DENIED = "denied"
    ARCHIVED = "archived"


class Deal(BaseModel):
    """
    Customer application/deal record — replaces fastcontractdocs.com.
    Consolidates all buyer, co-buyer, home, loan, and transaction data
    into a single record that can be used to generate any document.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))

    # ─── Assignment ───
    salesrep: Optional[str] = None
    status: DealStatus = DealStatus.PENDING

    # ─── Buyer ───
    buyer_first_name: Optional[str] = None
    buyer_last_name: Optional[str] = None
    buyer_phone: Optional[str] = None
    buyer_email: Optional[str] = None
    buyer_ssn: Optional[str] = None
    buyer_marital_status: Optional[str] = None  # "Married", "Single", "Divorced", "Widowed"

    # ─── Co-Buyer ───
    co_buyer_first_name: Optional[str] = None
    co_buyer_last_name: Optional[str] = None
    co_buyer_phone: Optional[str] = None
    co_buyer_ssn: Optional[str] = None
    co_buyer_marital_status: Optional[str] = None

    # ─── Employment (Buyer) ───
    employer_name: Optional[str] = None
    occupation: Optional[str] = None
    occupation_length: Optional[str] = None  # years
    work_phone: Optional[str] = None
    self_employed: bool = False
    previous_employer: Optional[str] = None
    previous_occupation: Optional[str] = None

    # ─── Employment (Co-Buyer) ───
    co_buyer_employer: Optional[str] = None
    co_buyer_occupation: Optional[str] = None
    co_buyer_occupation_length: Optional[str] = None
    co_buyer_work_phone: Optional[str] = None
    co_buyer_self_employed: bool = False

    # ─── Mailing Address (current residence) ───
    mailing_address: Optional[str] = None
    mailing_city: Optional[str] = None
    mailing_state: str = "TX"
    mailing_zip: Optional[str] = None
    mailing_length: Optional[str] = None  # years at address
    mailing_own_rent: Optional[str] = None  # "Own" or "Rent"

    # ─── Installation Address (where the home goes) ───
    buyer_address: Optional[str] = None
    buyer_city: Optional[str] = None
    buyer_county: Optional[str] = None
    buyer_state: str = "TX"
    buyer_zip: Optional[str] = None

    # ─── References ───
    reference1_name: Optional[str] = None
    reference1_phone: Optional[str] = None
    reference2_name: Optional[str] = None
    reference2_phone: Optional[str] = None

    # ─── Home Info ───
    inventory_id: Optional[str] = None  # link to Inventory record
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    year: Optional[str] = None
    serial_number_1: Optional[str] = None
    serial_number_2: Optional[str] = None
    label_number_1: Optional[str] = None
    label_number_2: Optional[str] = None
    no_of_sections: Optional[str] = None
    is_new: bool = True

    # ─── Pricing ───
    sales_price: Optional[float] = None
    down_payment: Optional[float] = None

    # ─── Financing / Loan ───
    creditor_name: Optional[str] = None
    creditor_address: Optional[str] = None
    creditor_city_state_zip: Optional[str] = None
    creditor_phone: Optional[str] = None
    loan_term: Optional[str] = None
    apr: Optional[str] = None
    finance_charge: Optional[float] = None
    max_financed: Optional[float] = None
    total_payments: Optional[float] = None
    payment_start_date: Optional[str] = None
    insurance_premium: Optional[float] = None

    # ─── Notes ───
    notes: Optional[str] = None

    # ─── Timestamps ───
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        use_enum_values = True

    def to_document_data(self) -> dict:
        """
        Convert deal to field_map.json-compatible data dict for document generation.
        This bridges the Deal model to the existing document engine — enter once, generate many.
        All computed fields are derived here so the document engine gets a flat dict.
        """
        # ─── Computed: Full names ───
        buyer_name_parts = [p for p in [self.buyer_first_name, self.buyer_last_name] if p]
        buyer_name = " ".join(buyer_name_parts) if buyer_name_parts else None

        co_buyer_parts = [p for p in [self.co_buyer_first_name, self.co_buyer_last_name] if p]
        co_buyer_name = " ".join(co_buyer_parts) if co_buyer_parts else None

        # ─── Computed: Combined address strings ───
        buyer_city_state_zip_parts = [p for p in [self.buyer_city, self.buyer_state, self.buyer_zip] if p]
        buyer_city_state_zip = f"{self.buyer_city or ''}, {self.buyer_state or 'TX'} {self.buyer_zip or ''}".strip() if any([self.buyer_city, self.buyer_zip]) else None

        buyer_full_address = None
        if self.buyer_address:
            buyer_full_address = self.buyer_address
            if buyer_city_state_zip:
                buyer_full_address += f", {buyer_city_state_zip}"

        mailing_city_state_zip = None
        if any([self.mailing_city, self.mailing_zip]):
            mailing_city_state_zip = f"{self.mailing_city or ''}, {self.mailing_state or 'TX'} {self.mailing_zip or ''}".strip()

        mailing_full_address = None
        if self.mailing_address:
            mailing_full_address = self.mailing_address
            if mailing_city_state_zip:
                mailing_full_address += f", {mailing_city_state_zip}"

        # ─── Computed: Home strings ───
        manufacturer_model = None
        if self.manufacturer and self.model:
            manufacturer_model = f"{self.manufacturer} {self.model}"
        elif self.manufacturer:
            manufacturer_model = self.manufacturer
        elif self.model:
            manufacturer_model = self.model

        serial_label_combined = None
        parts = []
        if self.serial_number_1:
            parts.append(f"S/N: {self.serial_number_1}")
        if self.serial_number_2:
            parts.append(f"S/N2: {self.serial_number_2}")
        if self.label_number_1:
            parts.append(f"HUD: {self.label_number_1}")
        if self.label_number_2:
            parts.append(f"HUD2: {self.label_number_2}")
        if parts:
            serial_label_combined = " | ".join(parts)

        new_used_text = "New" if self.is_new else "Pre-Owned"

        # ─── Computed: Unpaid balance ───
        unpaid_balance = None
        if self.sales_price is not None and self.down_payment is not None:
            unpaid_balance = max(0, self.sales_price - self.down_payment)

        data = {
            # ─── Buyer ───
            "buyer_name": buyer_name,
            "buyer_first_name": self.buyer_first_name,
            "buyer_last_name": self.buyer_last_name,
            "buyer_address": self.buyer_address,
            "buyer_city": self.buyer_city,
            "buyer_county": self.buyer_county,
            "buyer_state": self.buyer_state,
            "buyer_zip": self.buyer_zip,
            "buyer_city_state_zip": buyer_city_state_zip,
            "buyer_full_address": buyer_full_address,
            "buyer_phone": self.buyer_phone,
            "buyer_email": self.buyer_email,
            "buyer_ssn": self.buyer_ssn,
            "buyer_marital_status": self.buyer_marital_status,

            # ─── Co-Buyer ───
            "co_buyer_name": co_buyer_name,
            "co_buyer_first_name": self.co_buyer_first_name,
            "co_buyer_last_name": self.co_buyer_last_name,
            "co_buyer_phone": self.co_buyer_phone,
            "co_buyer_ssn": self.co_buyer_ssn,
            "co_buyer_marital_status": self.co_buyer_marital_status,

            # ─── Employment (Buyer) ───
            "employer_name": self.employer_name,
            "occupation": self.occupation,
            "occupation_length": self.occupation_length,
            "work_phone": self.work_phone,
            "self_employed": self.self_employed,
            "previous_employer": self.previous_employer,
            "previous_occupation": self.previous_occupation,

            # ─── Employment (Co-Buyer) ───
            "co_buyer_employer": self.co_buyer_employer,
            "co_buyer_occupation": self.co_buyer_occupation,
            "co_buyer_occupation_length": self.co_buyer_occupation_length,
            "co_buyer_work_phone": self.co_buyer_work_phone,
            "co_buyer_self_employed": self.co_buyer_self_employed,

            # ─── Mailing Address ───
            "mailing_address": self.mailing_address,
            "mailing_city": self.mailing_city,
            "mailing_state": self.mailing_state,
            "mailing_zip": self.mailing_zip,
            "mailing_city_state_zip": mailing_city_state_zip,
            "mailing_full_address": mailing_full_address,
            "mailing_length": self.mailing_length,
            "mailing_own_rent": self.mailing_own_rent,

            # ─── References ───
            "reference1_name": self.reference1_name,
            "reference1_phone": self.reference1_phone,
            "reference2_name": self.reference2_name,
            "reference2_phone": self.reference2_phone,

            # ─── Seller (defaults) ───
            "seller_name": "Texas Home Outlet",
            "seller_phone": "(281) 555-0199",
            "seller_email": "sales@texashomeoutlet.com",
            "salesrep": self.salesrep,

            # ─── Home ───
            "manufacturer": self.manufacturer,
            "model": self.model,
            "manufacturer_model": manufacturer_model,
            "year": self.year,
            "serial_number_1": self.serial_number_1,
            "serial_number_2": self.serial_number_2,
            "label_number_1": self.label_number_1,
            "label_number_2": self.label_number_2,
            "serial_label_combined": serial_label_combined,
            "no_of_sections": self.no_of_sections,
            "is_new": self.is_new,
            "is_used": not self.is_new,
            "new_used_text": new_used_text,

            # ─── Pricing ───
            "sales_price": self.sales_price,
            "down_payment": self.down_payment,
            "unpaid_balance": unpaid_balance,
            "total_unpaid_balance": unpaid_balance,

            # ─── Financing ───
            "creditor_name": self.creditor_name,
            "creditor_address": self.creditor_address,
            "creditor_city_state_zip": self.creditor_city_state_zip,
            "creditor_phone": self.creditor_phone,
            "loan_term": self.loan_term,
            "apr": self.apr,
            "interest_rate": self.apr,  # alias
            "finance_charge": self.finance_charge,
            "max_financed": self.max_financed,
            "total_payments": self.total_payments,
            "payment_start_date": self.payment_start_date,
            "insurance_premium": self.insurance_premium,

            # ─── Transaction ───
            "date_of_sale": self.created_at.strftime("%Y-%m-%d") if self.created_at else None,
            "notes": self.notes,
        }

        # Strip None values — document engine handles missing fields via defaults
        return {k: v for k, v in data.items() if v is not None}


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
