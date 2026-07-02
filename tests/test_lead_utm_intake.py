"""PR 2 — UTM intake wired into POST /api/contact and POST /api/appointments.

Honest attribution: first-party UTM fields are only captured when the visitor
actively reaches out (form submit or booking), never from passive browsing.
"""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_api_v1 import create_client


def _post_contact(client, **body):
    return client.post("/api/contact", json=body)


def test_contact_persists_utm_from_body(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    before = len(main.lead_manager.leads)

    body = _post_contact(
        client,
        name="Alice",
        phone="2813243020",
        utm_source="instagram",
        utm_medium="social",
        utm_campaign="spring-sale",
        utm_content="reel-a",
        referrer="https://t.co/x",
    ).json()
    assert body["success"] is True

    assert len(main.lead_manager.leads) == before + 1
    created = main.lead_manager.leads[-1]
    assert created.utm_source == "instagram"
    assert created.utm_medium == "social"
    assert created.utm_campaign == "spring-sale"
    assert created.utm_content == "reel-a"
    assert created.utm_term is None
    assert created.referrer == "https://t.co/x"


def test_contact_without_utm_leaves_fields_none(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    before = len(main.lead_manager.leads)

    body = _post_contact(client, name="Bob", phone="2813243020").json()
    assert body["success"] is True

    assert len(main.lead_manager.leads) == before + 1
    created = main.lead_manager.leads[-1]
    assert created.utm_source is None
    assert created.utm_medium is None
    assert created.utm_campaign is None
    assert created.utm_content is None
    assert created.utm_term is None
    assert created.referrer is None


def test_appointment_persists_utm_from_body(monkeypatch):
    client, main, *_ = create_client(monkeypatch)

    # The harness's FakeAppointment/FakeAppointmentManager can't model a booking,
    # so stub Appointment + create_appointment to reach the CRM-lead path.
    monkeypatch.setattr(main, "Appointment", lambda **kw: types.SimpleNamespace(**kw), raising=False)

    async def ok_create(_appt):
        return types.SimpleNamespace(to_dict=lambda: {"appointment_id": "appt_test"})

    monkeypatch.setattr(main.appointment_manager, "create_appointment", ok_create, raising=False)

    before = len(main.lead_manager.leads)

    r = client.post(
        "/api/appointments",
        json={
            "name": "Carol",
            "phone": "2813243020",
            "date": "2026-07-01",
            "time_slot": "10:00 AM",
            "utm_source": "google",
            "utm_campaign": "summer-sale",
        },
    )
    body = r.json()
    assert body["success"] is True

    assert len(main.lead_manager.leads) == before + 1
    created = main.lead_manager.leads[-1]
    assert created.utm_source == "google"
    assert created.utm_campaign == "summer-sale"
    assert created.utm_medium is None


def test_utm_values_are_length_capped(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    before = len(main.lead_manager.leads)

    long_campaign = "x" * 500
    body = _post_contact(
        client,
        name="Dave",
        phone="2813243020",
        utm_campaign=long_campaign,
    ).json()
    assert body["success"] is True

    assert len(main.lead_manager.leads) == before + 1
    created = main.lead_manager.leads[-1]
    assert len(created.utm_campaign) == 200
    assert created.utm_campaign == "x" * 200
