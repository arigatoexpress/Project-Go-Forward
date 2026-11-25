"""COD-254: one-tap contact logging, default assignment, and the SLA alert SQL."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from lead_management import (
    CONTACT_OUTCOMES,
    Lead,
    LeadManager,
    apply_contact_outcome,
    apply_default_assignment,
    default_lead_assignee,
)
from tests.test_lead_management import _FakeTransactionalDB

REPO = Path(__file__).resolve().parent.parent


def _lead(**kw) -> Lead:
    return Lead(lead_id="L1", user_id="U1", session_id="S1", created_at="2026-11-25T15:00:00", **kw)


# ─── apply_contact_outcome ─────────────────────────────────────────────────


@pytest.mark.parametrize("outcome", ["called", "texted", "emailed"])
def test_reached_outcome_starts_clock_and_marks_contacted(outcome):
    lead = _lead()
    assert apply_contact_outcome(lead, outcome, actor="admin:a", now="2026-11-25T15:07:00") is True
    assert lead.status == "contacted"
    assert lead.first_contacted_at == "2026-11-25T15:07:00"
    assert lead.first_contacted_by == "admin:a"
    assert lead.first_contact_outcome == outcome
    assert lead.contact_attempts == 1
    assert lead.last_contact_outcome == outcome


@pytest.mark.parametrize("outcome", ["left_voicemail", "no_answer"])
def test_attempt_without_reaching_starts_clock_but_keeps_lead_in_queue(outcome):
    lead = _lead()
    apply_contact_outcome(lead, outcome, actor="admin:a", now="2026-11-25T15:07:00")
    assert lead.status == "new"
    assert lead.first_contacted_at == "2026-11-25T15:07:00"
    assert lead.first_contact_outcome == outcome
    assert lead.contact_attempts == 1


def test_first_contact_clock_is_never_rewritten_by_later_attempts():
    lead = _lead()
    apply_contact_outcome(lead, "no_answer", actor="admin:a", now="2026-11-25T15:07:00")
    apply_contact_outcome(lead, "called", actor="admin:b", now="2026-11-25T16:00:00")
    assert lead.first_contacted_at == "2026-11-25T15:07:00"
    assert lead.first_contacted_by == "admin:a"
    assert lead.first_contact_outcome == "no_answer"
    assert lead.last_contact_outcome == "called"
    assert lead.contact_attempts == 2
    assert lead.status == "contacted"


def test_double_tap_is_ignored_but_a_real_retry_counts():
    lead = _lead()
    assert apply_contact_outcome(lead, "no_answer", actor="admin:a", now="2026-11-25T15:07:00")
    assert not apply_contact_outcome(lead, "no_answer", actor="admin:a", now="2026-11-25T15:07:05")
    assert lead.contact_attempts == 1
    assert apply_contact_outcome(lead, "no_answer", actor="admin:a", now="2026-11-25T17:00:00")
    assert lead.contact_attempts == 2


def test_reached_outcome_never_moves_a_later_stage_backwards():
    lead = _lead(status="qualified")
    apply_contact_outcome(lead, "called", actor="admin:a", now="2026-11-25T15:07:00")
    assert lead.status == "qualified"


def test_unknown_outcome_is_rejected():
    with pytest.raises(ValueError):
        apply_contact_outcome(_lead(), "met_in_person", actor="admin:a")


def test_contact_fields_round_trip_through_the_storage_schema():
    lead = _lead()
    apply_contact_outcome(lead, "left_voicemail", actor="admin:a", now="2026-11-25T15:07:00")
    stored = lead.to_dict()
    assert stored["contact_attempts"] == 1
    assert stored["last_contact_outcome"] == "left_voicemail"
    assert Lead.from_dict(stored).first_contact_outcome == "left_voicemail"


def test_manager_logs_contact_in_one_transaction(monkeypatch):
    import lead_management

    monkeypatch.setattr(lead_management.firestore, "transactional", lambda fn: fn)
    store = _lead().to_dict()
    lm = LeadManager.__new__(LeadManager)
    lm.db = _FakeTransactionalDB(store)
    lm.collection_name = "leads"
    lead, previous, recorded = asyncio.run(lm.log_contact_outcome("L1", "texted", actor="admin:a"))
    assert (previous, recorded, lead.status) == ("new", True, "contacted")
    assert store["first_contact_outcome"] == "texted"
    assert lm.db.txn.writes == 1


# ─── default assignment (flag off by default) ──────────────────────────────


def test_default_assignment_is_off_by_default(monkeypatch):
    monkeypatch.delenv("FF_LEAD_DEFAULT_ASSIGNMENT", raising=False)
    monkeypatch.delenv("FEATURE_FLAG_LEAD_DEFAULT_ASSIGNMENT", raising=False)
    lead = _lead()
    assert default_lead_assignee() is None
    assert apply_default_assignment(lead) is False
    assert lead.assigned_to is None


def test_default_assignment_uses_config_value_when_flag_is_on(monkeypatch):
    import yaml

    monkeypatch.setenv("FF_LEAD_DEFAULT_ASSIGNMENT", "1")
    monkeypatch.delenv("THO_LEAD_DEFAULT_ASSIGNEE", raising=False)
    expected = yaml.safe_load((REPO / "config.yaml").read_text())["leads"]["default_assignee"]
    lead = _lead()
    assert apply_default_assignment(lead) is True
    assert lead.assigned_to == expected


def test_default_assignment_env_override_and_never_overwrites_an_owner(monkeypatch):
    monkeypatch.setenv("FF_LEAD_DEFAULT_ASSIGNMENT", "true")
    monkeypatch.setenv("THO_LEAD_DEFAULT_ASSIGNEE", "sales-desk")
    lead = _lead()
    apply_default_assignment(lead)
    assert lead.assigned_to == "sales-desk"
    owned = _lead(assigned_to="someone-else")
    assert apply_default_assignment(owned) is False
    assert owned.assigned_to == "someone-else"


def test_no_staff_identity_is_hardcoded_in_assignment_code():
    source = (REPO / "lead_management.py").read_text()
    assert "@texashomeoutlet.com" not in source
    body = source.split("def default_lead_assignee")[1].split("class LeadManager")[0].lower()
    assert "mark" not in body


def test_create_lead_applies_default_assignment_and_survives_failure(monkeypatch):
    import lead_management

    saved = {}

    class _Doc:
        def set(self, data, timeout=None):
            saved.update(data)

    class _Coll:
        def document(self, _id):
            return _Doc()

    class _DB:
        def collection(self, _name):
            return _Coll()

    lm = LeadManager.__new__(LeadManager)
    lm.db = _DB()
    lm.collection_name = "leads"
    monkeypatch.setenv("FF_LEAD_DEFAULT_ASSIGNMENT", "1")
    monkeypatch.setenv("THO_LEAD_DEFAULT_ASSIGNEE", "owner-desk")
    asyncio.run(lm.create_lead(_lead()))
    assert saved["assigned_to"] == "owner-desk"

    def boom():
        raise RuntimeError("config unreadable")

    monkeypatch.setattr(lead_management, "default_lead_assignee", boom)
    saved.clear()
    asyncio.run(lm.create_lead(_lead()))
    assert saved["lead_id"] == "L1"  # lead capture still succeeds


# ─── POST /api/leads/{id}/contact-log ──────────────────────────────────────


def _client(monkeypatch):
    from tests.test_audit_coverage import _make_admin_client

    return _make_admin_client(monkeypatch)


def test_contact_log_endpoint_records_outcome_and_audits_without_pii(monkeypatch):
    client, main, headers, store = _client(monkeypatch)
    resp = client.post(
        "/api/leads/lead-1/contact-log", headers=headers, json={"outcome": "left_voicemail"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True and body["recorded"] is True
    assert body["lead"]["status"] == "new"
    assert body["lead"]["first_contacted_at"]
    assert body["lead"]["contact_attempts"] == 1
    entries = [
        e for e in store.collections["audit_log"]._docs if e.get("action") == "lead.contact_logged"
    ]
    assert len(entries) == 1
    assert entries[0]["details"]["outcome"] == "left_voicemail"
    flat = repr(entries[0])
    assert "lead@example.com" not in flat and "555-999-8888" not in flat


def test_contact_log_endpoint_rejects_bad_payloads_without_writing(monkeypatch):
    client, main, headers, store = _client(monkeypatch)
    for payload in ({"outcome": "bribed"}, {"outcome": "called", "status": "converted"}, {}):
        resp = client.post("/api/leads/lead-1/contact-log", headers=headers, json=payload)
        assert resp.status_code == 400, payload
    assert main.lead_manager.leads[0].contact_attempts == 0
    assert "audit_log" not in store.collections or not store.collections["audit_log"]._docs


def test_contact_log_endpoint_requires_staff_auth(monkeypatch):
    client, _main, _headers, _store = _client(monkeypatch)
    resp = client.post("/api/leads/lead-1/contact-log", json={"outcome": "called"})
    assert resp.status_code in (401, 403)


def test_contact_log_endpoint_404s_unknown_lead(monkeypatch):
    client, _main, headers, _store = _client(monkeypatch)
    resp = client.post("/api/leads/nope/contact-log", headers=headers, json={"outcome": "called"})
    assert resp.status_code == 404


def test_outcome_list_matches_the_storage_schema_and_the_endpoint():
    import ast
    import typing

    from database.models import LeadRecord

    allowed = set(
        typing.get_args(
            typing.get_args(LeadRecord.model_fields["last_contact_outcome"].annotation)[0]
        )
    )
    tree = ast.parse((REPO / "main.py").read_text())
    endpoint = next(
        node.value.args[0]
        for node in tree.body
        if isinstance(node, ast.Assign)
        and getattr(node.targets[0], "id", "") == "LEAD_CONTACT_OUTCOMES"
    )
    assert allowed == set(CONTACT_OUTCOMES) == set(ast.literal_eval(endpoint))


# ─── SLA alert SQL ──────────────────────────────────────────────────────────


def test_sla_sql_alerts_by_raising_and_stays_pii_free():
    sql = (REPO / "ops" / "lead_sla" / "lead_sla_daily.sql").read_text()
    assert "LEAD_SLA_BREACH" in sql and "ERROR(" in sql
    assert "tho_analytics.leads" in sql
    for column in ("name", "email", "phone"):
        assert f" {column}," not in sql.lower() and f".{column}" not in sql.lower()
