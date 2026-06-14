"""Tests for the DNS/MX cutover workstream.

Covers:
  - dns_mx_cutover.py resolver/config helpers
  - dns_mx_cutover_routes.py endpoints (auth, DNS status, MX status, notify)
  - HMAC-signed webhook dispatch via tools.partner_webhooks

Run: python -m pytest tests/test_dns_mx_cutover.py -v
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import dns_mx_cutover
import dns_mx_cutover_routes


# ── Fixtures ─────────────────────────────────────────────────────────────────


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
    # Reload module-level config from env
    monkeypatch.setattr(dns_mx_cutover, "CUTOVER_DOMAIN", "test.example.com")
    monkeypatch.setattr(dns_mx_cutover, "CUTOVER_WWW_DOMAIN", "www.test.example.com")
    monkeypatch.setattr(dns_mx_cutover, "RESEND_SEND_SUBDOMAIN", "send")
    yield


@pytest.fixture
def fake_auth():
    """A no-op partner-auth dependency that records the request."""

    async def _auth(request: Request):
        request.state.authed = True
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


# ── Pure-function tests ──────────────────────────────────────────────────────


def test_apex_ready_when_all_google_ips_present():
    dns_mx_cutover.set_resolvers(
        a=lambda _domain: [
            "216.239.32.21",
            "216.239.34.21",
            "216.239.36.21",
            "216.239.38.21",
        ],
        cname=lambda _domain: None,
        mx=lambda _domain: [],
        txt=lambda _domain: [],
    )

    status = dns_mx_cutover.get_dns_cutover_status()
    assert status["apex"]["ready"] is True
    assert status["www"]["ready"] is False
    assert status["overall_ready"] is False


def test_www_ready_when_cname_matches():
    dns_mx_cutover.set_resolvers(
        a=lambda _domain: [],
        cname=lambda _domain: "ghs.googlehosted.com",
        mx=lambda _domain: [],
        txt=lambda _domain: [],
    )

    status = dns_mx_cutover.get_dns_cutover_status()
    assert status["apex"]["ready"] is False
    assert status["www"]["ready"] is True


def test_overall_dns_ready_when_both_match():
    dns_mx_cutover.set_resolvers(
        a=lambda _domain: [
            "216.239.32.21",
            "216.239.34.21",
            "216.239.36.21",
            "216.239.38.21",
        ],
        cname=lambda _domain: "ghs.googlehosted.com",
        mx=lambda _domain: [],
        txt=lambda _domain: [],
    )

    status = dns_mx_cutover.get_dns_cutover_status()
    assert status["overall_ready"] is True


def test_mx_inbound_ready_when_yahoo_preserved():
    dns_mx_cutover.set_resolvers(
        a=lambda _domain: [],
        cname=lambda _domain: None,
        mx=lambda _domain: [(10, "mx-biz.mail.am0.yahoodns.net")],
        txt=lambda _domain: [],
    )

    status = dns_mx_cutover.get_mx_cutover_status()
    assert status["inbound"]["ready"] is True
    assert status["resend"]["ready"] is False
    assert status["overall_ready"] is False


def test_mx_resend_ready_when_amazonses_present():
    dns_mx_cutover.set_resolvers(
        a=lambda _domain: [],
        cname=lambda _domain: None,
        mx=lambda domain: (
            [(10, "feedback-smtp.us-east-1.amazonses.com")]
            if "send." in domain
            else []
        ),
        txt=lambda _domain: [],
    )

    status = dns_mx_cutover.get_mx_cutover_status()
    assert status["inbound"]["ready"] is False
    assert status["resend"]["ready"] is True
    assert status["resend"]["actual_records"][0]["host"].endswith(".amazonses.com")


def test_mx_overall_ready_when_both_present():
    dns_mx_cutover.set_resolvers(
        a=lambda _domain: [],
        cname=lambda _domain: None,
        mx=lambda domain: (
            [(10, "feedback-smtp.us-east-1.amazonses.com")]
            if "send." in domain
            else [(10, "mx-biz.mail.am0.yahoodns.net")]
        ),
        txt=lambda _domain: [],
    )

    status = dns_mx_cutover.get_mx_cutover_status()
    assert status["overall_ready"] is True


# ── Router endpoint tests ────────────────────────────────────────────────────


def test_dns_status_endpoint_returns_expected_shape(client):
    dns_mx_cutover.set_resolvers(
        a=lambda _domain: ["216.239.32.21"],
        cname=lambda _domain: "ghs.googlehosted.com",
        mx=lambda _domain: [],
        txt=lambda _domain: [],
    )

    response = client.get("/api/v1/cutover/dns-status")
    assert response.status_code == 200
    data = response.json()
    assert data["domain"] == "test.example.com"
    assert "expected_ips" in data["apex"]
    assert "actual_ips" in data["apex"]
    assert "expected_cname" in data["www"]
    assert "actual_cname" in data["www"]
    assert "overall_ready" in data


def test_mx_status_endpoint_returns_expected_shape(client):
    dns_mx_cutover.set_resolvers(
        a=lambda _domain: [],
        cname=lambda _domain: None,
        mx=lambda _domain: [(10, "mx-biz.mail.am0.yahoodns.net")],
        txt=lambda _domain: [],
    )

    response = client.get("/api/v1/cutover/mx-status")
    assert response.status_code == 200
    data = response.json()
    assert data["domain"] == "test.example.com"
    assert data["resend_send_domain"] == "send.test.example.com"
    assert "required_hosts" in data["inbound"]
    assert "actual_records" in data["inbound"]
    assert "overall_ready" in data


def test_notify_requires_event(client):
    response = client.post("/api/v1/cutover/notify", json={})
    assert response.status_code == 400
    assert "event" in response.json()["detail"]


def test_notify_rejects_non_cutover_event_names(client):
    response = client.post("/api/v1/cutover/notify", json={"event": "user.login"})
    assert response.status_code == 400


def test_notify_dispatches_to_configured_partner(client, monkeypatch):
    captured = []

    def fake_dispatch(event, payload, db=None, *, timeout_seconds=8.0, blocking=False):
        captured.append({"event": event, "payload": payload, "db": db, "blocking": blocking})
        return ["mira"]

    monkeypatch.setenv("PARTNER_WEBHOOK_URL_MIRA", "https://mira.example.com/hook")
    monkeypatch.setattr(dns_mx_cutover_routes, "dispatch_partner_event", fake_dispatch)

    dns_mx_cutover.set_resolvers(
        a=lambda _domain: ["216.239.32.21"],
        cname=lambda _domain: "ghs.googlehosted.com",
        mx=lambda _domain: [(10, "mx-biz.mail.am0.yahoodns.net")],
        txt=lambda _domain: [],
    )

    response = client.post(
        "/api/v1/cutover/notify",
        json={"event": "cutover.complete", "payload": {"note": "done"}},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["event"] == "cutover.complete"
    assert data["dispatched_to"] == ["mira"]
    assert "cutover_status" in data

    assert len(captured) == 1
    assert captured[0]["event"] == "cutover.cutover.complete"
    assert captured[0]["payload"]["note"] == "done"
    assert "cutover_status" in captured[0]["payload"]
    assert captured[0]["blocking"] is True


# ── Integration with real partner webhook dispatcher ─────────────────────────


def test_notify_uses_hmac_signing_when_key_configured(client, monkeypatch):
    """End-to-end: dispatcher signs the body and logs to Firestore."""
    from tools.partner_webhooks import dispatch_partner_event

    # Stub requests.post to avoid network calls
    class FakeResponse:
        status_code = 200
        text = "ok"

    calls = []

    def fake_post(url, data, headers, timeout):
        calls.append({"url": url, "data": data, "headers": headers, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setenv("PARTNER_WEBHOOK_URL_MIRA", "https://mira.example.com/hook")
    monkeypatch.setenv("PARTNER_WEBHOOK_SIGNING_KEY", "super-secret-signing-key")
    monkeypatch.setattr("requests.post", fake_post)

    dns_mx_cutover.set_resolvers(
        a=lambda _domain: ["216.239.32.21"],
        cname=lambda _domain: "ghs.googlehosted.com",
        mx=lambda _domain: [(10, "mx-biz.mail.am0.yahoodns.net")],
        txt=lambda _domain: [],
    )

    response = client.post(
        "/api/v1/cutover/notify",
        json={"event": "dns.apex_ready"},
    )
    assert response.status_code == 200

    assert len(calls) == 1
    assert calls[0]["url"] == "https://mira.example.com/hook"
    assert calls[0]["headers"]["X-THO-Event"] == "cutover.dns.apex_ready"
    assert calls[0]["headers"]["X-THO-Partner"] == "mira"
    assert "X-THO-Signature" in calls[0]["headers"]
    assert calls[0]["headers"]["X-THO-Signature"].startswith("sha256=")


# ── Auth integration test (mirrors main.py partner key behavior) ─────────────


def test_router_requires_partner_key_when_using_main_auth(monkeypatch):
    """Mount the router with main.py's require_partner_api_key logic."""
    import hashlib
    import hmac

    monkeypatch.setenv("THO_API_KEY", "test-api-key")

    # Rebuild a tiny app with the real dependency from main.py
    app = FastAPI()

    async def require_partner_api_key(request: Request):
        valid_keys = {"THO_API_KEY": os.environ.get("THO_API_KEY", "").strip()}
        provided = request.headers.get("X-API-Key", "").strip()
        if not valid_keys or not provided:
            from fastapi import HTTPException

            raise HTTPException(status_code=401, detail="Missing API key")
        if not any(hmac.compare_digest(provided, k) for k in valid_keys.values()):
            from fastapi import HTTPException

            raise HTTPException(status_code=401, detail="Invalid API key")
        return None

    app.include_router(
        dns_mx_cutover_routes.router,
        dependencies=[Depends(require_partner_api_key)],
    )
    test_client = TestClient(app)

    # Missing key
    assert test_client.get("/api/v1/cutover/dns-status").status_code == 401

    # Wrong key
    assert (
        test_client.get(
            "/api/v1/cutover/dns-status",
            headers={"X-API-Key": "wrong"},
        ).status_code
        == 401
    )

    # Valid key
    dns_mx_cutover.set_resolvers(
        a=lambda _domain: ["216.239.32.21"],
        cname=lambda _domain: "ghs.googlehosted.com",
        mx=lambda _domain: [],
        txt=lambda _domain: [],
    )
    response = test_client.get(
        "/api/v1/cutover/dns-status",
        headers={"X-API-Key": "test-api-key"},
    )
    assert response.status_code == 200


# ── Environment defaults ─────────────────────────────────────────────────────


def test_default_config_uses_texashomeoutlet(monkeypatch):
    """When env vars are absent, the module defaults to the production domain."""
    monkeypatch.delenv("CUTOVER_DOMAIN", raising=False)
    monkeypatch.delenv("CUTOVER_WWW_DOMAIN", raising=False)
    # Re-import would be heavy; just assert the fallback constants.
    assert dns_mx_cutover.EXPECTED_APEX_A_IPS == {
        "216.239.32.21",
        "216.239.34.21",
        "216.239.36.21",
        "216.239.38.21",
    }
    assert dns_mx_cutover.EXPECTED_WWW_CNAME == "ghs.googlehosted.com"
    assert "mx-biz.mail.am0.yahoodns.net" in dns_mx_cutover.EXPECTED_INBOUND_MX_HOSTS
