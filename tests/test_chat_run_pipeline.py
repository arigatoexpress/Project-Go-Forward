"""Tests for the public chat run pipeline with zero prior coverage.

Targets flagged by the Python-3.12 honest coverage re-measurement (see
docs/MAIN_COVERAGE_INVESTIGATION.md):

- ``POST /run``              — the public chat endpoint (session binding,
                               runner lifecycle, history/context/lead
                               persistence, timeout + failure envelopes)
- ``_execute_agent_run``     — ADK event-stream reducer (text collection,
                               error-event logging, empty-response fallback)

The Google ADK runner, conversation memory, and chat history are replaced
with in-memory fakes via monkeypatch on ``main`` module globals, and
``google.genai.types`` is stubbed in ``sys.modules`` (the endpoint imports it
lazily). Audit/activity logging and the contact-capture backstop are
no-op-patched: the real helpers write to Firestore, which hangs standalone
without the emulator (their own behavior is covered by dedicated test files).

Run: python -m pytest tests/test_chat_run_pipeline.py -v
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
from test_api_v1 import FakeLead, create_client  # noqa: E402

from chat_owner import CHAT_OWNER_COOKIE  # noqa: E402

# ─── Fakes ────────────────────────────────────────────────────────────────


class _Part:
    def __init__(self, text=None):
        self.text = text


class _Content:
    def __init__(self, role=None, parts=None):
        self.role = role
        self.parts = list(parts) if parts else []


class ErrorEvent:
    """Mimics ADK's ErrorEvent (branch detection is by type name)."""

    def __init__(self, error_message="boom"):
        self.error_message = error_message


class _Prefs:
    def __init__(self, bedrooms=None, bathrooms=None, max_budget=None):
        self.bedrooms = bedrooms
        self.bathrooms = bathrooms
        self.max_budget = max_budget

    def to_prompt_context(self):
        return ""


class _Context:
    def __init__(
        self,
        bedrooms=None,
        bathrooms=None,
        max_budget=None,
        homes_discussed=None,
        appointment_intent=False,
        financing_questions=0,
    ):
        self.preferences = _Prefs(bedrooms, bathrooms, max_budget)
        self.homes_discussed = list(homes_discussed or [])
        self.appointment_intent = appointment_intent
        self.financing_questions = financing_questions


class _FakeMemory:
    def __init__(self, context=None, fail_get=False, fail_update=False):
        self.context = context
        self.fail_get = fail_get
        self.fail_update = fail_update
        self.updated: list = []

    async def get_context(self, session_id, user_id):
        if self.fail_get:
            raise RuntimeError("memory down")
        return self.context

    async def update_from_interaction(
        self, session_id=None, user_id=None, user_message=None, search_results=None
    ):
        if self.fail_update:
            raise RuntimeError("memory down")
        self.updated.append(
            {
                "session_id": session_id,
                "user_id": user_id,
                "user_message": user_message,
            }
        )
        return self.context


class _FakeHistory:
    def __init__(self, fail=False):
        self.fail = fail
        self.messages: list = []

    async def add_message(self, session_id, user_id, role, text):
        if self.fail:
            raise RuntimeError("history down")
        self.messages.append({"session_id": session_id, "user_id": user_id, "role": role, "text": text})


class _SessionService:
    def __init__(self):
        self.created: list = []

    async def get_session(self, app_name, user_id, session_id):
        if self.created:
            return {"id": session_id}
        return None

    async def create_session(self, app_name, user_id, session_id):
        self.created.append(session_id)


class _FakeRunner:
    """Records run_async kwargs and replays a canned event stream."""

    def __init__(self, events=(), delay=0.0, error=None):
        self.events = list(events)
        self.delay = delay
        self.error = error
        self.session_service = _SessionService()
        self.calls: list = []

    def run_async(self, user_id=None, session_id=None, new_message=None):
        if self.error:
            raise self.error
        self.calls.append({"user_id": user_id, "session_id": session_id})
        return self._generate()

    async def _generate(self):
        for event in self.events:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield event


# ─── Shared setup ─────────────────────────────────────────────────────────


