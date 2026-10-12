"""Allowlist + SSRF guards for DocuSeal webhook document_url fetches."""

from __future__ import annotations

import ipaddress
import os
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

AddrInfoResolver = Callable[..., list]


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def allowed_document_hosts(api_url: str = "", extra_hosts: str = "") -> set[str]:
    hosts: set[str] = set()
    api = (api_url or os.environ.get("DOCUSEAL_API_URL") or "").strip()
    if api:
        api_host = urlsplit(api).hostname
        if api_host:
            hosts.add(api_host.lower())
    extras = extra_hosts
    if not extras:
        extras = os.environ.get("DOCUSEAL_DOCUMENT_HOSTS") or ""
    for raw in extras.split(","):
        host = raw.strip().lower()
        if host:
            hosts.add(host)
    return hosts


def is_safe_docuseal_document_url(
    url: str,
    *,
    api_url: str = "",
    extra_hosts: str = "",
    resolver: AddrInfoResolver = socket.getaddrinfo,
) -> bool:
    """Return True only for https URLs on an allowlisted public host.

    Host must equal the host of ``DOCUSEAL_API_URL`` or appear in
    ``DOCUSEAL_DOCUMENT_HOSTS`` (comma-separated). Resolved addresses must
    not be private, loopback, link-local, or reserved.
    """
    parsed = urlsplit((url or "").strip())
    if parsed.scheme.lower() != "https":
        return False
    host = parsed.hostname
    if not host or parsed.username or parsed.password:
        return False
    if host.lower() not in allowed_document_hosts(api_url, extra_hosts):
        return False

    try:
        literal_ip = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None
    if literal_ip is not None:
        return _is_public_ip(literal_ip)

    try:
        infos = resolver(host, None)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        if not _is_public_ip(ip):
            return False
    return True
