"""Signed-cookie session manager for admin auth.

Uses itsdangerous URLSafeTimedSerializer for tamper-resistant cookies.
"""

from __future__ import annotations

import os
import secrets
from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

DEFAULT_SESSION_TTL = 4 * 60 * 60  # 4 hours
DEFAULT_CHALLENGE_TTL = 5 * 60  # 5 minutes
PASSKEY_COOKIE_NAME = "tho_passkey_session"
# Historical builds briefly used ``tho_admin_session`` for passkey auth,
# while main.py verifies ``tho_passkey_session``. Keep one canonical cookie
# name so successful WebAuthn logins are actually honored by admin routes.
SESSION_COOKIE_NAME = PASSKEY_COOKIE_NAME


def _b64url_encode(data: bytes) -> str:
    return urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    padding = "=" * (-len(s) % 4)
    return urlsafe_b64decode(s + padding)


class SessionManager:
    """Signs / verifies session payloads using a server-side secret."""

    def __init__(
        self,
        secret_key: str | None = None,
        *,
        session_ttl: int = DEFAULT_SESSION_TTL,
        challenge_ttl: int = DEFAULT_CHALLENGE_TTL,
        salt: str = "tho-admin-session-v1",
    ) -> None:
        secret = secret_key or os.environ.get("ADMIN_SESSION_SECRET")
        if not secret:
            # Dev-only fallback. Real deploys must set ADMIN_SESSION_SECRET.
            secret = secrets.token_urlsafe(32)
        self._serializer = URLSafeTimedSerializer(secret, salt=salt)
        self._challenge_serializer = URLSafeTimedSerializer(secret, salt=f"{salt}-challenge")
        self.session_ttl = session_ttl
        self.challenge_ttl = challenge_ttl

    # ------------------- Session cookie -------------------

    def issue_session(self, user_id: str, **extra: Any) -> str:
        payload = {"user_id": user_id, **extra}
        return self._serializer.dumps(payload)

    def verify_session(self, token: str | None) -> dict | None:
        if not token:
            return None
        try:
            payload = self._serializer.loads(token, max_age=self.session_ttl)
        except (BadSignature, SignatureExpired):
            return None
        if not isinstance(payload, dict) or "user_id" not in payload:
            return None
        return payload

    # ------------------- Per-flow challenge -------------------

    @staticmethod
    def new_challenge_bytes() -> bytes:
        return secrets.token_bytes(32)

    def wrap_challenge(self, challenge: bytes, *, flow: str) -> str:
        return self._challenge_serializer.dumps(
            {"challenge_b64": _b64url_encode(challenge), "flow": flow}
        )

    def unwrap_challenge(self, handle: str | None, *, flow: str) -> bytes | None:
        if not handle:
            return None
        try:
            payload = self._challenge_serializer.loads(handle, max_age=self.challenge_ttl)
        except (BadSignature, SignatureExpired):
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("flow") != flow:
            return None
        try:
            return _b64url_decode(payload["challenge_b64"])
        except Exception:
            return None
