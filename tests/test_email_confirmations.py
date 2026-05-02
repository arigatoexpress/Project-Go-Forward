"""Tests for customer confirmation + team alert email templates.

Verifies:
  1. contact_form_customer_confirmation — body content + no raise on Resend failure
  2. contact_form_team_alert            — body content + multiple recipients
  3. appointment_booked_customer_confirmation — body content + .ics attachment
  4. appointment_booked_team_alert      — body content + no raise on Resend failure

Run: python -m pytest tests/test_email_confirmations.py -v
"""

import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── helpers ──────────────────────────────────────────────────────────────────

def _patch_resend_key():
    """Patch the module-level RESEND_API_KEY constant so send_email proceeds past the guard."""
    return mock.patch("email_service.RESEND_API_KEY", "re_test_key")


# ── 1. contact_form_customer_confirmation ────────────────────────────────────

class TestContactFormCustomerConfirmation:
    def test_template_inputs_in_body(self):
        """Name, message excerpt, office hours, and STOP line appear in the sent HTML."""
        from email_service import contact_form_customer_confirmation

        with _patch_resend_key():
            with mock.patch("resend.Emails.send", return_value={"id": "msg_001"}) as mock_send:
                result = contact_form_customer_confirmation(
                    to="jane@example.com",
                    name="Jane Smith",
                    message_excerpt="I want a 3-bedroom home near Huffman",
                )

        assert result["success"] is True
        payload = mock_send.call_args[0][0]
        assert "Jane" in payload["html"]
        assert "3-bedroom home" in payload["html"]
        assert "STOP" in payload["html"]
        # Office hours pulled from BUSINESS_HOURS constant
        assert "Mon" in payload["html"]
        assert "sales@texashomeoutlet.com" in payload["html"]
        assert payload["to"] == ["jane@example.com"]

    def test_resend_failure_does_not_raise(self):
        """A Resend exception is caught and returned as a failed result, not re-raised."""
        from email_service import contact_form_customer_confirmation

        with _patch_resend_key():
            with mock.patch("resend.Emails.send", side_effect=RuntimeError("network error")):
                result = contact_form_customer_confirmation(
                    to="jane@example.com",
                    name="Jane Smith",
                )

        assert result["success"] is False
        assert "network error" in result["error"]


# ── 2. contact_form_team_alert ────────────────────────────────────────────────

class TestContactFormTeamAlert:
    def test_template_inputs_in_body(self):
        """Name, phone, message, and CRM link appear in the team alert HTML."""
        from email_service import contact_form_team_alert

        with _patch_resend_key():
            with mock.patch("email_service.TEAM_ALERT_EMAILS", "sales@texashomeoutlet.com"):
                with mock.patch("resend.Emails.send", return_value={"id": "msg_002"}) as mock_send:
                    result = contact_form_team_alert(
                        name="Bob Rancher",
                        phone="(713) 555-1234",
                        email="bob@ranch.com",
                        message="Looking for a double-wide with a porch",
                        lead_id="contact_12345_abcd",
                    )

        assert result["success"] is True
        payload = mock_send.call_args[0][0]
        assert "Bob Rancher" in payload["html"]
        assert "(713) 555-1234" in payload["html"]
        assert "double-wide" in payload["html"]
        assert "contact_12345_abcd" in payload["html"]
        # Sent to the team list, not a single address
        assert isinstance(payload["to"], list)
        assert "sales@texashomeoutlet.com" in payload["to"]

    def test_resend_failure_does_not_raise(self):
        """A Resend exception is caught and returned as a failed result, not re-raised."""
        from email_service import contact_form_team_alert

        with _patch_resend_key():
            with mock.patch("email_service.TEAM_ALERT_EMAILS", "sales@texashomeoutlet.com"):
                with mock.patch("resend.Emails.send", side_effect=ConnectionError("timeout")):
                    result = contact_form_team_alert(
                        name="Bob Rancher",
                        phone="(713) 555-1234",
                    )

        assert result["success"] is False


