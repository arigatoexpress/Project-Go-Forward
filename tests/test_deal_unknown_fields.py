"""COD-158 observation step: unknown deal keys are logged by NAME only (never values)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_api_v1 import create_client  # noqa: E402

from database.models import unknown_deal_fields  # noqa: E402


def test_unknown_deal_fields_names_only():
    assert unknown_deal_fields({"buyer_first_name": "A", "notes": "x"}) == []
    assert unknown_deal_fields({"buyer_first_name": "A", "zz_typo": 1, "buyerSSN": "123"}) == ["buyerSSN", "zz_typo"]
    assert unknown_deal_fields(["not", "a", "dict"]) == []
    assert unknown_deal_fields(None) == []


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


def test_create_and_update_log_unknown_names_not_values(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch)
    fake = _FakeDealDB()
    monkeypatch.setattr(main, "_deal_db", fake)
    calls = _capture(monkeypatch, main)
    token = main._create_admin_token()
    h = {"Authorization": f"Bearer {token}"}

    secret_value = "999-88-7777"
    r = client.post("/api/deals", json={"buyer_first_name": "Pat", "mystery_field": secret_value}, headers=h)
    assert r.status_code == 200 and r.json().get("success") is True, (r.text)
    r = client.put("/api/deals/deal-1", json={"notes": "n", "other_unknown": secret_value}, headers=h)
    assert r.status_code == 200 and r.json().get("success") is True, r.text

    warn = [c for c in calls if c[1] == "Deal payload has unknown fields"]
    assert [(c[2]["route"], c[2]["unknown_fields"]) for c in warn] == [
        ("create", ["mystery_field"]),
        ("update", ["other_unknown"]),
    ]
    assert secret_value not in repr(warn)
    # behaviour unchanged: update still passes the key through (create path is
    # covered by the real Deal model in test_unknown_deal_fields_names_only)
    assert fake.updated[1].get("other_unknown") == secret_value


def test_known_fields_do_not_warn(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch)
    monkeypatch.setattr(main, "_deal_db", _FakeDealDB())
    calls = _capture(monkeypatch, main)
    token = main._create_admin_token()
    client.post("/api/deals", json={"buyer_first_name": "Pat"}, headers={"Authorization": f"Bearer {token}"})
    assert not [c for c in calls if c[1] == "Deal payload has unknown fields"]
