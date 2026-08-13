"""
THO Database Models - Firestore Data Layer
Pydantic models matching the database schema for Texas Home Outlet
"""

import hashlib
import json
import re
import uuid
from datetime import UTC, date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from scripts.google_ads_access_evidence import (
    MAX_EVIDENCE_TTL,
    AccessCheckKey,
    AccessEvidenceStatus,
    InvalidAccessEvidence,
    compute_evidence_digest,
)

_GOOGLE_ADS_SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"
_GOOGLE_ADS_DEPLOYMENT_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,62}--[0-9a-f]{64}$"
_GOOGLE_ADS_DEPLOYMENT_KEY_PATTERN = r"^[a-z0-9][a-z0-9-]{0,62}$"
_GOOGLE_ADS_CONTRACT_LABEL_PATTERN = r"^tho-contract-[0-9a-f]{12}$"
GoogleAdsDeploymentState = Literal[
    "INTERNAL_DRAFT",
    "SERVER_VALIDATED",
    "PAUSED_CREATE_APPROVED",
    "PAUSED_CREATED",
]
GoogleAdsAuthorityEventType = Literal[
    "INTERNAL_DRAFT_CREATED",
    "SERVER_VALIDATED",
    "PAUSED_CREATE_APPROVED",
    "PAUSED_CREATE_CLAIMED",
    "PAUSED_CREATE_RECLAIMED",
    "PAUSED_CREATE_FENCED",
    "PAUSED_CREATE_FENCED_FAILED",
    "PAUSED_CREATE_RECONCILIATION_CLAIMED",
    "PAUSED_CREATE_COMPLETED",
    "PAUSED_CREATE_CLAIM_RELEASED",
]
GoogleAdsPersistedErrorCode = Literal[
    "contract_mismatch",
    "invalid_create_graph",
    "ledger_write_failed",
    "provider_contract_mismatch",
    "provider_create_failed",
    "provider_not_paused",
    "provider_reconciliation_failed",
    "provider_timeout_unresolved",
    "provider_validation_failed",
]
GoogleAdsOutboxState = Literal["PENDING", "DISPATCHING", "DISPATCHED"]

_GOOGLE_ADS_EVENT_SEMANTICS = {
    "INTERNAL_DRAFT_CREATED": (None, "INTERNAL_DRAFT", False, False),
    "SERVER_VALIDATED": ("INTERNAL_DRAFT", "SERVER_VALIDATED", False, False),
    "PAUSED_CREATE_APPROVED": (
        "SERVER_VALIDATED",
        "PAUSED_CREATE_APPROVED",
        False,
        False,
    ),
    "PAUSED_CREATE_CLAIMED": (
        "PAUSED_CREATE_APPROVED",
        "PAUSED_CREATE_APPROVED",
        True,
        False,
    ),
    "PAUSED_CREATE_RECLAIMED": (
        "PAUSED_CREATE_APPROVED",
        "PAUSED_CREATE_APPROVED",
        True,
        False,
    ),
    "PAUSED_CREATE_FENCED": (
        "PAUSED_CREATE_APPROVED",
        "PAUSED_CREATE_APPROVED",
        True,
        False,
    ),
    "PAUSED_CREATE_FENCED_FAILED": (
        "PAUSED_CREATE_APPROVED",
        "PAUSED_CREATE_APPROVED",
        True,
        True,
    ),
    "PAUSED_CREATE_RECONCILIATION_CLAIMED": (
        "PAUSED_CREATE_APPROVED",
        "PAUSED_CREATE_APPROVED",
        True,
        False,
    ),
    "PAUSED_CREATE_COMPLETED": (
        "PAUSED_CREATE_APPROVED",
        "PAUSED_CREATED",
        True,
        False,
    ),
    "PAUSED_CREATE_CLAIM_RELEASED": (
        "PAUSED_CREATE_APPROVED",
        "PAUSED_CREATE_APPROVED",
        True,
        True,
    ),
}


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


