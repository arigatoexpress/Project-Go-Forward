"""Landing campaign fields stay on the lead and the conversion events."""

import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_api_v1 import create_client

JOURNEY_ID = "j_0123456789abcdef0123456789abcdef"
CAMPAIGN = {
    "utm_source": "google",
    "utm_medium": "cpc",
    "utm_campaign": "tho_search_high_intent_huffman",
    "utm_content": "rsa",
    "utm_term": "manufactured homes",
    "gclid": "test-gclid-123456",
    "wbraid": "WBRAID_valid1",
    "gbraid": "GBRAID_valid1",
}


def _events(main):
    return list(main._db.db.collections.get("analytics_events", {}).values())


def _stub_appointment(monkeypatch, main):
    monkeypatch.setattr(main, "Appointment", lambda **values: types.SimpleNamespace(**values))

    async def create_appointment(_appointment):
        return types.SimpleNamespace(to_dict=lambda: {"appointment_id": "appt_test"})

    monkeypatch.setattr(
        main.appointment_manager, "create_appointment", create_appointment, raising=False
    )


def test_contact_conversion_keeps_campaign_and_drops_contact_pii(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    response = client.post(
        "/api/contact",
        json={
            "name": "Phone Buyer",
            "phone": "2813243020",
            "message": "Please call about the Huffman home",
            "source": "inventory_quote",
            "journey_id": JOURNEY_ID,
            "referrer": "https://ads.example/?email=buyer@example.com",
            **CAMPAIGN,
        },
    )

    assert response.json()["success"] is True
    lead = main.lead_manager.leads[-1]
    assert lead.journey_id == JOURNEY_ID
    assert lead.utm_source == "google"
    assert lead.utm_medium == "cpc"
    assert lead.utm_campaign == "tho_search_high_intent_huffman"
    assert lead.utm_content == "rsa"
    assert lead.utm_term == "manufactured homes"
    assert lead.gclid == "test-gclid-123456"
    assert lead.wbraid == "WBRAID_valid1"
    assert lead.gbraid == "GBRAID_valid1"
    assert lead.referrer == "https://ads.example/?email=buyer@example.com"

    event = next(item for item in _events(main) if item["event"] == "lead_captured")
    assert event["journey_id"] == JOURNEY_ID
    assert event["props"]["utm_campaign"] == "tho_search_high_intent_huffman"
    assert event["props"]["gclid"] == "test-gclid-123456"
    assert event["props"]["wbraid"] == "WBRAID_valid1"
    assert event["props"]["gbraid"] == "GBRAID_valid1"
    assert "referrer" not in event["props"]
    blob = json.dumps(event)
    assert "Phone Buyer" not in blob
    assert "2813243020" not in blob
    assert "buyer@example.com" not in blob
    assert "Please call" not in blob


def test_malformed_click_id_is_dropped_from_the_lead_and_the_event(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    response = client.post(
        "/api/contact",
        json={
            "name": "Phone Buyer",
            "phone": "2813243020",
            "journey_id": JOURNEY_ID,
            "utm_source": "google",
            "gclid": "<script>alert(1)</script>",
            "wbraid": "nope",
        },
    )

    assert response.json()["success"] is True
    lead = main.lead_manager.leads[-1]
    assert lead.utm_source == "google"
    assert lead.gclid is None
    assert lead.wbraid is None
    event = next(item for item in _events(main) if item["event"] == "lead_captured")
    assert event["props"]["utm_source"] == "google"
    assert "gclid" not in event["props"]
    assert "wbraid" not in event["props"]
    assert "<script>" not in json.dumps(event)


def test_booked_visit_keeps_first_touch_and_backfills_the_click_id(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    _stub_appointment(monkeypatch, main)
    contact = client.post(
        "/api/contact",
        json={
            "name": "Phone Buyer",
            "phone": "2813243020",
            "journey_id": JOURNEY_ID,
            **{key: value for key, value in CAMPAIGN.items() if key != "gclid"},
        },
    )
    body = contact.json()
    assert body["success"] is True

    booked = client.post(
        "/api/appointments",
        json={
            "name": "Phone Buyer",
            "phone": "2813243020",
            "date": "2026-08-01",
            "time_slot": "10:00 AM",
            "lead_id": body["lead_id"],
            "source": "inventory_quote_handoff",
            "intent": "showroom_visit",
            "journey_id": JOURNEY_ID,
            "email": "buyer@example.com",
            "notes": "Private visit note",
            **CAMPAIGN,
            "utm_source": "facebook",
        },
    )
    assert booked.json()["success"] is True

    lead = main.lead_manager.leads[-1]
    assert lead.utm_source == "google"
    assert lead.utm_campaign == "tho_search_high_intent_huffman"
    assert lead.gclid == "test-gclid-123456"
    assert lead.journey_id == JOURNEY_ID
    assert lead.appointment_requested is True

    event = next(item for item in _events(main) if item["event"] == "appointment_booked")
    assert event["journey_id"] == JOURNEY_ID
    assert event["props"]["utm_source"] == "facebook"
    assert event["props"]["utm_campaign"] == "tho_search_high_intent_huffman"
    assert event["props"]["gclid"] == "test-gclid-123456"
    assert event["props"]["intent"] == "showroom_visit"
    blob = json.dumps(event)
    assert "buyer@example.com" not in blob
    assert "2813243020" not in blob
    assert "Private visit note" not in blob


def test_public_page_view_keeps_utm_and_drops_click_ids_and_email(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    response = client.post(
        "/api/analytics",
        json={
            "event": "page_viewed",
            "page_path": "/inventory",
            "journey_id": JOURNEY_ID,
            "email": "buyer@example.com",
            **CAMPAIGN,
        },
    )

    assert response.json() == {"ok": True}
    event = next(item for item in _events(main) if item["event"] == "page_viewed")
    assert event["journey_id"] == JOURNEY_ID
    assert event["props"]["utm_campaign"] == "tho_search_high_intent_huffman"
    assert event["props"]["page_path"] == "/inventory"
    assert "gclid" not in event["props"]
    assert "email" not in event["props"]
    assert "buyer@example.com" not in json.dumps(event)
