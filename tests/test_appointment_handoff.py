"""Golden evals for the contact/quote -> appointment conversion handoff.

The handoff may promote the lead that was just persisted, but only when the
submitted phone still matches. A transient CRM read failure must not create a
duplicate; the booked appointment remains the source of truth and the warning
makes the missed promotion observable.
"""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_api_v1 import create_client


def _stub_appointment_storage(monkeypatch, main):
    monkeypatch.setattr(
        main,
        "Appointment",
        lambda **kw: types.SimpleNamespace(**kw),
        raising=False,
    )

    async def ok_create(_appt):
        return types.SimpleNamespace(to_dict=lambda: {"appointment_id": "appt_test"})

    monkeypatch.setattr(
        main.appointment_manager,
        "create_appointment",
        ok_create,
        raising=False,
    )


def _capture_contact(client, *, phone="(281) 324-3020"):
    response = client.post(
        "/api/contact",
        json={
            "name": "Alice Buyer",
            "phone": phone,
            "email": "alice@example.com",
            "source": "inventory_quote",
        },
    )
    assert response.json()["success"] is True
    return response.json()["lead_id"]


def _book(client, *, lead_id, phone="2813243020"):
    return client.post(
        "/api/appointments",
        json={
            "name": "Alice Buyer",
            "phone": phone,
            "email": "alice@example.com",
            "date": "2026-08-01",
            "time_slot": "10:00 AM",
            "source": "inventory_quote_handoff",
            "lead_id": lead_id,
        },
    ).json()


def test_matching_handoff_promotes_existing_lead_without_duplicate(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    _stub_appointment_storage(monkeypatch, main)
    lead_id = _capture_contact(client)
    before = len(main.lead_manager.leads)

    body = _book(client, lead_id=lead_id)

    assert body["success"] is True
    assert len(main.lead_manager.leads) == before
    promoted = next(lead for lead in main.lead_manager.leads if lead.lead_id == lead_id)
    assert promoted.appointment_requested is True
    assert promoted.status == "qualified"
    assert promoted.source == "inventory_quote"


def test_mismatched_phone_never_mutates_supplied_lead(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    _stub_appointment_storage(monkeypatch, main)
    lead_id = _capture_contact(client)
    before = len(main.lead_manager.leads)

    body = _book(client, lead_id=lead_id, phone="7135550199")

    assert body["success"] is True
    assert len(main.lead_manager.leads) == before + 1
    untouched = next(lead for lead in main.lead_manager.leads if lead.lead_id == lead_id)
    assert untouched.appointment_requested is False
    assert untouched.status == "new"
    assert main.lead_manager.leads[-1].source == "inventory_quote_handoff"


def test_handoff_read_failure_warns_without_creating_duplicate(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    _stub_appointment_storage(monkeypatch, main)
    lead_id = _capture_contact(client)
    before = len(main.lead_manager.leads)

    async def unavailable(_lead_id):
        raise RuntimeError("firestore unavailable")

    monkeypatch.setattr(main.lead_manager, "get_lead", unavailable)
    body = _book(client, lead_id=lead_id)

    assert body["success"] is True
    assert "lead_promotion_failed" in body.get("warnings", [])
    assert len(main.lead_manager.leads) == before
