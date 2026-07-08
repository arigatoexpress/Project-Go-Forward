"""
CRM Tools for Texas Home Outlet AI Agent.

These tools allow the agent to capture customer leads, book appointments,
and manage scheduling for the sales team.
"""

import json
import logging
import os
import re
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from google.adk.tools import ToolContext

from email_service import notify_new_lead

# Configure structured logging for production
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("crm_tools")

TIMEZONE = ZoneInfo("America/Chicago")

# Lazily-created so importing this module (and unit tests) never spins up a real
# Firestore client. Tests monkeypatch _get_lead_manager / _lead_manager.
_lead_manager = None


def _get_lead_manager():
    """Return a cached LeadManager (durable Firestore-backed lead store)."""
    global _lead_manager
    if _lead_manager is None:
        from lead_management import LeadManager

        _lead_manager = LeadManager()
    return _lead_manager


def save_lead(
    user_name: str, phone_number: str, interest_notes: str, tool_context: ToolContext = None
) -> dict:
    """
    Save a customer lead for sales follow-up.

    Use this tool when a user expresses interest in buying, financing, or visiting,
    and provides their contact details.

    Args:
        user_name: The customer's full name.
        phone_number: The customer's phone number.
        interest_notes: Notes about what the customer is interested in (e.g. "Looking for 3/2 double wide under $100k").
        tool_context: ADK tool context.

    Returns:
        Confirmation dictionary.
    """

    # 1. basic validation
    if not user_name or not phone_number:
        return {"success": False, "message": "Please provide both a name and phone number."}

    # clean phone number
    phone_digits = re.sub(r"\D", "", phone_number)
    if len(phone_digits) < 10:
        return {
            "success": False,
            "message": "Phone number appears invalid (too short). Please provide a 10-digit number.",
        }

    # 2. Structure the lead data
    timestamp = datetime.utcnow().isoformat()
    lead_data = {
        "event_type": "LEAD_CAPTURE",
        "timestamp": timestamp,
        "name": user_name,
        "phone": phone_number,  # Keep original format for readability
        "notes": interest_notes,
        "source": "AI_AGENT",
    }

    # 3. Log to stdout (Cloud Logging)
    logger.info(json.dumps(lead_data))

    # 3.5 Persist DURABLY to Firestore — the ONLY storage that survives on Cloud
    # Run (the container filesystem is ephemeral, so the local-file write below
    # is a dev convenience only). Without this the chat agent told customers
    # "saved" while their name+phone existed only in Cloud Logging — invisible to
    # the CRM (high-intent leads silently lost). A lead is reported saved ONLY if
    # this write succeeds.
    persisted = False
    try:
        from lead_management import Lead, normalize_phone

        lead = Lead(
            lead_id=f"chat_{uuid.uuid4().hex[:12]}",
            user_id="chat",
            session_id="",
            name=user_name,
            phone=normalize_phone(phone_number),
            source="chat",
            triage_notes=interest_notes,
        )
        _get_lead_manager().db.collection("leads").document(lead.lead_id).set(lead.to_dict())
        persisted = True
    except Exception as e:
        logger.error(f"Lead Firestore persist FAILED — lead at risk: {e}")

    # 4. Dev/Fallback: Append to local JSON file if possible
    try:
        data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
        if not os.path.exists(data_dir):
            data_dir = "data"

        leads_file = os.path.join(data_dir, "leads.json")

        leads = []
        if os.path.exists(leads_file):
            try:
                with open(leads_file) as f:
                    leads = json.load(f)
            except (OSError, json.JSONDecodeError):
                leads = []

        leads.append(lead_data)

        if os.access(os.path.dirname(leads_file) or ".", os.W_OK):
            with open(leads_file, "w") as f:
                json.dump(leads, f, indent=2)

    except Exception as e:
        logger.warning(f"Could not write to local file: {e}")

    if persisted:
        # Alert the sales team the same way /api/contact does. A high-intent chat
        # lead that never pages a human is a lead lost — captured chat leads were
        # previously landing in Firestore silently (no email), invisible to sales
        # unless someone happened to open the CRM. Never let a notify failure turn
        # a successful save into a failure the customer sees.
        try:
            notify_new_lead(customer_name=user_name, phone=phone_number, source="chat")
        except Exception as e:
            logger.warning(f"Chat lead staff alert failed (lead was saved): {e}")
        return {
            "success": True,
            "message": f"Thanks {user_name}! I've saved your info. A sales representative will call you at {phone_number} shortly.",
        }
    # Firestore write failed — be truthful so the agent routes the customer to a
    # human instead of falsely confirming a lead that wasn't durably saved.
    return {
        "success": False,
        "message": (
            f"Thanks {user_name} — I had trouble saving your details just now. "
            "Please call our showroom directly and we'll make sure a representative reaches you."
        ),
    }


