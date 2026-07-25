"""P0 security regression guard (Mark Willcott live-site review).

Customer-facing endpoints must NOT expose PII to anyone who merely knows/guesses
an opaque id. A deal_id, appointment_id, or note_id is a capability, not a
credential — these endpoints now require phone-last-4 verification against the
record on file, and must not act as an existence oracle.

Covers:
  * /api/v1/customer/deal/{deal_id}            (Secure Hub portal)
  * /api/v1/customer/deal/{deal_id}/download/{note_id}  (signed PDF — SSN/DOB)
  * /api/appointments/{appointment_id}         (name/phone/email)
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))


# ─── unit: the verification helper ──────────────────────────────────────────


def test_verify_phone_last4_matches_and_rejects():
    import main

    assert main._verify_phone_last4("512-555-0123", "0123") is True
    assert main._verify_phone_last4("512-555-0123", "(512) 555-0123") is True
    assert main._verify_phone_last4("512-555-0123", "9999") is False
    assert main._verify_phone_last4("512-555-0123", "") is False
    assert main._verify_phone_last4(None, "0123") is False
    assert main._verify_phone_last4("512-555-0123", "123") is False  # < 4 digits


# ─── fakes for the Secure Hub Firestore reads ───────────────────────────────


class _Doc:
    def __init__(self, exists, data, doc_id="d1"):
        self.exists = exists
        self._data = data
        self.id = doc_id

    def to_dict(self):
        return self._data


class _DocRef:
    def __init__(self, doc):
        self._doc = doc

    def get(self, timeout: float | None = None):
        return self._doc


class _Coll:
    def __init__(self, doc=None, notes=()):
        self._doc = doc
        self._notes = notes

    def document(self, _id):
        return _DocRef(self._doc)

    def where(self, *a, **k):
        return self

    def stream(self, timeout: float | None = None):
        return iter(self._notes)


class _FakeDB:
    def __init__(self, deal_doc, note_doc=None, notes=()):
        self._c = {
            "deals": _Coll(deal_doc, notes=notes),
            "deal_notes": _Coll(note_doc, notes=notes),
        }
        # The endpoints use get_database().db (THODatabase wrapper -> raw client).
        # Mirror that here so the test exercises the real accessor — its absence
        # is why the original wrong-accessor 500 shipped undetected.
        self.db = self

    def collection(self, name):
        return self._c[name]


@pytest.fixture
def tc():
    import main

    return TestClient(main.app, raise_server_exceptions=False), main


# ─── Secure Hub deal ────────────────────────────────────────────────────────


def test_secure_hub_deal_requires_phone(tc, monkeypatch):
    client, main = tc
    deal = _Doc(
        True,
        {
            "buyer_phone": "512-555-0123",
            "buyer_first_name": "Jordan",
            "buyer_last_name": "Brooks",
            "status": "open",
        },
    )
    monkeypatch.setattr(main, "get_database", lambda: _FakeDB(deal))

    assert client.get("/api/v1/customer/deal/abc").status_code == 403  # no phone
    assert client.get("/api/v1/customer/deal/abc", params={"phone": "9999"}).status_code == 403
    ok = client.get("/api/v1/customer/deal/abc", params={"phone": "0123"})
    assert ok.status_code == 200 and ok.json()["success"] is True


def test_secure_hub_deal_no_existence_oracle(tc, monkeypatch):
    client, main = tc
    # Non-existent deal must return the SAME 403 as a wrong phone.
    monkeypatch.setattr(main, "get_database", lambda: _FakeDB(_Doc(False, None)))
    assert client.get("/api/v1/customer/deal/zzz", params={"phone": "0123"}).status_code == 403


def test_secure_hub_download_requires_phone(tc, monkeypatch):
    client, main = tc
    deal = _Doc(True, {"buyer_phone": "512-555-0123"})
    monkeypatch.setattr(main, "get_database", lambda: _FakeDB(deal))
    # Wrong/no phone must be rejected BEFORE any GCS signed-URL work.
    assert (
        client.get("/api/v1/customer/deal/abc/download/note1").status_code == 403
    )
    assert (
        client.get(
            "/api/v1/customer/deal/abc/download/note1", params={"phone": "9999"}
        ).status_code
        == 403
    )


# ─── Appointment lookup ─────────────────────────────────────────────────────


def test_appointment_get_requires_phone(tc, monkeypatch):
    client, main = tc
    appt = SimpleNamespace(
        phone="512-555-0144",
        to_dict=lambda: {"name": "Taylor", "phone": "512-555-0144", "email": "t@example.com"},
    )

    async def fake_get(_id):
        return appt

    monkeypatch.setattr(main.appointment_manager, "get_appointment", fake_get)

    assert client.get("/api/appointments/a1").status_code == 403
    assert client.get("/api/appointments/a1", params={"phone": "9999"}).status_code == 403
    ok = client.get("/api/appointments/a1", params={"phone": "0144"})
    assert ok.status_code == 200 and ok.json()["phone"] == "512-555-0144"
