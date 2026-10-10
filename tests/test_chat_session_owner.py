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
    CHAT_OWNER_COOKIE_TTL_SECONDS,
    PRIVATE_CHAT_CACHE_CONTROL,
    is_high_entropy_session_id,
    new_chat_session_id,
    resolve_public_session_id,
)

_TRANSCRIPT = "Please call Pat at 555-0100 about the three-bedroom home."
_SESSION_ID_RE = re.compile(r"[A-Za-z0-9_-]{43}")


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

    omitted, minted_flag = resolve_public_session_id(None)
    assert minted_flag is True
    assert is_high_entropy_session_id(omitted)
    kept, kept_flag = resolve_public_session_id("  legacy-id  ")
    assert (kept, kept_flag) == ("legacy-id", False)


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
    assert "Path=/api/chat" in header

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
