"""Client IP (H1 + M4): configured trusted hop + distributed failure alert.

Run: python -m pytest tests/test_client_ip.py -v
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.test_api_v1 import create_client, load_app  # noqa: E402
from tools.client_ip import get_client_ip  # noqa: E402


def _request(xff: str | None = None, host: str = "10.0.0.1"):
    headers = {}
    if xff is not None:
        headers["x-forwarded-for"] = xff
    return SimpleNamespace(headers=headers, client=SimpleNamespace(host=host))


def test_rightmost_hop_is_trusted_by_default(monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
    assert get_client_ip(_request("1.1.1.1, 8.8.8.8, 203.0.113.9")) == "203.0.113.9"


def test_trusted_proxy_hops_selects_nth_from_right(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "2")
    assert get_client_ip(_request("1.1.1.1, 8.8.8.8, 203.0.113.9")) == "8.8.8.8"


def test_invalid_selected_hop_falls_back_without_shifting_left(monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
    assert get_client_ip(_request("not-an-ip, , 203.0.113.9, unknown")) == "10.0.0.1"


def test_missing_xff_falls_back_to_request_client_host(monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
    assert get_client_ip(_request(None, host="192.0.2.10")) == "192.0.2.10"


def test_invalid_hops_env_defaults_to_one(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "nope")
    assert get_client_ip(_request("1.1.1.1, 203.0.113.9")) == "203.0.113.9"


def test_rotating_spoofed_leftmost_xff_does_not_reset_pin_lockout(monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("ADMIN_PIN_HASH", raising=False)
    main, _db, _logger = load_app(monkeypatch, tho_api_key="tho-secret", rate_limit_rpm="120")
    client = TestClient(main.app)
    real_ip = "203.0.113.9"
    now = time.time()
    main._pin_attempts_fallback[real_ip] = [now] * main.PIN_MAX_ATTEMPTS

    for spoof in ("198.51.100.1", "198.51.100.2", "198.51.100.3"):
        response = client.post(
            "/api/admin/verify",
            json={"pin": "0000"},
            headers={"X-Forwarded-For": f"{spoof}, {real_ip}"},
        )
        assert response.status_code == 429, response.text
        body = response.json()
        assert body["success"] is False
        assert "Too many failed attempts" in body["error"]


def test_global_failures_alert_but_valid_pin_still_authenticates(monkeypatch):
    import hashlib

    monkeypatch.setenv("ADMIN_PIN_HASH", hashlib.sha256(b"4832").hexdigest())
    client, main, _db, logger = create_client(monkeypatch, rate_limit_rpm="120")
    monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
    main._pin_global_attempts_fallback[:] = [time.time()] * main.PIN_GLOBAL_MAX_ATTEMPTS

    wrong = client.post("/api/admin/verify", json={"pin": "0000"})
    assert wrong.status_code == 401
    response = client.post("/api/admin/verify", json={"pin": "4832"})
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    assert "tho_admin_token" in response.cookies
    events = [e for e in logger.entries if e.get("event") == "admin_pin_global_failures"]
    assert events and events[0]["level"] == "error"
    assert len(main._pin_global_attempts_fallback) == main.PIN_GLOBAL_MAX_ATTEMPTS


def test_global_alert_preserves_per_ip_lockout(monkeypatch):
    client, main, *_ = create_client(monkeypatch)
    now = time.time()
    main._pin_global_attempts_fallback[:] = [now] * main.PIN_GLOBAL_MAX_ATTEMPTS
    main._pin_attempts_fallback["203.0.113.9"] = [now] * main.PIN_MAX_ATTEMPTS
    for path, body in (
        ("/api/admin/verify", {"pin": "4832"}),
        ("/api/admin/email-code/verify", {"email": "staff@example.com", "code": "123456"}),
    ):
        response = client.post(path, json=body, headers={"X-Forwarded-For": "203.0.113.9"})
        assert response.status_code == 429
        assert response.headers["Retry-After"] == str(main.PIN_LOCKOUT_SECONDS)


def test_run_chat_limit_uses_shared_client_ip(monkeypatch):
    client, main, *_ = create_client(monkeypatch, rate_limit_rpm="120")
    monkeypatch.setattr(main, "CHAT_RATE_LIMIT_MAX_REQUESTS", 1)
    main._chat_rate_limit_fallback.clear()

    headers = {"X-Forwarded-For": "198.51.100.1, 203.0.113.20"}
    first = client.post("/run", json={"userId": "u", "newMessage": {"text": "hi"}}, headers=headers)
    second = client.post(
        "/run",
        json={"userId": "u", "newMessage": {"text": "hi again"}},
        headers={"X-Forwarded-For": "198.51.100.9, 203.0.113.20"},
    )
    assert first.status_code != 429
    assert second.status_code == 429
    assert "203.0.113.20" in main._chat_rate_limit_fallback
    assert "198.51.100.1" not in main._chat_rate_limit_fallback


def test_short_proxy_chain_never_falls_back_to_supplied_leftmost_value(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "2")
    assert get_client_ip(_request("198.51.100.5")) == "10.0.0.1"


def test_load_balancer_topology_separates_clients_and_ignores_spoofed_prefix(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "2")
    balancer = "203.0.113.10"
    for client in ("198.51.100.1", "198.51.100.2"):
        for prefix in ("8.8.8.8", "invalid", "1.1.1.1, 8.8.8.8"):
            assert get_client_ip(_request(f"{prefix}, {client}, {balancer}")) == client
