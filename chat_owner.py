"""Bind a public chat session to the browser that started it.

The page keeps the session id so it can send messages. A separate httpOnly
cookie proves this browser is the owner. The cookie value is an HMAC of the
session id. The HMAC key is derived from ADMIN_SESSION_SECRET, so rotating
that secret makes every existing cookie fail. Visitors then start a fresh
chat instead of continuing the old one. Staff can still open the old
transcript in Chat History.

Do not log the cookie value or chat transcripts.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import secrets

from fastapi.responses import JSONResponse, Response

CHAT_OWNER_COOKIE = "tho_chat_owner"
# POST /run lives outside /api/chat. The browser only sends a cookie when the
# path matches, so this has to cover both the history read and the chat send.
CHAT_OWNER_COOKIE_PATH = "/"
CHAT_OWNER_COOKIE_TTL_SECONDS = 30 * 24 * 60 * 60
PRIVATE_CHAT_CACHE_CONTROL = "private, no-store"
_OWNER_KEY_PURPOSE = b"tho-chat-owner-v1"
_SESSION_ID_BYTES = 32
# secrets.token_urlsafe(32) is 43 unpadded url-safe characters (32 bytes).
_SESSION_ID_LENGTH = 43
_SESSION_ID_ALPHABET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")


def new_chat_session_id() -> str:
    """Return a new session id with 256 bits of entropy."""
    return secrets.token_urlsafe(_SESSION_ID_BYTES)


def is_high_entropy_session_id(session_id: object) -> bool:
    """True when ``session_id`` was produced by ``new_chat_session_id``."""
    if not isinstance(session_id, str) or len(session_id) != _SESSION_ID_LENGTH:
        return False
    if any(char not in _SESSION_ID_ALPHABET for char in session_id):
        return False
    try:
        padding = "=" * (-len(session_id) % 4)
        raw = base64.urlsafe_b64decode(session_id + padding)
    except (binascii.Error, ValueError):
        return False
    return len(raw) == _SESSION_ID_BYTES


def bind_run_session(raw: object, presented_cookie: str | None) -> tuple[str, bool]:
    """Return ``(session_id, replaced)`` for a public chat send.

    Continue ``raw`` only when ``presented_cookie`` proves this browser owns
    that id. Anyone else, including a caller who only knows the id, gets a
    new high-entropy session. Callers must use the returned id for memory,
    agent history, and the stored transcript, and must send it back so the
    page can switch.
    """
    session_id = raw.strip() if isinstance(raw, str) else ""
    if session_id and chat_owner_cookie_matches(presented_cookie, session_id):
        return session_id, False
    return new_chat_session_id(), True


def _owner_key() -> bytes:
    secret = os.environ.get("ADMIN_SESSION_SECRET", "")
    if not secret:
        raise RuntimeError("ADMIN_SESSION_SECRET is required to bind chat sessions")
    return hmac.new(secret.encode("utf-8"), _OWNER_KEY_PURPOSE, hashlib.sha256).digest()


def chat_owner_token(session_id: str) -> str:
    """HMAC that proves the server issued ownership of ``session_id``."""
    digest = hmac.new(_owner_key(), session_id.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def chat_owner_cookie_matches(presented: str | None, session_id: str) -> bool:
    """True when ``presented`` is the owner cookie for ``session_id``.

    A missing cookie, a mismatched cookie, and a low-entropy id are all
    mismatches. History reads answer those with 404. A chat send starts a
    fresh session instead of continuing the named one.
    """
    if not presented or not is_high_entropy_session_id(session_id):
        return False
    expected = chat_owner_token(session_id).encode("utf-8")
    actual = presented.encode("utf-8")
    if len(actual) != len(expected):
        return False
    return hmac.compare_digest(actual, expected)


def set_chat_owner_cookie(response: Response, session_id: str, *, secure: bool) -> None:
    """Set the owner cookie. ``secure`` is off only for local HTTP development."""
    response.set_cookie(
        key=CHAT_OWNER_COOKIE,
        value=chat_owner_token(session_id),
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=CHAT_OWNER_COOKIE_TTL_SECONDS,
        path=CHAT_OWNER_COOKIE_PATH,
    )


def private_chat_json(payload: dict, status_code: int = 200) -> JSONResponse:
    """JSON that must not be stored by a shared cache or reused across browsers."""
    response = JSONResponse(payload, status_code=status_code)
    response.headers["Cache-Control"] = PRIVATE_CHAT_CACHE_CONTROL
    response.headers["Vary"] = "Cookie"
    return response
