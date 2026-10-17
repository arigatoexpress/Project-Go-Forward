"""Coverage tests (c10-b) for DNS resolver internals and route exception branches.

Covers, without any real DNS traffic:
  - dns_mx_cutover._load_dns_python_resolver() and its four inner resolvers
    (success, DNSException, and generic-exception paths) via a fake ``dns``
    package injected into sys.modules.
  - dns_mx_cutover._load_socket_fallback_resolver() and its four inner resolvers
    via a patched socket.getaddrinfo.
  - dns_mx_cutover._ensure_resolvers() both loader branches.
  - dns_mx_cutover._has_spf_record() "any SPF record" branch (no expected value).
  - dns_mx_cutover_routes exception/validation branches (500s, invalid JSON,
    non-object payload, webhook dispatch failure).
"""

from __future__ import annotations

import logging
import sys
import types

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

import dns_mx_cutover
import dns_mx_cutover_routes

# ── Fixtures (self-contained; pytest-randomly is not installed) ──────────────


@pytest.fixture(autouse=True)
def reset_resolvers():
    """Give every test a clean resolver injection surface."""
    dns_mx_cutover.set_resolvers(None, None, None, None)
    yield
    dns_mx_cutover.set_resolvers(None, None, None, None)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Default to a predictable domain for unit tests."""
    monkeypatch.setenv("CUTOVER_DOMAIN", "test.example.com")
    monkeypatch.setenv("CUTOVER_WWW_DOMAIN", "www.test.example.com")
    monkeypatch.setenv("RESEND_SEND_SUBDOMAIN", "send")
    monkeypatch.setattr(dns_mx_cutover, "CUTOVER_DOMAIN", "test.example.com")
    monkeypatch.setattr(dns_mx_cutover, "CUTOVER_WWW_DOMAIN", "www.test.example.com")
    monkeypatch.setattr(dns_mx_cutover, "RESEND_SEND_SUBDOMAIN", "send")
    yield


@pytest.fixture
def fake_auth():
    """A no-op partner-auth dependency."""

    async def _auth(request: Request):
        return None

    return _auth


@pytest.fixture
def client(fake_auth):
    """Test client with the cutover router mounted and auth stubbed."""
    app = FastAPI()
    app.include_router(
        dns_mx_cutover_routes.router,
        dependencies=[Depends(fake_auth)],
    )
    return TestClient(app)


# ── Fake dnspython package ────────────────────────────────────────────────────


class _FakeMXRdata:
    """Minimal stand-in for an MX rdata object (preference/exchange)."""

    def __init__(self, preference: int, exchange: str):
        self.preference = preference
        self.exchange = exchange


@pytest.fixture
def fake_dns(monkeypatch):
    """Inject a fake ``dns`` package so _load_dns_python_resolver binds fakes.

    ``answers`` maps (domain, rdtype) -> iterable of rdata OR an Exception
    instance to raise (use the module's DNSException for the NXDOMAIN path).
    Unknown keys raise DNSException, mirroring an unresolvable name.
    """
    dns_mod = types.ModuleType("dns")
    exception_mod = types.ModuleType("dns.exception")

    class DNSException(Exception):
        pass

    exception_mod.DNSException = DNSException
    resolver_mod = types.ModuleType("dns.resolver")
    rdatatype_mod = types.ModuleType("dns.rdatatype")

    answers: dict = {}

    def resolve(domain, rdatatype, lifetime=5):
        spec = answers.get((domain, rdatatype))
        if spec is None:
            raise DNSException(f"NXDOMAIN: {domain}")
        if isinstance(spec, Exception):
            raise spec
        return spec

    resolver_mod.resolve = resolve
    dns_mod.exception = exception_mod
    dns_mod.resolver = resolver_mod
    dns_mod.rdatatype = rdatatype_mod
    monkeypatch.setitem(sys.modules, "dns", dns_mod)
    monkeypatch.setitem(sys.modules, "dns.exception", exception_mod)
    monkeypatch.setitem(sys.modules, "dns.resolver", resolver_mod)
    monkeypatch.setitem(sys.modules, "dns.rdatatype", rdatatype_mod)
    return types.SimpleNamespace(answers=answers, DNSException=DNSException)


def _load_dnspython(fake_dns):
    """Load the dnspython-backed resolvers against the fake dns package."""
    loaded = dns_mx_cutover._load_dns_python_resolver()
    assert loaded is not None
    return loaded


# ── _load_dns_python_resolver: import guards ──────────────────────────────────


def test_load_dns_python_resolver_returns_none_when_import_fails(monkeypatch):
    monkeypatch.setitem(sys.modules, "dns.exception", None)
    monkeypatch.setitem(sys.modules, "dns.rdatatype", None)
    monkeypatch.setitem(sys.modules, "dns.resolver", None)
    assert dns_mx_cutover._load_dns_python_resolver() is None


# ── dnspython a_records (L72-80) ──────────────────────────────────────────────


def test_dnspython_a_records_success_dedup_and_sort(fake_dns):
    fake_dns.answers[("example.com", "A")] = ["216.239.32.21", "216.239.32.21", "1.2.3.4"]
    a_records, _, _, _ = _load_dnspython(fake_dns)
    assert a_records("example.com") == ["1.2.3.4", "216.239.32.21"]


def test_dnspython_a_records_dns_exception_returns_empty(fake_dns):
    a_records, _, _, _ = _load_dnspython(fake_dns)
    assert a_records("missing.example.com") == []


def test_dnspython_a_records_generic_exception_logs_warning(fake_dns, caplog):
    fake_dns.answers[("example.com", "A")] = RuntimeError("boom")
    a_records, _, _, _ = _load_dnspython(fake_dns)
    with caplog.at_level(logging.WARNING):
        assert a_records("example.com") == []
    assert "A-record lookup failed for example.com" in caplog.text


# ── dnspython cname_record (L82-92) ───────────────────────────────────────────


def test_dnspython_cname_record_success_strips_dot(fake_dns):
    fake_dns.answers[("www.example.com", "CNAME")] = ["ghs.googlehosted.com."]
    _, cname_record, _, _ = _load_dnspython(fake_dns)
    assert cname_record("www.example.com") == "ghs.googlehosted.com"


def test_dnspython_cname_record_empty_answer_returns_none(fake_dns):
    fake_dns.answers[("www.example.com", "CNAME")] = []
    _, cname_record, _, _ = _load_dnspython(fake_dns)
    assert cname_record("www.example.com") is None


def test_dnspython_cname_record_dns_exception_returns_none(fake_dns):
    _, cname_record, _, _ = _load_dnspython(fake_dns)
    assert cname_record("missing.example.com") is None


def test_dnspython_cname_record_generic_exception_logs_warning(fake_dns, caplog):
    fake_dns.answers[("www.example.com", "CNAME")] = RuntimeError("boom")
    _, cname_record, _, _ = _load_dnspython(fake_dns)
    with caplog.at_level(logging.WARNING):
        assert cname_record("www.example.com") is None
    assert "CNAME lookup failed for www.example.com" in caplog.text


# ── dnspython mx_records (L94-107) ────────────────────────────────────────────


def test_dnspython_mx_records_success_sorts_tuples(fake_dns):
    fake_dns.answers[("example.com", "MX")] = [
        _FakeMXRdata(20, "b.example.com."),
        _FakeMXRdata(10, "a.example.com."),
    ]
    _, _, mx_records, _ = _load_dnspython(fake_dns)
    assert mx_records("example.com") == [(10, "a.example.com"), (20, "b.example.com")]


def test_dnspython_mx_records_dns_exception_returns_empty(fake_dns):
    _, _, mx_records, _ = _load_dnspython(fake_dns)
    assert mx_records("missing.example.com") == []


def test_dnspython_mx_records_generic_exception_logs_warning(fake_dns, caplog):
    fake_dns.answers[("example.com", "MX")] = RuntimeError("boom")
    _, _, mx_records, _ = _load_dnspython(fake_dns)
    with caplog.at_level(logging.WARNING):
        assert mx_records("example.com") == []
    assert "MX lookup failed for example.com" in caplog.text


# ── dnspython txt_records (L109-117) ──────────────────────────────────────────


def test_dnspython_txt_records_success_strips_quotes(fake_dns):
    fake_dns.answers[("example.com", "TXT")] = ['"v=spf1 -all"', '"hello"']
    _, _, _, txt_records = _load_dnspython(fake_dns)
    assert txt_records("example.com") == ["hello", "v=spf1 -all"]


def test_dnspython_txt_records_dns_exception_returns_empty(fake_dns):
    _, _, _, txt_records = _load_dnspython(fake_dns)
    assert txt_records("missing.example.com") == []


def test_dnspython_txt_records_generic_exception_logs_warning(fake_dns, caplog):
    fake_dns.answers[("example.com", "TXT")] = RuntimeError("boom")
    _, _, _, txt_records = _load_dnspython(fake_dns)
    with caplog.at_level(logging.WARNING):
        assert txt_records("example.com") == []
    assert "TXT lookup failed for example.com" in caplog.text


# ── socket fallback resolvers (L130-156) ──────────────────────────────────────


def _addrinfo_result(ips, canonical=""):
    return [(2, 1, 6, canonical, (ip, 0)) for ip in ips]


def test_socket_fallback_a_records_success_dedup_and_sort(monkeypatch):
    monkeypatch.setattr(
        dns_mx_cutover.socket,
        "getaddrinfo",
        lambda domain, port, family=0: _addrinfo_result(["5.6.7.8", "1.2.3.4", "1.2.3.4"]),
    )
    a_records, _, _, _ = dns_mx_cutover._load_socket_fallback_resolver()
    assert a_records("example.com") == ["1.2.3.4", "5.6.7.8"]


def test_socket_fallback_a_records_exception_logs_warning(monkeypatch, caplog):
    def _boom(domain, port, family=0):
        raise OSError("no dns")

    monkeypatch.setattr(dns_mx_cutover.socket, "getaddrinfo", _boom)
    a_records, _, _, _ = dns_mx_cutover._load_socket_fallback_resolver()
    with caplog.at_level(logging.WARNING):
        assert a_records("example.com") == []
    assert "Socket A-record lookup failed for example.com" in caplog.text


def test_socket_fallback_cname_returns_canonical_when_different(monkeypatch):
    monkeypatch.setattr(
        dns_mx_cutover.socket,
        "getaddrinfo",
        lambda domain, port, family=0: _addrinfo_result(["1.2.3.4"], canonical="cdn.example.com."),
    )
    _, cname_record, _, _ = dns_mx_cutover._load_socket_fallback_resolver()
    assert cname_record("www.example.com") == "cdn.example.com"


def test_socket_fallback_cname_returns_none_when_canonical_matches_domain(monkeypatch):
    monkeypatch.setattr(
        dns_mx_cutover.socket,
        "getaddrinfo",
        lambda domain, port, family=0: _addrinfo_result(["1.2.3.4"], canonical="WWW.EXAMPLE.COM"),
    )
    _, cname_record, _, _ = dns_mx_cutover._load_socket_fallback_resolver()
    assert cname_record("www.example.com") is None


def test_socket_fallback_cname_returns_none_on_empty_answer(monkeypatch):
    monkeypatch.setattr(dns_mx_cutover.socket, "getaddrinfo", lambda domain, port, family=0: [])
    _, cname_record, _, _ = dns_mx_cutover._load_socket_fallback_resolver()
    # infos[0] raises IndexError, swallowed by the bare except -> None
    assert cname_record("www.example.com") is None


def test_socket_fallback_cname_returns_none_on_getaddrinfo_error(monkeypatch):
    def _boom(domain, port, family=0):
        raise OSError("no dns")

    monkeypatch.setattr(dns_mx_cutover.socket, "getaddrinfo", _boom)
    _, cname_record, _, _ = dns_mx_cutover._load_socket_fallback_resolver()
    assert cname_record("www.example.com") is None


def test_socket_fallback_mx_and_txt_return_empty():
    _, _, mx_records, txt_records = dns_mx_cutover._load_socket_fallback_resolver()
    assert mx_records("example.com") == []
    assert txt_records("example.com") == []


# ── _ensure_resolvers (L159-172) ──────────────────────────────────────────────


def test_ensure_resolvers_loads_dnspython_when_available(monkeypatch, caplog):
    dns_mx_cutover.set_resolvers(None, None, None, None)
    sentinel = (lambda d: ["a"], lambda d: "c", lambda d: [("m",)], lambda d: ["t"])
    monkeypatch.setattr(dns_mx_cutover, "_load_dns_python_resolver", lambda: sentinel)
    with caplog.at_level(logging.INFO):
        assert dns_mx_cutover._ensure_resolvers() == sentinel
    assert dns_mx_cutover._resolve_a is sentinel[0]
    assert "dnspython resolver" in caplog.text


def test_ensure_resolvers_falls_back_to_socket_when_dnspython_missing(monkeypatch, caplog):
    dns_mx_cutover.set_resolvers(None, None, None, None)
    sentinel = (lambda d: ["a"], lambda d: "c", lambda d: [("m",)], lambda d: ["t"])
    monkeypatch.setattr(dns_mx_cutover, "_load_dns_python_resolver", lambda: None)
    monkeypatch.setattr(dns_mx_cutover, "_load_socket_fallback_resolver", lambda: sentinel)
    with caplog.at_level(logging.WARNING):
        assert dns_mx_cutover._ensure_resolvers() == sentinel
    assert dns_mx_cutover._resolve_a is sentinel[0]
    assert "socket fallback" in caplog.text


def test_ensure_resolvers_short_circuits_when_already_initialized():
    sentinel = (lambda d: ["a"], lambda d: "c", lambda d: [("m",)], lambda d: ["t"])
    dns_mx_cutover.set_resolvers(*sentinel)
    assert dns_mx_cutover._ensure_resolvers() == sentinel


# ── _has_spf_record "any SPF" branch (L246) ───────────────────────────────────


def test_has_spf_record_accepts_any_spf_when_no_expected_value():
    dns_mx_cutover.set_resolvers(
        a=lambda _domain: [],
        cname=lambda _domain: None,
        mx=lambda _domain: [],
        txt=lambda d: ["v=spf1 -all"],
    )
    ok, actual = dns_mx_cutover._has_spf_record("example.com")
    assert ok is True
    assert actual == ["v=spf1 -all"]


def test_has_spf_record_rejects_non_spf_when_no_expected_value():
    dns_mx_cutover.set_resolvers(
        a=lambda _domain: [],
        cname=lambda _domain: None,
        mx=lambda _domain: [],
        txt=lambda d: ["some-other-txt"],
    )
    ok, _ = dns_mx_cutover._has_spf_record("example.com")
    assert ok is False


# ── Route exception branches ──────────────────────────────────────────────────


def test_dns_status_returns_500_when_status_check_raises(client, monkeypatch):
    def _boom():
        raise RuntimeError("resolver exploded")

    monkeypatch.setattr(dns_mx_cutover, "get_dns_cutover_status", _boom)
    response = client.get("/api/v1/cutover/dns-status")
    assert response.status_code == 500
    assert "DNS status check failed" in response.json()["detail"]


def test_mx_status_returns_500_when_status_check_raises(client, monkeypatch):
    def _boom():
        raise RuntimeError("resolver exploded")

    monkeypatch.setattr(dns_mx_cutover, "get_mx_cutover_status", _boom)
    response = client.get("/api/v1/cutover/mx-status")
    assert response.status_code == 500
    assert "MX status check failed" in response.json()["detail"]


def test_email_auth_status_returns_500_when_status_check_raises(client, monkeypatch):
    def _boom():
        raise RuntimeError("resolver exploded")

    monkeypatch.setattr(dns_mx_cutover, "get_email_auth_status", _boom)
    response = client.get("/api/v1/cutover/email-auth-status")
    assert response.status_code == 500
    assert "Email auth status check failed" in response.json()["detail"]


def test_notify_rejects_invalid_json_body(client):
    response = client.post(
        "/api/v1/cutover/notify",
        content=b"{not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert "Invalid JSON body" in response.json()["detail"]


def test_notify_rejects_non_object_payload(client):
    response = client.post(
        "/api/v1/cutover/notify",
        json={"event": "dns.apex_ready", "payload": ["not", "an", "object"]},
    )
    assert response.status_code == 400
    assert "payload must be an object" in response.json()["detail"]


def test_notify_returns_500_when_webhook_dispatch_fails(client, monkeypatch):
    def _boom(event, payload, db=None, *, timeout_seconds=8.0, blocking=False):
        raise RuntimeError("partner webhook down")

    monkeypatch.setattr(dns_mx_cutover_routes, "dispatch_partner_event", _boom)
    dns_mx_cutover.set_resolvers(
        a=lambda _domain: ["216.239.32.21"],
        cname=lambda _domain: "ghs.googlehosted.com",
        mx=lambda _domain: [(10, "mx-biz.mail.am0.yahoodns.net")],
        txt=lambda _domain: [],
    )

    response = client.post("/api/v1/cutover/notify", json={"event": "cutover.complete"})
    assert response.status_code == 500
    assert "Webhook dispatch failed" in response.json()["detail"]
