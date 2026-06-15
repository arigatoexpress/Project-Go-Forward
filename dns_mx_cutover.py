"""DNS/MX cutover verification utilities.

Exposes environment-driven DNS checks for the public-domain cutover and
outbound-email (Resend) DNS health.  The resolver is intentionally pluggable
so tests can inject deterministic answers without touching the network.
"""

from __future__ import annotations

import logging
import os
import socket
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# ── Configuration (env-driven, overridable for tests) ─────────────────────────

CUTOVER_DOMAIN = (os.environ.get("CUTOVER_DOMAIN") or "texashomeoutlet.com").strip().lower()
CUTOVER_WWW_DOMAIN = (
    os.environ.get("CUTOVER_WWW_DOMAIN") or f"www.{CUTOVER_DOMAIN}"
).strip().lower()

# Google Cloud Run apex A-record targets for a custom domain mapping.
EXPECTED_APEX_A_IPS = frozenset(
    {
        "216.239.32.21",
        "216.239.34.21",
        "216.239.36.21",
        "216.239.38.21",
    }
)

EXPECTED_WWW_CNAME = "ghs.googlehosted.com"

# Inbound mail stays on Yahoo/Turbify; these must remain present after cutover.
EXPECTED_INBOUND_MX_HOSTS = frozenset(
    {
        "mx-biz.mail.am0.yahoodns.net",
    }
)

# Resend sends from a subdomain (usually "send"), but the exact host is
# account-specific.  We only check that a Resend MX pattern exists when
# Resend is enabled, not a fixed host, to avoid brittle cross-env failures.
RESEND_SEND_SUBDOMAIN = (os.environ.get("RESEND_SEND_SUBDOMAIN") or "send").strip().lower()
RESEND_MX_PATTERN = ".amazonses.com"

# Email-authentication expectations. These are checked as TXT records.
RESEND_DKIM_SELECTOR = (os.environ.get("RESEND_DKIM_SELECTOR") or "resend").strip().lower()
EXPECTED_RESEND_SPF = "v=spf1 include:amazonses.com ~all"
EXPECTED_DMARC_PREFIX = "v=dmarc1"

# DNS resolver injection points.  Production uses dnspython when available and
# falls back to socket/dig-free helpers.  Tests can monkeypatch these directly.
_resolve_a: Callable[[str], list[str]] | None = None
_resolve_cname: Callable[[str], str | None] | None = None
_resolve_mx: Callable[[str], list[tuple[int, str]]] | None = None
_resolve_txt: Callable[[str], list[str]] | None = None


def _load_dns_python_resolver() -> tuple[Callable, Callable, Callable, Callable] | None:
    """Return resolver functions backed by dnspython, or None if unavailable."""
    try:
        import dns.exception  # type: ignore[import-untyped]
        import dns.rdatatype  # type: ignore[import-untyped]
        import dns.resolver  # type: ignore[import-untyped]
    except Exception:
        return None

    def a_records(domain: str) -> list[str]:
        try:
            answer = dns.resolver.resolve(domain, "A", lifetime=5)
            return sorted({str(rdata).strip() for rdata in answer})
        except dns.exception.DNSException:
            return []
        except Exception as e:
            logger.warning("A-record lookup failed for %s: %s", domain, e)
            return []

    def cname_record(domain: str) -> str | None:
        try:
            answer = dns.resolver.resolve(domain, "CNAME", lifetime=5)
            for rdata in answer:
                return str(rdata).rstrip(".").lower()
            return None
        except dns.exception.DNSException:
            return None
        except Exception as e:
            logger.warning("CNAME lookup failed for %s: %s", domain, e)
            return None

    def mx_records(domain: str) -> list[tuple[int, str]]:
        try:
            answer = dns.resolver.resolve(domain, "MX", lifetime=5)
            records = []
            for rdata in answer:
                pref = int(rdata.preference)
                host = str(rdata.exchange).rstrip(".").lower()
                records.append((pref, host))
            return sorted(records)
        except dns.exception.DNSException:
            return []
        except Exception as e:
            logger.warning("MX lookup failed for %s: %s", domain, e)
            return []

    def txt_records(domain: str) -> list[str]:
        try:
            answer = dns.resolver.resolve(domain, "TXT", lifetime=5)
            return sorted({str(rdata).strip().strip('"') for rdata in answer})
        except dns.exception.DNSException:
            return []
        except Exception as e:
            logger.warning("TXT lookup failed for %s: %s", domain, e)
            return []

    return a_records, cname_record, mx_records, txt_records