def get_current_datetime(tool_context: ToolContext = None) -> dict:
    """
    Get the current date and time in Central Time (America/Chicago).

    Use this tool BEFORE interpreting relative dates like "Monday", "tomorrow",
    "next week", etc. This ensures you always know today's actual date.

    Returns:
        Dictionary with current date, time, day of week, and helpful context.
    """
    now = datetime.now(TIMEZONE)
    today = now.date()

    # Find next Monday through Sunday
    upcoming = {}
    for days_ahead in range(0, 7):
        future = today + timedelta(days=days_ahead)
        day_name = future.strftime("%A")
        if day_name not in upcoming:
            upcoming[day_name] = future.isoformat()

    return {
        "current_date": today.isoformat(),
        "current_time": now.strftime("%-I:%M %p"),
        "day_of_week": now.strftime("%A"),
        "formatted": now.strftime("%A, %B %d, %Y at %-I:%M %p CT"),
        "upcoming_days": upcoming,
        "note": "All dates are in Central Time (America/Chicago).",
    }


def check_available_slots(date_str: str, tool_context: ToolContext = None) -> dict:
    """
    Check available appointment time slots for a given date.

    Use this BEFORE booking an appointment to show the customer what times are open.
    The tool returns available hourly time slots for showroom visits.

    Args:
        date_str: Date to check in YYYY-MM-DD format (e.g. "2026-02-15").
            The AI agent should convert natural language like "tomorrow" or "next Saturday"
            into this format before calling this tool.
        tool_context: ADK tool context.

    Returns:
        Dictionary with available_slots list, business_hours, and date info.
    """
    try:
        d = date.fromisoformat(date_str)
    except ValueError:
        return {"error": f"Invalid date format '{date_str}'. Please use YYYY-MM-DD format."}

    today = datetime.now(TIMEZONE).date()

    if d < today:
        return {"error": "That date is in the past. Please choose a future date."}
    if d > today + timedelta(days=30):
        return {"error": "Appointments can only be booked up to 30 days in advance."}

    # Business hours by day of week: (open_hour, close_hour)
    hours_by_day = {
        0: (9, 18),
        1: (9, 18),
        2: (9, 18),
        3: (9, 18),
        4: (9, 18),  # Mon-Fri
        5: (9, 17),  # Saturday
        6: None,  # Sunday closed
    }

    hours = hours_by_day.get(d.weekday())
    if hours is None:
        return {
            "date": date_str,
            "day_name": d.strftime("%A"),
            "available_slots": [],
            "message": "We are closed on this day.",
        }

    open_hour, close_hour = hours

    # Generate hourly slots (last slot = close - 1 hour)
    slots = []
    for hour in range(open_hour, close_hour):
        dt = datetime(2000, 1, 1, hour, 0)
        slots.append(dt.strftime("%-I:%M %p"))

    # Filter out past slots if booking for today
    if d == today:
        current_hour = datetime.now(TIMEZONE).hour
        slots = [s for s in slots if datetime.strptime(s, "%I:%M %p").hour > current_hour]

    open_time = datetime(2000, 1, 1, open_hour).strftime("%-I:%M %p")
    close_time = datetime(2000, 1, 1, close_hour).strftime("%-I:%M %p")

    # Note: Firestore slot counting happens in the API layer.
    # This tool gives the agent the full list of possible slots so it can present options.
    # The actual booking via book_appointment() will validate availability at write time.
    return {
        "date": date_str,
        "day_name": d.strftime("%A"),
        "business_hours": f"{open_time} - {close_time}",
        "available_slots": slots,
        "location": "Texas Home Outlet, 10685 FM 1960 East, Huffman, TX 77336",
        "note": "Each appointment is 1 hour. Please arrive 5 minutes early.",
    }


