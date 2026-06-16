"""Lead phone numbers are normalized to E.164 on create, so the CRM is
consistent and click-to-call / SMS / dedup-grouping work. Normalization is
best-effort and never drops data (unrecognized formats pass through unchanged).
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lead_management import Lead, LeadManager, normalize_phone


def test_normalize_phone_us_formats():
    assert normalize_phone("(281) 324-3020") == "+12813243020"
    assert normalize_phone("281-324-3020") == "+12813243020"
    assert normalize_phone("2813243020") == "+12813243020"
    assert normalize_phone("1 (281) 324-3020") == "+12813243020"
    assert normalize_phone("+12813243020") == "+12813243020"  # idempotent


def test_normalize_phone_never_loses_data():
    assert normalize_phone(None) is None
    assert normalize_phone("") == ""
    assert normalize_phone("123") == "123"  # too short -> unchanged
    assert normalize_phone("+44 20 7946 0958") == "+44 20 7946 0958"  # intl -> unchanged


def test_create_lead_normalizes_phone():
    saved = {}

    class _Doc:
        def set(self, data, **k):
            saved.update(data)

    class _Coll:
        def document(self, _id):
            return _Doc()

    class _DB:
        def collection(self, _n):
            return _Coll()

    lm = LeadManager.__new__(LeadManager)  # bypass firestore.Client()
    lm.db = _DB()
    lm.collection_name = "leads"
    lead = Lead(lead_id="x", user_id="u", session_id="s", phone="(281) 324-3020")
    asyncio.run(lm.create_lead(lead))
    assert lead.phone == "+12813243020"
    # Firestore stores via Lead.to_dict() -> asdict(), i.e. snake_case field
    # names. (The capitalized "Phone" key only exists in to_csv_row().)
    assert saved.get("phone") == "+12813243020"


def test_update_lead_normalizes_phone():
    """The 'stored phones are E.164' invariant must survive edits, not just
    creation -- otherwise an admin CRM edit re-introduces a raw number."""
    saved = {}

    class _Doc:
        def set(self, data, **k):
            saved.update(data)

    class _Coll:
        def document(self, _id):
            return _Doc()

    class _DB:
        def collection(self, _n):
            return _Coll()

    lm = LeadManager.__new__(LeadManager)  # bypass firestore.Client()
    lm.db = _DB()
    lm.collection_name = "leads"
    lead = Lead(lead_id="x", user_id="u", session_id="s", phone="281-324-3020")
    asyncio.run(lm.update_lead(lead))
    assert lead.phone == "+12813243020"
    assert saved.get("phone") == "+12813243020"