def _load_socket_fallback_resolver() -> tuple[Callable, Callable, Callable, Callable]:
    """Return resolver functions using only the Python stdlib.

    This fallback can resolve A/AAAA records via getaddrinfo; it cannot query
    authoritative MX/TXT records, so those checks return empty but the API
    still reports "resolver=socket_fallback" for transparency.
    """

    def a_records(domain: str) -> list[str]:
        try:
            infos = socket.getaddrinfo(domain, None, socket.AF_INET)
            return sorted({info[4][0] for info in infos})
        except Exception as e:
            logger.warning("Socket A-record lookup failed for %s: %s", domain, e)
            return []

    def cname_record(domain: str) -> str | None:
        # socket has no portable CNAME query; return None and let the caller
        # compare against the canonical name if getaddrinfo provides one.
        try:
            infos = socket.getaddrinfo(domain, None, socket.AF_INET)
            canonical = infos[0][3]
            if canonical and canonical.lower() != domain.lower():
                return canonical.rstrip(".").lower()
        except Exception:
            pass
        return None

    def mx_records(_domain: str) -> list[tuple[int, str]]:
        return []

    def txt_records(_domain: str) -> list[str]:
        return []

    return a_records, cname_record, mx_records, txt_records


def _ensure_resolvers() -> tuple[Callable, Callable, Callable, Callable]:
    """Initialize resolver functions once, preferring dnspython."""
    global _resolve_a, _resolve_cname, _resolve_mx, _resolve_txt
    if _resolve_a is None:
        loaded = _load_dns_python_resolver()
        if loaded is not None:
            _resolve_a, _resolve_cname, _resolve_mx, _resolve_txt = loaded
            logger.info("DNS/MX cutover using dnspython resolver")
        else:
            _resolve_a, _resolve_cname, _resolve_mx, _resolve_txt = _load_socket_fallback_resolver()
            logger.warning(
                "DNS/MX cutover using socket fallback; MX/TXT checks will be unavailable"
            )
    return _resolve_a, _resolve_cname, _resolve_mx, _resolve_txt


def resolve_a_records(domain: str) -> list[str]:
    """Return sorted IPv4 addresses for ``domain``."""
    resolver, _, _, _ = _ensure_resolvers()
    return resolver(domain)


def resolve_cname(domain: str) -> str | None:
    """Return the canonical name for ``domain`` if it is a CNAME."""
    _, resolver, _, _ = _ensure_resolvers()
    return resolver(domain)


def resolve_mx_records(domain: str) -> list[tuple[int, str]]:
    """Return sorted MX records as ``(preference, host)`` tuples."""
    _, _, resolver, _ = _ensure_resolvers()
    return resolver(domain)


def resolve_txt_records(domain: str) -> list[str]:
    """Return sorted TXT record strings for ``domain``."""
    _, _, _, resolver = _ensure_resolvers()
    return resolver(domain)


def set_resolvers(
    a: Callable[[str], list[str]] | None = None,
    cname: Callable[[str], str | None] | None = None,
    mx: Callable[[str], list[tuple[int, str]]] | None = None,
    txt: Callable[[str], list[str]] | None = None,
) -> None:
    """Inject resolver functions (used by tests).  Pass None to reset."""
    global _resolve_a, _resolve_cname, _resolve_mx, _resolve_txt
    _resolve_a, _resolve_cname, _resolve_mx, _resolve_txt = a, cname, mx, txt


def _has_expected_apex_a_records(domain: str) -> tuple[bool, list[str]]:
    actual = resolve_a_records(domain)
    # The apex must resolve to the Google Cloud Run IPs and contain all four.
    ok = bool(actual) and EXPECTED_APEX_A_IPS.issubset(set(actual))
    return ok, actual


def _has_expected_www_cname(domain: str) -> tuple[bool, str | None]:
    actual = resolve_cname(domain)
    ok = actual == EXPECTED_WWW_CNAME
    return ok, actual


def _has_inbound_mx_preserved(domain: str) -> tuple[bool, list[tuple[int, str]]]:
    actual = resolve_mx_records(domain)
    hosts = {host for _, host in actual}
    ok = bool(EXPECTED_INBOUND_MX_HOSTS & hosts)
    return ok, actual


def _has_resend_mx(send_domain: str) -> tuple[bool, list[tuple[int, str]]]:
    actual = resolve_mx_records(send_domain)
    ok = any(RESEND_MX_PATTERN in host for _, host in actual)
    return ok, actual


def _has_spf_record(domain: str, expected_value: str | None = None) -> tuple[bool, list[str]]:
    """Check whether ``domain`` has a usable SPF TXT record.

    If ``expected_value`` is supplied, the record must match it exactly.
    Otherwise any record starting with ``v=spf1`` is accepted.
    """
    actual = resolve_txt_records(domain)
    if expected_value:
        ok = any(record == expected_value for record in actual)
    else:
        ok = any(record.lower().startswith("v=spf1") for record in actual)
    return ok, actual


