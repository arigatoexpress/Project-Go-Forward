"""Bind a public chat session to the browser that started it.

The page keeps the session id so it can send messages. A separate httpOnly
cookie proves this browser is the owner. The cookie value is an HMAC of the
session id under the existing server session secret, so any instance can
check it without a new secret or a Firestore field.

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
CHAT_OWNER_COOKIE_PATH = "/api/chat"
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


def resolve_public_session_id(raw: object) -> tuple[str, bool]:
    """Return ``(session_id, minted)``.

    A caller-supplied id is kept so an in-flight chat can still send messages.
    Only a missing id is replaced, and the replacement is high-entropy.
    """
    if isinstance(raw, str):
        session_id = raw.strip()
        if session_id:
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
    mismatches. Callers should answer those with 404.
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
