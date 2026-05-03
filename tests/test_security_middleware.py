"""Tests for tightened CSP headers and per-route rate limits.

Run: python -m pytest tests/test_security_middleware.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_api_v1 import create_client


class TestCSPHeaders:
    def test_script_src_has_no_unsafe_inline(self, monkeypatch):
        """script-src must not contain 'unsafe-inline' after tightening."""
        client, _main, _db, _logger = create_client(monkeypatch)
        r = client.get("/healthz")
        csp = r.headers.get("content-security-policy", "")
        for directive in csp.split(";"):
            if "script-src" in directive:
                assert "'unsafe-inline'" not in directive, (
                    f"'unsafe-inline' still present in script-src: {directive.strip()}"
                )
                return
        assert False, f"script-src directive not found in CSP: {csp}"

    def test_style_src_retains_unsafe_inline(self, monkeypatch):
        """style-src keeps 'unsafe-inline' for runtime-injected stylesheets."""
        client, _main, _db, _logger = create_client(monkeypatch)
        r = client.get("/healthz")
        csp = r.headers.get("content-security-policy", "")
        for directive in csp.split(";"):
            if "style-src" in directive:
                assert "'unsafe-inline'" in directive, (
                    f"'unsafe-inline' unexpectedly removed from style-src: {directive.strip()}"
                )
                return
        assert False, f"style-src directive not found in CSP: {csp}"

    def test_csp_header_present_on_every_response(self, monkeypatch):
        """CSP header is set on API responses, not just the SPA root."""
        client, _main, _db, _logger = create_client(monkeypatch)
        r = client.get("/api/not-real")
        assert "content-security-policy" in r.headers


class TestPerRouteRateLimits:
    """Per-route RPM caps are enforced independently of the global default."""

    def test_admin_verify_limited_at_5_rpm(self, monkeypatch):
        """First 5 requests to /api/admin/verify pass; 6th is blocked."""
        client, _main, _db, _logger = create_client(monkeypatch)

        for i in range(5):
            r = client.post("/api/admin/verify", json={"pin": "9999"})
            assert r.status_code != 429, f"Unexpected 429 at attempt {i + 1}"

        r = client.post("/api/admin/verify", json={"pin": "9999"})
        assert r.status_code == 429

    def test_contact_limited_at_10_rpm(self, monkeypatch):
        """POST /api/contact enforces 10 RPM."""
        client, _main, _db, _logger = create_client(monkeypatch)

        for i in range(10):
            r = client.post("/api/contact", json={"name": "A", "email": "a@b.com", "message": "hi"})
            assert r.status_code != 429, f"Unexpected 429 at request {i + 1}"

        r = client.post("/api/contact", json={"name": "A", "email": "a@b.com", "message": "hi"})
        assert r.status_code == 429

    def test_appointments_prefix_limited_at_10_rpm(self, monkeypatch):
        """/api/appointments/* sub-routes share the 10 RPM bucket."""
        client, _main, _db, _logger = create_client(monkeypatch)

        for i in range(10):
            r = client.get("/api/appointments/slots")
            assert r.status_code != 429, f"Unexpected 429 at request {i + 1}"

        r = client.get("/api/appointments/slots")
        assert r.status_code == 429

    def test_inventory_context_allows_high_rpm(self, monkeypatch):
        """GET /api/marketing/inventory-context tolerates page-load fan-out (100 req)."""
        client, _main, _db, _logger = create_client(monkeypatch)

        for i in range(100):
            r = client.get("/api/marketing/inventory-context")
            assert r.status_code != 429, f"Unexpected 429 at request {i + 1}"

    def test_default_rpm_applies_to_unlisted_routes(self, monkeypatch):
        """Routes outside _ROUTE_RPM fall back to the RATE_LIMIT_RPM default."""
        # Set default to 3 so we can probe it cheaply without touching per-route limits.
        client, _main, _db, _logger = create_client(monkeypatch, rate_limit_rpm="3")

        for i in range(3):
            r = client.get("/api/inventory")
            assert r.status_code != 429, f"Unexpected 429 at request {i + 1}"

        r = client.get("/api/inventory")
        assert r.status_code == 429

    def test_admin_token_bypasses_rate_limits(self, monkeypatch):
        """Admin-authenticated requests are exempt from all rate limiting."""
        # Use a very tight default to confirm the bypass works even under pressure.
        client, main, _db, _logger = create_client(monkeypatch, rate_limit_rpm="1")

        token = main._create_admin_token()
        headers = {"X-Admin-Token": token}

        for i in range(5):
            r = client.get("/api/inventory", headers=headers)
            assert r.status_code != 429, f"Unexpected 429 at request {i + 1}"

    def test_rate_limit_response_includes_retry_after_header(self, monkeypatch):
        """429 from the rate limiter includes Retry-After: 60."""
        client, _main, _db, _logger = create_client(monkeypatch)

        for _ in range(5):
            client.post("/api/admin/verify", json={"pin": "9999"})

        r = client.post("/api/admin/verify", json={"pin": "9999"})
        assert r.status_code == 429
        assert r.headers.get("retry-after") == "60"