class GoogleAdsDeploymentRecord(BaseModel):
    """Strict, sanitized Firestore authority record for a paused-only deployment.

    The schema intentionally has no account ID, credential, provider resource
    name, request ID, provider response, or arbitrary metadata field. Unknown
    fields fail validation instead of being silently retained.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    deployment_id: str = Field(pattern=_GOOGLE_ADS_DEPLOYMENT_ID_PATTERN)
    deployment_key: str = Field(pattern=_GOOGLE_ADS_DEPLOYMENT_KEY_PATTERN)
    contract_hash: str = Field(pattern=_GOOGLE_ADS_SHA256_PATTERN)
    contract_label: str = Field(pattern=_GOOGLE_ADS_CONTRACT_LABEL_PATTERN)
    state: GoogleAdsDeploymentState = "INTERNAL_DRAFT"
    version: int = Field(default=1, ge=1)
    worker_claim_hash: str | None = Field(default=None, pattern=_GOOGLE_ADS_SHA256_PATTERN)
    claim_expires_at: datetime | None = None
    create_fenced_at: datetime | None = None
    create_fence_claim_hash: str | None = Field(default=None, pattern=_GOOGLE_ADS_SHA256_PATTERN)
    provider_reference_hash: str | None = Field(default=None, pattern=_GOOGLE_ADS_SHA256_PATTERN)
    error_code: GoogleAdsPersistedErrorCode | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", "claim_expires_at", "create_fenced_at")
    @classmethod
    def timestamps_are_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value) if value is not None else None

    @model_validator(mode="after")
    def claim_and_terminal_state_are_consistent(self):
        digest = self.contract_hash.removeprefix("sha256:")
        if self.deployment_id != f"{self.deployment_key}--{digest}":
            raise ValueError("deployment identity does not match contract digest")
        if self.contract_label != f"tho-contract-{digest[:12]}":
            raise ValueError("contract label does not match contract digest")
        if (self.worker_claim_hash is None) != (self.claim_expires_at is None):
            raise ValueError("worker claim hash and expiry must be set together")
        if self.state != "PAUSED_CREATE_APPROVED" and self.worker_claim_hash is not None:
            raise ValueError("worker claims are allowed only while paused-create is approved")
        if self.state != "PAUSED_CREATE_APPROVED" and self.error_code is not None:
            raise ValueError("errors are allowed only while paused-create is approved")
        if self.state == "PAUSED_CREATED":
            if self.provider_reference_hash is None:
                raise ValueError("paused-created record requires a provider reference hash")
            if self.create_fenced_at is None:
                raise ValueError("paused-created record requires a durable create fence")
            if self.create_fence_claim_hash is not None:
                raise ValueError("paused-created record cannot retain a fence claimant")
        elif self.provider_reference_hash is not None:
            raise ValueError("provider reference hash is allowed only after paused creation")
        if self.create_fenced_at is not None:
            if self.state not in {"PAUSED_CREATE_APPROVED", "PAUSED_CREATED"}:
                raise ValueError("create fence is allowed only after paused-create approval")
            if not self.created_at <= self.create_fenced_at <= self.updated_at:
                raise ValueError("create fence timestamp must be within the record lifetime")
            if self.state == "PAUSED_CREATE_APPROVED":
                if self.worker_claim_hash is None or self.create_fence_claim_hash is None:
                    raise ValueError("an active create fence must retain hashed claimants")
                if self.create_fenced_at > self.claim_expires_at:
                    raise ValueError("create fence must precede claim expiry")
        elif self.create_fence_claim_hash is not None:
            raise ValueError("fence claimant requires a durable create fence")
        if (
            self.error_code is not None
            and self.worker_claim_hash is not None
            and self.create_fenced_at is None
        ):
            raise ValueError("a pre-fence failed claim must be released")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        return self


class GoogleAdsAuthorityEventRecord(BaseModel):
    """Append-only, PII-free evidence for one authority-record version."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    event_id: str = Field(pattern=r"^[0-9]{20}-[a-z0-9-]{1,64}$")
    deployment_id: str = Field(pattern=_GOOGLE_ADS_DEPLOYMENT_ID_PATTERN)
    contract_hash: str = Field(pattern=_GOOGLE_ADS_SHA256_PATTERN)
    event_type: GoogleAdsAuthorityEventType
    from_state: GoogleAdsDeploymentState | None = None
    to_state: GoogleAdsDeploymentState
    record_version: int = Field(ge=1)
    worker_claim_hash: str | None = Field(default=None, pattern=_GOOGLE_ADS_SHA256_PATTERN)
    error_code: GoogleAdsPersistedErrorCode | None = None
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def timestamp_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def event_identity_and_semantics_are_consistent(self):
        digest = self.contract_hash.removeprefix("sha256:")
        if not self.deployment_id.endswith(f"--{digest}"):
            raise ValueError("event deployment identity does not match contract digest")
        event_slug = self.event_type.lower().replace("_", "-")
        expected_id = f"{self.record_version:020d}-{event_slug}"
        if self.event_id != expected_id:
            raise ValueError("event ID does not match version and event type")
        from_state, to_state, requires_claim, requires_error = _GOOGLE_ADS_EVENT_SEMANTICS[
            self.event_type
        ]
        if self.from_state != from_state or self.to_state != to_state:
            raise ValueError("event transition does not match event type")
        if (self.worker_claim_hash is not None) != requires_claim:
            raise ValueError("event claimant presence does not match event type")
        if (self.error_code is not None) != requires_error:
            raise ValueError("event error presence does not match event type")
        return self


