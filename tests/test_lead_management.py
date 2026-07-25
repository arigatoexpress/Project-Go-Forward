"""Resilience tests for LeadManager.list_leads.

A single malformed / out-of-band Firestore lead document must NOT 500 the whole
admin CRM list — that list is the owner's only post-cutover visibility while
transactional email is off. Lead.from_dict already drops unknown keys; the list
path additionally skips-and-logs any doc that still fails to construct.
"""

import asyncio
import logging

import pytest

from lead_management import Lead, LeadManager, apply_lead_status_transition


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