def _setup(monkeypatch, runner=None, memory=None, history=None):
    client, main, fake_db, _logger = create_client(monkeypatch)
    monkeypatch.setattr(main, "_check_chat_rate_limit", lambda client_ip: True)
    monkeypatch.setattr(main, "log_user_action", lambda **kwargs: None)
    captured: dict = {}

    async def _capture(text, session_id, user_id, lead_manager=None, notify=None, utm=None):
        captured.update(
            {"text": text, "session_id": session_id, "user_id": user_id, "utm": utm}
        )

    monkeypatch.setattr(main, "capture_contact_from_message", _capture)
    monkeypatch.setattr(main, "apply_utm", lambda lead, utm: None)

    genai_module = types.ModuleType("google.genai")
    genai_types = types.SimpleNamespace(Content=_Content, Part=_Part)
    genai_module.types = genai_types
    monkeypatch.setitem(sys.modules, "google.genai", genai_module)

    if runner is not None:
        monkeypatch.setattr(main, "_get_runner", lambda: runner)
    if memory is not None:
        monkeypatch.setattr(main, "conversation_memory", memory)
    if history is not None:
        monkeypatch.setattr(main, "chat_history", history)
    return client, main, fake_db, captured


def _chat_payload(text="hello there", session_id=None, user_id=None):
    payload = {"newMessage": {"parts": [{"text": text}]}}
    if session_id is not None:
        payload["sessionId"] = session_id
    if user_id is not None:
        payload["userId"] = user_id
    return payload


# ─── _execute_agent_run (direct) ──────────────────────────────────────────


class TestExecuteAgentRun:
    def test_collects_model_text_across_events(self, monkeypatch):
        _client, main, _db, _c = _setup(monkeypatch)
        events = [
            types.SimpleNamespace(content=_Content("user", [_Part("ignored")])),
            types.SimpleNamespace(content=_Content("model", [_Part("Hello "), _Part(None)])),
            types.SimpleNamespace(content=_Content("assistant", [_Part("world")])) ,
            types.SimpleNamespace(content=None),
            types.SimpleNamespace(),
        ]
        runner = _FakeRunner(events=events)

        final_text, event_count = asyncio.run(
            main._execute_agent_run(runner, "u1", "s1", "msg", "rid")
        )

        assert final_text == "Hello world"
        assert event_count == 5

    def test_error_events_are_logged_not_raised(self, monkeypatch):
        _client, main, _db, _c = _setup(monkeypatch)
        events = [
            ErrorEvent("model exploded"),
            types.SimpleNamespace(
                content=_Content("error", [_Part("bad things")]),
                error_message="content error",
            ),
        ]
        runner = _FakeRunner(events=events)

        final_text, event_count = asyncio.run(
            main._execute_agent_run(runner, "u1", "s1", "msg", "rid")
        )

        # Neither event carries model text, so the apology fallback fires,
        # and both error branches logged without raising.
        assert final_text == "I apologize, but I couldn't generate a response. Please try again."
        assert event_count == 2

    def test_empty_stream_falls_back_to_apology(self, monkeypatch):
        _client, main, _db, _c = _setup(monkeypatch)
        runner = _FakeRunner(events=[types.SimpleNamespace(content=_Content("model", []))])

        final_text, event_count = asyncio.run(
            main._execute_agent_run(runner, "u1", "s1", "msg", "rid")
        )

        assert final_text == "I apologize, but I couldn't generate a response. Please try again."
        assert event_count == 1


# ─── POST /run ────────────────────────────────────────────────────────────


