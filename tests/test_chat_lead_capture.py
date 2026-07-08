"""Chat-sourced leads must reach the sales team — safely — not sit invisibly in
Firestore and not degrade the chat path.

Gaps pinned here:
1. `save_lead` (the AI chat's capture tool) persisted the lead but never fired
   the staff "New Lead Alert" that /api/contact sends.
2. A visitor who simply *types* their phone/email in chat was never captured; a
   passive backstop attaches that contact and alerts staff exactly once.

Hardening (post adversarial review), all covered below:
3. The contact regex is length-capped so a huge /run message can't drive O(n^2)
   backtracking (ReDoS) on the event loop.
4. A bare digit-run (MLS/serial/price) is not mistaken for a phone unless it is
   phone-formatted or the message shows contact intent.
5. The staff-alert send is offloaded + time-bounded so a slow Resend never
   stalls the chat worker.
6. The backstop dedupes BY PHONE against any already-captured lead (e.g. one the
   model's save_lead tool persisted this turn), so one customer never gets two
   alerts / two records — yet it STILL fires when save_lead was called but failed
   to persist (no silent lead loss).
"""

import asyncio
import re
import time

from lead_management import Lead, normalize_phone
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


# ── ReDoS cap + phone false-positive guard (post-review) ────────────


def test_extract_contact_caps_scanned_length():
    # A phone/email past the scan cap is ignored — this bounds the input the
    # regexes ever see, so a giant /run message cannot drive O(n^2) backtracking.
    buried = "x" * 3000 + " call me at 281-324-3020 or a@b.com"
    assert extract_contact(buried) == (None, None)


def test_extract_contact_rejects_bare_number_without_contact_cue():
    # Real-estate chat is full of numbers (MLS, serial, price). A bare 10-digit
    # run with no phone formatting and no contact intent must NOT become a lead.
    assert extract_contact("I'm looking at MLS 2813243020, is it available?") == (None, None)


def test_extract_contact_accepts_bare_number_with_cue():
    phone, _ = extract_contact("sure, call me at 2813243020")
    assert phone is not None and _digits(phone) == "2813243020"


def test_extract_contact_accepts_formatted_number_without_cue():
    phone, _ = extract_contact("here it is 281-324-3020")
    assert phone is not None and _digits(phone) == "2813243020"


# ── Passive backstop behaviour ──────────────────────────────────────


class _FakeLeadManager:
    def __init__(self):
        self.leads = []

    async def get_lead_by_session(self, session_id):
        for lead in self.leads:
            if lead.session_id == session_id:
                return lead
        return None

    async def get_lead_by_phone(self, phone):
        for lead in self.leads:
            if lead.phone and lead.phone == phone:
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


def test_backstop_dedups_against_an_existing_phone_lead():
    # Simulate the agent's save_lead tool having already captured + alerted this
    # phone (it stores a NORMALIZED number, session_id=""). The backstop must NOT
    # create a duplicate lead or fire a second alert.
    lm = _FakeLeadManager()
    lm.leads.append(
        Lead(
            lead_id="chat_existing",
            user_id="chat",
            session_id="",
            name="Jane",
            phone=normalize_phone("281-324-3020"),
            source="chat",
        )
    )
    calls = []
    result = asyncio.run(
        capture_contact_from_message(
            "yes, call me at 281-324-3020", "sess_new", "u",
            lead_manager=lm, notify=lambda **k: calls.append(k),
        )
    )
    assert len(lm.leads) == 1  # no duplicate
    assert calls == []  # no second staff alert
    assert result is not None  # returned the already-captured lead


def test_backstop_dedup_still_merges_a_newly_volunteered_email():
    # A known-phone customer volunteers a NEW email in the same message. The
    # backstop must not create a duplicate or re-alert, but must NOT drop the
    # email either (that would be silent data loss).
    lm = _FakeLeadManager()
    lm.leads.append(
        Lead(
            lead_id="chat_existing",
            user_id="chat",
            session_id="",
            name="Jane",
            phone=normalize_phone("281-324-3020"),
            source="chat",
        )
    )
    calls = []
    asyncio.run(
        capture_contact_from_message(
            "call me at 281-324-3020, my email is jane@example.com", "sess_new", "u",
            lead_manager=lm, notify=lambda **k: calls.append(k),
        )
    )
    assert len(lm.leads) == 1  # no duplicate
    assert calls == []  # no re-alert
    assert lm.leads[0].email == "jane@example.com"  # email merged, not dropped


def test_backstop_still_fires_when_no_matching_phone_lead_exists():
    # save_lead was called but FAILED to persist -> no lead with this phone ->
    # the backstop is the safety net: it captures + alerts exactly once so the
    # high-intent lead is never silently lost.
    lm = _FakeLeadManager()
    calls = []
    asyncio.run(
        capture_contact_from_message(
            "call me at 281-324-3020", "s1", "u",
            lead_manager=lm, notify=lambda **k: calls.append(k) or {"success": True},
        )
    )
    assert len(lm.leads) == 1
    assert len(calls) == 1


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


def test_backstop_notify_is_offloaded_and_bounded_when_send_is_slow():
    # A slow/hung staff-alert send must not block the caller (the chat event
    # loop). capture must return before the slow notify finishes.
    lm = _FakeLeadManager()
    notify_done = {"v": False}

    def slow_notify(**kwargs):
        time.sleep(0.6)  # simulate a hung Resend send
        notify_done["v"] = True

    async def _run():
        await capture_contact_from_message(
            "call me at 281-324-3020", "s1", "u1",
            lead_manager=lm, notify=slow_notify, notify_timeout=0.1,
        )
        # Right after capture returns, the slow notify should still be running.
        return notify_done["v"]

    finished_before_notify_done = asyncio.run(_run())
    assert finished_before_notify_done is False  # did not wait on the slow send
    assert len(lm.leads) == 1  # lead still captured


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
