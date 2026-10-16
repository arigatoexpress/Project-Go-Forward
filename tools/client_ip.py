"""Shared client-IP extraction with an explicitly verified proxy-hop count.

The correct X-Forwarded-For position depends on ingress topology. A single
appended client uses one hop; a client followed by a load-balancer IP uses two.
Validate candidate ingress before configuring TRUSTED_PROXY_HOPS or promotion.
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
    2 = second-from-right, etc. Malformed or short chains fall back to
    ``request.client.host``; never shift to an attacker-controlled earlier hop.
    """
    hops = trusted_proxy_hops()
    forwarded = ""
    try:
        headers = getattr(request, "headers", None)
        if headers is not None:
            forwarded = headers.get("x-forwarded-for", "") or ""
    except Exception:
        forwarded = ""

    tokens = str(forwarded).split(",")
    if len(tokens) >= hops:
        selected = _valid_ip(tokens[-hops])
        if selected:
            return selected

    try:
        client = getattr(request, "client", None)
        host = getattr(client, "host", None) if client is not None else None
        if host:
            return str(host)
    except Exception:
        pass
    return default
