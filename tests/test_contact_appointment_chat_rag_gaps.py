"""Tests for four coverage-gap targets (Python-3.12 honest-coverage backlog).

Targets (see docs/MAIN_COVERAGE_INVESTIGATION.md and the 3.12 re-measurement):

- ``POST /api/contact`` — invalid-JSON envelope, Notion lead-sync success and
  failure branches, welcome-email exception branch, staff-task exception
  branch, and the outer generic-failure envelope
- ``POST /api/appointments`` — required-field and short-phone validation,
  confirmation/owner-notification exception branches, 11-digit handoff-phone
  normalization, and the ValueError / generic exception envelopes
- ``POST /api/chat/search`` — empty-query rejection, success envelope, and
  search-failure envelope (admin only)
- ``_get_document_rag`` — lazy singleton load/caching (the FileNotFoundError
  503 path is already covered elsewhere; guarded here against regression)

The contact and appointment routes carry slowapi caps, so the limiter is
disabled per test via ``main.limiter.enabled`` (the endpoint logic under
test is independent of the cap). Audit helpers are no-op patched so the
Firestore emulator is never required.

Run: python -m pytest tests/test_contact_appointment_chat_rag_gaps.py -v
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(Path(__file__).parent))
from test_api_v1 import create_client  # noqa: E402

import tools  # noqa: E402  (already imported by main; parent package for stubs)


def _intake_client(monkeypatch):
    """Client with limiter disabled, audit helpers silenced, delivery happy."""
    client, main, fake_db, _logger = create_client(monkeypatch)
    monkeypatch.setattr(main.limiter, "enabled", False)
    monkeypatch.setattr(main, "log_user_action", lambda **kwargs: None)
    monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
    monkeypatch.setattr(main, "send_lead_welcome", lambda **kwargs: {"success": True})
    monkeypatch.setattr(main, "notify_new_lead", lambda **kwargs: {"success": True})
    return client, main, fake_db


def _contact_payload(**overrides):
    payload = {
        "name": "Gap Tester",
        "phone": "5125550123",
        "email": "gap@example.com",
    }
    payload.update(overrides)
    return payload


def _stub_notion_writer(monkeypatch, *, write=None):
    """Install a fake tools.notion_lead_writer (module + parent attr)."""
    written: list = []

    def default_write(*args, **kwargs):
        written.append({"args": args, "kwargs": kwargs})

    fake_module = types.ModuleType("tools.notion_lead_writer")
    fake_module.is_lead_sync_enabled = lambda: True
    fake_module.write_lead = write if write is not None else default_write
    monkeypatch.setitem(sys.modules, "tools.notion_lead_writer", fake_module)
    monkeypatch.setattr(tools, "notion_lead_writer", fake_module, raising=False)
    return written


def _stub_appointment_storage(monkeypatch, main):
    monkeypatch.setattr(
        main,
        "Appointment",
        lambda **kw: types.SimpleNamespace(**kw),
        raising=False,
    )

    async def ok_create(appt):
        return types.SimpleNamespace(
            to_dict=lambda: {"appointment_id": getattr(appt, "appointment_id", "appt_test")}
        )

    monkeypatch.setattr(
        main.appointment_manager,
        "create_appointment",
        ok_create,
        raising=False,
    )


def _appointment_payload(**overrides):
    payload = {
        "name": "Gap Tester",
        "phone": "5125550123",
        "email": "gap@example.com",
        "date": "2026-08-01",
        "time_slot": "10:00 AM",
    }
    payload.update(overrides)
    return payload


# ─── POST /api/contact ─────────────────────────────────────────────────────


class TestSubmitContactFormGaps:
    def test_invalid_json_returns_400_envelope(self, monkeypatch):
        client, _main, _db = _intake_client(monkeypatch)

        resp = client.post(
            "/api/contact",
            content="{not json",
            headers={"Content-Type": "application/json"},
        )

        assert resp.status_code == 400
        assert resp.json() == {"success": False, "error": "Invalid request."}

    def test_unexpected_failure_returns_generic_envelope(self, monkeypatch):
        client, main, _db = _intake_client(monkeypatch)

        def _boom(**kwargs):
            raise RuntimeError("synthetic private detail")

        # log_user_action runs outside the inner per-integration try blocks,
        # so raising there exercises the handler's outer except.
        monkeypatch.setattr(main, "log_user_action", _boom)

        resp = client.post("/api/contact", json=_contact_payload())

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "try again" in body["error"]
        assert "synthetic private detail" not in str(body)

    def test_notion_sync_enabled_mirrors_lead(self, monkeypatch):
        client, _main, _db = _intake_client(monkeypatch)
        written = _stub_notion_writer(monkeypatch)

        resp = client.post("/api/contact", json=_contact_payload(source="website"))

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert len(written) == 1
        assert written[0]["args"][0] == "Gap Tester"
        assert written[0]["kwargs"]["source"] == "website"

    def test_notion_sync_failure_stays_invisible_to_visitor(self, monkeypatch):
        client, _main, _db = _intake_client(monkeypatch)
        _stub_notion_writer(
            monkeypatch,
            write=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("notion down")),
        )

        resp = client.post("/api/contact", json=_contact_payload())

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "warnings" not in body  # notion failure never surfaces publicly

    def test_welcome_email_exception_warns(self, monkeypatch):
        client, main, _db = _intake_client(monkeypatch)

        def _boom(**kwargs):
            raise RuntimeError("synthetic provider detail")

        monkeypatch.setattr(main, "send_lead_welcome", _boom)

        resp = client.post("/api/contact", json=_contact_payload())

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "welcome_email_failed" in body["warnings"]
        assert "synthetic provider detail" not in str(body)

    def test_staff_task_failure_does_not_break_intake(self, monkeypatch):
        client, main, _db = _intake_client(monkeypatch)
        monkeypatch.setattr(main, "docuseal_is_configured", lambda: True)

        def _boom(**kwargs):
            raise RuntimeError("task queue down")

        monkeypatch.setattr(main, "_queue_staff_task", _boom)

        resp = client.post("/api/contact", json=_contact_payload())

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        # The staff-task failure is logged, not surfaced to the visitor.
        assert "warnings" not in body


# ─── POST /api/appointments ────────────────────────────────────────────────


class TestCreateAppointmentGaps:
    def test_missing_fields_rejected(self, monkeypatch):
        client, main, _db = _intake_client(monkeypatch)
        _stub_appointment_storage(monkeypatch, main)

        resp = client.post("/api/appointments", json={"name": "Gap Tester"})

        assert resp.json() == {
            "success": False,
            "error": "Name, phone, date, and time_slot are required.",
        }

    def test_short_phone_rejected(self, monkeypatch):
        client, main, _db = _intake_client(monkeypatch)
        _stub_appointment_storage(monkeypatch, main)

        resp = client.post(
            "/api/appointments", json=_appointment_payload(phone="555-1234")
        )

        assert resp.json() == {
            "success": False,
            "error": "Please provide a valid 10-digit phone number.",
        }

    def test_confirmation_email_exception_warns(self, monkeypatch):
        client, main, _db = _intake_client(monkeypatch)
        _stub_appointment_storage(monkeypatch, main)

        def _boom(**kwargs):
            raise RuntimeError("synthetic provider detail")

        monkeypatch.setattr(main, "send_appointment_confirmation", _boom)

        resp = client.post("/api/appointments", json=_appointment_payload())

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "appointment_confirmation_failed" in body["warnings"]
        assert "synthetic provider detail" not in str(body)

    def test_owner_notify_exception_warns(self, monkeypatch):
        client, main, _db = _intake_client(monkeypatch)
        _stub_appointment_storage(monkeypatch, main)

        def _boom(**kwargs):
            raise RuntimeError("synthetic provider detail")

        monkeypatch.setattr(main, "notify_new_appointment", _boom)

        resp = client.post("/api/appointments", json=_appointment_payload())

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "owner_notify_failed" in body["warnings"]
        assert "synthetic provider detail" not in str(body)

    def test_handoff_strips_leading_1_from_lead_phone(self, monkeypatch):
        client, main, _db = _intake_client(monkeypatch)
        _stub_appointment_storage(monkeypatch, main)
        lead = types.SimpleNamespace(
            phone="15125550123",
            journey_id=None,
            home_id=None,
            home_model=None,
        )
        updates: list = []

        async def fake_get(lead_id):
            return lead

        async def fake_update(existing):
            updates.append(existing)

        monkeypatch.setattr(main.lead_manager, "get_lead", fake_get, raising=False)
        monkeypatch.setattr(main.lead_manager, "update_lead", fake_update, raising=False)

        resp = client.post(
            "/api/appointments",
            json=_appointment_payload(lead_id="contact_1700000000_ab12"),
        )

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert updates == [lead]
        assert lead.appointment_requested is True
        assert lead.status == "qualified"

    def test_value_error_returns_plain_error(self, monkeypatch):
        client, main, _db = _intake_client(monkeypatch)
        _stub_appointment_storage(monkeypatch, main)

        async def bad_create(_appt):
            raise ValueError("synthetic slot detail")

        monkeypatch.setattr(main.appointment_manager, "create_appointment", bad_create)

        resp = client.post("/api/appointments", json=_appointment_payload())

        assert resp.json() == {"success": False, "error": "synthetic slot detail"}

    def test_generic_failure_returns_retry_message(self, monkeypatch):
        client, main, _db = _intake_client(monkeypatch)
        _stub_appointment_storage(monkeypatch, main)

        async def bad_create(_appt):
            raise RuntimeError("synthetic private detail")

        monkeypatch.setattr(main.appointment_manager, "create_appointment", bad_create)

        resp = client.post("/api/appointments", json=_appointment_payload())

        body = resp.json()
        assert body["success"] is False
        assert "try again" in body["error"]
        assert "synthetic private detail" not in str(body)


# ─── POST /api/chat/search (admin) ─────────────────────────────────────────


class TestSearchChatConversations:
    def _admin_client(self, monkeypatch):
        client, main, fake_db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
        headers = {"X-Admin-Token": main._create_admin_token()}
        return client, main, headers

    def test_empty_query_rejected(self, monkeypatch):
        client, _main, headers = self._admin_client(monkeypatch)

        resp = client.post("/api/chat/search", json={"query": "   "}, headers=headers)

        assert resp.json() == {"success": False, "error": "Query required"}

    def test_search_returns_results(self, monkeypatch):
        client, main, headers = self._admin_client(monkeypatch)

        async def fake_search(query):
            return [{"id": "conv-1"}, {"id": "conv-2"}]

        monkeypatch.setattr(
            main.chat_history, "search_conversations", fake_search, raising=False
        )

        resp = client.post("/api/chat/search", json={"query": "financing"}, headers=headers)

        assert resp.json() == {
            "success": True,
            "results": [{"id": "conv-1"}, {"id": "conv-2"}],
            "count": 2,
        }

    def test_search_failure_returns_generic_envelope(self, monkeypatch):
        client, main, headers = self._admin_client(monkeypatch)

        async def boom(query):
            raise RuntimeError("synthetic search detail")

        monkeypatch.setattr(
            main.chat_history, "search_conversations", boom, raising=False
        )

        resp = client.post("/api/chat/search", json={"query": "financing"}, headers=headers)

        body = resp.json()
        assert body == {"success": False, "error": "Search failed"}
        assert "synthetic search detail" not in str(body)


# ─── _get_document_rag (lazy singleton) ────────────────────────────────────


class TestGetDocumentRag:
    def _stub_rag_module(self, monkeypatch, main, *, load_error=None):
        calls = {"loads": 0}

        class FakeDocumentRAG:
            def __init__(self):
                self.loaded = False

            def load(self):
                calls["loads"] += 1
                if load_error is not None:
                    raise load_error
                self.loaded = True

        fake_module = types.ModuleType("tools.document_rag")
        fake_module.DocumentRAG = FakeDocumentRAG
        monkeypatch.setitem(sys.modules, "tools.document_rag", fake_module)
        monkeypatch.setattr(tools, "document_rag", fake_module, raising=False)
        monkeypatch.setattr(main, "_rag_instance", None)
        return calls

    def test_lazy_load_builds_and_caches_singleton(self, monkeypatch):
        _client, main, _db, _logger = create_client(monkeypatch)
        calls = self._stub_rag_module(monkeypatch, main)

        first = main._get_document_rag()
        second = main._get_document_rag()

        assert first is second
        assert first.loaded is True
        assert calls["loads"] == 1  # second call served from the cached instance
        assert main._rag_instance is first

    def test_missing_index_raises_service_unavailable(self, monkeypatch):
        _client, main, _db, _logger = create_client(monkeypatch)
        self._stub_rag_module(monkeypatch, main, load_error=FileNotFoundError("no index"))

        with pytest.raises(main.HTTPException) as excinfo:
            main._get_document_rag()

        assert excinfo.value.status_code == 503
        assert "RAG index not built" in excinfo.value.detail
        assert main._rag_instance is None  # failed load must not cache
