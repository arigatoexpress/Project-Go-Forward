"""Tests for appointment_manager.py — pure functions and mocked Firestore logic.

Run: python -m pytest tests/test_appointment_manager.py -v
"""

import asyncio
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def run(coro):
    """Run a coroutine synchronously."""
    return asyncio.run(coro)

sys.path.insert(0, str(Path(__file__).parent.parent))

# Patch firestore before importing appointment_manager so we don't need credentials
with patch("google.cloud.firestore.Client"):
    from appointment_manager import (
        BOOKING_WINDOW_DAYS,
        HOURS_BY_DAY,
        MAX_PER_SLOT,
        Appointment,
        AppointmentManager,
        _generate_slots,
        _get_hours_for_date,
    )


# ---------------------------------------------------------------------------
# _get_hours_for_date — pure function, no mocks needed
# ---------------------------------------------------------------------------

class TestGetHoursForDate:
    def test_monday_returns_nine_to_six(self):
        # 2026-04-20 is a Monday
        d = date(2026, 4, 20)
        assert d.weekday() == 0
        assert _get_hours_for_date(d) == (9, 18)

    def test_saturday_returns_nine_to_five(self):
        # 2026-04-18 is a Saturday
        d = date(2026, 4, 18)
        assert d.weekday() == 5
        assert _get_hours_for_date(d) == (9, 17)

    def test_sunday_returns_noon_to_three(self):
        # 2026-04-19 is a Sunday
        d = date(2026, 4, 19)
        assert d.weekday() == 6
        assert _get_hours_for_date(d) == (12, 15)

    def test_all_weekdays_covered(self):
        """Every weekday 0-6 returns a tuple, not None."""
        for weekday in range(7):
            assert weekday in HOURS_BY_DAY
            open_h, close_h = HOURS_BY_DAY[weekday]
            assert open_h < close_h


# ---------------------------------------------------------------------------
# _generate_slots — pure function
# ---------------------------------------------------------------------------

class TestGenerateSlots:
    def test_monday_slots(self):
        slots = _generate_slots(9, 18)
        assert len(slots) == 9  # 9,10,11,12,13,14,15,16,17
        assert slots[0] == "9:00 AM"
        assert "12:00 PM" in slots
        assert slots[-1] == "5:00 PM"

    def test_sunday_slots(self):
        slots = _generate_slots(12, 15)
        assert len(slots) == 3
        assert slots[0] == "12:00 PM"
        assert slots[-1] == "2:00 PM"

    def test_saturday_slots(self):
        slots = _generate_slots(9, 17)
        assert len(slots) == 8

    def test_no_slots_same_hour(self):
        assert _generate_slots(9, 9) == []

    def test_single_slot(self):
        slots = _generate_slots(10, 11)
        assert slots == ["10:00 AM"]


# ---------------------------------------------------------------------------
# Appointment dataclass
# ---------------------------------------------------------------------------

class TestAppointmentDataclass:
    def _make(self, **kwargs):
        defaults = dict(
            appointment_id="appt-001",
            name="Jane Doe",
            phone="555-555-5555",
            date="2026-05-01",
            time_slot="10:00 AM",
        )
        defaults.update(kwargs)
        return Appointment(**defaults)

    def test_created_at_set_automatically(self):
        appt = self._make()
        assert appt.created_at is not None
        assert "T" in appt.created_at  # ISO format

    def test_updated_at_set_on_init(self):
        appt = self._make()
        assert appt.updated_at is not None

    def test_defaults(self):
        appt = self._make()
        assert appt.status == "confirmed"
        assert appt.source == "website"
        assert appt.duration_minutes == 60
        assert appt.email is None
        assert appt.notes is None

    def test_to_dict_roundtrip(self):
        appt = self._make(email="jane@example.com", notes="Interested in 3BR")
        d = appt.to_dict()
        restored = Appointment.from_dict(d)
        assert restored.appointment_id == appt.appointment_id
        assert restored.name == appt.name
        assert restored.email == appt.email
        assert restored.notes == appt.notes
        assert restored.status == appt.status

    def test_from_dict_ignores_unknown_fields(self):
        data = {
            "appointment_id": "x",
            "name": "Bob",
            "phone": "555-000-0000",
            "unknown_field": "should be ignored",
        }
        appt = Appointment.from_dict(data)
        assert appt.name == "Bob"
        assert not hasattr(appt, "unknown_field")

    def test_custom_status(self):
        appt = self._make(status="cancelled")
        assert appt.status == "cancelled"


# ---------------------------------------------------------------------------
# AppointmentManager.get_available_slots — mock Firestore + internal method
# ---------------------------------------------------------------------------

