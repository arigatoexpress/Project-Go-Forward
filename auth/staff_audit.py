"""Stdout JSON audit for staff sign-in, passkeys, and admin actions.

Cloud Logging already collects Cloud Run stdout. Each line is one JSON object
with ``lane="pgf"`` and ``service="staff-admin"`` so it can be filtered in
the ``tho-ai-agent`` ``_Default`` bucket. The line never includes email codes,
PINs, session tokens, public keys, credential ids, or request bodies.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

_ALLOWED_SEVERITIES = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_DROP_KEY_PARTS = (
    "attestation",
    "authenticator",
    "challenge",
    "client_data",
    "code",
    "cookie",
    "credential",
    "csrf",
    "password",
    "pin",
    "public_key",
    "secret",
    "session",
    "signature",
    "token",
)
_LONG_SEGMENT = re.compile(r"^[A-Za-z0-9_-]{16,}$")
_EXPLICIT_AUTH_PREFIXES = (
    "/api/admin/check",
    "/api/admin/email-code/",
    "/api/admin/logout",
    "/api/admin/passkey/login",
    "/api/admin/passkey/logout",
    "/api/admin/passkey/register",
    "/api/admin/passkey/status",
    "/api/admin/passkey/whoami",
    "/api/admin/sign-in/options",
    "/api/admin/verify",
)
_STAFF_PAGES = {
    "/admin",
    "/ad-studio",
    "/analytics",
    "/chat-history",
    "/copilot",
    "/crm",
    "/documents",
    "/getting-started",
    "/health",
    "/login",
    "/manage-inventory",
    "/passkeys",
    "/photos",
    "/staff",
    "/staff-home",
    "/studio",
    "/system",
    "/team",
}
_STAFF_ACTION_PREFIXES = (
    "/api/admin/",
    "/api/crm",
    "/api/deals",
    "/api/documents",
    "/api/inventory",
    "/api/leads",
    "/api/marketing",
)


def redact_staff_path(path: str) -> str:
    """Drop query strings and long opaque path segments."""
    raw = str(path or "").split("?", 1)[0].split("#", 1)[0]
    if not raw.startswith("/"):
        raw = f"/{raw}" if raw else "/"
    parts: list[str] = []
    for part in raw.split("/"):
        if part and ("@" in part or _LONG_SEGMENT.fullmatch(part)):
            parts.append("{redacted}")
        else:
            parts.append(part)
    redacted = "/".join(parts) or "/"
    return re.sub(r"/{2,}", "/", redacted)


def _drop_field(key: str) -> bool:
    normalized = key.strip().lower()
    if not normalized or normalized in {"message", "severity", "lane", "service", "event"}:
        return True
    return any(part in normalized for part in _DROP_KEY_PARTS)


def log_staff_action(
    event: str,
    *,
    severity: str = "INFO",
    message: str,
    **fields: Any,
) -> None:
    """Write one staff-admin audit line to stdout. Failures are ignored."""
    try:
        level = str(severity or "INFO").upper()
        if level not in _ALLOWED_SEVERITIES:
            level = "INFO"
        payload: dict[str, Any] = {
            "severity": level,
            "message": " ".join(str(message or "").split())[:200],
            "lane": "pgf",
            "service": "staff-admin",
            "event": " ".join(str(event or "").split())[:80],
        }
        for key, value in fields.items():
            if value is None or _drop_field(str(key)):
                continue
            name = str(key).strip().lower()
            if name == "path":
                payload["path"] = redact_staff_path(str(value))[:200]
                continue
            if isinstance(value, bool | int | float):
                payload[name] = value
                continue
            if isinstance(value, str):
                payload[name] = " ".join(value.split())[:200]
        sys.stdout.write(json.dumps(payload, separators=(",", ":"), ensure_ascii=True) + "\n")
        sys.stdout.flush()
    except Exception:
        return


def staff_request_event(method: str, path: str) -> tuple[str, str] | None:
    """Return ``(event, redacted path)`` for a cheap page or admin hook.

    Sign-in and passkey ceremonies are logged by their own handlers. This
    classifier skips those paths so a request is not audited twice.
    """
    redacted = redact_staff_path(path).rstrip("/") or "/"
    if any(redacted.startswith(prefix.rstrip("/")) for prefix in _EXPLICIT_AUTH_PREFIXES):
        return None
    upper = str(method or "").upper()
    if upper in {"GET", "HEAD"} and redacted in _STAFF_PAGES:
        return "staff.page.view", redacted
    if redacted.startswith("/api/admin/") or redacted.startswith("/api/admin"):
        if upper in {"GET", "HEAD"}:
            return "staff.feature.use", redacted
        if upper in {"POST", "PUT", "PATCH", "DELETE"}:
            return "staff.action", redacted
        return None
    if upper in {"POST", "PUT", "PATCH", "DELETE"} and redacted.startswith(_STAFF_ACTION_PREFIXES):
        return "staff.action", redacted
    return None
