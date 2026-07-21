"""PR A — lead-capture correctness for POST /api/contact.

Two regressions that silently lost or corrupted leads (= lost revenue):
1. Backend accepted any non-empty phone while /api/appointments requires 10
   digits — garbage numbers entered the leads CSV sales follows up from.
2. A Firestore write failure was swallowed (warning-logged) while the endpoint
   returned {success:True}, so a dropped lead was invisible. It is now flagged
   in a `warnings` list so a Cloud Run log/response alert can fire — while the
   owner-notification email remains the fallback delivery path.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_api_v1 import create_client


def _post(client, **body):
    return client.post("/api/contact", json=body)


def test_contact_requires_name_and_phone(monkeypatch):
    client, *_ = create_client(monkeypatch)
    assert _post(client, name="", phone="2813243020").json() == {
        "success": False,
        "error": "Name and phone are required",
    }


def test_contact_rejects_sub_10_digit_phone(monkeypatch):
    client, *_ = create_client(monkeypatch)
    body = _post(client, name="Alice", phone="123").json()
    assert body["success"] is False
    assert "10-digit" in body["error"]


def test_contact_accepts_formatted_10_digit_phone_clean_happy_path(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    body = _post(client, name="Alice", phone="(281) 324-3020", email="a@example.com").json()
    assert body["success"] is True
    assert "warnings" not in body  # nothing failed -> no noise
    assert body["lead_id"] == main.lead_manager.leads[-1].lead_id


def test_contact_flags_lead_storage_failure_as_warning(monkeypatch):
    client, *_ = create_client(monkeypatch)
    import main

    async def boom(_lead):
        raise RuntimeError("firestore unavailable")

    monkeypatch.setattr(main.lead_manager, "create_lead", boom)
    body = _post(client, name="Bob", phone="281-324-3020").json()
    # Visitor still sees success (owner email is the fallback path) ...
    assert body["success"] is True
    # ... but the dropped lead is now loud + alertable.
    assert "lead_storage_failed" in body.get("warnings", [])
    # Never hand the browser a capability for a lead that was not persisted.
    assert "lead_id" not in body


def test_contact_creates_lead_with_name_phone_and_source(monkeypatch):
    """A valid name+phone POST persists a Lead carrying those fields + source."""
    client, main, *_ = create_client(monkeypatch)
    before = len(main.lead_manager.leads)

    body = _post(client, name="Carol", phone="(281) 324-3020", email="carol@example.com").json()
    assert body["success"] is True

    # FakeLeadManager.create_lead appends the persisted Lead to .leads.
    assert len(main.lead_manager.leads) == before + 1
    created = main.lead_manager.leads[-1]
    assert created.name == "Carol"
    assert created.phone == "(281) 324-3020"
    assert created.email == "carol@example.com"
    assert created.source == "contact_form"  # default source for the contact form


def test_contact_lead_carries_explicit_source(monkeypatch):
    """A caller-supplied `source` flows through to the persisted Lead."""
    client, main, *_ = create_client(monkeypatch)
    before = len(main.lead_manager.leads)

    body = _post(client, name="Dave", phone="2813243020", source="facebook_ad").json()
    assert body["success"] is True

    assert len(main.lead_manager.leads) == before + 1
    created = main.lead_manager.leads[-1]
    assert created.name == "Dave"
    assert created.phone == "2813243020"
    assert created.source == "facebook_ad"


def test_contact_invalid_phone_rejected_and_creates_no_lead(monkeypatch):
    """An invalid/short phone is rejected (success=false) and no Lead is stored."""
    client, main, *_ = create_client(monkeypatch)
    before = len(main.lead_manager.leads)

    body = _post(client, name="Eve", phone="55512").json()
    assert body["success"] is False
    assert "error" in body
    # Rejection happens before lead creation, so nothing is persisted.
    assert len(main.lead_manager.leads) == before
