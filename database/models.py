"""
THO Database Models - Firestore Data Layer
Pydantic models matching the database schema for Texas Home Outlet
"""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator
import re
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
    full_name: str = Field(min_length=1)
    phone: Optional[str] = None
    email: Optional[str] = None
    status: CustomerStatus = CustomerStatus.LEAD

    # Billing info
    billing_account: Optional[str] = None  # e.g., "Prosperity Acquisitions LLC - 15th"

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        if v is not None and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
            raise ValueError("Invalid email format")
        return v

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v):
        if v is not None and len(re.sub(r"\D", "", v)) < 10:
            raise ValueError("Phone must contain at least 10 digits")
        return v

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
    serial_number: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    manufacturer: Optional[str] = None  # e.g., "Tru Belton", "Champion LA"

    # Pricing
    invoice_date: Optional[date] = None
    invoice_amount: Optional[float] = Field(default=None, ge=0)
    msrp: Optional[float] = Field(default=None, ge=0)

    # Specs (parsed from model name)
    bedrooms: Optional[int] = Field(default=None, ge=0, le=10)
    bathrooms: Optional[int] = Field(default=None, ge=0, le=10)
    sqft: Optional[int] = Field(default=None, ge=0)
    width: Optional[int] = Field(default=None, ge=0)   # e.g., 14, 28, 32
    length: Optional[int] = Field(default=None, ge=0)  # e.g., 60, 66, 76

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
    sale_price: Optional[float] = Field(default=None, ge=0)

    # Financing
    down_payment: Optional[float] = Field(default=None, ge=0)
    financed_amount: Optional[float] = Field(default=None, ge=0)
    monthly_payment: Optional[float] = Field(default=None, ge=0)
    loan_term_months: Optional[int] = Field(default=None, ge=0)
    interest_rate: Optional[float] = Field(default=None, ge=0, le=100)

    # Status
    contract_status: str = "pending"  # pending, approved, funded, complete
    notes: Optional[str] = None