class TestGetAvailableSlots:
    """Tests for the complex slot-availability logic, with Firestore mocked."""

    def _manager(self):
        with patch("google.cloud.firestore.Client"):
            mgr = AppointmentManager()
        return mgr

    def test_invalid_date_format(self):
        mgr = self._manager()
        result = run(mgr.get_available_slots("not-a-date"))
        assert "error" in result

    def test_past_date_returns_error(self):
        mgr = self._manager()
        yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()
        result = run(mgr.get_available_slots(yesterday))
        assert "error" in result
        assert "past" in result["error"].lower()

    def test_too_far_future_returns_error(self):
        mgr = self._manager()
        far_future = (datetime.now().date() + timedelta(days=BOOKING_WINDOW_DAYS + 5)).isoformat()
        result = run(mgr.get_available_slots(far_future))
        assert "error" in result

    def test_valid_future_weekday_returns_slots(self):
        mgr = self._manager()
        today = datetime.now().date()
        days_ahead = (7 - today.weekday()) % 7 or 7  # next Monday
        next_monday = today + timedelta(days=days_ahead)
        if (next_monday - today).days > BOOKING_WINDOW_DAYS:
            pytest.skip("Next Monday is outside booking window")

        mgr.get_appointments_by_date = AsyncMock(return_value=[])
        result = run(mgr.get_available_slots(next_monday.isoformat()))

        assert "available_slots" in result
        assert len(result["available_slots"]) == 9  # Mon 9-18 → 9 slots
        assert result["day_name"] == "Monday"
        assert "business_hours" in result

    def test_fully_booked_slot_excluded(self):
        mgr = self._manager()
        today = datetime.now().date()
        days_ahead = (7 - today.weekday()) % 7 or 7
        next_monday = today + timedelta(days=days_ahead)
        if (next_monday - today).days > BOOKING_WINDOW_DAYS:
            pytest.skip("Next Monday is outside booking window")

        booked = [
            Appointment(
                appointment_id=f"appt-{i}",
                name="Someone",
                phone="555-000-0000",
                date=next_monday.isoformat(),
                time_slot="10:00 AM",
            )
            for i in range(MAX_PER_SLOT)
        ]
        mgr.get_appointments_by_date = AsyncMock(return_value=booked)
        result = run(mgr.get_available_slots(next_monday.isoformat()))

        assert "10:00 AM" not in result["available_slots"]
        assert result["booked_slots"] == 1

    def test_partially_booked_slot_still_available(self):
        mgr = self._manager()
        today = datetime.now().date()
        days_ahead = (7 - today.weekday()) % 7 or 7
        next_monday = today + timedelta(days=days_ahead)
        if (next_monday - today).days > BOOKING_WINDOW_DAYS:
            pytest.skip("Next Monday is outside booking window")

        booked = [
            Appointment(
                appointment_id="appt-x",
                name="Someone",
                phone="555-000-0000",
                date=next_monday.isoformat(),
                time_slot="10:00 AM",
            )
        ]
        mgr.get_appointments_by_date = AsyncMock(return_value=booked)
        result = run(mgr.get_available_slots(next_monday.isoformat()))

        assert "10:00 AM" in result["available_slots"]

    def test_response_structure(self):
        mgr = self._manager()
        today = datetime.now().date()
        days_ahead = (7 - today.weekday()) % 7 or 7
        next_monday = today + timedelta(days=days_ahead)
        if (next_monday - today).days > BOOKING_WINDOW_DAYS:
            pytest.skip("Next Monday is outside booking window")

        mgr.get_appointments_by_date = AsyncMock(return_value=[])
        result = run(mgr.get_available_slots(next_monday.isoformat()))

        required_keys = {"date", "day_name", "business_hours", "available_slots", "total_slots", "booked_slots"}
        assert required_keys.issubset(result.keys())


# ---------------------------------------------------------------------------
# AppointmentManager.create_appointment — date validation (no Firestore needed)
# ---------------------------------------------------------------------------

class TestCreateAppointmentValidation:
    def _manager(self):
        with patch("google.cloud.firestore.Client"):
            mgr = AppointmentManager()
        return mgr

    def _appt(self, date_str, time_slot="10:00 AM"):
        return Appointment(
            appointment_id="appt-test",
            name="Test User",
            phone="555-123-4567",
            date=date_str,
            time_slot=time_slot,
        )

    def test_past_date_raises(self):
        mgr = self._manager()
        yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()
        with pytest.raises(ValueError, match="past"):
            run(mgr.create_appointment(self._appt(yesterday)))

    def test_invalid_date_format_raises(self):
        mgr = self._manager()
        with pytest.raises(ValueError, match="Invalid date"):
            run(mgr.create_appointment(self._appt("04/20/2026")))
