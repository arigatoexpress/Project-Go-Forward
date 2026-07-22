"""
Lead Capture and Management System for THO AI Agent
Stores lead information in Firestore and provides export capabilities
"""

import asyncio
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from google.cloud import firestore

from database.models import LeadRecord

logger = logging.getLogger(__name__)

VALID_LEAD_STATUSES = frozenset({"new", "contacted", "qualified", "converted", "archived"})
CONTACTED_LEAD_STATUSES = frozenset({"contacted", "qualified", "converted"})


def normalize_phone(phone: str | None) -> str | None:
    """Best-effort E.164 normalization for US numbers. Never raises; returns the
    original string when it can't confidently normalize, so a lead is never lost
    or mangled. Keeps the CRM consistent and makes click-to-call / SMS / dedup
    reliable (e.g. "(281) 324-3020" and "281-324-3020" both -> "+12813243020")."""
    if not phone:
        return phone
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return phone


@dataclass
class Lead:
    """Lead information captured during conversation"""

    lead_id: str
    user_id: str
    session_id: str

    # Contact information
    name: str | None = None
    email: str | None = None
    phone: str | None = None

    # Preferences from conversation
    bedrooms: int | None = None
    bathrooms: int | None = None
    budget_max: float | None = None
    home_type: str | None = None

    # Engagement tracking
    homes_viewed: list[str] = None
    appointment_requested: bool = False
    financing_discussed: bool = False

    # Metadata
    source: str = "chat"  # "chat", "appointment", "calculator"
    status: str = "new"  # "new", "contacted", "qualified", "converted"
    created_at: str = None
    updated_at: str = None
    first_contacted_at: str | None = None
    first_contacted_by: str | None = None
    status_changed_at: str | None = None
    status_changed_by: str | None = None

    # Triage / routing (populated by Mira or CRM operators)
    priority: str | None = None  # "low", "medium", "high"
    assigned_to: str | None = None  # e.g. sales rep identifier
    triage_notes: str | None = None
    triage_reason: str | None = None  # e.g. "hot_lead", "needs_follow_up"
    last_triage_at: str | None = None
    # Durable proof that the visitor explicitly asked the team to contact them.
    contact_consent_at: str | None = None
    contact_consent_source: str | None = None

    # Session-scoped, anonymous first-party attribution. This is never sent to
    # Google and cannot identify a person outside the submitted lead record.
    journey_id: str | None = None
    home_id: str | None = None
    home_model: str | None = None

    # Marketing attribution (first-party UTM carried on a reached-out lead; NOT visitor tracking)
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    utm_content: str | None = None
    utm_term: str | None = None
    referrer: str | None = None
    # Google Ads click IDs retained only when a visitor actively submits a
    # lead. They enable deterministic offline conversion attribution without
    # placing contact PII in analytics events.
    gclid: str | None = None
    gbraid: str | None = None
    wbraid: str | None = None

    def __post_init__(self):
        if self.homes_viewed is None:
            self.homes_viewed = []
        if self.created_at is None:
            self.created_at = datetime.utcnow().isoformat()
        if self.updated_at is None:
            self.updated_at = datetime.utcnow().isoformat()

    def to_dict(self) -> dict:
        """Validate against the canonical Firestore schema before storage."""
        return LeadRecord.model_validate(asdict(self)).model_dump()

    @classmethod
    def from_dict(cls, data: dict) -> "Lead":
        """Create from dictionary, ignoring unknown keys.

        Mirrors ``Appointment.from_dict``: a malformed or out-of-band Firestore
        document (extra/legacy fields) must not 500 the admin CRM list — the
        owner's only post-cutover visibility while email is off.
        """
        valid_fields = {f.name for f in __import__("dataclasses").fields(cls)}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)

    def to_csv_row(self) -> dict:
        """Convert to CSV-friendly format"""
        return {
            "Lead ID": self.lead_id,
            "Name": self.name or "",
            "Email": self.email or "",
            "Phone": self.phone or "",
            "Bedrooms": self.bedrooms or "",
            "Bathrooms": self.bathrooms or "",
            "Max Budget": f"${int(self.budget_max):,}" if self.budget_max else "",
            "Home Type": self.home_type or "",
            "Homes Viewed": ", ".join(self.homes_viewed[:3]) if self.homes_viewed else "",
            "Appointment Requested": "Yes" if self.appointment_requested else "No",
            "Status": self.status,
            "Created": self.created_at,
            "Source": self.source,
            "Google Click ID": self.gclid or self.gbraid or self.wbraid or "",
        }


