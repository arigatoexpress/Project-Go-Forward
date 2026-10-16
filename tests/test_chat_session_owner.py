"""Public chat history is readable only by the browser that started the session.

COD-157 / L8: GET /api/chat/session/{id} used to return the transcript to
anyone who knew the id. These tests cover the owner cookie, the 404 for a
missing or wrong cookie, and high-entropy session ids.
"""

from __future__ import annotations

import base64
import re

import pytest
from fastapi.testclient import TestClient

import main
from chat_owner import (
    CHAT_OWNER_COOKIE,
    CHAT_OWNER_COOKIE_PATH,
    CHAT_OWNER_COOKIE_TTL_SECONDS,
    PRIVATE_CHAT_CACHE_CONTROL,
    bind_run_session,
    chat_owner_cookie_matches,
    chat_owner_token,
    is_high_entropy_session_id,
    new_chat_session_id,
)

_TRANSCRIPT = "Please call Pat at 555-0100 about the three-bedroom home."
_PRIOR_DETAIL = "Pat Nguyen is buying the Tyler three-bedroom and the callback name is Pat Nguyen."
_SESSION_ID_RE = re.compile(r"[A-Za-z0-9_-]{43}")


def _cookie_path_matches(cookie_path: str, request_path: str) -> bool:
    """RFC 6265 path-match: the browser sends the cookie only when this is true."""
    if request_path == cookie_path:
        return True
    prefix = cookie_path if cookie_path.endswith("/") else f"{cookie_path}/"
    return request_path.startswith(prefix)


class _Msg:
    def __init__(self, text: str):
        self.role = "user"
        self.text = text
        self.timestamp = "2026-10-10T00:00:00+00:00"


class _Session:
    def __init__(self, session_id: str, text: str = _TRANSCRIPT):
        self.session_id = session_id
        self.user_id = "web_user"
        self.status = "active"
        self.created_at = "2026-10-10T00:00:00+00:00"
        self.updated_at = "2026-10-10T00:00:00+00:00"
        self.lead_id = None
        self.messages = [_Msg(text)]


@pytest.fixture
def client():
    with TestClient(main.app) as test_client:
        yield test_client


def _plant(monkeypatch, sessions: dict[str, _Session]) -> None:
    async def get_session(session_id: str):
        return sessions.get(session_id)

    monkeypatch.setattr(main.chat_history, "get_session", get_session)


