"""SSRF guard for fetches of inventory- or request-supplied URLs.

Allows http and https only. Literal and resolved addresses must be globally
routable. Link-local, loopback, private, shared (CGNAT), reserved, and
multicast addresses are refused, including the Cloud metadata address.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

AddrInfoResolver = Callable[..., list]

_BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata",
    "metadata.google.internal",
}


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_multicast or ip.is_unspecified:
        return False
    return bool(ip.is_global)


def is_public_fetch_url(
    url: str,
    *,
    resolver: AddrInfoResolver | None = None,
) -> bool:
    """Return True only for an http(s) URL whose addresses are all public.

    DNS failures refuse the URL. ``resolver`` defaults to ``socket.getaddrinfo``
    and is looked up at call time so tests can stub it.
    """
    if resolver is None:
        resolver = socket.getaddrinfo
    parsed = urlsplit((url or "").strip())
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    if parsed.username or parsed.password:
        return False
    host = parsed.hostname
    if not host:
        return False
    host_name = host.lower().rstrip(".")
    if (
        host_name in _BLOCKED_HOSTS
        or host_name.endswith(".local")
        or host_name.endswith(".internal")
        or host_name.isdigit()
    ):
        return False
    try:
        literal_ip = ipaddress.ip_address(host_name)
    except ValueError:
        literal_ip = None
    if literal_ip is not None:
        return _is_public_ip(literal_ip)
    try:
        infos = resolver(host_name, None)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except (ValueError, IndexError, TypeError):
            return False
        if not _is_public_ip(ip):
            return False
    return True
