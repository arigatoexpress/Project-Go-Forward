"""Tests for Cloud Run health probes."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_api_v1 import create_client


def test_healthz_returns_minimal_public_probe(monkeypatch):
    """Public healthz must not leak dependency configs or warnings."""
    monkeypatch.setenv("APP_VERSION", "test-sha")
    client, _main, _db, _logger = create_client(monkeypatch)

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == "test-sha"
    assert "dependencies" not in body
    assert "warnings" not in body
    assert "no-store" in response.headers.get("cache-control", "").lower()


def test_healthz_trailing_slash_returns_minimal_probe(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "test-sha")
    client, _main, _db, _logger = create_client(monkeypatch)

    response = client.get("/healthz/")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == "test-sha"


def test_healthz_body_is_strict_json_with_concrete_values(monkeypatch):
    """Regression: the body must be parseable JSON with concrete values, NEVER
    a schema-style description (e.g. ``{ status: string, uptime_s: int }``).
    Locks the contract: Cache-Control: no-store, JSON content-type, types
    are values not type names.
    """
    import json as _json

    client, _main, _db, _logger = create_client(monkeypatch)

    for path in ("/healthz", "/healthz/"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["content-type"].startswith("application/json"), path

        # Strict parse — schema-style bodies (unquoted keys / type-name values)
        # would raise here.
        body = _json.loads(response.text)

        assert isinstance(body["status"], str) and body["status"] == "ok", path
        assert isinstance(body["version"], str), path
        # Minimal response: only 'status' and 'version' keys should be present
        assert set(body.keys()) == {"status", "version"}, path

        # Cache-Control must prevent any proxy/CDN caching of the probe.
        assert "no-store" in response.headers.get("cache-control", "").lower(), path


def test_healthz_is_not_rate_limited(monkeypatch):
    client, _main, _db, _logger = create_client(monkeypatch, rate_limit_rpm="1")

    responses = [client.get("/healthz") for _ in range(3)]
    trailing_slash = client.get("/healthz/")

    assert [response.status_code for response in responses] == [200, 200, 200]
    assert trailing_slash.status_code == 200


def test_llms_txt_serves_plain_text_agent_context(monkeypatch):
    client, _main, _db, _logger = create_client(monkeypatch)

    response = client.get("/llms.txt")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["cache-control"] == "public, max-age=3600"
    body = response.text
    assert "# Texas Home Outlet" in body
    assert "https://tho.sapphirealpha.xyz/" in body
    assert "No public THO route authorizes private customer-data disclosure" in body


def test_llms_txt_does_not_redirect_from_run_app_host(monkeypatch):
    client, _main, _db, _logger = create_client(monkeypatch)

    response = client.get(
        "/llms.txt",
        headers={"host": "project-go-forward-trgi34bxuq-uc.a.run.app"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")


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

    # slowapi caps /api/admin/verify at 5/min; legacy lockout is 10 attempts.
    # In a rapid burst, slowapi fires first (6th request = 429).
    bad_attempts = [client.post("/api/admin/verify", json={"pin": "0000"}) for _ in range(5)]
    locked = client.post("/api/admin/verify", json={"pin": "0000"})

    assert main.PIN_MAX_ATTEMPTS == 10
    assert [response.status_code for response in bad_attempts] == [401] * 5
    assert locked.status_code == 429
    # Retry-After comes from slowapi (per-minute window), not legacy lockout.
    assert "Retry-After" in locked.headers


def test_successful_admin_login_clears_failed_attempts(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch)

    for _ in range(3):
        assert client.post("/api/admin/verify", json={"pin": "0000"}).status_code == 401

    # Verify attempts are tracked
    assert len(main._pin_attempts_fallback) > 0

    success = client.post("/api/admin/verify", json={"pin": "4832"})
    assert success.status_code == 200
    assert main._pin_attempts_fallback == {}