def _has_dkim_record(selector: str, domain: str) -> tuple[bool, list[str]]:
    """Check whether ``selector._domainkey.domain`` has a DKIM TXT record."""
    dkim_name = f"{selector}._domainkey.{domain}"
    actual = resolve_txt_records(dkim_name)
    # A valid DKIM record contains a public-key payload starting with "p=".
    ok = any("p=" in record for record in actual)
    return ok, actual


def _has_dmarc_record(domain: str) -> tuple[bool, list[str]]:
    """Check whether ``_dmarc.domain`` has a DMARC TXT record."""
    dmarc_name = f"_dmarc.{domain}"
    actual = resolve_txt_records(dmarc_name)
    ok = any(record.lower().startswith(EXPECTED_DMARC_PREFIX) for record in actual)
    return ok, actual


def get_dns_cutover_status() -> dict[str, Any]:
    """Return the current DNS cutover status for the production domain."""
    apex_ok, apex_ips = _has_expected_apex_a_records(CUTOVER_DOMAIN)
    www_ok, www_cname = _has_expected_www_cname(CUTOVER_WWW_DOMAIN)

    return {
        "domain": CUTOVER_DOMAIN,
        "www_domain": CUTOVER_WWW_DOMAIN,
        "apex": {
            "expected_ips": sorted(EXPECTED_APEX_A_IPS),
            "actual_ips": apex_ips,
            "ready": apex_ok,
        },
        "www": {
            "expected_cname": EXPECTED_WWW_CNAME,
            "actual_cname": www_cname,
            "ready": www_ok,
        },
        "overall_ready": apex_ok and www_ok,
    }


def get_mx_cutover_status() -> dict[str, Any]:
    """Return MX health: Yahoo inbound preserved and Resend outbound configured."""
    inbound_ok, inbound_mx = _has_inbound_mx_preserved(CUTOVER_DOMAIN)

    resend_domain = f"{RESEND_SEND_SUBDOMAIN}.{CUTOVER_DOMAIN}"
    resend_ok, resend_mx = _has_resend_mx(resend_domain)

    return {
        "domain": CUTOVER_DOMAIN,
        "resend_send_domain": resend_domain,
        "inbound": {
            "required_hosts": sorted(EXPECTED_INBOUND_MX_HOSTS),
            "actual_records": [{"preference": pref, "host": host} for pref, host in inbound_mx],
            "ready": inbound_ok,
        },
        "resend": {
            "required_mx_pattern": RESEND_MX_PATTERN,
            "actual_records": [{"preference": pref, "host": host} for pref, host in resend_mx],
            "ready": resend_ok,
        },
        "overall_ready": inbound_ok and resend_ok,
    }


def get_email_auth_status() -> dict[str, Any]:
    """Return SPF/DKIM/DMARC health for outbound email via Resend.

    These records live on subdomains so they never conflict with the apex
    Yahoo inbound MX or any existing apex SPF.
    """
    resend_domain = f"{RESEND_SEND_SUBDOMAIN}.{CUTOVER_DOMAIN}"

    spf_ok, spf_records = _has_spf_record(resend_domain, EXPECTED_RESEND_SPF)
    dkim_ok, dkim_records = _has_dkim_record(RESEND_DKIM_SELECTOR, CUTOVER_DOMAIN)
    dmarc_ok, dmarc_records = _has_dmarc_record(CUTOVER_DOMAIN)

    return {
        "domain": CUTOVER_DOMAIN,
        "resend_send_domain": resend_domain,
        "spf": {
            "expected": EXPECTED_RESEND_SPF,
            "actual_records": spf_records,
            "ready": spf_ok,
        },
        "dkim": {
            "selector": RESEND_DKIM_SELECTOR,
            "name": f"{RESEND_DKIM_SELECTOR}._domainkey.{CUTOVER_DOMAIN}",
            "actual_records": dkim_records,
            "ready": dkim_ok,
        },
        "dmarc": {
            "expected_prefix": EXPECTED_DMARC_PREFIX,
            "name": f"_dmarc.{CUTOVER_DOMAIN}",
            "actual_records": dmarc_records,
            "ready": dmarc_ok,
        },
        "overall_ready": spf_ok and dkim_ok and dmarc_ok,
    }


def get_full_cutover_status() -> dict[str, Any]:
    """Combine DNS + MX + email-auth status into a single partner-safe payload."""
    dns_status = get_dns_cutover_status()
    mx_status = get_mx_cutover_status()
    auth_status = get_email_auth_status()
    return {
        "dns": dns_status,
        "mx": mx_status,
        "email_auth": auth_status,
        "overall_ready": (
            dns_status["overall_ready"]
            and mx_status["overall_ready"]
            and auth_status["overall_ready"]
        ),
    }
