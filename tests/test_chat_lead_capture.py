"""Chat-sourced leads must reach the sales team — not sit invisibly in Firestore.

Two gaps this pins (both = high-intent chat leads Lee never hears about):
1. `save_lead` (the AI chat's capture tool) persisted the lead but never fired
   the staff "New Lead Alert" that /api/contact sends — so captured chat leads
   were second-class: no email, no follow-up.
2. Tool-calls are unreliable; a visitor who simply *types* their phone/email in
   chat was never captured at all. A passive backstop attaches that contact to
   the session's lead and alerts staff exactly once.
"""

import asyncio
import re

from tools import crm_tools
from tools.contact_capture import capture_contact_from_message, extract_contact


def _digits(s):
    return re.sub(r"\D", "", s or "")


# ── Passive extraction ──────────────────────────────────────────────


def test_extract_contact_finds_phone_and_email():
    phone, email = extract_contact(
        "sure, you can call me at (281) 324-3020 or email jane@example.com"
    )
    assert phone is not None and _digits(phone) == "2813243020"
    assert email == "jane@example.com"


def test_extract_contact_ignores_ordinary_browsing_text():
    assert extract_contact("do you have any 3 bedroom homes under 80k?") == (None, None)


# ── Passive backstop in the chat flow ───────────────────────────────


class _FakeLeadManager:
    def __init__(self):
        self.leads = []

    async def get_lead_by_session(self, session_id):
        for lead in self.leads:
            if lead.session_id == session_id:
                return lead
        return None

    async def create_lead(self, lead):
        self.leads.append(lead)
        return lead

    async def update_lead(self, lead):
        for i, existing in enumerate(self.leads):
            if existing.lead_id == lead.lead_id:
                self.leads[i] = lead
                return lead
        self.leads.append(lead)
        return lead


def test_backstop_creates_lead_and_alerts_staff_once():
    lm = _FakeLeadManager()
    calls = []

    def notify(**kwargs):
        calls.append(kwargs)
        return {"success": True}

    lead = asyncio.run(
        capture_contact_from_message(
            "my number is 281-324-3020", "sess1", "user1", lead_manager=lm, notify=notify
        )
    )
    assert lead is not None
    assert _digits(lead.phone) == "2813243020"
    assert len(lm.leads) == 1
    assert len(calls) == 1  # staff alerted exactly once
    assert _digits(calls[0]["phone"]) == "2813243020"


def test_backstop_does_not_double_alert_same_session():
    lm = _FakeLeadManager()
    calls = []

    def notify(**kwargs):
        calls.append(kwargs)
        return {"success": True}

    asyncio.run(
        capture_contact_from_message(
            "call me at 281-324-3020", "sess1", "user1", lead_manager=lm, notify=notify
        )
    )
    # A later message in the SAME session adds email but must NOT re-alert.
    asyncio.run(
        capture_contact_from_message(
            "and my email is jane@example.com", "sess1", "user1", lead_manager=lm, notify=notify
        )
    )
    assert len(calls) == 1  # still only one alert
    assert lm.leads[0].email == "jane@example.com"  # but the email was still captured


def test_backstop_noop_when_no_contact_present():
    lm = _FakeLeadManager()
    calls = []
    result = asyncio.run(
        capture_contact_from_message(
            "just looking around, thanks", "s", "u", lead_manager=lm, notify=lambda **k: calls.append(k)
        )
    )
    assert result is None
    assert lm.leads == []
    assert calls == []


# ── save_lead now alerts staff (mirrors /api/contact) ───────────────


def _fake_firestore(fail=False):
    class _Doc:
        def set(self, *a, **k):
            if fail:
                raise RuntimeError("firestore unavailable")

    class _Coll:
        def document(self, *a, **k):
            return _Doc()

    class _DB:
        def collection(self, *a, **k):
            return _Coll()

    class _LM:
        db = _DB()

    return _LM()


def test_save_lead_alerts_staff_on_successful_persist(monkeypatch):
    calls = []
    monkeypatch.setattr(crm_tools, "_get_lead_manager", lambda: _fake_firestore(fail=False))
    monkeypatch.setattr(
        crm_tools, "notify_new_lead", lambda **k: calls.append(k) or {"success": True}
    )
    result = crm_tools.save_lead("Jane Buyer", "281-324-3020", "wants a 3/2 under $80k")
    assert result["success"] is True
    assert len(calls) == 1
    assert calls[0]["customer_name"] == "Jane Buyer"
    assert _digits(calls[0]["phone"]) == "2813243020"


def test_save_lead_does_not_alert_when_persist_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(crm_tools, "_get_lead_manager", lambda: _fake_firestore(fail=True))
    monkeypatch.setattr(crm_tools, "notify_new_lead", lambda **k: calls.append(k))
    result = crm_tools.save_lead("Jane Buyer", "281-324-3020", "notes")
    assert result["success"] is False
    assert calls == []  # never tell the team about a lead we failed to save
