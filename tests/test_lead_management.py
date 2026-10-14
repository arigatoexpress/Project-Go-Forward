"""Resilience tests for LeadManager.list_leads.

A single malformed / out-of-band Firestore lead document must NOT 500 the whole
admin CRM list — that list is the owner's only post-cutover visibility while
transactional email is off. Lead.from_dict already drops unknown keys; the list
path additionally skips-and-logs any doc that still fails to construct.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta

import pytest

from lead_management import (
    Lead,
    LeadManager,
    apply_lead_status_transition,
    capture_contact_info,
    mark_appointment_intent,
    normalize_phone,
)


def test_from_dict_tolerates_unknown_keys():
    lead = Lead.from_dict(
        {
            "lead_id": "L1",
            "user_id": "U1",
            "session_id": "S1",
            "name": "Jane",
            "legacy_crm_field": "ignored",  # out-of-band fields must be dropped
            "another_unexpected": 42,
        }
    )
    assert lead.lead_id == "L1"
    assert lead.name == "Jane"


def test_from_dict_tolerates_missing_optionals():
    lead = Lead.from_dict({"lead_id": "L1", "user_id": "U1", "session_id": "S1"})
    assert lead.email is None
    assert lead.homes_viewed == []


def test_from_dict_preserves_stored_updated_at():
    stored = "2026-07-20T12:34:56+00:00"
    lead = Lead.from_dict(
        {
            "lead_id": "L1",
            "user_id": "U1",
            "session_id": "S1",
            "updated_at": stored,
        }
    )
    assert lead.updated_at == stored


def test_first_explicit_contact_sets_immutable_response_clock():
    lead = Lead(
        lead_id="L1",
        user_id="U1",
        session_id="S1",
        created_at="2026-07-22T12:00:00+00:00",
    )

    changed = apply_lead_status_transition(
        lead,
        "contacted",
        actor="admin:ari",
        now="2026-07-22T12:07:00+00:00",
    )

    assert changed is True
    assert lead.status == "contacted"
    assert lead.status_changed_at == "2026-07-22T12:07:00+00:00"
    assert lead.status_changed_by == "admin:ari"
    assert lead.first_contacted_at == "2026-07-22T12:07:00+00:00"
    assert lead.first_contacted_by == "admin:ari"

    # Retrying the same request must not move either lifecycle clock.
    changed = apply_lead_status_transition(
        lead,
        "contacted",
        actor="admin:other",
        now="2026-07-22T12:12:00+00:00",
    )
    assert changed is False
    assert lead.status_changed_at == "2026-07-22T12:07:00+00:00"
    assert lead.first_contacted_at == "2026-07-22T12:07:00+00:00"
    assert lead.first_contacted_by == "admin:ari"


def test_qualified_counts_as_contact_but_archive_does_not():
    qualified = Lead(lead_id="L1", user_id="U1", session_id="S1")
    apply_lead_status_transition(
        qualified,
        "qualified",
        actor="system:mira",
        now="2026-07-22T13:00:00+00:00",
    )
    assert qualified.first_contacted_at == "2026-07-22T13:00:00+00:00"

    archived = Lead(lead_id="L2", user_id="U2", session_id="S2")
    apply_lead_status_transition(
        archived,
        "archived",
        actor="admin:ari",
        now="2026-07-22T13:00:00+00:00",
    )
    assert archived.first_contacted_at is None
    assert archived.first_contacted_by is None


def test_backward_transition_never_erases_first_contact():
    lead = Lead(lead_id="L1", user_id="U1", session_id="S1")
    apply_lead_status_transition(
        lead,
        "converted",
        actor="admin:ari",
        now="2026-07-22T13:00:00+00:00",
    )
    apply_lead_status_transition(
        lead,
        "new",
        actor="admin:ari",
        now="2026-07-22T14:00:00+00:00",
    )
    assert lead.status == "new"
    assert lead.status_changed_at == "2026-07-22T14:00:00+00:00"
    assert lead.first_contacted_at == "2026-07-22T13:00:00+00:00"


def test_invalid_status_has_no_side_effects():
    lead = Lead(lead_id="L1", user_id="U1", session_id="S1")
    original = lead.to_dict()

    with pytest.raises(ValueError, match="Invalid lead status"):
        apply_lead_status_transition(
            lead,
            "won-ish",
            actor="admin:ari",
            now="2026-07-22T13:00:00+00:00",
        )

    assert lead.to_dict() == original


class _FakeTransaction:
    def __init__(self, store):
        self.store = store
        self.writes = 0

    def set(self, doc_ref, data, merge=False):
        assert merge is True
        self.store.update(data)
        self.writes += 1


class _FakeTransactionalDoc:
    def __init__(self, store, db):
        self.store = store
        self._db = db

    def get(self, transaction=None, timeout=None):
        assert transaction is not None
        self._db.last_get_kwargs = {"transaction": transaction, "timeout": timeout}
        return type(
            "Snapshot",
            (),
            {"exists": bool(self.store), "to_dict": lambda _self: dict(self.store)},
        )()


class _FakeTransactionalCollection:
    def __init__(self, store, db):
        self.store = store
        self._db = db

    def document(self, _lead_id):
        return _FakeTransactionalDoc(self.store, self._db)


class _FakeTransactionalDB:
    def __init__(self, store):
        self.store = store
        self.txn = _FakeTransaction(store)
        self.last_get_kwargs = None

    def collection(self, _name):
        return _FakeTransactionalCollection(self.store, self)

    def transaction(self):
        return self.txn


def test_manager_persists_lifecycle_in_a_firestore_transaction(monkeypatch):
    import lead_management

    monkeypatch.setattr(lead_management.firestore, "transactional", lambda fn: fn)
    store = Lead(lead_id="L1", user_id="U1", session_id="S1").to_dict()
    lm = LeadManager.__new__(LeadManager)
    lm.db = _FakeTransactionalDB(store)
    lm.collection_name = "leads"

    transitioned, previous, changed = asyncio.run(
        lm.transition_lead_status("L1", "contacted", actor="admin:ari")
    )

    assert changed is True
    assert previous == "new"
    assert transitioned.status == "contacted"
    assert store["first_contacted_by"] == "admin:ari"
    assert lm.db.txn.writes == 1
    # Transactional read must be bounded against a Firestore hang (risk #2).
    from database.rpc_timeout import FIRESTORE_RPC_TIMEOUT

    assert lm.db.last_get_kwargs["timeout"] == FIRESTORE_RPC_TIMEOUT

    _, _, replay_changed = asyncio.run(
        lm.transition_lead_status("L1", "contacted", actor="admin:other")
    )
    assert replay_changed is False
    assert store["first_contacted_by"] == "admin:ari"
    assert lm.db.txn.writes == 1


class _FakeDoc:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    def to_dict(self):
        return self._data


class _FakeQuery:
    def __init__(self, docs):
        self._docs = docs

    def where(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def stream(self, timeout: float | None = None):
        return iter(self._docs)


class _FakeDB:
    def __init__(self, docs):
        self._docs = docs

    def collection(self, _name):
        return _FakeQuery(self._docs)


def _manager_with(docs):
    lm = LeadManager.__new__(LeadManager)  # bypass firestore.Client() in __init__
    lm.db = _FakeDB(docs)
    lm.collection_name = "leads"
    return lm


def test_list_leads_skips_malformed_doc_without_failing(caplog):
    good = _FakeDoc(
        "good",
        {"lead_id": "L1", "user_id": "U1", "session_id": "S1", "name": "Jane"},
    )
    # Missing required lead_id/user_id/session_id -> Lead(**filtered) raises.
    bad = _FakeDoc("bad", {"name": "Orphan", "status": "new"})

    lm = _manager_with([good, bad])
    with caplog.at_level(logging.WARNING, logger="lead_management"):
        leads = asyncio.run(lm.list_leads())

    assert len(leads) == 1, "the malformed doc must be skipped, not fatal"
    assert leads[0].lead_id == "L1"
    assert any("Skipping unparseable lead doc" in r.getMessage() for r in caplog.records)


def test_list_leads_returns_all_well_formed_docs():
    docs = [
        _FakeDoc(str(i), {"lead_id": f"L{i}", "user_id": "U", "session_id": "S"}) for i in range(3)
    ]
    leads = asyncio.run(_manager_with(docs).list_leads())
    assert len(leads) == 3


def test_lead_accepts_and_roundtrips_utm_fields():
    lead = Lead(
        lead_id="l1",
        user_id="u1",
        session_id="s1",
        utm_source="instagram",
        utm_medium="social",
        utm_campaign="spring-sale",
        utm_content="reel-a",
        utm_term=None,
        referrer="https://t.co/x",
        gclid="EAIaIQobChMI_test-123",
        gbraid="GBRAID_test-123",
        wbraid="WBRAID_test-123",
    )
    d = lead.to_dict()
    assert d["utm_source"] == "instagram"
    assert d["utm_medium"] == "social"
    assert d["utm_campaign"] == "spring-sale"
    assert d["utm_content"] == "reel-a"
    assert d["utm_term"] is None
    assert d["referrer"] == "https://t.co/x"
    assert d["gclid"] == "EAIaIQobChMI_test-123"
    assert d["gbraid"] == "GBRAID_test-123"
    assert d["wbraid"] == "WBRAID_test-123"

    roundtripped = Lead.from_dict(d)
    assert roundtripped.utm_source == "instagram"
    assert roundtripped.utm_medium == "social"
    assert roundtripped.utm_campaign == "spring-sale"
    assert roundtripped.utm_content == "reel-a"
    assert roundtripped.utm_term is None
    assert roundtripped.referrer == "https://t.co/x"
    assert roundtripped.gclid == "EAIaIQobChMI_test-123"
    assert roundtripped.gbraid == "GBRAID_test-123"
    assert roundtripped.wbraid == "WBRAID_test-123"


def test_lead_roundtrips_anonymous_journey_and_structured_home():
    lead = Lead(
        lead_id="l1",
        user_id="u1",
        session_id="s1",
        journey_id="j_0123456789abcdef0123456789abcdef",
        home_id="home-42",
        home_model="Sapphire 3-Bed",
    )

    restored = Lead.from_dict(lead.to_dict())

    assert restored.journey_id == "j_0123456789abcdef0123456789abcdef"
    assert restored.home_id == "home-42"
    assert restored.home_model == "Sapphire 3-Bed"


def test_lead_from_dict_defaults_utm_to_none():
    lead = Lead.from_dict({"lead_id": "l1", "user_id": "u1", "session_id": "s1"})
    assert lead.utm_source is None
    assert lead.utm_medium is None
    assert lead.utm_campaign is None
    assert lead.utm_content is None
    assert lead.utm_term is None
    assert lead.referrer is None
    assert lead.gclid is None
    assert lead.gbraid is None
    assert lead.wbraid is None


def test_lead_from_dict_still_drops_unknown_keys():
    lead = Lead.from_dict(
        {
            "lead_id": "l1",
            "user_id": "u1",
            "session_id": "s1",
            "utm_source": "instagram",
            "junk_field": "should_be_dropped",
        }
    )
    assert lead.utm_source == "instagram"
    assert not hasattr(lead, "junk_field")


def test_lead_roundtrips_explicit_contact_consent():
    lead = Lead(
        lead_id="callback-1",
        user_id="u1",
        session_id="s1",
        contact_consent_at="2026-07-22T01:00:00+00:00",
        contact_consent_source="chat_callback",
    )

    restored = Lead.from_dict(lead.to_dict())

    assert restored.contact_consent_at == "2026-07-22T01:00:00+00:00"
    assert restored.contact_consent_source == "chat_callback"


# ---------------------------------------------------------------------------
# LeadManager data-access paths against a fake Firestore client
# ---------------------------------------------------------------------------


class _LMSnap:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class _LMDocRef:
    def __init__(self, collection, doc_id):
        self._collection = collection
        self.id = doc_id

    def get(self, timeout=None):
        assert timeout is not None, "Firestore RPCs must carry the bounded timeout"
        return _LMSnap(self.id, self._collection._docs.get(self.id))

    def set(self, data, merge=False, timeout=None):
        assert timeout is not None, "Firestore RPCs must carry the bounded timeout"
        if merge:
            self._collection._docs.setdefault(self.id, {}).update(data)
        else:
            self._collection._docs[self.id] = dict(data)
        self._collection.ops.append(("set", self.id, merge))


class _LMQuery:
    """Query result carrying (doc_id, data) pairs; supports the where /
    order_by / limit / stream chain the module uses."""

    def __init__(self, pairs):
        self._pairs = list(pairs)

    def where(self, field, op, value):
        assert op == "=="
        return _LMQuery([(k, v) for k, v in self._pairs if v.get(field) == value])

    def order_by(self, *a, **k):
        return self

    def limit(self, n):
        return self

    def stream(self, timeout=None):
        assert timeout is not None, "Firestore RPCs must carry the bounded timeout"
        for doc_id, data in list(self._pairs):
            yield _LMSnap(doc_id, dict(data))


class _LMCollection:
    def __init__(self, docs):
        self._docs = dict(docs)
        self.ops = []

    def document(self, doc_id):
        return _LMDocRef(self, doc_id)

    def where(self, field, op, value):
        return _LMQuery(list(self._docs.items())).where(field, op, value)

    def order_by(self, *a, **k):
        return _LMQuery(list(self._docs.items()))

    def limit(self, n):
        return _LMQuery(list(self._docs.items()))

    def stream(self, timeout=None):
        return _LMQuery(list(self._docs.items())).stream(timeout=timeout)


class _LMClient:
    def __init__(self, docs=None):
        self._coll = _LMCollection(docs or {})

    def collection(self, name):
        assert name == "leads"
        return self._coll



def _manager(docs=None) -> LeadManager:
    lm = LeadManager("test-project")  # lazy db: no ADC needed at construction
    lm.db = _LMClient(docs)
    return lm


def _lead_dict(lead_id="L1", **overrides):
    data = {"lead_id": lead_id, "user_id": "U1", "session_id": "S1", "status": "new"}
    data.update(overrides)
    return data


class TestNormalizePhone:
    def test_ten_digits_gets_us_country_code(self):
        assert normalize_phone("(281) 324-3020") == "+12813243020"
        assert normalize_phone("281-324-3020") == "+12813243020"

    def test_eleven_digits_with_leading_one(self):
        assert normalize_phone("1-281-324-3020") == "+12813243020"

    def test_unconfident_input_passes_through(self):
        assert normalize_phone("12345") == "12345"
        assert normalize_phone("") == ""
        assert normalize_phone(None) is None


class TestLeadModelBasics:
    def test_post_init_stamps_defaults(self):
        lead = Lead(lead_id="L1", user_id="U1", session_id="S1")
        assert lead.homes_viewed == []
        assert lead.created_at
        assert lead.updated_at

    def test_to_csv_row_formats_values(self):
        lead = Lead(
            lead_id="L1",
            user_id="U1",
            session_id="S1",
            name="Jane",
            budget_max=89900.0,
            homes_viewed=["h1", "h2", "h3", "h4"],
            appointment_requested=True,
            gbraid="GBRAID_x",
        )
        row = lead.to_csv_row()
        assert row["Lead ID"] == "L1"
        assert row["Name"] == "Jane"
        assert row["Max Budget"] == "$89,900"
        assert row["Homes Viewed"] == "h1, h2, h3"  # capped at 3
        assert row["Appointment Requested"] == "Yes"
        assert row["Google Click ID"] == "GBRAID_x"  # gclid empty -> gbraid


class TestLazyDbProperty:
    def test_client_created_once_on_first_use(self, monkeypatch):
        import lead_management

        calls = []
        monkeypatch.setattr(
            lead_management.firestore, "Client", lambda project=None: calls.append(project) or _LMClient()
        )
        lm = LeadManager("my-project")
        assert lm._db is None  # nothing constructed yet

        lm.db.collection("leads")
        lm.db.collection("leads")
        assert calls == ["my-project"]  # lazy + cached


class TestCreateUpdateGet:
    def test_create_lead_normalizes_phone_and_writes(self):
        lm = _manager()
        lead = Lead(lead_id="L1", user_id="U1", session_id="S1", phone="(281) 324-3020")

        result = asyncio.run(lm.create_lead(lead))

        assert result.phone == "+12813243020"
        stored = lm.db._coll._docs["L1"]
        assert stored["phone"] == "+12813243020"
        assert ("set", "L1", False) in lm.db._coll.ops

    def test_update_lead_merges_and_stamps_updated_at(self):
        lm = _manager({"L1": _lead_dict(name="Jane", updated_at="2020-01-01T00:00:00")})
        lead = Lead.from_dict(lm.db._coll._docs["L1"])
        lead.name = "Janet"

        asyncio.run(lm.update_lead(lead))

        stored = lm.db._coll._docs["L1"]
        assert stored["name"] == "Janet"
        assert stored["updated_at"] > "2020-01-01T00:00:00"
        assert ("set", "L1", True) in lm.db._coll.ops

    def test_get_lead_hit_and_miss(self):
        lm = _manager({"L1": _lead_dict(name="Jane")})
        hit = asyncio.run(lm.get_lead("L1"))
        assert hit.name == "Jane"
        assert asyncio.run(lm.get_lead("nope")) is None


class TestLeadLookupQueries:
    def test_get_by_session(self):
        lm = _manager({"L1": _lead_dict(session_id="sess-9")})
        hit = asyncio.run(lm.get_lead_by_session("sess-9"))
        assert hit.lead_id == "L1"
        assert asyncio.run(lm.get_lead_by_session("other")) is None

    def test_get_by_phone_short_circuits_without_phone(self):
        lm = _manager({"L1": _lead_dict()})
        assert asyncio.run(lm.get_lead_by_phone(None)) is None
        assert asyncio.run(lm.get_lead_by_phone("")) is None
        # No query should have been issued for the short-circuit cases.
        assert lm.db._coll.ops == []

    def test_get_by_phone_hit(self):
        lm = _manager({"L1": _lead_dict(phone="+12813243020")})
        hit = asyncio.run(lm.get_lead_by_phone("+12813243020"))
        assert hit.lead_id == "L1"


class TestTriageListing:
    def test_without_min_age_returns_all_matching_status(self):
        lm = _manager(
            {
                "L1": _lead_dict(created_at="2026-01-01T00:00:00"),
                "L2": _lead_dict(created_at="2026-01-02T00:00:00"),
            }
        )
        leads = asyncio.run(lm.list_leads_needing_triage())
        assert len(leads) == 2

    def test_min_age_excludes_recent_and_malformed(self):
        old = (datetime.utcnow() - timedelta(hours=48)).isoformat()
        recent = datetime.utcnow().isoformat()
        lm = _manager(
            {
                "L1": _lead_dict(created_at=old),
                "L2": _lead_dict(created_at=recent),
                "L3": _lead_dict(created_at="not-a-date"),
                "L4": _lead_dict(created_at=None),
            }
        )
        leads = asyncio.run(lm.list_leads_needing_triage(min_age_hours=24))
        assert [lead.lead_id for lead in leads] == ["L1"]


class TestTriageLead:
    def test_missing_lead_returns_none(self):
        lm = _manager()
        assert asyncio.run(lm.triage_lead("nope", {"priority": "high"})) is None

    def test_full_triage_update_persists(self):
        lm = _manager({"L1": _lead_dict(status="new")})
        updated = asyncio.run(
            lm.triage_lead(
                "L1",
                {
                    "status": "qualified",
                    "priority": "high",
                    "assigned_to": "rep-1",
                    "triage_notes": "Hot lead",
                    "triage_reason": "hot_lead",
                    "hacker_field": "ignored",
                },
                actor="system:mira",
            )
        )
        assert updated.status == "qualified"
        assert updated.priority == "high"
        assert updated.assigned_to == "rep-1"
        assert updated.triage_notes == "Hot lead"
        assert updated.triage_reason == "hot_lead"
        assert not hasattr(updated, "hacker_field")
        assert updated.last_triage_at is not None
        assert updated.status_changed_by == "system:mira"
        assert updated.first_contacted_at == updated.status_changed_at
        # Persisted through update_lead (merge).
        stored = lm.db._coll._docs["L1"]
        assert stored["priority"] == "high"
        assert ("set", "L1", True) in lm.db._coll.ops

    def test_invalid_status_rejected_before_any_write(self):
        lm = _manager({"L1": _lead_dict(status="new", priority="low")})
        with pytest.raises(ValueError, match="Invalid lead status"):
            asyncio.run(lm.triage_lead("L1", {"status": "bogus", "priority": "high"}))
        stored = lm.db._coll._docs["L1"]
        assert stored["status"] == "new"
        assert stored["priority"] == "low"
        assert lm.db._coll.ops == []


class TestExportCsv:
    def test_empty_leads_returns_none(self):
        assert _manager().export_to_csv([]) is None

    def test_writes_rows(self, tmp_path):
        leads = [
            Lead(lead_id="L1", user_id="U", session_id="S", name="Jane"),
            Lead(lead_id="L2", user_id="U", session_id="S", name="John"),
        ]
        out = tmp_path / "export.csv"
        result = _manager().export_to_csv(leads, str(out))

        assert result == str(out)
        lines = out.read_text().strip().splitlines()
        assert lines[0].startswith("Lead ID,Name,Email")
        assert lines[1].startswith("L1,Jane")
        assert lines[2].startswith("L2,John")


class TestAgentTools:
    def test_capture_contact_info(self):
        payload = json.loads(capture_contact_info("Jane", email="j@x.com", phone="281"))
        assert payload == {
            "action": "capture_contact",
            "name": "Jane",
            "email": "j@x.com",
            "phone": "281",
        }

    def test_mark_appointment_intent(self):
        assert json.loads(mark_appointment_intent()) == {"action": "mark_appointment_intent"}
