"""PR-1 — analytics pipeline + lead/appointment observability.

1. /api/analytics is a dedicated fire-and-forget event sink. Client events used
   to POST to /api/contact, which validates name/phone and silently rejected
   every event (the whole client event stream was lost + contact logs polluted).
2. The appointment handler now surfaces email/notify/lead-storage failures in a
   `warnings` list (parity with /api/contact) so a dropped appointment-lead is
   observable instead of swallowed.
"""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_api_v1 import create_client


def test_analytics_endpoint_never_errors(monkeypatch):
    client, *_ = create_client(monkeypatch)
    # Valid event, junk shapes, and an empty/eventless body all return 200 {ok}
    for body in (
        {"event": "home_view", "home": "Nassau"},
        {"_event": "legacy_name_still_ok"},
        {"nope": "no event key"},
        [1, 2, 3],
        {},
    ):
        r = client.post("/api/analytics", json=body)
        assert r.status_code == 200, body
        assert r.json() == {"ok": True}, body


def test_analytics_event_is_stored(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    captured = {}

    class RecColl:
        def __init__(self, name):
            self.name = name

        def add(self, doc):
            captured.setdefault(self.name, []).append(doc)

    monkeypatch.setattr(main._db.db, "collection", lambda name: RecColl(name))

    r = client.post("/api/analytics", json={"event": "tour_click", "home": "Nassau", "path": "/inventory"})
    assert r.json() == {"ok": True}
    events = captured.get("analytics_events", [])
    assert events, "event should be written to analytics_events"
    rec = events[0]
    assert rec["event"] == "tour_click"
    assert rec["props"]["home"] == "Nassau"
    assert rec["props"]["path"] == "/inventory"
    assert "created_at" in rec and "event" not in rec["props"]


def test_analytics_storage_failure_is_swallowed(monkeypatch):
    client, main, *_ = create_client(monkeypatch)

    def boom(_name):
        raise RuntimeError("firestore unavailable")

    monkeypatch.setattr(main._db.db, "collection", boom)
    r = client.post("/api/analytics", json={"event": "home_view"})
    assert r.status_code == 200 and r.json() == {"ok": True}  # fire-and-forget


def test_appointment_surfaces_lead_storage_warning(monkeypatch):
    client, main, *_ = create_client(monkeypatch)

    # The harness's FakeAppointment/FakeAppointmentManager can't model a booking,
    # so stub Appointment + create_appointment to reach the CRM-lead failure path.
    monkeypatch.setattr(main, "Appointment", lambda **kw: types.SimpleNamespace(**kw), raising=False)

    async def ok_create(_appt):
        return types.SimpleNamespace(to_dict=lambda: {"appointment_id": "appt_test"})

    async def boom(_lead):
        raise RuntimeError("firestore down")

    monkeypatch.setattr(main.appointment_manager, "create_appointment", ok_create, raising=False)
    monkeypatch.setattr(main.lead_manager, "create_lead", boom)

    r = client.post(
        "/api/appointments",
        json={"name": "Al Buyer", "phone": "2813243020", "date": "2026-07-01", "time_slot": "10:00 AM"},
    )
    body = r.json()
    assert body["success"] is True  # appointment still booked
    assert "lead_storage_failed" in body.get("warnings", [])  # but the lost lead is loud
