"""Client IP (H1 + M4): rightmost trusted hop + global PIN budget.

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


def test_empty_and_invalid_xff_entries_are_ignored(monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
    assert get_client_ip(_request("not-an-ip, , 203.0.113.9, unknown")) == "203.0.113.9"


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


def test_global_pin_budget_locks_every_ip(monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("ADMIN_PIN_HASH", raising=False)
    main, _db, logger = load_app(monkeypatch, tho_api_key="tho-secret", rate_limit_rpm="120")
    client = TestClient(main.app)
    now = time.time()
    main._pin_global_attempts_fallback[:] = [now] * main.PIN_GLOBAL_MAX_ATTEMPTS

    pin_resp = client.post(
        "/api/admin/verify",
        json={"pin": "0000"},
        headers={"X-Forwarded-For": "198.51.100.80"},
    )
    code_resp = client.post(
        "/api/admin/email-code/verify",
        json={"email": "staff@example.com", "code": "000000"},
        headers={"X-Forwarded-For": "198.51.100.81"},
    )
    assert pin_resp.status_code == 429
    assert code_resp.status_code == 429
    events = [e for e in logger.entries if e.get("event") == "admin_pin_global_lockout"]
    assert events
    assert events[0]["level"] == "error"


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
