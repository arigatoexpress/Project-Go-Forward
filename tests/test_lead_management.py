"""Resilience tests for LeadManager.list_leads.

A single malformed / out-of-band Firestore lead document must NOT 500 the whole
admin CRM list — that list is the owner's only post-cutover visibility while
transactional email is off. Lead.from_dict already drops unknown keys; the list
path additionally skips-and-logs any doc that still fails to construct.
"""

import asyncio
import logging

from lead_management import Lead, LeadManager


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

    def stream(self):
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
        _FakeDoc(str(i), {"lead_id": f"L{i}", "user_id": "U", "session_id": "S"})
        for i in range(3)
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
    )
    d = lead.to_dict()
    assert d["utm_source"] == "instagram"
    assert d["utm_medium"] == "social"
    assert d["utm_campaign"] == "spring-sale"
    assert d["utm_content"] == "reel-a"
    assert d["utm_term"] is None
    assert d["referrer"] == "https://t.co/x"

    roundtripped = Lead.from_dict(d)
    assert roundtripped.utm_source == "instagram"
    assert roundtripped.utm_medium == "social"
    assert roundtripped.utm_campaign == "spring-sale"
    assert roundtripped.utm_content == "reel-a"
    assert roundtripped.utm_term is None
    assert roundtripped.referrer == "https://t.co/x"


def test_lead_from_dict_defaults_utm_to_none():
    lead = Lead.from_dict({"lead_id": "l1", "user_id": "u1", "session_id": "s1"})
    assert lead.utm_source is None
    assert lead.utm_medium is None
    assert lead.utm_campaign is None
    assert lead.utm_content is None
    assert lead.utm_term is None
    assert lead.referrer is None


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