def _decoded_token_bytes(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def test_owner_browser_can_read_its_history(client, monkeypatch):
    created = client.post("/api/chat/session")
    assert created.status_code == 200
    session_id = created.json()["session_id"]
    _plant(monkeypatch, {session_id: _Session(session_id)})

    response = client.get(f"/api/chat/session/{session_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["messages"] == [
        {
            "role": "user",
            "text": _TRANSCRIPT,
            "timestamp": "2026-10-10T00:00:00+00:00",
        }
    ]
    assert response.headers["cache-control"] == PRIVATE_CHAT_CACHE_CONTROL
    assert "cookie" in response.headers["vary"].lower()
    assert created.cookies[CHAT_OWNER_COOKIE] != session_id


def test_history_without_cookie_is_404(client, monkeypatch):
    created = client.post("/api/chat/session")
    session_id = created.json()["session_id"]
    calls = {"n": 0}

    async def get_session(session_id: str):
        calls["n"] += 1
        return _Session(session_id)

    monkeypatch.setattr(main.chat_history, "get_session", get_session)

    with TestClient(main.app) as stranger:
        response = stranger.get(f"/api/chat/session/{session_id}")
        legacy = stranger.get("/api/chat/session/anon_abc123def456")

    assert response.status_code == 404
    assert response.json() == {"success": False, "error": "Not found"}
    assert _TRANSCRIPT not in response.text
    assert calls["n"] == 0
    assert legacy.status_code == 404
    assert legacy.json() == {"success": False, "error": "Not found"}


def test_history_with_wrong_cookie_is_404(monkeypatch):
    with TestClient(main.app) as owner:
        created = owner.post("/api/chat/session")
        session_id = created.json()["session_id"]
    _plant(monkeypatch, {session_id: _Session(session_id)})

    with TestClient(main.app) as stranger:
        stranger.cookies.set(CHAT_OWNER_COOKIE, "wrong-cookie-value", path="/api/chat")
        wrong = stranger.get(f"/api/chat/session/{session_id}")
        assert wrong.status_code == 404
        assert _TRANSCRIPT not in wrong.text

        other = stranger.post("/api/chat/session")
        other_id = other.json()["session_id"]
        assert other_id != session_id
        crossed = stranger.get(f"/api/chat/session/{session_id}")
        assert crossed.status_code == 404
        assert _TRANSCRIPT not in crossed.text


def test_new_session_ids_are_high_entropy(client):
    minted = []
    for _ in range(5):
        response = client.post("/api/chat/session")
        assert response.status_code == 200
        session_id = response.json()["session_id"]
        minted.append(session_id)
        assert _SESSION_ID_RE.fullmatch(session_id)
        assert is_high_entropy_session_id(session_id)
        assert len(_decoded_token_bytes(session_id)) == 32
        assert not session_id.startswith("anon_")

    assert len(set(minted)) == len(minted)
    generated = new_chat_session_id()
    assert is_high_entropy_session_id(generated)
    assert len(_decoded_token_bytes(generated)) == 32

    omitted, replaced = bind_run_session(None, None)
    assert replaced is True
    assert is_high_entropy_session_id(omitted)
    legacy, legacy_replaced = bind_run_session("  anon_legacy  ", None)
    assert legacy_replaced is True
    assert legacy != "anon_legacy"
    assert is_high_entropy_session_id(legacy)
    owned = new_chat_session_id()
    kept, kept_replaced = bind_run_session(f"  {owned}  ", chat_owner_token(owned))
    assert (kept, kept_replaced) == (owned, False)
    stranger, stranger_replaced = bind_run_session(owned, chat_owner_token(new_chat_session_id()))
    assert stranger_replaced is True
    assert stranger != owned


def test_mint_does_not_adopt_a_caller_supplied_session(client, monkeypatch):
    victim = new_chat_session_id()
    _plant(monkeypatch, {victim: _Session(victim)})

    created = client.post(
        "/api/chat/session",
        json={"session_id": victim, "sessionId": victim},
    )
    minted = created.json()["session_id"]

    assert minted != victim
    denied = client.get(f"/api/chat/session/{victim}")
    assert denied.status_code == 404
    assert _TRANSCRIPT not in denied.text
    owned = client.get(f"/api/chat/session/{minted}")
    assert owned.status_code == 200
    assert owned.json()["messages"] == []


def test_owner_cookie_flags(client, monkeypatch):
    local = client.post("/api/chat/session")
    header = local.headers["set-cookie"]
    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    assert "Secure" not in header
    assert f"Max-Age={CHAT_OWNER_COOKIE_TTL_SECONDS}" in header
    assert f"Path={CHAT_OWNER_COOKIE_PATH}" in header
    assert _cookie_path_matches(CHAT_OWNER_COOKIE_PATH, "/run")
    assert _cookie_path_matches(CHAT_OWNER_COOKIE_PATH, "/api/chat/session")

    monkeypatch.setattr(main, "IS_LOCAL", False)
    with TestClient(main.app) as secure_client:
        hosted = secure_client.post("/api/chat/session")
    hosted_header = hosted.headers["set-cookie"]
    assert "HttpOnly" in hosted_header
    assert "Secure" in hosted_header
    assert "SameSite=lax" in hosted_header


def test_staff_history_does_not_need_the_visitor_cookie(client, monkeypatch):
    session_id = new_chat_session_id()
    _plant(monkeypatch, {session_id: _Session(session_id)})
    token = main._create_admin_token()

    response = client.get(
        f"/api/chat/history/{session_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["session"]["messages"][0]["text"] == _TRANSCRIPT
    assert CHAT_OWNER_COOKIE not in client.cookies


def test_owner_read_does_not_log_transcript_or_cookie(client, monkeypatch):
    created = client.post("/api/chat/session")
    session_id = created.json()["session_id"]
    cookie = created.cookies[CHAT_OWNER_COOKIE]
    _plant(monkeypatch, {session_id: _Session(session_id)})
    logged: list[str] = []

    def spy(message, **kwargs):
        logged.append(repr((message, kwargs)))

    monkeypatch.setattr(main.struct_logger, "info", spy)
    monkeypatch.setattr(main.struct_logger, "warning", spy)
    monkeypatch.setattr(main.struct_logger, "error", spy)

    response = client.get(f"/api/chat/session/{session_id}")

    assert response.status_code == 200
    blob = "\n".join(logged)
    assert _TRANSCRIPT not in blob
    assert cookie not in blob


class _Prefs:
    def __init__(self, leak: str = ""):
        self.bedrooms = None
        self.bathrooms = None
        self.max_budget = None
        self._leak = leak

    def to_prompt_context(self) -> str:
        return self._leak


class _RunContext:
    def __init__(self, leak: str = ""):
        self.preferences = _Prefs(leak)
        self.homes_discussed = [leak] if leak else []
        self.appointment_intent = False
        self.financing_questions = 0


class _RunRecorder:
    def __init__(self):
        self.session_service = self
        self.context_reads: list[str] = []
        self.context_updates: list[str] = []
        self.history_writes: list[str] = []
        self.runs: list[dict[str, str]] = []
        self.lead_reads: list[str] = []
        self.lead_writes: list[str] = []
        self.captures: list[str] = []
        self.transcripts: dict[str, list[str]] = {}
        self.history: dict[str, str] = {}

    async def get_session(self, *, app_name: str, user_id: str, session_id: str):
        return {"session_id": session_id} if session_id in self.history else None

    async def create_session(self, *, app_name: str, user_id: str, session_id: str):
        return {"session_id": session_id}


def _install_run_stubs(monkeypatch, recorder: _RunRecorder) -> None:
    """Stub the chat send so a test can see which session id each store receives."""

    async def get_context(session_id: str, user_id: str):
        recorder.context_reads.append(session_id)
        return _RunContext(recorder.history.get(session_id, ""))

    async def update_from_interaction(
        session_id: str, user_id: str, user_message: str, search_results=None
    ):
        recorder.context_updates.append(session_id)
        return _RunContext(recorder.history.get(session_id, ""))

    async def add_message(session_id: str, user_id: str, role: str, text: str, metadata=None):
        recorder.history_writes.append(session_id)
        recorder.transcripts.setdefault(session_id, []).append(text)

    async def execute(runner, user_id, session_id, new_message, request_id):
        parts = getattr(new_message, "parts", None) or []
        text = " ".join(getattr(part, "text", "") or "" for part in parts)
        recorder.runs.append({"session_id": session_id, "user_id": user_id, "text": text})
        prior = recorder.history.get(session_id)
        if prior:
            return f"Earlier you said: {prior}", 1
        return "Hello from a new chat.", 1

    async def get_lead(session_id: str):
        recorder.lead_reads.append(session_id)
        return None

    async def write_lead(lead):
        recorder.lead_writes.append(getattr(lead, "session_id", ""))

    async def capture(text, session_id, user_id, **kwargs):
        recorder.captures.append(session_id)

    monkeypatch.setattr(main, "_check_chat_rate_limit", lambda client_ip: True)
    monkeypatch.setattr(main.conversation_memory, "get_context", get_context)
    monkeypatch.setattr(
        main.conversation_memory, "update_from_interaction", update_from_interaction
    )
    monkeypatch.setattr(main.chat_history, "add_message", add_message)
    monkeypatch.setattr(main, "_get_runner", lambda: recorder)
    monkeypatch.setattr(main, "_execute_agent_run", execute)
    monkeypatch.setattr(main.lead_manager, "get_lead_by_session", get_lead)
    monkeypatch.setattr(main.lead_manager, "create_lead", write_lead)
    monkeypatch.setattr(main.lead_manager, "update_lead", write_lead)
    monkeypatch.setattr(main, "capture_contact_from_message", capture)
    monkeypatch.setattr(main, "log_user_action", lambda *args, **kwargs: None)


def _run_payload(session_id: str, text: str = "Please repeat what I told you earlier.") -> dict:
    return {
        "userId": f"web_user_{session_id}",
        "sessionId": session_id,
        "newMessage": {"role": "user", "parts": [{"text": text}]},
    }


def _touched(recorder: _RunRecorder) -> set[str]:
    return set(
        recorder.context_reads
        + recorder.context_updates
        + recorder.history_writes
        + recorder.lead_reads
        + recorder.lead_writes
        + recorder.captures
        + [run["session_id"] for run in recorder.runs]
    )


def test_run_without_owner_cookie_does_not_read_or_extend_victim(client, monkeypatch):
    victim = new_chat_session_id()
    recorder = _RunRecorder()
    recorder.history[victim] = _PRIOR_DETAIL
    recorder.transcripts[victim] = [_PRIOR_DETAIL]
    _install_run_stubs(monkeypatch, recorder)
    logged: list[str] = []

    def spy(message, **kwargs):
        logged.append(repr((message, kwargs)))

    monkeypatch.setattr(main.struct_logger, "info", spy)
    monkeypatch.setattr(main.struct_logger, "warning", spy)
    monkeypatch.setattr(main.struct_logger, "error", spy)

    response = client.post("/run", json=_run_payload(victim))

    assert response.status_code == 200
    body = response.json()
    minted = body["session_id"]
    assert minted != victim
    assert is_high_entropy_session_id(minted)
    assert body["text"] == "Hello from a new chat."
    assert _PRIOR_DETAIL not in response.text
    assert victim not in _touched(recorder)
    assert recorder.transcripts[victim] == [_PRIOR_DETAIL]
    assert recorder.runs[0]["session_id"] == minted
    assert recorder.runs[0]["user_id"] == f"web_user_{minted}"
    assert _PRIOR_DETAIL not in recorder.runs[0]["text"]
    cookie = response.cookies[CHAT_OWNER_COOKIE]
    assert chat_owner_cookie_matches(cookie, minted)
    assert cookie not in response.text
    blob = "\n".join(logged)
    assert _PRIOR_DETAIL not in blob
    assert cookie not in blob
    assert victim not in blob


def test_run_with_owner_cookie_continues_the_same_chat(client, monkeypatch):
    recorder = _RunRecorder()
    _install_run_stubs(monkeypatch, recorder)
    created = client.post("/api/chat/session")
    session_id = created.json()["session_id"]
    recorder.history[session_id] = _PRIOR_DETAIL
    recorder.transcripts[session_id] = [_PRIOR_DETAIL]

    response = client.post("/run", json=_run_payload(session_id, "What home was I asking about?"))

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert body["text"] == f"Earlier you said: {_PRIOR_DETAIL}"
    assert recorder.runs[0]["session_id"] == session_id
    assert recorder.runs[0]["user_id"] == f"web_user_{session_id}"
    assert session_id in recorder.context_reads
    assert session_id in recorder.context_updates
    assert recorder.history_writes.count(session_id) == 2
    assert recorder.transcripts[session_id][0] == _PRIOR_DETAIL
    assert "What home was I asking about?" in recorder.transcripts[session_id]
    cookie = response.cookies[CHAT_OWNER_COOKIE]
    assert chat_owner_cookie_matches(cookie, session_id)


def test_run_with_another_sessions_cookie_starts_fresh(monkeypatch):
    victim = new_chat_session_id()
    recorder = _RunRecorder()
    recorder.history[victim] = _PRIOR_DETAIL
    recorder.transcripts[victim] = [_PRIOR_DETAIL]
    _install_run_stubs(monkeypatch, recorder)

    with TestClient(main.app) as owner:
        created = owner.post("/api/chat/session")
        owned = created.json()["session_id"]
        owner_cookie = created.cookies[CHAT_OWNER_COOKIE]
    recorder.history[owned] = "owner private note about a different home"
    recorder.transcripts[owned] = ["owner private note about a different home"]

    with TestClient(main.app) as stranger:
        stranger.cookies.set(CHAT_OWNER_COOKIE, owner_cookie, path="/")
        response = stranger.post("/run", json=_run_payload(victim))

    assert response.status_code == 200
    minted = response.json()["session_id"]
    assert minted not in {victim, owned}
    assert is_high_entropy_session_id(minted)
    assert _PRIOR_DETAIL not in response.text
    assert "owner private note" not in response.text
    assert victim not in _touched(recorder)
    assert owned not in _touched(recorder)
    assert recorder.transcripts[victim] == [_PRIOR_DETAIL]
    assert recorder.transcripts[owned] == ["owner private note about a different home"]


def test_legacy_session_id_starts_a_fresh_chat(client, monkeypatch):
    recorder = _RunRecorder()
    recorder.history["anon_abc123"] = _PRIOR_DETAIL
    recorder.transcripts["anon_abc123"] = [_PRIOR_DETAIL]
    _install_run_stubs(monkeypatch, recorder)

    response = client.post("/run", json=_run_payload("anon_abc123"))

    assert response.status_code == 200
    body = response.json()
    assert "error" not in body
    assert body["text"] == "Hello from a new chat."
    assert is_high_entropy_session_id(body["session_id"])
    assert body["session_id"] != "anon_abc123"
    assert "anon_abc123" not in _touched(recorder)
    assert recorder.transcripts["anon_abc123"] == [_PRIOR_DETAIL]


def test_rotating_admin_session_secret_starts_a_fresh_chat(client, monkeypatch):
    recorder = _RunRecorder()
    _install_run_stubs(monkeypatch, recorder)
    created = client.post("/api/chat/session")
    session_id = created.json()["session_id"]
    recorder.history[session_id] = _PRIOR_DETAIL
    recorder.transcripts[session_id] = [_PRIOR_DETAIL]
    monkeypatch.setenv("ADMIN_SESSION_SECRET", "rotated-independent-session-signing-key")

    response = client.post("/run", json=_run_payload(session_id))

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] != session_id
    assert is_high_entropy_session_id(body["session_id"])
    assert _PRIOR_DETAIL not in response.text
    assert session_id not in _touched(recorder)
    assert recorder.transcripts[session_id] == [_PRIOR_DETAIL]
    assert chat_owner_cookie_matches(response.cookies[CHAT_OWNER_COOKIE], body["session_id"])
