"""Tests for five coverage-gap targets (Python-3.12 honest-coverage backlog).

Targets (see the 2026-10 3.12 re-measurement in docs/MAIN_COVERAGE_INVESTIGATION.md):

- ``GET /api/leads/{lead_id}`` — found / not-found / failure envelopes
- ``GET /api/deals/{deal_id}`` — found (SSN-stripped) / not-found / failure
- ``POST /api/documents/sales-contract`` — the legacy contract endpoint's
  success, engine-failure, and exception envelopes (direct calls: the suite's
  ``create_client`` swaps the body schema for ``DummyBodyModel``, and the
  success path's audit actor is patched, so the request object is unused)
- ``PUT /api/crm/tasks/{task_id}`` — not-found, malformed JSON, non-object
  body, invalid status, and a full field update (in-memory store; the
  Firestore collection accessor is patched to None)
- ``POST /api/admin/email-code/verify`` — PIN lockout short-circuit,
  non-object body, per-code attempt cap (burns the code), and the
  empty-code-and-link-token mismatch branch

The verify route's limiter is disabled per test. Audit helpers are no-op
patched so the Firestore emulator is never needed.

Run: python -m pytest tests/test_leads_deals_tasks_gaps.py -v
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(Path(__file__).parent))
from test_api_v1 import create_client  # noqa: E402


def _admin_headers(main) -> dict:
    return {"X-Admin-Token": main._create_admin_token()}


# ─── GET /api/leads/{lead_id} ──────────────────────────────────────────────


class TestGetLeadApi:
    def _client(self, monkeypatch):
        client, main, fake_db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
        return client, main

    def test_lead_found_returns_lead(self, monkeypatch):
        client, main = self._client(monkeypatch)

        async def fake_get(lead_id):
            return types.SimpleNamespace(
                to_dict=lambda: {"lead_id": lead_id, "email": "buyer@example.com"}
            )

        monkeypatch.setattr(main.lead_manager, "get_lead", fake_get, raising=False)

        resp = client.get("/api/leads/lead-1", headers=_admin_headers(main))

        assert resp.status_code == 200
        assert resp.json() == {
            "success": True,
            "lead": {"lead_id": "lead-1", "email": "buyer@example.com"},
        }

    def test_lead_not_found(self, monkeypatch):
        client, main = self._client(monkeypatch)

        async def fake_get(lead_id):
            return None

        monkeypatch.setattr(main.lead_manager, "get_lead", fake_get, raising=False)

        resp = client.get("/api/leads/nope", headers=_admin_headers(main))

        assert resp.json() == {"success": False, "error": "Lead not found"}

    def test_lookup_failure_returns_generic_error(self, monkeypatch):
        client, main = self._client(monkeypatch)

        async def boom(lead_id):
            raise RuntimeError("synthetic lookup detail")

        monkeypatch.setattr(main.lead_manager, "get_lead", boom, raising=False)

        resp = client.get("/api/leads/lead-1", headers=_admin_headers(main))

        assert resp.json() == {
            "success": False,
            "error": "Failed to load lead details. Please try again.",
        }


# ─── GET /api/deals/{deal_id} ──────────────────────────────────────────────


class TestGetDeal:
    def _client(self, monkeypatch, *, deal=None, error=None):
        client, main, fake_db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)

        def fake_get_deal(deal_id):
            if error is not None:
                raise error
            return deal

        monkeypatch.setattr(main, "_deal_db", types.SimpleNamespace(get_deal=fake_get_deal))
        monkeypatch.setattr(
            main,
            "_strip_pii_from_deal",
            lambda d: {k: v for k, v in d.items() if k != "ssn"},
        )
        return client, main

    def test_deal_found_returns_ssn_stripped(self, monkeypatch):
        client, main = self._client(
            monkeypatch, deal={"id": "deal-1", "sale_price": 50000, "ssn": "123-45-6789"}
        )

        resp = client.get("/api/deals/deal-1", headers=_admin_headers(main))

        assert resp.json() == {
            "success": True,
            "deal": {"id": "deal-1", "sale_price": 50000},
        }

    def test_deal_not_found(self, monkeypatch):
        client, main = self._client(monkeypatch, deal=None)

        resp = client.get("/api/deals/nope", headers=_admin_headers(main))

        assert resp.json() == {"success": False, "error": "Deal not found"}

    def test_lookup_failure_returns_generic_error(self, monkeypatch):
        client, main = self._client(monkeypatch, error=RuntimeError("synthetic detail"))

        resp = client.get("/api/deals/deal-1", headers=_admin_headers(main))

        assert resp.json() == {
            "success": False,
            "error": "Failed to load deal details. Please try again.",
        }


# ─── POST /api/documents/sales-contract (legacy, direct) ───────────────────


class TestCreateSalesContract:
    def _client(self, monkeypatch, *, result=None, error=None):
        client, main, fake_db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
        monkeypatch.setattr(main, "log_user_action", lambda **kwargs: None)
        monkeypatch.setattr(main, "_audit_actor", lambda request: "actor:test")

        def fake_generate(form_data):
            if error is not None:
                raise error
            return result

        monkeypatch.setattr(main, "generate_sales_contract_pdf", fake_generate)
        return main

    def test_success_returns_download_url(self, monkeypatch):
        main = self._client(
            monkeypatch, result={"success": True, "filename": "contract-1.pdf"}
        )
        body = types.SimpleNamespace()

        result = asyncio.run(main.create_sales_contract(body, None))

        assert result == {
            "success": True,
            "download_url": "/api/documents/download/contract-1.pdf",
            "filename": "contract-1.pdf",
        }

    def test_engine_failure_returns_message(self, monkeypatch):
        main = self._client(
            monkeypatch, result={"success": False, "message": "Template not mapped."}
        )
        body = types.SimpleNamespace()

        result = asyncio.run(main.create_sales_contract(body, None))

        assert result == {"success": False, "error": "Template not mapped."}

    def test_engine_exception_returns_generic_envelope(self, monkeypatch):
        main = self._client(monkeypatch, error=RuntimeError("synthetic engine detail"))
        body = types.SimpleNamespace()

        result = asyncio.run(main.create_sales_contract(body, None))

        assert result == {
            "success": False,
            "error": "Document generation failed. Please try again.",
        }


# ─── PUT /api/crm/tasks/{task_id} ──────────────────────────────────────────


def _crm_client(monkeypatch, tasks=None):
    client, main, fake_db, _logger = create_client(monkeypatch)
    monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
    monkeypatch.setattr(main, "log_user_action", lambda **kwargs: None)
    store = dict(tasks or {})
    monkeypatch.setattr(main, "_crm_tasks", store)
    monkeypatch.setattr(main, "_crm_task_collection", lambda: None)
    headers = _admin_headers(main)
    return client, main, store, headers


_TASK = {
    "task_id": "task-1",
    "title": "Call buyer back",
    "status": "pending",
    "priority": "medium",
    "created_at": "2026-01-01T00:00:00+00:00",
}


class TestUpdateCrmTask:
    def test_task_not_found_returns_404(self, monkeypatch):
        client, _main, _store, headers = _crm_client(monkeypatch)

        resp = client.put("/api/crm/tasks/nope", json={"status": "completed"}, headers=headers)

        assert resp.status_code == 404
        assert resp.json() == {"success": False, "error": "Task not found."}

    def test_malformed_json_returns_400(self, monkeypatch):
        client, _main, _store, headers = _crm_client(monkeypatch, tasks={"task-1": dict(_TASK)})

        resp = client.put(
            "/api/crm/tasks/task-1",
            content="{not json",
            headers={**headers, "Content-Type": "application/json"},
        )

        assert resp.status_code == 400
        assert resp.json() == {"success": False, "error": "Malformed JSON body."}

    def test_non_object_body_returns_400(self, monkeypatch):
        client, _main, _store, headers = _crm_client(monkeypatch, tasks={"task-1": dict(_TASK)})

        resp = client.put("/api/crm/tasks/task-1", json=["not", "object"], headers=headers)

        assert resp.status_code == 400
        assert resp.json() == {
            "success": False,
            "error": "Request body must be a JSON object.",
        }

    def test_invalid_status_returns_400(self, monkeypatch):
        client, _main, store, headers = _crm_client(monkeypatch, tasks={"task-1": dict(_TASK)})

        resp = client.put(
            "/api/crm/tasks/task-1", json={"status": "in-progress"}, headers=headers
        )

        assert resp.status_code == 400
        assert resp.json() == {
            "success": False,
            "error": "status must be pending or completed.",
        }
        assert store["task-1"]["status"] == "pending"  # unchanged

    def test_full_update_saves_and_audits(self, monkeypatch):
        client, main, store, headers = _crm_client(monkeypatch, tasks={"task-1": dict(_TASK)})
        audited: list = []
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: audited.append(kwargs))

        resp = client.put(
            "/api/crm/tasks/task-1",
            json={
                "status": "completed",
                "title": "  Called buyer back  ",
                "due_date": "2026-02-01",
                "priority": "HIGH",
                "unknown_field": "ignored",
            },
            headers=headers,
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["task"]["status"] == "completed"
        assert body["task"]["title"] == "Called buyer back"
        assert body["task"]["due_date"] == "2026-02-01"
        assert body["task"]["priority"] == "high"
        assert "unknown_field" not in body["task"]
        assert store["task-1"]["status"] == "completed"
        assert audited and audited[0]["action"] == "crm_task.update"


# ─── POST /api/admin/email-code/verify ─────────────────────────────────────


class _FakeCodeStore:
    def __init__(self, rec=None, attempts=1):
        self.rec = rec
        self.attempts = attempts
        self.deleted: list = []

    def get(self, email):
        return self.rec

    def increment_attempts(self, email):
        return self.attempts

    def delete(self, email):
        self.deleted.append(email)


def _verify_client(monkeypatch, store):
    client, main, fake_db, _logger = create_client(monkeypatch)
    monkeypatch.setattr(main.limiter, "enabled", False)
    monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
    monkeypatch.setattr(main, "log_user_action", lambda **kwargs: None)
    monkeypatch.setattr(main, "_pin_budget_lockout_response", lambda client_ip: None)
    monkeypatch.setattr(main, "ADMIN_PIN_HASH", "configured")
    monkeypatch.setattr(main, "is_allowed_admin_email", lambda email: True)
    monkeypatch.setattr(main, "_add_pin_attempt", lambda client_ip, now: None)
    monkeypatch.setattr(main, "default_code_store", lambda: store)
    monkeypatch.setattr(main, "hash_code", lambda value: f"hashed:{value}")
    return client, main


class TestVerifyAdminEmailCodeGaps:
    def test_pin_lockout_short_circuits(self, monkeypatch):
        store = _FakeCodeStore()
        client, main = _verify_client(monkeypatch, store)
        from fastapi.responses import JSONResponse

        monkeypatch.setattr(
            main,
            "_pin_budget_lockout_response",
            lambda client_ip: JSONResponse({"error": "Too many attempts."}, status_code=429),
        )

        resp = client.post(
            "/api/admin/email-code/verify",
            json={"email": "owner@texashomeoutlet.com", "code": "123456"},
        )

        assert resp.status_code == 429
        assert store.deleted == []  # never reached the store

    def test_non_object_body_returns_400(self, monkeypatch):
        store = _FakeCodeStore()
        client, _main = _verify_client(monkeypatch, store)

        resp = client.post("/api/admin/email-code/verify", json=["not", "object"])

        assert resp.status_code == 400
        assert resp.json() == {
            "success": False,
            "error": "Request body must be a JSON object.",
        }

    def test_malformed_json_returns_400(self, monkeypatch):
        store = _FakeCodeStore()
        client, _main = _verify_client(monkeypatch, store)

        resp = client.post(
            "/api/admin/email-code/verify",
            content="{not json",
            headers={"Content-Type": "application/json"},
        )

        assert resp.status_code == 400
        assert resp.json() == {"success": False, "error": "Malformed JSON body."}

    def test_non_string_code_returns_400(self, monkeypatch):
        store = _FakeCodeStore()
        client, _main = _verify_client(monkeypatch, store)

        resp = client.post(
            "/api/admin/email-code/verify",
            json={"email": "owner@texashomeoutlet.com", "code": 123456},
        )

        assert resp.status_code == 400
        assert resp.json() == {"success": False, "error": "Code must be a string."}

    def test_unconfigured_admin_auth_fails_closed_503(self, monkeypatch):
        store = _FakeCodeStore()
        client, main = _verify_client(monkeypatch, store)
        monkeypatch.setattr(main, "ADMIN_PIN_HASH", "")

        resp = client.post(
            "/api/admin/email-code/verify",
            json={"email": "owner@texashomeoutlet.com", "code": "123456"},
        )

        assert resp.status_code == 503
        assert resp.json() == {"success": False, "error": "Admin auth not configured."}
        assert store.deleted == []  # fail closed BEFORE consuming a valid code

    def test_disallowed_email_gets_generic_401(self, monkeypatch):
        store = _FakeCodeStore(rec=types.SimpleNamespace(code_hash="x", link_hash="x"))
        client, main = _verify_client(monkeypatch, store)
        monkeypatch.setattr(main, "is_allowed_admin_email", lambda email: False)

        resp = client.post(
            "/api/admin/email-code/verify",
            json={"email": "intruder@example.com", "code": "123456"},
        )

        assert resp.status_code == 401
        assert store.deleted == []

    def test_store_unavailable_fails_closed_503(self, monkeypatch):
        store = _FakeCodeStore()
        client, main = _verify_client(monkeypatch, store)

        def _boom():
            raise main.EmailLoginCodeStoreUnavailable("firestore down")

        monkeypatch.setattr(main, "default_code_store", _boom)

        resp = client.post(
            "/api/admin/email-code/verify",
            json={"email": "owner@texashomeoutlet.com", "code": "123456"},
        )

        assert resp.status_code == 503
        assert resp.json() == {"success": False, "error": "Admin auth not configured."}

    def test_attempt_cap_burns_code(self, monkeypatch):
        rec = types.SimpleNamespace(code_hash="hashed:123456", link_hash=None)
        store = _FakeCodeStore(rec=rec, attempts=999)
        client, main = _verify_client(monkeypatch, store)

        resp = client.post(
            "/api/admin/email-code/verify",
            json={"email": "owner@texashomeoutlet.com", "code": "123456"},
        )

        assert resp.status_code == 401
        assert store.deleted == ["owner@texashomeoutlet.com"]

    def test_empty_code_and_link_token_is_invalid(self, monkeypatch):
        rec = types.SimpleNamespace(code_hash="hashed:123456", link_hash="hashed:tok")
        store = _FakeCodeStore(rec=rec, attempts=1)
        client, _main = _verify_client(monkeypatch, store)

        resp = client.post(
            "/api/admin/email-code/verify",
            json={"email": "owner@texashomeoutlet.com", "code": "", "link_token": ""},
        )

        assert resp.status_code == 401
        assert store.deleted == []  # mismatch does not consume the code
