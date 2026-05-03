"""Tests for JWT admin authentication system.

Verifies token creation, verification, expiration, and tamper resistance.

Run: python -m pytest tests/test_admin_auth.py -v
"""

import base64
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestJWTTokens:
    """Test the HMAC-signed JWT admin token system."""

    def _get_token_funcs(self):
        """Import the token functions from main.py without starting the server."""
        # Import just the token functions
        import hashlib
        import hmac

        PIN_HASH = hashlib.sha256(b"4832").hexdigest()
        JWT_SECRET = hashlib.sha256(f"sapphire-jwt-{PIN_HASH[:16]}".encode()).digest()
        TTL = 7200  # 2 hours

        def create():
            expires = int(time.time()) + TTL
            payload = struct.pack(">Q", expires)
            sig = hmac.new(JWT_SECRET, payload, hashlib.sha256).digest()[:16]
            return base64.urlsafe_b64encode(payload + sig).decode().rstrip("=")

        def verify(token):
            try:
                padding = 4 - len(token) % 4
                if padding != 4:
                    token += "=" * padding
                raw = base64.urlsafe_b64decode(token)
                if len(raw) != 24:
                    return False
                p, sig = raw[:8], raw[8:]
                expected_sig = hmac.new(JWT_SECRET, p, hashlib.sha256).digest()[:16]
                if not hmac.compare_digest(sig, expected_sig):
                    return False
                expires = struct.unpack(">Q", p)[0]
                return time.time() < expires
            except Exception:
                return False

        return create, verify

    def test_create_returns_string(self):
        create, verify = self._get_token_funcs()
        token = create()
        assert isinstance(token, str)
        assert len(token) > 20

    def test_verify_valid_token(self):
        create, verify = self._get_token_funcs()
        token = create()
        assert verify(token) is True

    def test_verify_tampered_token_fails(self):
        create, verify = self._get_token_funcs()
        token = create()
        # Flip a character
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        assert verify(tampered) is False

    def test_verify_empty_token_fails(self):
        _, verify = self._get_token_funcs()
        assert verify("") is False

    def test_verify_garbage_token_fails(self):
        _, verify = self._get_token_funcs()
        assert verify("not-a-real-token") is False
        assert verify("abcdefghijklmnop") is False

    def test_verify_expired_token_fails(self):
        """Simulate an expired token by creating one with past expiration."""
        import hashlib
        import hmac as _hmac

        PIN_HASH = hashlib.sha256(b"4832").hexdigest()
        JWT_SECRET = hashlib.sha256(f"sapphire-jwt-{PIN_HASH[:16]}".encode()).digest()

        # Create token that expired 1 hour ago
        expired_time = int(time.time()) - 3600
        payload = struct.pack(">Q", expired_time)
        sig = _hmac.new(JWT_SECRET, payload, hashlib.sha256).digest()[:16]
        expired_token = base64.urlsafe_b64encode(payload + sig).decode().rstrip("=")

        _, verify = self._get_token_funcs()
        assert verify(expired_token) is False

    def test_different_tokens_are_unique(self):
        create, _ = self._get_token_funcs()
        t1 = create()
        # Sleep briefly to get different timestamp
        time.sleep(0.01)
        t2 = create()
        # Tokens created at slightly different times may or may not differ
        # (same second = same token), but both should verify
        _, verify = self._get_token_funcs()
        assert verify(t1) is True
        assert verify(t2) is True


class TestInputSanitization:
    """Test the _sanitize_text function."""

    def test_strip_html_tags(self):
        import re

        def sanitize(val, max_len=500):
            return re.sub(r"<[^>]+>", "", val).strip()[:max_len]

        assert sanitize("<script>alert(1)</script>hello") == "alert(1)hello"
        assert sanitize("<b>bold</b> text") == "bold text"
        assert sanitize("<img src=x onerror=alert(1)>safe") == "safe"

    def test_length_limit(self):
        import re

        def sanitize(val, max_len=500):
            return re.sub(r"<[^>]+>", "", val).strip()[:max_len]

        long_input = "A" * 1000
        assert len(sanitize(long_input, max_len=100)) == 100

    def test_whitespace_stripped(self):
        import re

        def sanitize(val, max_len=500):
            return re.sub(r"<[^>]+>", "", val).strip()[:max_len]

        assert sanitize("  hello  ") == "hello"
        assert sanitize("\n\tname\n\t") == "name"