# ── 3. appointment_booked_customer_confirmation ───────────────────────────────

class TestAppointmentBookedCustomerConfirmation:
    def test_template_inputs_and_ics_attachment(self):
        """Date, time, location appear in HTML; .ics attachment is included."""
        from email_service import appointment_booked_customer_confirmation

        with _patch_resend_key():
            with mock.patch("resend.Emails.send", return_value={"id": "msg_003"}) as mock_send:
                result = appointment_booked_customer_confirmation(
                    to="customer@example.com",
                    name="Maria Lopez",
                    date="2026-06-15",
                    time_slot="10:00 AM",
                    location="10685 FM 1960 East, Huffman, TX 77338",
                    appointment_id="appt_99_xyz",
                )

        assert result["success"] is True
        payload = mock_send.call_args[0][0]
        assert "Maria" in payload["html"]
        assert "2026-06-15" in payload["html"]
        assert "10:00 AM" in payload["html"]
        assert "Huffman" in payload["html"]
        assert "STOP" in payload["html"]
        # .ics attachment should be present
        assert "attachments" in payload
        assert len(payload["attachments"]) == 1
        att = payload["attachments"][0]
        assert att["filename"] == "appointment.ics"
        # ICS content is a list of ints (bytes) and contains VCALENDAR
        ics_bytes = bytes(att["content"])
        assert b"VCALENDAR" in ics_bytes
        assert b"Texas Home Outlet" in ics_bytes

    def test_resend_failure_does_not_raise(self):
        """A Resend exception is caught and returned as a failed result, not re-raised."""
        from email_service import appointment_booked_customer_confirmation

        with _patch_resend_key():
            with mock.patch("resend.Emails.send", side_effect=OSError("smtp gone")):
                result = appointment_booked_customer_confirmation(
                    to="customer@example.com",
                    name="Maria Lopez",
                    date="2026-06-15",
                    time_slot="10:00 AM",
                )

        assert result["success"] is False


# ── 4. appointment_booked_team_alert ─────────────────────────────────────────

class TestAppointmentBookedTeamAlert:
    def test_template_inputs_in_body(self):
        """Customer details appear in the team alert HTML."""
        from email_service import appointment_booked_team_alert

        with _patch_resend_key():
            with mock.patch("email_service.TEAM_ALERT_EMAILS", "sales@texashomeoutlet.com,ops@texashomeoutlet.com"):
                with mock.patch("resend.Emails.send", return_value={"id": "msg_004"}) as mock_send:
                    result = appointment_booked_team_alert(
                        name="Carlos Reyes",
                        date="2026-06-20",
                        time_slot="2:00 PM",
                        contact_info={
                            "phone": "(832) 555-9999",
                            "email": "carlos@example.com",
                            "notes": "Interested in repo homes",
                            "appointment_id": "appt_777_abc",
                        },
                    )

        assert result["success"] is True
        payload = mock_send.call_args[0][0]
        assert "Carlos Reyes" in payload["html"]
        assert "2026-06-20" in payload["html"]
        assert "2:00 PM" in payload["html"]
        assert "(832) 555-9999" in payload["html"]
        assert "repo homes" in payload["html"]
        # Both team recipients present
        assert "sales@texashomeoutlet.com" in payload["to"]
        assert "ops@texashomeoutlet.com" in payload["to"]

    def test_resend_failure_does_not_raise(self):
        """A Resend exception is caught and returned as a failed result, not re-raised."""
        from email_service import appointment_booked_team_alert

        with _patch_resend_key():
            with mock.patch("email_service.TEAM_ALERT_EMAILS", "sales@texashomeoutlet.com"):
                with mock.patch("resend.Emails.send", side_effect=ValueError("bad payload")):
                    result = appointment_booked_team_alert(
                        name="Carlos Reyes",
                        date="2026-06-20",
                        time_slot="2:00 PM",
                    )

        assert result["success"] is False
