"""A deterministic chat callback handoff turns anonymous intent into a reachable lead.

The path is deliberately storage-only: it records explicit callback consent and
puts the lead in the CRM response queue, but does not send email or messages.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_api_v1 import create_client

from scripts.sync_leads_to_bigquery import project_lead


def _phone_digits(value):
    return "".join(character for character in str(value or "") if character.isdigit())[-10:]


def _post(client, **overrides):
    body = {
        "sessionId": "session-1",
        "name": "Maria Buyer",
        "phone": "281-324-3020",
        "consent": True,
        **overrides,
    }
    return client.post("/api/chat/contact", json=body)


def test_chat_callback_requires_explicit_consent(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    before = [lead.to_dict() for lead in main.lead_manager.leads]

    response = _post(client, consent=False)

    assert response.status_code == 400
    assert "consent" in response.json()["error"].lower()
    assert [lead.to_dict() for lead in main.lead_manager.leads] == before


def test_chat_callback_rejects_invalid_contact_data(monkeypatch):
    client, *_ = create_client(monkeypatch)

    assert _post(client, name="").status_code == 400
    assert _post(client, phone="123").status_code == 400
    assert _post(client, sessionId="../../bad").status_code == 400
    assert client.post("/api/chat/contact", json=[]).status_code == 400


def test_chat_callback_promotes_existing_anonymous_session_lead(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    lead = main.lead_manager.leads[0]
    lead.name = None
    lead.phone = None
    lead.email = None

    response = _post(
        client,
        email="maria@example.com",
        utm_source="google",
        utm_campaign="local-inventory",
        gclid="EAIaIQobChMI_valid-123",
    )

    assert response.status_code == 200
    assert response.json() == {"success": True, "lead_id": "lead-1"}
    assert len(main.lead_manager.leads) == 1
    assert lead.name == "Maria Buyer"
    assert _phone_digits(lead.phone) == "2813243020"
    assert lead.email == "maria@example.com"
    assert lead.priority == "high"
    assert lead.triage_reason == "callback_requested"
    assert lead.contact_consent_at
    assert lead.contact_consent_source == "chat_callback"
    assert lead.utm_source == "google"
    assert lead.utm_campaign == "local-inventory"
    assert lead.gclid == "EAIaIQobChMI_valid-123"


def test_chat_callback_creates_reachable_chat_lead_when_session_has_none(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    before = len(main.lead_manager.leads)

    response = _post(client, sessionId="new-session-123")

    assert response.status_code == 200
    assert len(main.lead_manager.leads) == before + 1
    created = main.lead_manager.leads[-1]
    assert created.source == "chat"
    assert created.session_id == "new-session-123"
    assert _phone_digits(created.phone) == "2813243020"
    assert created.triage_reason == "callback_requested"
    assert response.json()["lead_id"] == created.lead_id


def test_chat_callback_does_not_overwrite_a_completed_session_lead(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    completed = main.lead_manager.leads[0]
    completed.status = "converted"
    completed.name = "Existing Customer"
    completed.phone = "555-999-8888"
    completed.bedrooms = 3
    before = len(main.lead_manager.leads)

    response = _post(client, name="Returning Customer", phone="281-324-3020")

    assert response.status_code == 200
    assert len(main.lead_manager.leads) == before + 1
    assert completed.status == "converted"
    assert completed.name == "Existing Customer"
    assert completed.phone == "555-999-8888"
    callback = main.lead_manager.leads[-1]
    assert callback.lead_id != completed.lead_id
    assert callback.name == "Returning Customer"
    assert callback.bedrooms == 3
    assert callback.status == "new"


def test_phone_dedupe_fills_blanks_without_overwriting_identity(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    existing = main.lead_manager.leads[0]
    existing.session_id = "other-session"
    existing.name = "Existing Name"
    existing.phone = "+12813243020"
    existing.email = "existing@example.com"

    response = _post(
        client,
        sessionId="new-session-789",
        name="Different Name",
        phone="281-324-3020",
        email="different@example.com",
    )

    assert response.status_code == 200
    assert len(main.lead_manager.leads) == 1
    assert existing.name == "Existing Name"
    assert existing.email == "existing@example.com"
    assert existing.triage_reason == "callback_requested"


def test_chat_callback_storage_failure_is_not_reported_as_success(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    main.lead_manager.leads.clear()

    async def fail(_lead):
        raise RuntimeError("firestore unavailable")

    monkeypatch.setattr(main.lead_manager, "create_lead", fail)
    response = _post(client, sessionId="new-session-456")

    assert response.status_code == 503
    assert response.json()["success"] is False


def test_chat_callback_has_pii_free_bigquery_measurement():
    projected = project_lead(
        {
            "lead_id": "callback-1",
            "name": "Private Name",
            "phone": "281-324-3020",
            "email": "private@example.com",
            "triage_reason": "callback_requested",
            "contact_consent_at": "2026-07-22T01:00:00+00:00",
            "contact_consent_source": "chat_callback",
        }
    )

    assert projected["callback_requested"] is True
    assert projected["contact_consent_source"] == "chat_callback"
    assert projected["contact_consent_at"] == "2026-07-22T01:00:00+00:00"
    assert projected["has_name"] is True
    assert projected["has_phone"] is True
    assert projected["has_email"] is True
    assert "name" not in projected
    assert "phone" not in projected
    assert "email" not in projected
