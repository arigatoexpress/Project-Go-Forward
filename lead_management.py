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

logger = logging.getLogger(__name__)


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

    # Triage / routing (populated by Mira or CRM operators)
    priority: str | None = None  # "low", "medium", "high"
    assigned_to: str | None = None  # e.g. sales rep identifier
    triage_notes: str | None = None
    triage_reason: str | None = None  # e.g. "hot_lead", "needs_follow_up"
    last_triage_at: str | None = None

    def __post_init__(self):
        if self.homes_viewed is None:
            self.homes_viewed = []
        if self.created_at is None:
            self.created_at = datetime.utcnow().isoformat()
        self.updated_at = datetime.utcnow().isoformat()

    def to_dict(self) -> dict:
        """Convert to dictionary for storage"""
        return asdict(self)

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
        }


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

    async def triage_lead(self, lead_id: str, update: dict) -> Lead | None:
        """Apply a triage update to a lead and persist it.

        Allowed update keys mirror the triage fields on ``Lead``:
        status, priority, assigned_to, triage_notes, triage_reason.
        Returns the updated lead or None if the lead was not found.
        """
        lead = await self.get_lead(lead_id)
        if lead is None:
            return None

        allowed = {"status", "priority", "assigned_to", "triage_notes", "triage_reason"}
        for key, value in update.items():
            if key in allowed and hasattr(lead, key):
                setattr(lead, key, value)

        lead.last_triage_at = datetime.utcnow().isoformat()
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
