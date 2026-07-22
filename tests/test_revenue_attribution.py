"""Server-authoritative, anonymous journey-to-conversion attribution."""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_api_v1 import create_client

JOURNEY_ID = "j_0123456789abcdef0123456789abcdef"


def _events(main):
    return list(main._db.db.collections.get("analytics_events", {}).values())


def test_persisted_inventory_lead_writes_one_joinable_conversion(monkeypatch):
    client, main, *_ = create_client(monkeypatch)

    response = client.post(
        "/api/contact",
        json={
            "name": "Phone Buyer",
            "phone": "2813243020",
            "source": "inventory_quote",
            "journey_id": JOURNEY_ID,
            "home_id": "home-42",
            "home_model": "Sapphire 3-Bed",
        },
    )

    assert response.json()["success"] is True
    lead = main.lead_manager.leads[-1]
    assert (lead.journey_id, lead.home_id, lead.home_model) == (
        JOURNEY_ID,
        "home-42",
        "Sapphire 3-Bed",
    )
    conversions = [event for event in _events(main) if event["event"] == "lead_captured"]
    assert conversions == [
        {
            "event": "lead_captured",
            "schema_version": 2,
            "props": {
                "source": "inventory_quote",
                "type": "quote",
                "home": "Sapphire 3-Bed",
                "home_id": "home-42",
            },
            "created_at": conversions[0]["created_at"],
            "journey_id": JOURNEY_ID,
        }
    ]
    assert "client_ip" not in conversions[0]


def test_failed_lead_storage_writes_no_conversion(monkeypatch):
    client, main, *_ = create_client(monkeypatch)

    async def fail(_lead):
        raise RuntimeError("firestore unavailable")

    monkeypatch.setattr(main.lead_manager, "create_lead", fail)
    response = client.post(
        "/api/contact",
        json={
            "name": "Phone Buyer",
            "phone": "2813243020",
            "journey_id": JOURNEY_ID,
        },
    )

    assert "lead_storage_failed" in response.json().get("warnings", [])
    assert not [event for event in _events(main) if event["event"] == "lead_captured"]


def test_durable_appointment_writes_one_joinable_conversion(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    monkeypatch.setattr(main, "Appointment", lambda **values: types.SimpleNamespace(**values))

    async def create_appointment(_appointment):
        return types.SimpleNamespace(to_dict=lambda: {"appointment_id": "appt_test"})

    monkeypatch.setattr(
        main.appointment_manager, "create_appointment", create_appointment, raising=False
    )
    response = client.post(
        "/api/appointments",
        json={
            "name": "Phone Buyer",
            "phone": "2813243020",
            "date": "2026-08-01",
            "time_slot": "10:00 AM",
            "source": "inventory_quote_handoff",
            "journey_id": JOURNEY_ID,
            "home_id": "home-42",
            "home_model": "Sapphire 3-Bed",
        },
    )

    assert response.json()["success"] is True
    conversions = [event for event in _events(main) if event["event"] == "appointment_booked"]
    assert len(conversions) == 1
    assert conversions[0]["journey_id"] == JOURNEY_ID
    assert conversions[0]["props"] == {
        "source": "inventory_quote_handoff",
        "intent": "showroom_visit",
        "home": "Sapphire 3-Bed",
        "home_id": "home-42",
    }


def test_legacy_contact_without_journey_still_persists(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    response = client.post(
        "/api/contact",
        json={"name": "Legacy Buyer", "phone": "2813243020"},
    )

    assert response.json()["success"] is True
    assert main.lead_manager.leads[-1].journey_id is None
    conversion = next(event for event in _events(main) if event["event"] == "lead_captured")
    assert "journey_id" not in conversion