class GoogleAdsOperationKeyRecord(BaseModel):
    """One-way idempotency evidence isolated from the strict v1 authority record."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    operation: Literal["SERVER_VALIDATION"] = "SERVER_VALIDATION"
    deployment_id: str = Field(pattern=_GOOGLE_ADS_DEPLOYMENT_ID_PATTERN)
    contract_hash: str = Field(pattern=_GOOGLE_ADS_SHA256_PATTERN)
    key_hash: str = Field(pattern=_GOOGLE_ADS_SHA256_PATTERN)
    record_version: Literal[2] = 2
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def timestamp_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def deployment_identity_matches_contract(self):
        digest = self.contract_hash.removeprefix("sha256:")
        if not self.deployment_id.endswith(f"--{digest}"):
            raise ValueError("operation deployment identity does not match contract digest")
        return self


class GoogleAdsAccessEvidenceRecord(BaseModel):
    """Exact sanitized shape for current and append-only access evidence."""

    model_config = ConfigDict(extra="forbid", strict=True)

    deployment_id: str = Field(pattern=_GOOGLE_ADS_DEPLOYMENT_ID_PATTERN)
    check_key: Literal["google_ads_account_access_green"]
    status: Literal["PASSED", "FAILED", "ERROR"]
    observed_at: datetime
    expires_at: datetime
    source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    evidence_digest: str = Field(pattern=_GOOGLE_ADS_SHA256_PATTERN)

    @field_validator("observed_at", "expires_at")
    @classmethod
    def evidence_timestamps_are_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def evidence_timeline_and_digest_are_consistent(self):
        if self.expires_at <= self.observed_at:
            raise ValueError("evidence expiry must follow observation")
        if self.expires_at - self.observed_at > MAX_EVIDENCE_TTL:
            raise ValueError("evidence expiry exceeds the freshness limit")
        try:
            expected = compute_evidence_digest(
                deployment_id=self.deployment_id,
                check_key=AccessCheckKey(self.check_key),
                status=AccessEvidenceStatus(self.status),
                observed_at=self.observed_at,
                expires_at=self.expires_at,
                source_revision=self.source_revision,
            )
        except (InvalidAccessEvidence, TypeError, ValueError):
            raise ValueError("access evidence is invalid") from None
        if self.evidence_digest != expected:
            raise ValueError("evidence digest does not match evidence")
        return self


class GoogleAdsStepUpCapsRecord(BaseModel):
    """Server-owned caps embedded in sanitized owner step-up evidence."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    average_daily_usd: int = Field(gt=0)
    max_single_day_charge_usd: int = Field(gt=0)
    monthly_charge_limit_usd: int = Field(gt=0)
    max_cpc_usd: int = Field(gt=0)


class GoogleAdsOwnerStepUpEvidenceRecord(BaseModel):
    """Strict persisted UV evidence; raw WebAuthn/account material is impossible."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal[1] = 1
    evidence_id: str = Field(pattern=_GOOGLE_ADS_SHA256_PATTERN)
    purpose: Literal["PAUSED_CREATE"] = "PAUSED_CREATE"
    deployment_id: str = Field(pattern=_GOOGLE_ADS_DEPLOYMENT_ID_PATTERN)
    contract_hash: str = Field(pattern=_GOOGLE_ADS_SHA256_PATTERN)
    caps: GoogleAdsStepUpCapsRecord
    evidence_digest: str = Field(pattern=_GOOGLE_ADS_SHA256_PATTERN)
    context_digest: str = Field(pattern=_GOOGLE_ADS_SHA256_PATTERN)
    nonce_hash: str = Field(pattern=_GOOGLE_ADS_SHA256_PATTERN)
    owner_email_hash: str = Field(pattern=_GOOGLE_ADS_SHA256_PATTERN)
    credential_id_hash: str = Field(pattern=_GOOGLE_ADS_SHA256_PATTERN)
    verified_at: datetime

    @field_validator("verified_at")
    @classmethod
    def verified_timestamp_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def identity_and_digests_are_consistent(self):
        digest = self.contract_hash.removeprefix("sha256:")
        if not self.deployment_id.endswith(f"--{digest}"):
            raise ValueError("step-up deployment identity does not match contract")
        context = {
            "caps": self.caps.model_dump(mode="json"),
            "contract_hash": self.contract_hash,
            "deployment_id": self.deployment_id,
            "evidence_digest": self.evidence_digest,
            "purpose": self.purpose,
        }
        raw_context = json.dumps(context, sort_keys=True, separators=(",", ":")).encode()
        expected_context = "sha256:" + hashlib.sha256(raw_context).hexdigest()
        if self.context_digest != expected_context:
            raise ValueError("step-up context digest mismatch")
        raw_evidence_id = "|".join(
            (
                self.nonce_hash,
                self.context_digest,
                self.owner_email_hash,
                self.credential_id_hash,
            )
        ).encode()
        expected_evidence_id = "sha256:" + hashlib.sha256(raw_evidence_id).hexdigest()
        if self.evidence_id != expected_evidence_id:
            raise ValueError("step-up evidence identity mismatch")
        return self


class GoogleAdsPausedCreateApprovalProofRecord(BaseModel):
    """Single-use sanitized proof-consumption marker."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    proof_id: str = Field(pattern=_GOOGLE_ADS_SHA256_PATTERN)
    proof_reference_hash: str = Field(pattern=_GOOGLE_ADS_SHA256_PATTERN)
    deployment_id: str = Field(pattern=_GOOGLE_ADS_DEPLOYMENT_ID_PATTERN)
    contract_hash: str = Field(pattern=_GOOGLE_ADS_SHA256_PATTERN)
    access_evidence_id: str = Field(pattern=_GOOGLE_ADS_SHA256_PATTERN)
    authority_from_version: Literal[2] = 2
    authority_to_version: Literal[3] = 3
    consumed_at: datetime

    @field_validator("consumed_at")
    @classmethod
    def consumed_timestamp_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def deployment_matches_contract(self):
        if not self.deployment_id.endswith(f"--{self.contract_hash.removeprefix('sha256:')}"):
            raise ValueError("proof deployment identity does not match contract")
        return self