class Lease(BaseModel):
    """Active lease/rent-to-own agreement"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    customer_id: str
    property_id: str

    # Terms
    monthly_payment: float = Field(ge=0)
    lease_start_date: Optional[date] = None
    lease_end_date: Optional[date] = None

    # Billing
    billing_account: Optional[str] = None
    payment_day: int = Field(default=1, ge=1, le=31)  # Day of month payment is due

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
    sales_price: Optional[float] = Field(default=None, ge=0)
    down_payment: Optional[float] = Field(default=None, ge=0)

    # ─── Financing / Loan ───
    creditor_name: Optional[str] = None
    creditor_address: Optional[str] = None
    creditor_city_state_zip: Optional[str] = None
    creditor_phone: Optional[str] = None
    loan_term: Optional[str] = None
    apr: Optional[str] = None
    finance_charge: Optional[float] = Field(default=None, ge=0)
    max_financed: Optional[float] = Field(default=None, ge=0)
    total_payments: Optional[float] = Field(default=None, ge=0)
    payment_start_date: Optional[str] = None
    insurance_premium: Optional[float] = Field(default=None, ge=0)

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


# ═══════════════════════════════════════════════════════════════════════════════
# LINEAR-INSPIRED PROJECT MANAGEMENT MODELS (AI PM Manager Core)
# ═══════════════════════════════════════════════════════════════════════════════

class WorkflowState(str, Enum):
    """Linear-inspired workflow states for any project/task"""
    BACKLOG = "backlog"           # Not ready to start
    TODO = "todo"                 # Ready to start
    IN_PROGRESS = "in_progress"   # Actively being worked
    IN_REVIEW = "in_review"       # Needs review/approval
    DONE = "done"                 # Completed
    CANCELLED = "cancelled"       # Won't do
    BLOCKED = "blocked"           # Blocked by dependency


class TaskPriority(int, Enum):
    """Priority levels - like Linear"""
    NO_PRIORITY = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    URGENT = 4


class ProjectType(str, Enum):
    """Types of projects the AI PM can manage"""
    SOFTWARE = "software"         # Code repos (Sapphire, etc.)
    BUSINESS = "business"         # Business operations (THO)
    RESEARCH = "research"         # R&D, experiments
    INFRASTRUCTURE = "infrastructure"  # DevOps, deployments


class Project(BaseModel):
    """
    A project managed by the AI PM - like a Linear project/workspace
    Can represent a GitHub repo, business initiative, etc.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    
    # Identity
    name: str = Field(min_length=1)
    description: Optional[str] = None
    project_type: ProjectType = ProjectType.BUSINESS
    
    # External links
    github_repo: Optional[str] = None       # e.g., "arigatoexpress/Sapphire"
    cloud_run_service: Optional[str] = None # e.g., "sapphire-dashboard"
    notion_url: Optional[str] = None
    
    # Status
    status: str = "active"  # active, paused, archived
    
    # Team
    owner: Optional[str] = None
    members: List[str] = Field(default_factory=list)
    
    # Stats
    total_tasks: int = 0
    completed_tasks: int = 0
    
    # For software projects
    default_branch: str = "main"
    deployment_url: Optional[str] = None
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Task(BaseModel):
    """
    Universal task model - like a Linear issue
    Works for software tasks, business tasks, service requests
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    
    # Content
    title: str = Field(min_length=1)
    description: Optional[str] = None
    
    # Organization
    project_id: str  # Links to Project
    state: WorkflowState = WorkflowState.BACKLOG
    priority: TaskPriority = TaskPriority.NO_PRIORITY
    labels: List[str] = Field(default_factory=list)  # e.g., ["bug", "urgent"]
    
    # Assignment
    assignee: Optional[str] = None
    creator: Optional[str] = None
    
    # Relations
    parent_task_id: Optional[str] = None      # Subtasks
    related_github_issue: Optional[int] = None
    related_github_pr: Optional[int] = None
    related_deal_id: Optional[str] = None     # Link to business deal
    
    # Estimation (like Linear)
    estimate_hours: Optional[float] = None
    actual_hours: Optional[float] = None
    
    # Timing
    due_date: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    # State history for audit trail
    state_history: List[Dict] = Field(default_factory=list)
    # [{"from": "backlog", "to": "in_progress", "at": "2026-03-02T...", "by": "user"}]
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    def transition_to(self, new_state: WorkflowState, changed_by: Optional[str] = None):
        """Record state transition with audit trail"""
        self.state_history.append({
            "from": self.state,
            "to": new_state,
            "at": datetime.utcnow().isoformat(),
            "by": changed_by or "system"
        })
        self.state = new_state
        self.updated_at = datetime.utcnow()
        
        if new_state == WorkflowState.IN_PROGRESS and not self.started_at:
            self.started_at = datetime.utcnow()
        if new_state == WorkflowState.DONE and not self.completed_at:
            self.completed_at = datetime.utcnow()


class Cycle(BaseModel):
    """
    Time-boxed planning period - like Linear's cycles
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    
    name: str = Field(min_length=1)  # e.g., "Week 9", "March Sprint"
    description: Optional[str] = None
    
    # Timing
    start_date: datetime
    end_date: datetime
    
    # Status
    status: str = "planning"  # planning, active, completed
    
    # Goals
    goals: List[str] = Field(default_factory=list)
    
    # Projects included in this cycle
    project_ids: List[str] = Field(default_factory=list)
    
    # Stats
    total_tasks: int = 0
    completed_tasks: int = 0
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    def is_active(self) -> bool:
        now = datetime.utcnow()
        return self.start_date <= now <= self.end_date and self.status == "active"


class Activity(BaseModel):
    """
    Activity log - like Linear's issue history
    Tracks everything that happens across all projects
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    
    # What happened
    activity_type: str  # "task_created", "state_changed", "comment_added", "assigned"
    description: str    # Human-readable summary
    
    # Where
    project_id: str
    task_id: Optional[str] = None
    
    # Who
    actor: Optional[str] = None  # User or "AI PM"
    
    # Details
    metadata: Dict = Field(default_factory=dict)
    # e.g., {"from_state": "backlog", "to_state": "in_progress"}
    
    created_at: datetime = Field(default_factory=datetime.utcnow)


class View(BaseModel):
    """
    Saved view configuration - like Linear's custom views
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(min_length=1)
    
    # Filters
    project_id: Optional[str] = None
    filter_states: List[WorkflowState] = Field(default_factory=list)
    filter_labels: List[str] = Field(default_factory=list)
    filter_assignee: Optional[str] = None
    filter_priority: Optional[TaskPriority] = None
    
    # Grouping & sorting
    group_by: str = "state"  # state, assignee, priority, project
    sort_by: str = "priority"
    sort_order: str = "desc"  # asc, desc
    
    # Owner
    owner_id: str
    is_shared: bool = False
    
    created_at: datetime = Field(default_factory=datetime.utcnow)


# Default label suggestions for AI PM
DEFAULT_PM_LABELS = [
    ("bug", "#ef4444"),
    ("feature", "#22c55e"),
    ("improvement", "#3b82f6"),
    ("documentation", "#a855f7"),
    ("urgent", "#f97316"),
    ("debt", "#6b7280"),
    ("research", "#ec4899"),
]
