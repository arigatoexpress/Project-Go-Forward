"""Signed-in staff get a per-session rate-limit bucket with a higher cap.

Several staff share one shop IP. Before this, every admin click counted
against one shared per-IP bucket (60/min legacy, 100/min slowapi), so a few
staff clicking quickly hit 429s. Unauthenticated sign-in / code endpoints must
stay on the strict per-IP limits.

Run: python -m pytest tests/test_staff_rate_limits.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.test_api_v1 import load_app  # noqa: E402

# Rightmost hop is the Cloud Run client; leftmost is attacker-controlled.
SHOP_IP = {"X-Forwarded-For": "198.51.100.1, 203.0.113.7"}


def _app(monkeypatch, rpm: str = "20"):
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("ADMIN_PIN_HASH", raising=False)
    main, _db, _logger = load_app(monkeypatch, tho_api_key="tho-secret", rate_limit_rpm=rpm)
    return main, TestClient(main.app)


def _staff_headers(main, email: str) -> dict[str, str]:
    return {**SHOP_IP, "Authorization": f"Bearer {main._create_admin_token(email=email)}"}


def test_two_staff_on_one_ip_do_not_share_a_bucket(monkeypatch):
    main, client = _app(monkeypatch, rpm="20")
    alice = _staff_headers(main, "alice@texashomeoutlet.com")
    bob = _staff_headers(main, "bob@texashomeoutlet.com")

    # Each staff session goes well past the per-IP cap (20) from the same IP.
    for i in range(30):
        assert client.get("/api/metrics", headers=alice).status_code == 200, f"alice #{i + 1}"
    for i in range(30):
        assert client.get("/api/metrics", headers=bob).status_code == 200, f"bob #{i + 1}"


def test_staff_session_still_has_a_cap(monkeypatch):
    main, client = _app(monkeypatch, rpm="20")
    monkeypatch.setattr(main, "STAFF_RATE_LIMIT_RPM", 25)
    alice = _staff_headers(main, "alice@texashomeoutlet.com")

    codes = [client.get("/api/metrics", headers=alice).status_code for _ in range(26)]
    assert codes[:25] == [200] * 25
    assert codes[25] == 429


def test_staff_traffic_does_not_starve_public_ip_bucket(monkeypatch):
    main, client = _app(monkeypatch, rpm="20")
    alice = _staff_headers(main, "alice@texashomeoutlet.com")
    for _ in range(30):
        client.get("/api/metrics", headers=alice)

    # The anonymous bucket for the same IP is untouched by staff clicks.
    assert client.get("/api/metrics", headers=SHOP_IP).status_code == 401


def test_unauthenticated_requests_keep_strict_per_ip_limit(monkeypatch):
    _main, client = _app(monkeypatch, rpm="20")
    codes = [client.get("/api/metrics", headers=SHOP_IP).status_code for _ in range(21)]
    assert codes[:20] == [401] * 20
    assert codes[20] == 429


def test_forged_staff_cookie_gets_public_bucket(monkeypatch):
    _main, client = _app(monkeypatch, rpm="20")
    forged = {**SHOP_IP, "Authorization": "Bearer not-a-real-token"}
    codes = [client.get("/api/metrics", headers=forged).status_code for _ in range(21)]
    assert codes[20] == 429


def test_sign_in_endpoint_stays_per_ip_even_with_staff_cookie(monkeypatch):
    """A valid staff cookie must not buy extra PIN guesses: 5/min per IP."""
    main, client = _app(monkeypatch, rpm="120")
    alice = _staff_headers(main, "alice@texashomeoutlet.com")
    bob = _staff_headers(main, "bob@texashomeoutlet.com")

    codes = []
    for i in range(6):
        headers = alice if i % 2 == 0 else bob
        codes.append(client.post("/api/admin/verify", json={"pin": "0000"}, headers=headers).status_code)
    assert codes[:5] == [401] * 5
    assert codes[5] == 429


def test_strict_auth_paths_cover_sign_in_and_code_endpoints(monkeypatch):
    main, _client = _app(monkeypatch, rpm="120")
    for prefix in ("/api/admin/verify", "/api/admin/email-code/verify", "/api/admin/passkey/login/begin"):
        assert main._is_strict_auth_path(prefix)
    assert not main._is_strict_auth_path("/api/admin/check")
    assert not main._is_strict_auth_path("/api/inventory")
