"""Shared double-submit CSRF protection for cookie-authenticated admin routes."""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request, Response

CSRF_COOKIE_NAME = "tho_csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def create_csrf_token() -> str:
    """Return a high-entropy token suitable for the double-submit cookie flow."""
    return secrets.token_hex(32)


def verify_request_csrf(request: Request) -> bool:
    """Validate CSRF for a request that has already passed admin authentication.

    Safe methods do not mutate state. Explicit bearer/custom-header authentication
    is exempt because browsers cannot attach those headers cross-site without a
    successful CORS preflight. Cookie-authenticated mutations must present a
    matching readable cookie and custom header.
    """
    if request.method in _SAFE_METHODS:
        return True
    if request.headers.get("X-Admin-Token", "").strip():
        return True
    authorization = request.headers.get("Authorization", "").strip()
    if authorization.lower().startswith("bearer "):
        return True
    csrf_cookie = request.cookies.get(CSRF_COOKIE_NAME, "")
    csrf_header = request.headers.get(CSRF_HEADER_NAME, "")
    return bool(csrf_cookie and csrf_header and secrets.compare_digest(csrf_cookie, csrf_header))


def require_request_csrf(request: Request) -> None:
    """Raise a consistent 403 when a cookie-authenticated mutation lacks CSRF."""
    if not verify_request_csrf(request):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")


def require_cookie_csrf(request: Request) -> None:
    """Require strict double-submit CSRF with no bearer/header exemption.

    Owner-sensitive routes use this after authenticating an exact passkey
    session. A bearer or shared-admin header must never weaken that proof.
    """
    csrf_cookie = request.cookies.get(CSRF_COOKIE_NAME, "")
    csrf_header = request.headers.get(CSRF_HEADER_NAME, "")
    if not (csrf_cookie and csrf_header and secrets.compare_digest(csrf_cookie, csrf_header)):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")


def set_csrf_cookie(
    response: Response,
    token: str,
    *,
    secure: bool,
    max_age: int,
) -> None:
    """Attach the readable half of the double-submit token pair."""
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        httponly=False,
        secure=secure,
        samesite="strict",
        max_age=max_age,
        path="/",
    )
