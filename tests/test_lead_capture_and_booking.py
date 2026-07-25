"""P1b regression guards (holistic review): durable lead capture + booking race.

1. The chat agent's save_lead tool must persist the customer's name+phone to
   Firestore (durable), not only to stdout + an ephemeral local file — otherwise
   high-intent chat leads are silently lost on Cloud Run while the customer is
   told "saved". It must also report failure truthfully.
2. The appointment booking transaction must READ the slot count inside the
   transaction (stream(transaction=...)), else two concurrent bookings can both
   pass the MAX_PER_SLOT check and oversell a slot.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─── chat lead persistence ──────────────────────────────────────────────────


class _FakeDocRef:
    def __init__(self, sink):
        self._sink = sink

    def set(self, data, timeout=None):
        self._sink["data"] = data
        self._sink["timeout"] = timeout


class _FakeColl:
    def __init__(self, sink):
        self._sink = sink

    def document(self, doc_id):
        self._sink["doc_id"] = doc_id
        return _FakeDocRef(self._sink)


class _FakeDB:
    def __init__(self, sink):
        self._sink = sink

    def collection(self, name):
        self._sink["collection"] = name
        return _FakeColl(self._sink)


def test_save_lead_persists_to_firestore(monkeypatch):
    import tools.crm_tools as crm

    sink = {}
    monkeypatch.setattr(crm, "_get_lead_manager", lambda: type("M", (), {"db": _FakeDB(sink)})())

    res = crm.save_lead("Jordan Brooks", "512-555-0123", "wants a 3/2 doublewide under 100k")

    assert res["success"] is True
    assert sink["collection"] == "leads"
    assert sink["data"]["name"] == "Jordan Brooks"
    assert "5125550123" in sink["data"]["phone"].replace("+1", "")  # normalized + stored
    assert sink["data"]["source"] == "chat"
    assert sink["data"]["triage_notes"] == "wants a 3/2 doublewide under 100k"
    # Durable write must be bounded against a Firestore hang (event-loop wedge).
    from database.rpc_timeout import FIRESTORE_RPC_TIMEOUT

    assert sink["timeout"] == FIRESTORE_RPC_TIMEOUT


def test_save_lead_reports_failure_when_firestore_down(monkeypatch):
    import tools.crm_tools as crm

    def boom():
        raise RuntimeError("firestore unreachable")

    monkeypatch.setattr(crm, "_get_lead_manager", boom)

    res = crm.save_lead("Jordan Brooks", "512-555-0123", "interested")
    # Must NOT falsely confirm a lead that wasn't durably saved.
    assert res["success"] is False


def test_save_lead_still_validates_input():
    import tools.crm_tools as crm

    assert crm.save_lead("", "512-555-0123", "x")["success"] is False  # no name
    assert crm.save_lead("Jordan", "123", "x")["success"] is False  # too short


# ─── appointment double-booking race ────────────────────────────────────────


def test_create_appointment_reads_inside_transaction():
    import appointment_manager as am
    from appointment_manager import Appointment, AppointmentManager

    captured = {}

    class FakeQuery:
        def where(self, *a, **k):
            return self

        def stream(self, transaction=None, timeout: float | None = None):
            captured["transaction"] = transaction
            return iter([])  # no existing bookings

    class FakeColl:
        def where(self, *a, **k):
            return FakeQuery()

        def document(self, _id):
            return object()

    class FakeTxn:
        def set(self, *a, **k):
            pass

    mgr = AppointmentManager.__new__(AppointmentManager)
    mgr._collection = lambda: FakeColl()
    sentinel_txn = FakeTxn()
    mgr.db = type("DB", (), {"transaction": lambda self: sentinel_txn})()

    # Next Monday (open 9–6, so "10:00 AM" is a valid on-grid slot) within the
    # 30-day booking window. PR #221 added a window check that rejects far-future
    # dates BEFORE the transaction logic this test exercises, so a hardcoded
    # 2031 date now raises early and never reaches the slot read. Keep it relative.
    from datetime import datetime, timedelta

    today = datetime.now(am.TIMEZONE).date()
    next_monday = today + timedelta(days=((0 - today.weekday()) % 7) or 7)

    appt = Appointment(
        appointment_id="a1",
        name="X",
        phone="5125550000",
        date=next_monday.isoformat(),
        time_slot="10:00 AM",
        status="confirmed",
    )

    # Make @firestore.transactional a pass-through so txn_create runs directly.
    with patch.object(am.firestore, "transactional", lambda f: f):
        mgr.create_appointment_sync(appt)

    assert captured.get("transaction") is sentinel_txn, "slot read not bound to the transaction"