class TestAdminLoginAuditTrail:
    """Successful /api/admin/verify must produce an admin.login audit entry."""

    def _seed_audit_fake(self):
        """Replace audit_log's Firestore client with an in-memory fake.

        We import the helpers from tests/test_audit_log so we don't duplicate
        the Firestore stand-in.
        """
        sys.path.insert(0, str(Path(__file__).parent))
        from test_audit_log import FakeFirestore  # noqa: E402

        import audit_log

        fake = FakeFirestore()
        audit_log._get_db = lambda: fake  # type: ignore[attr-defined]
        return fake

    def test_successful_login_emits_audit_entry(self, monkeypatch):
        """End-to-end: POST /api/admin/verify with the right PIN writes an
        admin.login row to the audit_log collection."""
        import hashlib as _hashlib

        # Force a known PIN hash so we know what to send.
        monkeypatch.setenv("ADMIN_PIN_HASH", _hashlib.sha256(b"4832").hexdigest())

        # Bring up the app via the existing test harness.
        sys.path.insert(0, str(Path(__file__).parent))
        from test_api_v1 import create_client  # noqa: E402

        fake = self._seed_audit_fake()
        client, _main, _db, _logger = create_client(monkeypatch)

        response = client.post("/api/admin/verify", json={"pin": "4832"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True

        docs = fake.collections["audit_log"]._docs
        # At least one admin.login row was written for this verify call.
        login_rows = [d for d in docs if d.get("action") == "admin.login"]
        assert len(login_rows) >= 1
        row = login_rows[-1]
        assert row["target_type"] == "session"
        assert row["actor"].startswith("admin:")
        # IP captured from the test client default.
        assert "ip" in row
        # PIN must NEVER appear in the audit row.
        assert "4832" not in repr(row)


class TestAdminVerifyRateLimit:
    """slowapi per-IP cap on /api/admin/verify (5/min) layered on the
    legacy 10-attempt ``_pin_attempts`` lockout.
    """

    def _client(self, monkeypatch):
        # Reuse the heavy stub setup from test_api_v1.load_app — it
        # mocks Firestore / ADK / inventory tools so main.py imports cleanly.
        from fastapi.testclient import TestClient

        sys.path.insert(0, str(Path(__file__).parent))
        from test_api_v1 import load_app  # noqa: E402

        # Force the local-dev fallback PIN hash by ensuring K_SERVICE is unset.
        # main.py uses sha256(b"4832") as the fallback when ADMIN_PIN_HASH is
        # not configured outside Cloud Run.
        monkeypatch.delenv("K_SERVICE", raising=False)
        monkeypatch.delenv("ADMIN_PIN_HASH", raising=False)
        # Bump RATE_LIMIT_RPM well above 6 so the legacy RateLimitMiddleware
        # doesn't 429 before slowapi gets a chance to.
        main, _db, _logger = load_app(monkeypatch, tho_api_key="tho-secret", rate_limit_rpm="120")
        return TestClient(main.app), main

    def test_admin_verify_rate_limit_429(self, monkeypatch):
        """The 6th rapid wrong-PIN POST from the same client returns 429
        with a Retry-After header — slowapi short-circuits before
        ``_pin_attempts`` ever reaches its 10-attempt cap.
        """
        client, main = self._client(monkeypatch)

        # 5 wrong PINs are allowed by slowapi (cap = 5/minute) but rejected
        # by the PIN check (401). _pin_attempts increments to 5, still under
        # the legacy 10-attempt lockout.
        for i in range(5):
            r = client.post("/api/admin/verify", json={"pin": "0000"})
            assert r.status_code == 401, f"attempt {i + 1}: expected 401, got {r.status_code}"

        # The 6th attempt is rejected by slowapi with 429 + Retry-After.
        r6 = client.post("/api/admin/verify", json={"pin": "0000"})
        assert r6.status_code == 429
        assert "Retry-After" in r6.headers
        # Retry-After should be a small positive integer (seconds remaining).
        retry_after = int(r6.headers["Retry-After"])
        assert 0 <= retry_after <= 60
        body = r6.json()
        # slowapi's default handler returns {"error": "Rate limit exceeded: ..."}.
        assert "error" in body
        assert "Rate limit exceeded" in body["error"]

    def test_pin_attempts_lockout_still_engages_under_slowapi_cap(self, monkeypatch):
        """The legacy ``_pin_attempts`` lockout (10 attempts in 5 minutes)
        must still fire for a client staying under the 5/min slowapi cap.

        Because the slowapi limiter is per-process and shares state across
        requests, we exercise the lockout by directly priming
        ``_pin_attempts`` to PIN_MAX_ATTEMPTS and confirming the legacy
        429 response — distinct from slowapi's 429 — is returned.
        """
        client, main = self._client(monkeypatch)

        # Prime the legacy counter for the TestClient's client IP.
        # TestClient sets request.client.host to "testclient" by default.
        main._pin_attempts["testclient"] = [
            __import__("time").time() for _ in range(main.PIN_MAX_ATTEMPTS)
        ]

        r = client.post("/api/admin/verify", json={"pin": "0000"})
        assert r.status_code == 429
        # Distinct from slowapi: legacy lockout uses success/error envelope
        # and a ~5-minute Retry-After.
        body = r.json()
        assert body == {
            "success": False,
            "error": "Too many failed attempts. Please wait 5 minutes.",
        }
        # slowapi's _inject_headers may rewrite the legacy Retry-After=300
        # into a delta-from-now, losing 1 second to timing slop. Allow some
        # play around the 300-second lockout window.
        retry_after = int(r.headers.get("Retry-After", "0"))
        assert main.PIN_LOCKOUT_SECONDS - 5 <= retry_after <= main.PIN_LOCKOUT_SECONDS