def apply_lead_status_transition(
    lead: Lead,
    new_status: str,
    *,
    actor: str,
    now: str | None = None,
) -> bool:
    """Apply one validated, idempotent lifecycle transition in memory.

    The first transition into a contacted stage records an immutable response
    clock. Replays and later/backward transitions never rewrite that first
    response, which makes speed-to-lead reporting trustworthy.
    """
    if new_status not in VALID_LEAD_STATUSES:
        raise ValueError(f"Invalid lead status: {new_status!r}")
    if lead.status == new_status:
        return False

    timestamp = now or datetime.utcnow().isoformat()
    lead.status = new_status
    lead.status_changed_at = timestamp
    lead.status_changed_by = actor
    if new_status in CONTACTED_LEAD_STATUSES and not lead.first_contacted_at:
        lead.first_contacted_at = timestamp
        lead.first_contacted_by = actor
    return True


class LeadManager:
    """Manages lead storage and retrieval"""

    def __init__(self, project_id: str = None):
        """Initialize Firestore client"""
        self.db = firestore.Client(project=project_id)
        self.collection_name = "leads"

    async def create_lead(self, lead: Lead) -> Lead:
        """Create a new lead"""
        lead.phone = normalize_phone(lead.phone)
        doc_ref = self.db.collection(self.collection_name).document(lead.lead_id)

        def _save():
            doc_ref.set(lead.to_dict())

        await asyncio.to_thread(_save)
        return lead

    async def update_lead(self, lead: Lead) -> Lead:
        """Update existing lead"""
        lead.phone = normalize_phone(lead.phone)
        lead.updated_at = datetime.utcnow().isoformat()
        doc_ref = self.db.collection(self.collection_name).document(lead.lead_id)

        def _update():
            doc_ref.set(lead.to_dict(), merge=True)

        await asyncio.to_thread(_update)
        return lead

    async def transition_lead_status(
        self,
        lead_id: str,
        new_status: str,
        *,
        actor: str,
    ) -> tuple[Lead | None, str | None, bool]:
        """Persist one explicit lifecycle transition.

        Returns ``(lead, previous_status, changed)``. An idempotent replay does
        not write or move ``updated_at``. Firestore's transaction prevents two
        operators racing on a new lead from overwriting the first responder.
        """
        doc_ref = self.db.collection(self.collection_name).document(lead_id)
        transaction = self.db.transaction()

        @firestore.transactional
        def _transition(txn):
            snapshot = doc_ref.get(transaction=txn)
            if not snapshot.exists:
                return None, None, False
            lead = Lead.from_dict(snapshot.to_dict())
            previous_status = lead.status
            changed = apply_lead_status_transition(lead, new_status, actor=actor)
            if changed:
                lead.updated_at = datetime.utcnow().isoformat()
                txn.set(doc_ref, lead.to_dict(), merge=True)
            return lead, previous_status, changed

        return await asyncio.to_thread(_transition, transaction)

    async def get_lead(self, lead_id: str) -> Lead | None:
        """Retrieve lead by ID"""
        doc_ref = self.db.collection(self.collection_name).document(lead_id)

        def _get():
            return doc_ref.get()

        doc = await asyncio.to_thread(_get)

        if doc.exists:
            return Lead.from_dict(doc.to_dict())
        return None

    async def get_lead_by_session(self, session_id: str) -> Lead | None:
        """Retrieve lead by session ID"""
        query = (
            self.db.collection(self.collection_name).where("session_id", "==", session_id).limit(1)
        )

        def _stream():
            for doc in query.stream():
                return Lead.from_dict(doc.to_dict())
            return None

        return await asyncio.to_thread(_stream)

    async def get_lead_by_phone(self, phone: str | None) -> "Lead | None":
        """Retrieve a lead carrying this (normalized) phone, if any.

        Used to dedupe the chat contact-capture backstop against a lead the
        agent's ``save_lead`` tool already persisted, so one customer is never
        double-captured / double-alerted. A single-field equality query is
        auto-indexed by Firestore (no composite index needed)."""
        if not phone:
            return None
        query = self.db.collection(self.collection_name).where("phone", "==", phone).limit(1)

        def _stream():
            for doc in query.stream():
                return Lead.from_dict(doc.to_dict())
            return None

        return await asyncio.to_thread(_stream)

    async def list_leads(self, status: str | None = None, limit: int = 100) -> list[Lead]:
        """List leads with optional status filter"""
        query = self.db.collection(self.collection_name)

        if status:
            query = query.where("status", "==", status)

        query = query.order_by("created_at", direction=firestore.Query.DESCENDING).limit(limit)

        def _stream():
            leads: list[Lead] = []
            for doc in query.stream():
                try:
                    leads.append(Lead.from_dict(doc.to_dict()))
                except Exception as exc:
                    # One malformed / out-of-band Firestore document must not
                    # 500 the whole admin CRM list (the owner's only
                    # post-cutover visibility while email is off). Skip + log
                    # the offender instead of failing the entire response.
                    logger.warning(
                        "Skipping unparseable lead doc %s: %s",
                        getattr(doc, "id", "?"),
                        exc,
                    )
            return leads

        return await asyncio.to_thread(_stream)

    async def list_leads_needing_triage(
        self,
        status: str = "new",
        min_age_hours: int | None = None,
        limit: int = 100,
    ) -> list[Lead]:
        """List leads awaiting triage, optionally older than ``min_age_hours``."""
        leads = await self.list_leads(status=status, limit=limit)

        if min_age_hours is None:
            return leads

        cutoff = datetime.utcnow() - timedelta(hours=min_age_hours)
        result: list[Lead] = []
        for lead in leads:
            created = lead.created_at
            if not created:
                continue
            try:
                dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                if dt >= cutoff:
                    continue
            except Exception:
                continue
            result.append(lead)
        return result

    async def triage_lead(
        self,
        lead_id: str,
        update: dict,
        *,
        actor: str = "system:mira",
    ) -> Lead | None:
        """Apply a triage update to a lead and persist it.

        Allowed update keys mirror the triage fields on ``Lead``:
        status, priority, assigned_to, triage_notes, triage_reason.
        Returns the updated lead or None if the lead was not found.
        """
        lead = await self.get_lead(lead_id)
        if lead is None:
            return None

        now = datetime.utcnow().isoformat()
        if "status" in update:
            # Validate before applying any other field so a malformed status
            # cannot produce a partial triage write.
            apply_lead_status_transition(lead, update["status"], actor=actor, now=now)

        allowed = {"priority", "assigned_to", "triage_notes", "triage_reason"}
        for key, value in update.items():
            if key in allowed and hasattr(lead, key):
                setattr(lead, key, value)

        lead.last_triage_at = now
        return await self.update_lead(lead)

    def export_to_csv(self, leads: list[Lead], filename: str = "leads_export.csv") -> str:
        """Export leads to CSV file"""
        import csv

        if not leads:
            return None

        fieldnames = list(leads[0].to_csv_row().keys())

        with open(filename, "w", newline="") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for lead in leads:
                writer.writerow(lead.to_csv_row())

        return filename


# Tool functions for Google ADK agents
def capture_contact_info(name: str, email: str = None, phone: str = None) -> str:
    """
    Tool to capture contact information from a user during conversation.

    Args:
        name: User's full name
        email: User's email address (optional)
        phone: User's phone number (optional)

    Returns:
        Confirmation message
    """
    # This will be called by the agent and processed in main.py
    return json.dumps({"action": "capture_contact", "name": name, "email": email, "phone": phone})


def mark_appointment_intent() -> str:
    """
    Tool to mark that a user has expressed intent to schedule an appointment.

    Returns:
        Confirmation message
    """
    return json.dumps({"action": "mark_appointment_intent"})
