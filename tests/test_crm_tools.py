"""Edge-path tests for tools/crm_tools.py beyond the happy-path contract in test_crm.py.

Covers:
  * _get_lead_manager lazy load (fake lead_management module)
  * save_lead durable-persist failure (truthful "couldn't save" response)
  * save_lead staff-alert failure (lead still saved)
  * save_lead local-file fallback branches (missing data dir, corrupt JSON,
    successful write, write exception)
  * get_current_datetime / get_business_hours contracts
  * check_available_slots validation, closed-day, hourly slot generation,
    and same-day past-slot filtering
  * book_appointment validation matrix and lead-capture side effect
  * cancel_appointment validation and confirmation
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import mock_open

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from tools import crm_tools  # noqa: E402


@pytest.fixture
def hermetic(monkeypatch):
    """No local file writes, no real Firestore, no real staff email."""
    monkeypatch.setattr(crm_tools.os, "access", lambda *a, **k: False)

    class _FakeDocRef:
        def __init__(self):
            self.written = None

        def set(self, data, timeout=None):
            self.written = data

    class _FakeColl:
        def __init__(self):
            self.docs: dict[str, _FakeDocRef] = {}

        def document(self, doc_id):
            return self.docs.setdefault(doc_id, _FakeDocRef())

    class _FakeDB:
        def __init__(self):
            self.leads = _FakeColl()

        def collection(self, name):
            assert name == "leads"
            return self.leads

    manager = SimpleNamespace(db=_FakeDB())
    monkeypatch.setattr(crm_tools, "_get_lead_manager", lambda: manager)
    monkeypatch.setattr(
        crm_tools, "notify_new_lead", lambda **kwargs: None
    )
    return manager


def _today() -> datetime.date:
    return datetime.now(crm_tools.TIMEZONE).date()


def _future_date(days: int) -> str:
    return (_today() + timedelta(days=days)).isoformat()


# ─── _get_lead_manager lazy load ────────────────────────────────────────────


def test_get_lead_manager_lazy_loads_and_caches(monkeypatch):
    """The real import path constructs LeadManager once and caches it."""
    constructed = []

    class _FakeLeadManager:
        def __init__(self):
            constructed.append(self)

    fake_module = SimpleNamespace(LeadManager=_FakeLeadManager)
    monkeypatch.setitem(sys.modules, "lead_management", fake_module)
    monkeypatch.setattr(crm_tools, "_lead_manager", None, raising=False)

    manager = crm_tools._get_lead_manager()
    assert manager is crm_tools._lead_manager
    assert constructed == [manager]
    # Second call returns the cache without constructing again
    assert crm_tools._get_lead_manager() is manager
    assert len(constructed) == 1


# ─── save_lead durable persist + alert paths ────────────────────────────────


def test_save_lead_truthful_failure_when_persist_raises(
    hermetic, monkeypatch, caplog
):
    """Firestore failure must NOT produce a false 'saved' confirmation."""

    def _boom():
        raise RuntimeError("firestore unavailable")

    monkeypatch.setattr(crm_tools, "_get_lead_manager", _boom)

    with caplog.at_level(logging.ERROR, logger="crm_tools"):
        result = crm_tools.save_lead(
            user_name="Pat Doe",
            phone_number="555-123-4567",
            interest_notes="interested in a double wide",
        )

    assert result["success"] is False
    assert "showroom" in result["message"].lower()
    assert any("persist FAILED" in r.message for r in caplog.records)


def test_save_lead_alert_failure_still_saves(hermetic, monkeypatch, caplog):
    """A failed staff email must not turn a saved lead into a failure."""

    def _boom(**kwargs):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(crm_tools, "notify_new_lead", _boom)

    with caplog.at_level(logging.WARNING, logger="crm_tools"):
        result = crm_tools.save_lead(
            user_name="Pat Doe",
            phone_number="555-123-4567",
            interest_notes="test",
        )

    assert result["success"] is True
    assert any("staff alert failed" in r.message for r in caplog.records)


def test_save_lead_persists_structured_lead_document(hermetic):
    """The durable write carries the normalized chat-lead fields."""
    result = crm_tools.save_lead(
        user_name="Pat Doe",
        phone_number="(281) 555-0100",
        interest_notes="looking for 3/2",
    )
    assert result["success"] is True
    docs = hermetic.db.leads.docs
    assert len(docs) == 1
    data = next(iter(docs.values())).written
    assert data["name"] == "Pat Doe"
    assert data["source"] == "chat"
    assert data["user_id"] == "chat"
    assert data["triage_notes"] == "looking for 3/2"
    assert data["lead_id"].startswith("chat_")


# ─── save_lead local-file fallback branches ─────────────────────────────────


def test_save_lead_uses_plain_data_dir_when_repo_dir_missing(
    hermetic, monkeypatch
):
    monkeypatch.setattr(crm_tools.os.path, "exists", lambda p: False)
    result = crm_tools.save_lead(
        user_name="Pat Doe",
        phone_number="555-123-4567",
        interest_notes="test",
    )
    assert result["success"] is True  # missing dir must not break the save


def test_save_lead_recovers_from_corrupt_leads_file(hermetic, monkeypatch):
    m = mock_open(read_data="not valid json {{{")
    monkeypatch.setattr("builtins.open", m)
    monkeypatch.setattr(crm_tools.os.path, "exists", lambda p: True)
    result = crm_tools.save_lead(
        user_name="Pat Doe",
        phone_number="555-123-4567",
        interest_notes="test",
    )
    assert result["success"] is True


def test_save_lead_writes_leads_file_when_writable(hermetic, monkeypatch):
    m = mock_open(read_data="[]")
    monkeypatch.setattr("builtins.open", m)
    monkeypatch.setattr(crm_tools.os.path, "exists", lambda p: True)
    monkeypatch.setattr(crm_tools.os, "access", lambda *a, **k: True)

    result = crm_tools.save_lead(
        user_name="Pat Doe",
        phone_number="555-123-4567",
        interest_notes="test notes",
    )
    assert result["success"] is True
    written = "".join(call.args[0] for call in m().write.call_args_list)
    assert json.loads(written)[0]["name"] == "Pat Doe"
    assert json.loads(written)[0]["notes"] == "test notes"


def test_save_lead_warns_when_file_write_raises(hermetic, monkeypatch, caplog):
    def _boom(path, mode="r", *a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", _boom)
    monkeypatch.setattr(crm_tools.os.path, "exists", lambda p: True)
    # hermetic forces access False (no writes); re-enable so the write is
    # attempted and its failure observed.
    monkeypatch.setattr(crm_tools.os, "access", lambda *a, **k: True)

    with caplog.at_level(logging.WARNING, logger="crm_tools"):
        result = crm_tools.save_lead(
            user_name="Pat Doe",
            phone_number="555-123-4567",
            interest_notes="test",
        )
    assert result["success"] is True  # file failure is non-fatal
    assert any("could not write to local file" in r.message.lower()
               for r in caplog.records)


# ─── get_current_datetime / get_business_hours ──────────────────────────────


def test_get_current_datetime_contract():
    out = crm_tools.get_current_datetime()
    assert out["current_date"] == _today().isoformat()
    assert out["day_of_week"] == _today().strftime("%A")
    assert "CT" in out["formatted"]
    assert len(out["upcoming_days"]) == 7
    # First upcoming day is today; all values are ISO dates
    assert out["upcoming_days"][out["day_of_week"]] == out["current_date"]
    for iso in out["upcoming_days"].values():
        datetime.fromisoformat(iso)


def test_get_business_hours_contract():
    out = crm_tools.get_business_hours()
    assert "Huffman, TX" in out["location"]
    assert "Sun: Closed" in out["hours"]
    assert out["phone"] == "(281) 324-3020"
    assert "30 days" in out["appointments"]


# ─── check_available_slots ──────────────────────────────────────────────────


def test_check_slots_rejects_invalid_date():
    out = crm_tools.check_available_slots("next Tuesday")
    assert "error" in out
    assert "YYYY-MM-DD" in out["error"]


def test_check_slots_rejects_past_date():
    out = crm_tools.check_available_slots(
        (_today() - timedelta(days=1)).isoformat()
    )
    assert "past" in out["error"]


def test_check_slots_rejects_too_far_out():
    out = crm_tools.check_available_slots(
        (_today() + timedelta(days=31)).isoformat()
    )
    assert "30 days" in out["error"]


def test_check_slots_sunday_is_closed():
    delta = (6 - _today().weekday()) % 7  # days until next Sunday
    sunday = (_today() + timedelta(days=delta)).isoformat()
    out = crm_tools.check_available_slots(sunday)
    assert out["available_slots"] == []
    assert "closed" in out["message"].lower()


def test_check_slots_saturday_uses_short_hours():
    # ``or 7`` skips today when today is Saturday. Same-day slots drop hours
    # that have already passed, so this must use a future Saturday.
    delta = (5 - _today().weekday()) % 7 or 7
    saturday = (_today() + timedelta(days=delta)).isoformat()
    out = crm_tools.check_available_slots(saturday)
    assert out["business_hours"] == "10:00 AM - 3:00 PM"
    assert len(out["available_slots"]) == 5  # 10, 11, 12, 1, 2


def test_check_slots_weekday_lists_hourly_slots():
    d = _today() + timedelta(days=1)
    if d.weekday() == 6:  # Sunday — step forward to Monday
        d += timedelta(days=1)
    elif d.weekday() == 5:  # Saturday — step forward to Monday
        d += timedelta(days=2)
    out = crm_tools.check_available_slots(d.isoformat())
    assert out["business_hours"] == "9:00 AM - 6:00 PM"
    assert len(out["available_slots"]) == 9
    assert out["available_slots"][0] == "9:00 AM"
    assert "FM 1960" in out["location"]


def test_check_slots_today_filters_past_hours():
    out = crm_tools.check_available_slots(_today().isoformat())
    now_hour = datetime.now(crm_tools.TIMEZONE).hour
    for slot in out["available_slots"]:
        hour = datetime.strptime(slot, "%I:%M %p").hour
        assert hour > now_hour


# ─── book_appointment ───────────────────────────────────────────────────────


def test_book_appointment_rejects_missing_fields():
    out = crm_tools.book_appointment(
        user_name="", phone_number="555-123-4567",
        appointment_date=_future_date(2), time_slot="10:00 AM",
    )
    assert out["success"] is False


def test_book_appointment_rejects_short_phone():
    out = crm_tools.book_appointment(
        user_name="Pat", phone_number="12",
        appointment_date=_future_date(2), time_slot="10:00 AM",
    )
    assert out["success"] is False
    assert "10" in out["message"]


def test_book_appointment_rejects_invalid_date():
    out = crm_tools.book_appointment(
        user_name="Pat", phone_number="555-123-4567",
        appointment_date="02/15/2026", time_slot="10:00 AM",
    )
    assert out["success"] is False
    assert "YYYY-MM-DD" in out["message"]


def test_book_appointment_rejects_past_date():
    out = crm_tools.book_appointment(
        user_name="Pat", phone_number="555-123-4567",
        appointment_date=(_today() - timedelta(days=1)).isoformat(),
        time_slot="10:00 AM",
    )
    assert out["success"] is False
    assert "past" in out["message"].lower()


def test_book_appointment_captures_lead_and_confirms(hermetic, monkeypatch):
    calls = []

    def _fake_save_lead(user_name, phone_number, interest_notes, ctx=None):
        calls.append((user_name, phone_number, interest_notes))
        return {"success": True, "message": "saved"}

    monkeypatch.setattr(crm_tools, "save_lead", _fake_save_lead)
    target = _future_date(2)

    out = crm_tools.book_appointment(
        user_name="Pat Doe",
        phone_number="555-123-4567",
        appointment_date=target,
        time_slot="10:00 AM",
        notes="wants to see Model 1672",
    )

    assert out["success"] is True
    assert out["appointment"]["date"] == target
    assert out["appointment"]["time_slot"] == "10:00 AM"
    assert out["appointment"]["name"] == "Pat Doe"
    # The booking must be captured as a lead with the appointment context
    assert calls == [
        (
            "Pat Doe",
            "555-123-4567",
            f"APPOINTMENT: {target} at 10:00 AM. wants to see Model 1672",
        )
    ]


# ─── cancel_appointment ─────────────────────────────────────────────────────


def test_cancel_appointment_requires_both_fields():
    out = crm_tools.cancel_appointment(appointment_id="", phone_number="")
    assert out["success"] is False


def test_cancel_appointment_submits_request():
    out = crm_tools.cancel_appointment(
        appointment_id="appt-123", phone_number="555-123-4567"
    )
    assert out["success"] is True
    assert "appt-123" in out["message"]
    assert "confirm" in out["message"].lower()