class GoogleAdsPausedCreateOutboxRecord(BaseModel):
    """Durable, sanitized job-dispatch intent for PAUSED creation only."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    outbox_id: Literal["paused-create"] = "paused-create"
    deployment_id: str = Field(pattern=_GOOGLE_ADS_DEPLOYMENT_ID_PATTERN)
    contract_hash: str = Field(pattern=_GOOGLE_ADS_SHA256_PATTERN)
    approval_record_version: Literal[3] = 3
    proof_id: str = Field(pattern=_GOOGLE_ADS_SHA256_PATTERN)
    access_evidence_id: str = Field(pattern=_GOOGLE_ADS_SHA256_PATTERN)
    state: GoogleAdsOutboxState = "PENDING"
    attempt_count: int = Field(default=0, ge=0)
    dispatcher_claim_hash: str | None = Field(default=None, pattern=_GOOGLE_ADS_SHA256_PATTERN)
    claim_expires_at: datetime | None = None
    error_code: Literal["job_invocation_failed"] | None = None
    created_at: datetime
    updated_at: datetime
    dispatched_at: datetime | None = None

    @field_validator("created_at", "updated_at", "claim_expires_at", "dispatched_at")
    @classmethod
    def outbox_timestamps_are_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value) if value is not None else None

    @model_validator(mode="after")
    def state_and_identity_are_consistent(self):
        if not self.deployment_id.endswith(f"--{self.contract_hash.removeprefix('sha256:')}"):
            raise ValueError("outbox deployment identity does not match contract")
        if self.updated_at < self.created_at:
            raise ValueError("outbox update cannot precede creation")
        claimed = self.dispatcher_claim_hash is not None or self.claim_expires_at is not None
        if (self.dispatcher_claim_hash is None) != (self.claim_expires_at is None):
            raise ValueError("outbox claim hash and expiry must be set together")
        if self.state == "DISPATCHING":
            if not claimed or self.dispatched_at is not None:
                raise ValueError("dispatching outbox requires one live claim")
        elif claimed:
            raise ValueError("only dispatching outbox may retain a claim")
        if self.state == "DISPATCHED":
            if self.dispatched_at is None or self.error_code is not None:
                raise ValueError("dispatched outbox requires a clean dispatch timestamp")
        elif self.dispatched_at is not None:
            raise ValueError("pending outbox cannot have a dispatch timestamp")
        if self.state == "PENDING" and self.error_code is not None and self.attempt_count < 1:
            raise ValueError("failed pending outbox requires an attempted dispatch")
        return self


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


class LeadRecord(BaseModel):
    """Canonical Firestore shape for documents in the ``leads`` collection."""

    model_config = ConfigDict(extra="ignore")

    lead_id: str
    user_id: str
    session_id: str

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    budget_max: float | None = None
    home_type: str | None = None

    homes_viewed: list[str] = Field(default_factory=list)
    appointment_requested: bool = False
    financing_discussed: bool = False

    source: str = "chat"
    status: str = "new"
    created_at: str | None = None
    updated_at: str | None = None
    first_contacted_at: str | None = None
    first_contacted_by: str | None = None
    status_changed_at: str | None = None
    status_changed_by: str | None = None

    priority: str | None = None
    assigned_to: str | None = None
    triage_notes: str | None = None
    triage_reason: str | None = None
    last_triage_at: str | None = None
    contact_consent_at: str | None = None
    contact_consent_source: str | None = None

    journey_id: str | None = Field(
        default=None, min_length=34, max_length=34, pattern=r"^j_[0-9a-f]{32}$"
    )
    home_id: str | None = Field(default=None, max_length=200)
    home_model: str | None = Field(default=None, max_length=200)

    utm_source: str | None = Field(default=None, max_length=200)
    utm_medium: str | None = Field(default=None, max_length=200)
    utm_campaign: str | None = Field(default=None, max_length=200)
    utm_content: str | None = Field(default=None, max_length=200)
    utm_term: str | None = Field(default=None, max_length=200)
    referrer: str | None = Field(default=None, max_length=200)

    gclid: str | None = Field(
        default=None, min_length=6, max_length=200, pattern=r"^[A-Za-z0-9._~-]+$"
    )
    gbraid: str | None = Field(
        default=None, min_length=6, max_length=200, pattern=r"^[A-Za-z0-9._~-]+$"
    )
    wbraid: str | None = Field(
        default=None, min_length=6, max_length=200, pattern=r"^[A-Za-z0-9._~-]+$"
    )


class Customer(BaseModel):
    """Customer/Tenant record"""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    full_name: str = Field(min_length=1)
    phone: str | None = None
    email: str | None = None
    status: CustomerStatus = CustomerStatus.LEAD

    # Billing info
    billing_account: str | None = None  # e.g., "Texas Home Outlet - 15th"

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

    model_config = ConfigDict(use_enum_values=True)


class Property(BaseModel):
    """Physical property/land location"""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    address: str
    city: str | None = None
    state: str = "TX"
    zip_code: str | None = None

    # Tax info
    county: str | None = None
    school_district: str | None = None
    county_account_number: str | None = None
    county_tax_link: str | None = None
    isd_tax_link: str | None = None

    # Owner reference
    customer_id: str | None = None

    model_config = ConfigDict(use_enum_values=True)


class Inventory(BaseModel):
    """Manufactured home inventory"""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    serial_number: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    manufacturer: str | None = None  # e.g., "Tru Belton", "Champion LA"

    # Pricing
    invoice_date: date | None = None
    invoice_amount: float | None = Field(default=None, ge=0)
    msrp: float | None = Field(default=None, ge=0)
    sale_price: float | None = Field(default=None, ge=0)

    # Specs (parsed from model name)
    bedrooms: int | None = Field(default=None, ge=0, le=10)
    bathrooms: int | None = Field(default=None, ge=0, le=10)
    sqft: int | None = Field(default=None, ge=0)
    width: int | None = Field(default=None, ge=0)  # e.g., 14, 28, 32
    length: int | None = Field(default=None, ge=0)  # e.g., 60, 66, 76

    # Status
    status: InventoryStatus = InventoryStatus.AVAILABLE
    notes: str | None = None

    # Media
    photos: list[str] = Field(default_factory=list)
    video_tour_url: str | None = None

    @field_validator("bedrooms", "bathrooms", "sqft")
    @classmethod
    def validate_specs_non_negative(cls, v, info):
        """Specs must be >= 0 when populated. Field-level ge=0 already guards
        most cases; this validator gives a clearer error path for the spec
        triple and protects against future changes that loosen the field
        constraint (e.g., dropping ge=0)."""
        if v is not None and v < 0:
            raise ValueError(f"{info.field_name} must be >= 0 when populated, got {v}")
        return v

    @field_validator("status")
    @classmethod
    def validate_available_has_price(cls, v, info):
        """If status is AVAILABLE, at least one of msrp / sale_price must be
        populated and > 0. Production must not surface a buyable home with no
        price.

        Pydantic v2 runs field validators in declaration order, so by the
        time this runs both msrp and sale_price are present in info.data."""
        # Normalize status to its string value (use_enum_values=True returns str)
        status_value = v.value if hasattr(v, "value") else v
        if status_value == InventoryStatus.AVAILABLE.value:
            msrp = info.data.get("msrp")
            sale_price = info.data.get("sale_price")
            has_price = (msrp is not None and msrp > 0) or (
                sale_price is not None and sale_price > 0
            )
            if not has_price:
                raise ValueError("AVAILABLE inventory must have msrp or sale_price > 0")
        return v

    model_config = ConfigDict(use_enum_values=True)


class Sale(BaseModel):
    """Links customer to purchased inventory"""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    customer_id: str
    inventory_id: str

    # Sale details
    salesman: str | None = None
    customer_number: str | None = None  # 21st Mortgage customer #
    sale_date: date | None = None
    sale_price: float | None = Field(default=None, ge=0)

    # Financing
    down_payment: float | None = Field(default=None, ge=0)
    financed_amount: float | None = Field(default=None, ge=0)
    monthly_payment: float | None = Field(default=None, ge=0)
    loan_term_months: int | None = Field(default=None, ge=0)
    interest_rate: float | None = Field(default=None, ge=0, le=100)

    # Status
    contract_status: str = "pending"  # pending, approved, funded, complete
    notes: str | None = None


class Lease(BaseModel):
    """Active lease/rent-to-own agreement"""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    customer_id: str
    property_id: str

    # Terms
    monthly_payment: float = Field(ge=0)
    lease_start_date: date | None = None
    lease_end_date: date | None = None

    # Billing
    billing_account: str | None = None
    payment_day: int = Field(default=1, ge=1, le=31)  # Day of month payment is due

    # Status
    status: str = "active"  # active, delinquent, paid_off, terminated


class TaxPayment(BaseModel):
    """Annual property tax records"""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    property_id: str
    tax_year: int

    # Tax amounts
    county_taxes: float | None = None
    school_taxes: float | None = None
    total_taxes: float | None = None

    # Escrow tracking
    escrow_balance: float | None = None
    payment_date: date | None = None

    notes: str | None = None


class DealStatus(str, Enum):
    """Deal/application lifecycle status — mirrors fastcontractdocs.com workflow"""

    PENDING = "pending"
    APPROVED = "approved"
    CONTRACT = "contract"
    FUNDED = "funded"
    COMPLETE = "complete"
    DENIED = "denied"
    ARCHIVED = "archived"


class InventoryWrite(BaseModel):
    """Permissive validation for admin inventory create/update payloads.

    Inventory is a flexible staff-managed home record, so this accepts and
    passes through extra fields (``extra="allow"``) while normalizing the core
    typed fields the public read path relies on. All fields are optional: the
    create route requires ``model_name``; update is a partial merge.
    """

    model_config = ConfigDict(extra="allow")

    model_name: str | None = None
    manufacturer: str | None = None
    classification: str | None = None
    status: str | None = None
    is_new: bool | None = None
    serial_number: str | None = None
    bedrooms: int | None = Field(default=None, ge=0, le=10)
    bathrooms: float | None = Field(default=None, ge=0, le=10)
    sqft: int | None = Field(default=None, ge=0)
    width: int | None = Field(default=None, ge=0)
    length: int | None = Field(default=None, ge=0)
    sale_price: float | None = Field(default=None, ge=0)
    msrp: float | None = Field(default=None, ge=0)
    features: list[str] | None = None
    marketing_tags: list[str] | None = None
    photos: list[str] | None = None
    gallery_images: list[str] | None = None
    image_url: str | None = None
    floorplan_url: str | None = None
    matterport_id: str | None = None
    description: str | None = None


class Deal(BaseModel):
    """
    Customer application/deal record — replaces fastcontractdocs.com.
    Consolidates all buyer, co-buyer, home, loan, and transaction data
    into a single record that can be used to generate any document.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))

    # ─── Assignment ───
    salesrep: str | None = None
    status: DealStatus = DealStatus.PENDING

    # ─── Seller ───
    # Registered legal entity (per founding-partner directive 2026-05-18):
    # "Prosperity Acquisitions, Inc. dba Texas Home Outlet" — no longer an LLC.
    seller_name: str = "Prosperity Acquisitions, Inc. dba Texas Home Outlet"
    seller_rbi: str | None = "35248"
    seller_phone: str | None = "(281) 324-3020"
    seller_address: str | None = "10685 FM 1960 East"
    seller_city: str | None = "Huffman"
    seller_state: str = "TX"
    seller_zip: str | None = "77336"

    # ─── Buyer ───
    buyer_first_name: str | None = None
    buyer_last_name: str | None = None
    buyer_phone: str | None = None
    buyer_email: str | None = None
    buyer_ssn: str | None = None
    buyer_dob: str | None = (
        None  # ISO date; maps to Buyer_DOB on creditapp / homestead / patriot-act (PII)
    )
    buyer_marital_status: str | None = (
        None  # "Married" / "Unmarried" (Mark Willcott: idiot-proof dropdown)
    )

    # ─── Co-Buyer ───
    co_buyer_first_name: str | None = None
    co_buyer_last_name: str | None = None
    co_buyer_phone: str | None = None
    co_buyer_ssn: str | None = None
    co_buyer_dob: str | None = (
        None  # "Co-buyer has no birthday" (Mark Willcott markup) — mirror buyer_dob (PII)
    )
    co_buyer_marital_status: str | None = None

    # ─── Employment (Buyer) ───
    employer_name: str | None = None
    occupation: str | None = None
    occupation_length: str | None = None  # years
    work_phone: str | None = None
    self_employed: bool = False
    previous_employer: str | None = None
    previous_occupation: str | None = None

    # ─── Employment (Co-Buyer) ───
    co_buyer_employer: str | None = None
    co_buyer_occupation: str | None = None
    co_buyer_occupation_length: str | None = None
    co_buyer_work_phone: str | None = None
    co_buyer_self_employed: bool = False

    # ─── Mailing Address (current residence) ───
    mailing_address: str | None = None
    mailing_city: str | None = None
    mailing_state: str = "TX"
    mailing_zip: str | None = None
    mailing_length: str | None = None  # years at address
    mailing_own_rent: str | None = None  # "Own" or "Rent"

    # ─── Installation Address (where the home goes) ───
    buyer_address: str | None = None
    buyer_city: str | None = None
    buyer_county: str | None = None
    buyer_state: str = "TX"
    buyer_zip: str | None = None

    # ─── References ───
    reference1_name: str | None = None
    reference1_phone: str | None = None
    reference2_name: str | None = None
    reference2_phone: str | None = None

    # ─── Home Info ───
    inventory_id: str | None = None  # link to Inventory record
    manufacturer: str | None = None
    model: str | None = None
    year: str | None = None
    serial_number_1: str | None = None
    serial_number_2: str | None = None
    label_number_1: str | None = None
    label_number_2: str | None = None
    manufacturer_address: str | None = None
    manufacturer_city: str | None = None
    manufacturer_state: str | None = None
    manufacturer_zip: str | None = None
    manufacturer_city_state_zip: str | None = None
    no_of_sections: str | None = None
    is_new: bool = True

    # ─── Pricing ───
    sales_price: float | None = Field(default=None, ge=0)
    down_payment: float | None = Field(default=None, ge=0)

    # ─── Escrow inputs (Mark Willcott spec, 2026-06-24) ───
    # Staff enters the ANNUAL insurance premium; monthly INS escrow = annual / 12.
    annual_insurance: float | None = Field(default=None, ge=0)
    # Tax rate as a percent (e.g. 2.5 for 2.5%); taxable_value defaults to
    # sales_price when omitted. annual tax = (tax_rate/100) * taxable_value;
    # monthly tax escrow = annual tax / 12. Escrow amounts are derived onto the
    # real field_map keys in tools.document_quality.enrich_document_data
    # (mirrored in tools.document_engine._compute_fields for the legacy path).
    tax_rate: float | None = Field(default=None, ge=0)
    taxable_value: float | None = Field(default=None, ge=0)

    # ─── Financing / Loan ───
    creditor_name: str | None = None
    creditor_address: str | None = None
    creditor_city_state_zip: str | None = None
    creditor_phone: str | None = None
    # Loan number (Mark Willcott markup, punch-list #8/#9). Feeds the Compliance
    # Agreement + two-party contract via the Portfolio[0] widget; enrich backfills
    # portfolio from loan_number.
    loan_number: str | None = None
    loan_term: str | None = None
    apr: str | None = None
    finance_charge: float | None = Field(default=None, ge=0)
    max_financed: float | None = Field(default=None, ge=0)
    total_payments: float | None = Field(default=None, ge=0)
    payment_start_date: str | None = None
    insurance_premium: float | None = Field(default=None, ge=0)

    # ─── Notes ───
    notes: str | None = None

    # ─── Timestamps ───
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = ConfigDict(use_enum_values=True)

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
        buyer_city_state_zip = (
            f"{self.buyer_city or ''}, {self.buyer_state or 'TX'} {self.buyer_zip or ''}".strip()
            if any([self.buyer_city, self.buyer_zip])
            else None
        )

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

        # ─── Computed: Seller strings ───
        seller_city_state_zip = None
        if any([self.seller_city, self.seller_zip]):
            seller_city_state_zip = f"{self.seller_city or ''}, {self.seller_state or 'TX'} {self.seller_zip or ''}".strip()

        seller_full_address = None
        if self.seller_address:
            seller_full_address = self.seller_address
            if seller_city_state_zip:
                seller_full_address += f", {seller_city_state_zip}"

        # ─── Computed: Home strings ───
        manufacturer_city_state_zip = self.manufacturer_city_state_zip
        if not manufacturer_city_state_zip and any([self.manufacturer_city, self.manufacturer_zip]):
            manufacturer_city_state_zip = f"{self.manufacturer_city or ''}, {self.manufacturer_state or ''} {self.manufacturer_zip or ''}".strip()

        manufacturer_full_address = None
        if self.manufacturer_address:
            manufacturer_full_address = self.manufacturer_address
            if manufacturer_city_state_zip:
                manufacturer_full_address += f", {manufacturer_city_state_zip}"

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
            "buyer_dob": self.buyer_dob,
            "buyer_marital_status": self.buyer_marital_status,
            # ─── Co-Buyer ───
            "co_buyer_name": co_buyer_name,
            "co_buyer_first_name": self.co_buyer_first_name,
            "co_buyer_last_name": self.co_buyer_last_name,
            "co_buyer_phone": self.co_buyer_phone,
            "co_buyer_ssn": self.co_buyer_ssn,
            "co_buyer_dob": self.co_buyer_dob,
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
            # ─── Seller ───
            "seller_name": self.seller_name,
            "seller_phone": self.seller_phone or "(281) 324-3020",
            "seller_email": "sales@texashomeoutlet.com",
            "seller_rbi": self.seller_rbi,
            "seller_address": self.seller_address,
            "seller_city": self.seller_city,
            "seller_state": self.seller_state,
            "seller_zip": self.seller_zip,
            "seller_city_state_zip": seller_city_state_zip,
            "seller_full_address": seller_full_address,
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
            "manufacturer_address": self.manufacturer_address,
            "manufacturer_city": self.manufacturer_city,
            "manufacturer_state": self.manufacturer_state,
            "manufacturer_zip": self.manufacturer_zip,
            "manufacturer_city_state_zip": manufacturer_city_state_zip,
            "manufacturer_full_address": manufacturer_full_address,
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
            # ─── Escrow inputs (monthly escrow derived in document_engine) ───
            "annual_insurance": self.annual_insurance,
            "tax_rate": self.tax_rate,
            "taxable_value": self.taxable_value,
            # ─── Financing ───
            "creditor_name": self.creditor_name,
            "creditor_address": self.creditor_address,
            "creditor_city_state_zip": self.creditor_city_state_zip,
            "creditor_phone": self.creditor_phone,
            "loan_number": self.loan_number,
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
    property_id: str | None = None
    inventory_id: str | None = None  # For warranty lookup

    # Issue details
    issue_type: str  # structural, plumbing, electrical, hvac, cosmetic, appliance
    description: str
    photos: list[str] = Field(default_factory=list)

    # Warranty
    is_warranty_claim: bool = False
    warranty_status: str | None = None  # covered, not_covered, pending
    warranty_notes: str | None = None

    # Resolution
    status: str = "open"  # open, in_progress, scheduled, resolved, closed
    assigned_contractor: str | None = None
    resolution_notes: str | None = None
    resolved_date: date | None = None

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ═══════════════════════════════════════════════════════════════════════════════
# LINEAR-INSPIRED PROJECT MANAGEMENT MODELS (AI PM Manager Core)
# ═══════════════════════════════════════════════════════════════════════════════


