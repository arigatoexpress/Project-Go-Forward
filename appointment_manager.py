"""
Appointment Scheduling System for THO AI Agent
Manages showroom appointment booking with Firestore persistence.
"""

import asyncio
import logging
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from google.cloud import firestore

logger = logging.getLogger(__name__)

TIMEZONE = ZoneInfo("America/Chicago")
MAX_PER_SLOT = 2
BOOKING_WINDOW_DAYS = 30
SLOT_DURATION_MINUTES = 60

# Business hours: (open_hour, close_hour) — last bookable slot is close - 1
HOURS_BY_DAY = {
    0: (9, 18),  # Monday
    1: (9, 18),  # Tuesday
    2: (9, 18),  # Wednesday
    3: (9, 18),  # Thursday
    4: (9, 18),  # Friday
    5: (9, 17),  # Saturday
    6: (12, 15),  # Sunday
}


@dataclass
class Appointment:
    """Appointment information for showroom visits"""

    appointment_id: str

    # Customer info
    name: str
    phone: str
    email: str | None = None

    # Scheduling
    date: str = ""  # "2026-02-15"
    time_slot: str = ""  # "10:00 AM"
    duration_minutes: int = SLOT_DURATION_MINUTES

    # Details
    notes: str | None = None
    source: str = "website"  # "website" | "chat" | "phone"

    # Status
    status: str = "confirmed"  # "confirmed" | "cancelled" | "completed" | "no_show"

    # Metadata
    created_at: str | None = None
    updated_at: str | None = None

    def __post_init__(self):
        now = datetime.now(TIMEZONE).isoformat()
        if self.created_at is None:
            self.created_at = now
        self.updated_at = now

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Appointment":
        valid_fields = {f.name for f in __import__("dataclasses").fields(cls)}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


def _get_hours_for_date(d: date) -> tuple | None:
    """Return (open_hour, close_hour) for a given date, or None if closed."""
    return HOURS_BY_DAY.get(d.weekday())


def _generate_slots(open_hour: int, close_hour: int) -> list[str]:
    """Generate hourly time slot strings between open and close - 1."""
    slots = []
    for hour in range(open_hour, close_hour):
        dt = datetime(2000, 1, 1, hour, 0)
        # Handle Windows/Linux format differences for removing leading zero
        time_str = dt.strftime("%I:%M %p")
        if time_str.startswith("0"):
            time_str = time_str[1:]
        slots.append(time_str)
    return slots


