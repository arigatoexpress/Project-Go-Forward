"""SSRF guard for fetches of inventory- or request-supplied URLs.

Allows http and https only. Literal and resolved addresses must be globally
routable. Link-local, loopback, private, shared (CGNAT), reserved, and
multicast addresses are refused, including the Cloud metadata address.

The photo fetch connects to the address from that check. A later DNS answer
cannot move the TCP connection. TLS still uses the original hostname.
Redirects are not followed, and environment proxies are ignored, so neither
can resolve the name again.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
from collections.abc import Callable
from urllib.parse import urlsplit

import requests

AddrInfoResolver = Callable[..., list]

_BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata",
    "metadata.google.internal",
}
_install_lock = threading.Lock()
_pin_local = threading.local()
_ORIGINAL_GETADDRINFO = socket.getaddrinfo


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_multicast or ip.is_unspecified:
        return False
    return bool(ip.is_global)


def _host_name(host: str) -> str:
    return str(host).lower().rstrip(".").strip("[]")


def _pinning_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    """Answer the pinned photo host from the address we already validated."""
    pin = getattr(_pin_local, "pin", None)
    if pin and _host_name(host) == pin["host"]:
        ip = ipaddress.ip_address(pin["ip"])
        fam = socket.AF_INET6 if isinstance(ip, ipaddress.IPv6Address) else socket.AF_INET
        if family not in (0, fam):
            raise socket.gaierror(socket.EAI_FAMILY, "pinned address family does not match")
        sockaddr = (pin["ip"], port, 0, 0) if fam == socket.AF_INET6 else (pin["ip"], port)
        return [(fam, socket.SOCK_STREAM, proto or 6, "", sockaddr)]
    fallback = getattr(_pinning_getaddrinfo, "fallback", _ORIGINAL_GETADDRINFO)
    return fallback(host, port, family, type, proto, flags)


def _ensure_pinning_resolver() -> None:
    """Install the pin once. Other threads keep the real resolver."""
    if socket.getaddrinfo is _pinning_getaddrinfo:
        return
    with _install_lock:
        if socket.getaddrinfo is _pinning_getaddrinfo:
            return
        _pinning_getaddrinfo.fallback = socket.getaddrinfo
        socket.getaddrinfo = _pinning_getaddrinfo


def _public_addresses(url: str, resolver: AddrInfoResolver | None) -> list | None:
    """Public addresses for ``url``, or None when the URL is refused."""
    if resolver is None:
        resolver = socket.getaddrinfo
    parsed = urlsplit((url or "").strip())
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    if parsed.username or parsed.password:
        return None
    host = parsed.hostname
    if not host:
        return None
    host_name = _host_name(host)
    if (
        host_name in _BLOCKED_HOSTS
        or host_name.endswith(".local")
        or host_name.endswith(".internal")
        or host_name.isdigit()
    ):
        return None
    try:
        literal_ip = ipaddress.ip_address(host_name)
    except ValueError:
        literal_ip = None
    if literal_ip is not None:
        if isinstance(literal_ip, ipaddress.IPv6Address) and literal_ip.ipv4_mapped is not None:
            literal_ip = literal_ip.ipv4_mapped
        if not _is_public_ip(literal_ip):
            return None
        return [literal_ip]
    try:
        infos = resolver(host_name, None)
    except OSError:
        return None
    if not infos:
        return None
    found = []
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except (ValueError, IndexError, TypeError):
            return None
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if not _is_public_ip(ip):
            return None
        found.append(ip)
    return found or None


def _choose_ip(addresses: list) -> str:
    """Prefer IPv4 so an IPv4-only connect path can use a dual-stack answer."""
    for ip in addresses:
        if isinstance(ip, ipaddress.IPv4Address):
            return str(ip)
    return str(addresses[0])


def is_public_fetch_url(
    url: str,
    *,
    resolver: AddrInfoResolver | None = None,
) -> bool:
    """Return True only for an http(s) URL whose addresses are all public.

    DNS failures refuse the URL. ``resolver`` defaults to ``socket.getaddrinfo``
    and is looked up at call time so tests can stub it.
    """
    return _public_addresses(url, resolver) is not None


def _http_get(url: str, timeout: float):
    """GET without using HTTP(S)_PROXY, which would resolve the name again."""
    session = requests.Session()
    session.trust_env = False
    try:
        return session.get(url, timeout=timeout, allow_redirects=False)
    finally:
        session.close()


def fetch_public_url(
    url: str,
    *,
    timeout: float,
    resolver: AddrInfoResolver | None = None,
):
    """GET a public URL, connecting to one address from the allow check.

    Raises ValueError when the URL is refused. The response may still be a
    redirect; callers treat 3xx as a refusal instead of following it.
    """
    addresses = _public_addresses(url, resolver)
    if not addresses:
        raise ValueError("refused non-public URL")
    host = _host_name(urlsplit(url.strip()).hostname or "")
    _ensure_pinning_resolver()
    previous = getattr(_pin_local, "pin", None)
    _pin_local.pin = {"host": host, "ip": _choose_ip(addresses)}
    try:
        return _http_get(url, timeout)
    finally:
        if previous is None:
            if hasattr(_pin_local, "pin"):
                del _pin_local.pin
        else:
            _pin_local.pin = previous