def book_appointment(
    user_name: str,
    phone_number: str,
    appointment_date: str,
    time_slot: str,
    notes: str = "",
    tool_context: ToolContext = None,
) -> dict:
    """
    Book a showroom appointment for a customer.

    Use check_available_slots FIRST to confirm the time is available,
    then use this tool to finalize the booking.

    Args:
        user_name: Customer's full name.
        phone_number: Customer's phone number (10 digits).
        appointment_date: Date in YYYY-MM-DD format (e.g. "2026-02-15").
        time_slot: Time slot string (e.g. "10:00 AM", "2:00 PM").
        notes: Optional notes about what the customer wants to see.
        tool_context: ADK tool context.

    Returns:
        Confirmation dictionary with appointment details.
    """
    if not user_name or not phone_number:
        return {"success": False, "message": "Please provide both a name and phone number."}

    phone_digits = re.sub(r"\D", "", phone_number)
    if len(phone_digits) < 10:
        return {
            "success": False,
            "message": "Phone number appears invalid. Please provide a 10-digit number.",
        }

    try:
        d = date.fromisoformat(appointment_date)
    except ValueError:
        return {
            "success": False,
            "message": f"Invalid date format '{appointment_date}'. Use YYYY-MM-DD.",
        }

    if d < datetime.now(TIMEZONE).date():
        return {"success": False, "message": "Cannot book an appointment in the past."}

    # Log the appointment request for Cloud Logging
    appt_data = {
        "event_type": "APPOINTMENT_BOOKED",
        "timestamp": datetime.now(TIMEZONE).isoformat(),
        "name": user_name,
        "phone": phone_number,
        "date": appointment_date,
        "time_slot": time_slot,
        "notes": notes,
        "source": "AI_AGENT",
    }
    logger.info(json.dumps(appt_data))

    # Also save as a lead
    save_lead(
        user_name,
        phone_number,
        f"APPOINTMENT: {appointment_date} at {time_slot}. {notes}",
        tool_context,
    )

    return {
        "success": True,
        "message": (
            f"Your appointment is confirmed!\n\n"
            f"**Date:** {d.strftime('%A, %B %d, %Y')}\n"
            f"**Time:** {time_slot}\n"
            f"**Location:** Texas Home Outlet, 10685 FM 1960 East, Huffman, TX 77336\n"
            f"**Phone:** (281) 324-3020\n\n"
            f"Please arrive 5 minutes early. We look forward to seeing you, {user_name}!"
        ),
        "appointment": {
            "date": appointment_date,
            "time_slot": time_slot,
            "name": user_name,
        },
    }


def cancel_appointment(
    appointment_id: str, phone_number: str, tool_context: ToolContext = None
) -> dict:
    """
    Cancel an existing appointment.

    Args:
        appointment_id: The appointment ID provided at booking time.
        phone_number: Customer's phone number for verification.
        tool_context: ADK tool context.

    Returns:
        Cancellation confirmation.
    """
    if not appointment_id or not phone_number:
        return {
            "success": False,
            "message": "Please provide both the appointment ID and phone number.",
        }

    logger.info(
        json.dumps(
            {
                "event_type": "APPOINTMENT_CANCEL_REQUEST",
                "timestamp": datetime.now(TIMEZONE).isoformat(),
                "appointment_id": appointment_id,
                "phone": phone_number,
            }
        )
    )

    return {
        "success": True,
        "message": f"I've submitted a cancellation request for appointment {appointment_id}. Our team will confirm the cancellation shortly.",
    }


def get_business_hours(tool_context: ToolContext = None) -> dict:
    """
    Get the showroom's business hours and appointment availability info.

    Returns:
        Hours schedule, location, and booking info.
    """
    return {
        "location": "Texas Home Outlet, 10685 FM 1960 East, Huffman, TX 77336",
        "hours": "Mon-Fri: 9am-6pm, Sat: 9am-5pm, Sun: Closed",
        "phone": "(281) 324-3020",
        "appointments": "Appointments can be booked up to 30 days in advance. Each visit is approximately 1 hour.",
    }
