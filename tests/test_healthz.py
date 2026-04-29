"""Tests for Cloud Run health probes."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_api_v1 import create_client


def test_healthz_returns_structured_probe(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "test-sha")
    client, _main, _db, _logger = create_client(monkeypatch)

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == "test-sha"
    assert body["sha"] == "test-sha"
    assert isinstance(body["uptime_s"], int)
    assert body["uptime_s"] >= 0
    assert set(body["dependencies"]) == {"drive", "secrets", "db", "email"}
    assert body["dependencies"]["secrets"] in {"configured", "missing"}
    assert body["dependencies"]["db"] == "configured"
    assert body["dependencies"]["email"] == "missing"
    assert body["warnings"] == ["email_not_configured"]


def test_healthz_reports_email_configured_when_resend_key_exists(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "test-sha")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    client, _main, _db, _logger = create_client(monkeypatch)

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["dependencies"]["email"] == "configured"
    assert body["warnings"] == []


def test_healthz_trailing_slash_returns_structured_probe(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "test-sha")
    client, _main, _db, _logger = create_client(monkeypatch)

    response = client.get("/healthz/")

    assert response.status_code == 200
    assert response.json()["version"] == "test-sha"


def test_healthz_is_not_rate_limited(monkeypatch):
    client, _main, _db, _logger = create_client(monkeypatch, rate_limit_rpm="1")

    responses = [client.get("/healthz") for _ in range(3)]
    trailing_slash = client.get("/healthz/")

    assert [response.status_code for response in responses] == [200, 200, 200]
    assert trailing_slash.status_code == 200


def test_unknown_api_paths_do_not_fall_back_to_spa(monkeypatch):
    client, _main, _db, _logger = create_client(monkeypatch)

    response = client.get("/api/not-real")
    normalized_traversal = client.get("/api/documents/download/../secret.txt")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert "<!doctype html>" not in response.text.lower()
    assert normalized_traversal.status_code == 404
    assert normalized_traversal.headers["content-type"].startswith("application/json")


def test_admin_pin_locks_out_after_ten_bad_attempts(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch)

    bad_attempts = [client.post("/api/admin/verify", json={"pin": "0000"}) for _ in range(10)]
    locked = client.post("/api/admin/verify", json={"pin": "0000"})

    assert main.PIN_MAX_ATTEMPTS == 10
    assert [response.status_code for response in bad_attempts] == [401] * 10
    assert locked.status_code == 429
    assert locked.headers["Retry-After"] == str(main.PIN_LOCKOUT_SECONDS)


def test_successful_admin_login_clears_failed_attempts(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch)

    for _ in range(3):
        assert client.post("/api/admin/verify", json={"pin": "0000"}).status_code == 401

    success = client.post("/api/admin/verify", json={"pin": "4832"})
    assert success.status_code == 200
    assert main._pin_attempts == {}
