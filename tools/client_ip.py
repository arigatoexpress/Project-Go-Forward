"""Shared client-IP extraction for Cloud Run (rightmost trusted hop).

Google's front end APPENDS the connecting client to X-Forwarded-For, so the
rightmost valid hop is the trustworthy one. Leftmost values are attacker-
controlled and must not key lockouts or per-IP limits.
"""

from __future__ import annotations

import ipaddress
import os
from typing import Any

_DEFAULT_TRUSTED_PROXY_HOPS = 1


def trusted_proxy_hops() -> int:
    raw = os.environ.get("TRUSTED_PROXY_HOPS", str(_DEFAULT_TRUSTED_PROXY_HOPS))
    try:
        hops = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_TRUSTED_PROXY_HOPS
    return hops if hops >= 1 else _DEFAULT_TRUSTED_PROXY_HOPS


def _valid_ip(value: str) -> str | None:
    candidate = (value or "").strip()
    if not candidate:
        return None
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return None
    return candidate


def get_client_ip(request: Any, *, default: str = "unknown") -> str:
    """Return the client IP, trusting the Nth X-Forwarded-For hop from the right.

    ``TRUSTED_PROXY_HOPS`` (default 1) selects that hop: 1 = last entry,
    2 = second-from-right, etc. Empty and non-IP tokens are ignored. Falls
    back to ``request.client.host``.
    """
    hops = trusted_proxy_hops()
    forwarded = ""
    try:
        headers = getattr(request, "headers", None)
        if headers is not None:
            forwarded = headers.get("x-forwarded-for", "") or ""
    except Exception:
        forwarded = ""

    parsed: list[str] = []
    for token in str(forwarded).split(","):
        ip = _valid_ip(token)
        if ip:
            parsed.append(ip)

    if parsed:
        return parsed[-min(hops, len(parsed))]

    try:
        client = getattr(request, "client", None)
        host = getattr(client, "host", None) if client is not None else None
        if host:
            return str(host)
    except Exception:
        pass
    return default