class WorkflowState(str, Enum):
    """Linear-inspired workflow states for any project/task"""

    BACKLOG = "backlog"  # Not ready to start
    TODO = "todo"  # Ready to start
    IN_PROGRESS = "in_progress"  # Actively being worked
    IN_REVIEW = "in_review"  # Needs review/approval
    DONE = "done"  # Completed
    CANCELLED = "cancelled"  # Won't do
    BLOCKED = "blocked"  # Blocked by dependency


class TaskPriority(int, Enum):
    """Priority levels - like Linear"""

    NO_PRIORITY = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    URGENT = 4


class ProjectType(str, Enum):
    """Types of projects the AI PM can manage"""

    SOFTWARE = "software"  # Code repos (Sapphire, etc.)
    BUSINESS = "business"  # Business operations (THO)
    RESEARCH = "research"  # R&D, experiments
    INFRASTRUCTURE = "infrastructure"  # DevOps, deployments


class Project(BaseModel):
    """
    A project managed by the AI PM - like a Linear project/workspace
    Can represent a GitHub repo, business initiative, etc.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))

    # Identity
    name: str = Field(min_length=1)
    description: str | None = None
    project_type: ProjectType = ProjectType.BUSINESS

    # External links
    github_repo: str | None = None  # e.g., "arigatoexpress/Sapphire"
    cloud_run_service: str | None = None  # e.g., "sapphire-dashboard"
    notion_url: str | None = None

    # Status
    status: str = "active"  # active, paused, archived

    # Team
    owner: str | None = None
    members: list[str] = Field(default_factory=list)

    # Stats
    total_tasks: int = 0
    completed_tasks: int = 0

    # For software projects
    default_branch: str = "main"
    deployment_url: str | None = None

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
    description: str | None = None

    # Organization
    project_id: str  # Links to Project
    state: WorkflowState = WorkflowState.BACKLOG
    priority: TaskPriority = TaskPriority.NO_PRIORITY
    labels: list[str] = Field(default_factory=list)  # e.g., ["bug", "urgent"]

    # Assignment
    assignee: str | None = None
    creator: str | None = None

    # Relations
    parent_task_id: str | None = None  # Subtasks
    related_github_issue: int | None = None
    related_github_pr: int | None = None
    related_deal_id: str | None = None  # Link to business deal

    # Estimation (like Linear)
    estimate_hours: float | None = None
    actual_hours: float | None = None

    # Timing
    due_date: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    # State history for audit trail
    state_history: list[dict] = Field(default_factory=list)
    # [{"from": "backlog", "to": "in_progress", "at": "2026-03-02T...", "by": "user"}]

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def transition_to(self, new_state: WorkflowState, changed_by: str | None = None):
        """Record state transition with audit trail"""
        self.state_history.append(
            {
                "from": self.state,
                "to": new_state,
                "at": datetime.utcnow().isoformat(),
                "by": changed_by or "system",
            }
        )
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
    description: str | None = None

    # Timing
    start_date: datetime
    end_date: datetime

    # Status
    status: str = "planning"  # planning, active, completed

    # Goals
    goals: list[str] = Field(default_factory=list)

    # Projects included in this cycle
    project_ids: list[str] = Field(default_factory=list)

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
    description: str  # Human-readable summary

    # Where
    project_id: str
    task_id: str | None = None

    # Who
    actor: str | None = None  # User or "AI PM"

    # Details
    metadata: dict = Field(default_factory=dict)
    # e.g., {"from_state": "backlog", "to_state": "in_progress"}

    created_at: datetime = Field(default_factory=datetime.utcnow)


class View(BaseModel):
    """
    Saved view configuration - like Linear's custom views
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(min_length=1)

    # Filters
    project_id: str | None = None
    filter_states: list[WorkflowState] = Field(default_factory=list)
    filter_labels: list[str] = Field(default_factory=list)
    filter_assignee: str | None = None
    filter_priority: TaskPriority | None = None

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