class AppointmentManager:
    """Manages appointment storage and retrieval via Firestore"""

    def __init__(self, project_id: str = None):
        self.db = firestore.Client(project=project_id)
        self.collection_name = "appointments"

    def _collection(self):
        return self.db.collection(self.collection_name)

    async def create_appointment(self, appt: Appointment) -> Appointment:
        """Create appointment with double-booking protection via transaction."""
        return await asyncio.to_thread(self.create_appointment_sync, appt)

    def create_appointment_sync(self, appt: Appointment) -> Appointment:
        """Synchronous version of create_appointment for non-async callers."""
        # Validate date is not in the past
        try:
            appt_date = datetime.strptime(appt.date, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError(f"Invalid date format: {appt.date}. Use YYYY-MM-DD.")

        now = datetime.now(TIMEZONE)
        if appt_date < now.date():
            raise ValueError("Cannot book appointments in the past.")

        # Enforce the booking window, closed days, and on-grid time slots — mirroring
        # get_available_slots so direct-API bookings can't bypass the UI rules (book
        # beyond the window, on a closed day, or in an off-grid/oversized slot).
        if appt_date > now.date() + timedelta(days=BOOKING_WINDOW_DAYS):
            raise ValueError(
                f"Appointments can only be booked up to {BOOKING_WINDOW_DAYS} days in advance."
            )
        hours = _get_hours_for_date(appt_date)
        if hours is None:
            raise ValueError(f"We are closed on {appt_date.strftime('%A')}.")
        if appt.time_slot and appt.time_slot not in _generate_slots(*hours):
            raise ValueError(
                f"{appt.time_slot} is not a valid time slot for {appt_date.strftime('%A')}."
            )

        # If booking today, ensure the time slot hasn't passed
        if appt_date == now.date() and appt.time_slot:
            slot_hour = datetime.strptime(appt.time_slot, "%I:%M %p").hour
            if slot_hour <= now.hour:
                raise ValueError(f"Time slot {appt.time_slot} has already passed today.")

        doc_ref = self._collection().document(appt.appointment_id)

        @firestore.transactional
        def txn_create(transaction):
            # Check current bookings for this date+slot
            query = (
                self._collection()
                .where("date", "==", appt.date)
                .where("time_slot", "==", appt.time_slot)
                .where("status", "==", "confirmed")
            )
            # Read INSIDE the transaction (transaction=transaction) so the slot
            # count joins the transaction's read-set / optimistic lock. Without
            # it the read is non-transactional and two concurrent bookings can
            # both see < MAX_PER_SLOT and both commit -> the slot is oversold.
            existing = list(query.stream(transaction=transaction))
            if len(existing) >= MAX_PER_SLOT:
                raise ValueError(f"Time slot {appt.time_slot} on {appt.date} is fully booked.")
            transaction.set(doc_ref, appt.to_dict())

        transaction = self.db.transaction()
        txn_create(transaction)
        return appt

    async def cancel_appointment(self, appointment_id: str) -> Optional["Appointment"]:
        """Cancel an appointment, freeing the slot."""
        def _cancel():
            doc_ref = self._collection().document(appointment_id)
            doc = doc_ref.get()
            if not doc.exists:
                return None

            appt = Appointment.from_dict(doc.to_dict())
            appt.status = "cancelled"
            appt.updated_at = datetime.now(TIMEZONE).isoformat()
            doc_ref.update({"status": "cancelled", "updated_at": appt.updated_at})
            return appt

        return await asyncio.to_thread(_cancel)

    async def get_appointment(self, appointment_id: str) -> Appointment | None:
        """Retrieve appointment by ID."""
        def _get():
            doc = self._collection().document(appointment_id).get()
            if doc.exists:
                return Appointment.from_dict(doc.to_dict())
            return None

        return await asyncio.to_thread(_get)

    async def get_appointments_by_date(self, date_str: str) -> list[Appointment]:
        """Get all confirmed appointments for a date."""
        def _query():
            q = self._collection().where("date", "==", date_str).where("status", "==", "confirmed")
            return [Appointment.from_dict(doc.to_dict()) for doc in q.stream()]

        return await asyncio.to_thread(_query)

    async def get_appointments_by_phone(self, phone: str) -> list[Appointment]:
        """Get appointments for a customer by phone number."""
        def _query():
            q = (
                self._collection()
                .where("phone", "==", phone)
                .where("status", "==", "confirmed")
                .order_by("date")
            )
            return [Appointment.from_dict(doc.to_dict()) for doc in q.stream()]

        return await asyncio.to_thread(_query)

    async def get_available_slots(self, date_str: str) -> dict:
        """
        Get available time slots for a given date.
        Returns dict with date, day_name, slots list, and business hours.
        """
        try:
            d = date.fromisoformat(date_str)
        except ValueError:
            return {"error": f"Invalid date format: {date_str}. Use YYYY-MM-DD."}

        today = datetime.now(TIMEZONE).date()

        if d < today:
            return {"error": "Cannot book appointments in the past."}
        if d > today + timedelta(days=BOOKING_WINDOW_DAYS):
            return {
                "error": f"Appointments can only be booked up to {BOOKING_WINDOW_DAYS} days in advance."
            }

        hours = _get_hours_for_date(d)
        if hours is None:
            return {
                "date": date_str,
                "day_name": d.strftime("%A"),
                "available_slots": [],
                "message": "We are closed on this day.",
            }

        open_hour, close_hour = hours
        all_slots = _generate_slots(open_hour, close_hour)

        # If booking for today, filter out past time slots (include buffer for current hour)
        if d == today:
            now = datetime.now(TIMEZONE)
            all_slots = [s for s in all_slots if datetime.strptime(s, "%I:%M %p").hour > now.hour]

        # Get existing bookings
        existing = await self.get_appointments_by_date(date_str)
        slot_counts = Counter(appt.time_slot for appt in existing)

        available = [s for s in all_slots if slot_counts.get(s, 0) < MAX_PER_SLOT]

        def _format_hour(h: int) -> str:
            s = datetime(2000, 1, 1, h).strftime("%I:%M %p")
            return s[1:] if s.startswith("0") else s

        open_time = _format_hour(open_hour)
        close_time = _format_hour(close_hour)

        return {
            "date": date_str,
            "day_name": d.strftime("%A"),
            "business_hours": f"{open_time} - {close_time}",
            "available_slots": available,
            "total_slots": len(all_slots),
            "booked_slots": len(all_slots) - len(available),
        }

    async def list_appointments(
        self, status: str | None = None, limit: int = 100
    ) -> list[Appointment]:
        """List appointments with optional status filter."""
        def _query():
            q = self._collection()
            if status:
                q = q.where("status", "==", status)
            q = q.order_by("date", direction=firestore.Query.DESCENDING).limit(limit)
            return [Appointment.from_dict(doc.to_dict()) for doc in q.stream()]

        return await asyncio.to_thread(_query)
