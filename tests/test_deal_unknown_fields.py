"""COD-158: deal create and update reject keys that are not on the Deal model.

The 422 names the unexpected fields and never includes their values. Nothing
is written to the deal store.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_api_v1 import create_client  # noqa: E402

from database.models import Deal, parse_deal_update, unknown_deal_fields  # noqa: E402

# New Deal form in frontend/src/pages/CRM.jsx (NewDealForm.handleSubmit).
# Empty inputs are omitted. sales_price and down_payment are parseFloat numbers.
STAFF_NEW_DEAL_PAYLOAD = {
    "buyer_first_name": "John",
    "buyer_last_name": "Smith",
    "buyer_phone": "555-123-4567",
    "buyer_email": "john@example.com",
    "buyer_address": "123 Main St",
    "buyer_city": "Houston",
    "buyer_county": "Harris",
    "buyer_state": "TX",
    "buyer_zip": "77001",
    "manufacturer": "Clayton Homes",
    "model": "The Breeze",
    "serial_number_1": "CLT-2026-001",
    "salesrep": "Adriana Squyres",
    "sales_price": 72000,
    "down_payment": 8000,
}


def test_unknown_deal_fields_names_only():
    assert unknown_deal_fields({"buyer_first_name": "A", "notes": "x"}) == []
    assert unknown_deal_fields({"buyer_first_name": "A", "zz_typo": 1, "buyerSSN": "123"}) == [
        "buyerSSN",
        "zz_typo",
    ]
    assert unknown_deal_fields(["not", "a", "dict"]) == []
    assert unknown_deal_fields(None) == []


def test_staff_form_keys_are_on_the_deal_model():
    assert set(STAFF_NEW_DEAL_PAYLOAD) <= set(Deal.model_fields)


def test_stored_import_tags_still_load_onto_deal():
    """Firestore deals from the FCD importer carry extra keys. Reads keep ignoring them."""
    deal = Deal.model_validate(
        {"buyer_first_name": "Pat", "source": "fcd_import", "fcd_app_id": "210602034358"}
    )
    assert deal.buyer_first_name == "Pat"
    assert deal.to_document_data()["buyer_first_name"] == "Pat"


def test_update_ignores_server_managed_fields():
    changes = parse_deal_update(
        {
            "id": "forged",
            "created_at": "2000-01-01T00:00:00",
            "updated_at": "2000-01-01T00:00:00",
            "notes": "kept",
        }
    )
    assert changes == {"notes": "kept"}


class _FakeDealDB:
    def __init__(self):
        self.created = None
        self.updated = None

    def create_deal(self, data):
        self.created = data
        return "deal-1"

    def get_deal(self, deal_id):
        return {**(self.created or {}), "id": deal_id}

    def update_deal(self, deal_id, data):
        self.updated = (deal_id, data)
        return True


def _capture(monkeypatch, main):
    calls = []

    class _L:
        def __getattr__(self, name):
            def rec(msg, **kw):
                calls.append((name, msg, kw))

            return rec

    monkeypatch.setattr(main, "struct_logger", _L())
    return calls


def _client(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch)
    fake = _FakeDealDB()
    monkeypatch.setattr(main, "_deal_db", fake)
    calls = _capture(monkeypatch, main)
    token = main._create_admin_token()
    headers = {"Authorization": f"Bearer {token}"}
    return client, fake, calls, headers


def _full_deal_payload() -> dict:
    """Every Deal field, with a few realistic values filled in."""
    deal = Deal(
        buyer_first_name="Pat",
        buyer_last_name="Lee",
        buyer_phone="555-0100",
        buyer_email="pat@example.com",
        salesrep="Adriana Squyres",
        manufacturer="Clayton Homes",
        model="The Breeze",
        serial_number_1="CLT-2026-001",
        sales_price=72000,
        down_payment=8000,
        notes="Full record",
        status="pending",
    )
    return deal.model_dump(mode="json")


def test_create_accepts_full_payload(monkeypatch):
    client, fake, calls, headers = _client(monkeypatch)
    payload = _full_deal_payload()

    response = client.post("/api/deals", json=payload, headers=headers)

    assert response.status_code == 200, response.text
    assert response.json().get("success") is True
    assert fake.created is not None
    assert set(fake.created) <= set(Deal.model_fields)
    assert fake.created["buyer_first_name"] == "Pat"
    assert fake.created["sales_price"] == 72000
    assert fake.created["notes"] == "Full record"
    assert not [c for c in calls if c[1] == "Deal payload has unknown fields"]


def test_update_accepts_full_payload(monkeypatch):
    client, fake, _calls, headers = _client(monkeypatch)
    payload = _full_deal_payload()

    response = client.put("/api/deals/deal-1", json=payload, headers=headers)

    assert response.status_code == 200, response.text
    assert response.json().get("success") is True
    deal_id, written = fake.updated
    assert deal_id == "deal-1"
    assert set(written) <= set(Deal.model_fields) - {"id", "created_at", "updated_at"}
    assert "id" not in written
    assert "created_at" not in written
    assert "updated_at" not in written
    assert written["buyer_last_name"] == "Lee"
    assert written["notes"] == "Full record"


def test_update_accepts_partial_payload(monkeypatch):
    client, fake, _calls, headers = _client(monkeypatch)

    response = client.put(
        "/api/deals/deal-1",
        json={"notes": "Called the buyer", "salesrep": "Adriana Squyres"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert fake.updated == (
        "deal-1",
        {"notes": "Called the buyer", "salesrep": "Adriana Squyres"},
    )


def test_create_and_update_reject_unknown_keys_without_writing(monkeypatch):
    client, fake, calls, headers = _client(monkeypatch)
    secret_value = "999-88-7777"

    created = client.post(
        "/api/deals",
        json={"buyer_first_name": "Pat", "mystery_field": secret_value},
        headers=headers,
    )
    updated = client.put(
        "/api/deals/deal-1",
        json={"notes": "n", "other_unknown": secret_value, "buyerSSN": secret_value},
        headers=headers,
    )

    assert created.status_code == 422, created.text
    assert updated.status_code == 422, updated.text
    assert "mystery_field" in created.json()["message"]
    assert "buyerSSN" in updated.json()["message"]
    assert "other_unknown" in updated.json()["message"]
    assert secret_value not in created.text
    assert secret_value not in updated.text
    assert fake.created is None
    assert fake.updated is None

    warn = [c for c in calls if c[1] == "Deal payload has unknown fields"]
    assert [(c[2]["route"], c[2]["unknown_fields"]) for c in warn] == [
        ("create", ["mystery_field"]),
        ("update", ["buyerSSN", "other_unknown"]),
    ]
    assert secret_value not in repr(warn)


def test_staff_new_deal_payload_is_accepted(monkeypatch):
    client, fake, calls, headers = _client(monkeypatch)

    response = client.post("/api/deals", json=STAFF_NEW_DEAL_PAYLOAD, headers=headers)

    assert response.status_code == 200, response.text
    assert response.json().get("success") is True
    assert fake.created["buyer_first_name"] == "John"
    assert fake.created["buyer_last_name"] == "Smith"
    assert fake.created["sales_price"] == 72000
    assert fake.created["down_payment"] == 8000
    assert fake.created["serial_number_1"] == "CLT-2026-001"
    assert not [c for c in calls if c[1] == "Deal payload has unknown fields"]


def test_known_fields_do_not_warn(monkeypatch):
    client, _fake, calls, headers = _client(monkeypatch)
    client.post("/api/deals", json={"buyer_first_name": "Pat"}, headers=headers)
    assert not [c for c in calls if c[1] == "Deal payload has unknown fields"]