class TestRunAgent:
    def test_rate_limited_returns_429(self, monkeypatch):
        client, main, _db, _c = _setup(monkeypatch)
        monkeypatch.setattr(main, "_check_chat_rate_limit", lambda client_ip: False)

        resp = client.post("/run", json=_chat_payload())

        assert resp.status_code == 429
        assert "too fast" in resp.json()["error"]

    def test_new_session_happy_path(self, monkeypatch):
        runner = _FakeRunner(
            events=[types.SimpleNamespace(content=_Content("model", [_Part("Hi!")]))]
        )
        memory = _FakeMemory(context=_Context())
        history = _FakeHistory()
        client, _main, _db, captured = _setup(
            monkeypatch, runner=runner, memory=memory, history=history
        )

        resp = client.post("/run", json=_chat_payload("I need a home"))

        assert resp.status_code == 200
        body = resp.json()
        assert body["text"] == "Hi!"
        session_id = body["session_id"]
        # Fresh session: created, bound user id, owner cookie issued.
        assert runner.session_service.created == [session_id]
        assert runner.calls == [{"user_id": f"web_user_{session_id}", "session_id": session_id}]
        assert CHAT_OWNER_COOKIE in resp.headers.get("set-cookie", "")
        # History persisted both turns; memory updated; backstop invoked.
        assert [(m["role"], m["text"]) for m in history.messages] == [
            ("user", "I need a home"),
            ("model", "Hi!"),
        ]
        assert memory.updated[0]["session_id"] == session_id
        assert captured["text"] == "I need a home"
        assert captured["session_id"] == session_id

    def test_owner_cookie_continues_session_with_supplied_user(self, monkeypatch):
        runner = _FakeRunner(
            events=[
                types.SimpleNamespace(content=_Content("model", [_Part("one")])),
                types.SimpleNamespace(content=_Content("model", [_Part("two")])),
            ]
        )
        client, _main, _db, _c = _setup(
            monkeypatch, runner=runner, memory=_FakeMemory(context=_Context()), history=_FakeHistory()
        )

        first = client.post("/run", json=_chat_payload("first")).json()
        second = client.post(
            "/run",
            json=_chat_payload("second", session_id=first["session_id"], user_id="operator_1"),
        )

        assert second.status_code == 200
        assert second.json()["session_id"] == first["session_id"]
        # No second create_session; the supplied user id survived binding.
        assert runner.session_service.created == [first["session_id"]]
        assert runner.calls[-1]["user_id"] == "operator_1"

    def test_runner_unavailable_returns_service_error(self, monkeypatch):
        client, main, _db, _c = _setup(monkeypatch)

        def _boom():
            raise RuntimeError("ADK not initialized")

        monkeypatch.setattr(main, "_get_runner", _boom)

        resp = client.post("/run", json=_chat_payload())

        assert resp.status_code == 200
        body = resp.json()
        assert "temporarily unavailable" in body["error"]
        assert body["session_id"]

    def test_agent_timeout_returns_friendly_message(self, monkeypatch):
        runner = _FakeRunner(
            events=[types.SimpleNamespace(content=_Content("model", [_Part("slow")]))],
            delay=1.0,
        )
        client, main, _db, _c = _setup(
            monkeypatch, runner=runner, memory=_FakeMemory(context=_Context()), history=_FakeHistory()
        )
        monkeypatch.setattr(main, "AI_RUN_TIMEOUT", 0.05)

        resp = client.post("/run", json=_chat_payload())

        assert resp.status_code == 200
        assert "timed out" in resp.json()["text"]

    def test_unexpected_agent_error_returns_brain_message(self, monkeypatch):
        runner = _FakeRunner(error=RuntimeError("genai exploded"))
        client, _main, _db, _c = _setup(
            monkeypatch, runner=runner, memory=_FakeMemory(context=_Context()), history=_FakeHistory()
        )

        resp = client.post("/run", json=_chat_payload())

        assert resp.status_code == 200
        body = resp.json()
        assert "trouble connecting to my brain" in body["error"]
        assert body["session_id"]

    def test_chat_history_failure_is_swallowed(self, monkeypatch):
        runner = _FakeRunner(
            events=[types.SimpleNamespace(content=_Content("model", [_Part("Hi!")]))]
        )
        client, _main, _db, _c = _setup(
            monkeypatch,
            runner=runner,
            memory=_FakeMemory(context=_Context()),
            history=_FakeHistory(fail=True),
        )

        resp = client.post("/run", json=_chat_payload())

        assert resp.status_code == 200
        assert resp.json()["text"] == "Hi!"

    def test_context_retrieval_failure_is_swallowed(self, monkeypatch):
        runner = _FakeRunner(
            events=[types.SimpleNamespace(content=_Content("model", [_Part("Hi!")]))]
        )
        memory = _FakeMemory(context=_Context(), fail_get=True)
        client, _main, _db, _c = _setup(
            monkeypatch, runner=runner, memory=memory, history=_FakeHistory()
        )

        resp = client.post("/run", json=_chat_payload())

        assert resp.status_code == 200
        assert resp.json()["text"] == "Hi!"

    def test_context_update_failure_skips_lead_block(self, monkeypatch):
        runner = _FakeRunner(
            events=[types.SimpleNamespace(content=_Content("model", [_Part("Hi!")]))]
        )
        memory = _FakeMemory(context=_Context(bedrooms=3), fail_update=True)
        client, main, _db, _c = _setup(
            monkeypatch, runner=runner, memory=memory, history=_FakeHistory()
        )

        resp = client.post("/run", json=_chat_payload())

        assert resp.status_code == 200
        # Lead block never ran: only the manager's pre-seeded chat lead exists.
        chat_leads = [lead for lead in main.lead_manager.leads if lead.source == "chat"]
        assert len(chat_leads) == 1

    def test_existing_lead_updated_from_context(self, monkeypatch):
        runner = _FakeRunner(
            events=[types.SimpleNamespace(content=_Content("model", [_Part("Hi!")]))]
        )
        client, main, _db, _c = _setup(
            monkeypatch, runner=runner, memory=None, history=_FakeHistory()
        )
        # First message establishes the session and its owner cookie.
        first = client.post("/run", json=_chat_payload("first")).json()
        session_id = first["session_id"]
        existing = FakeLead(
            lead_id="lead-x",
            user_id=f"web_user_{session_id}",
            session_id=session_id,
            status="new",
        )
        main.lead_manager.leads.append(existing)
        memory = _FakeMemory(
            context=_Context(
                bedrooms=3,
                bathrooms=2,
                max_budget=80000,
                homes_discussed=["inv-1", "inv-2"],
                appointment_intent=True,
                financing_questions=2,
            )
        )
        monkeypatch.setattr(main, "conversation_memory", memory)

        resp = client.post(
            "/run",
            json=_chat_payload("second", session_id=session_id),
        )

        assert resp.status_code == 200
        assert existing.bedrooms == 3
        assert existing.bathrooms == 2
        assert existing.budget_max == 80000
        assert existing.homes_viewed == ["inv-1", "inv-2"]
        assert existing.appointment_requested is True
        assert existing.financing_discussed is True
        # Update, not create.
        assert len(main.lead_manager.leads) == 2  # seed lead-x + FakeLeadManager's default

    def test_new_lead_created_from_context_preferences(self, monkeypatch):
        runner = _FakeRunner(
            events=[types.SimpleNamespace(content=_Content("model", [_Part("Hi!")]))]
        )
        memory = _FakeMemory(context=_Context(bedrooms=2, max_budget=60000))
        client, main, _db, _c = _setup(
            monkeypatch, runner=runner, memory=memory, history=_FakeHistory()
        )

        resp = client.post("/run", json=_chat_payload("I want 2 bedrooms"))

        assert resp.status_code == 200
        session_id = resp.json()["session_id"]
        created = [lead for lead in main.lead_manager.leads if lead.session_id == session_id]
        assert len(created) == 1
        lead = created[0]
        assert lead.lead_id.startswith("lead_")
        assert lead.session_id == session_id
        assert lead.bedrooms == 2
        assert lead.budget_max == 60000

    def test_lead_management_failure_is_swallowed(self, monkeypatch):
        runner = _FakeRunner(
            events=[types.SimpleNamespace(content=_Content("model", [_Part("Hi!")]))]
        )
        memory = _FakeMemory(context=_Context(bedrooms=2))

        async def _boom(lead):
            raise RuntimeError("firestore down")

        client, main, _db, _c = _setup(
            monkeypatch, runner=runner, memory=memory, history=_FakeHistory()
        )
        monkeypatch.setattr(main.lead_manager, "create_lead", _boom)

        resp = client.post("/run", json=_chat_payload("I want 2 bedrooms"))

        assert resp.status_code == 200
        assert resp.json()["text"] == "Hi!"

    def test_pii_in_message_only_logs_warning(self, monkeypatch):
        runner = _FakeRunner(
            events=[types.SimpleNamespace(content=_Content("model", [_Part("Hi!")]))]
        )
        client, _main, _db, captured = _setup(
            monkeypatch,
            runner=runner,
            memory=_FakeMemory(context=_Context()),
            history=_FakeHistory(),
        )

        resp = client.post("/run", json=_chat_payload("my ssn is 123-45-6789"))

        assert resp.status_code == 200
        assert resp.json()["text"] == "Hi!"
        # The message still flowed through the backstop unredacted.
        assert captured["text"] == "my ssn is 123-45-6789"

    def test_contact_capture_backstop_failure_is_swallowed(self, monkeypatch):
        runner = _FakeRunner(
            events=[types.SimpleNamespace(content=_Content("model", [_Part("Hi!")]))]
        )
        client, main, _db, _c = _setup(
            monkeypatch,
            runner=runner,
            memory=_FakeMemory(context=_Context()),
            history=_FakeHistory(),
        )

        async def _boom(text, session_id, user_id, lead_manager=None, notify=None, utm=None):
            raise RuntimeError("capture down")

        monkeypatch.setattr(main, "capture_contact_from_message", _boom)

        resp = client.post("/run", json=_chat_payload("call me 555-867-5309"))

        assert resp.status_code == 200
        assert resp.json()["text"] == "Hi!"
