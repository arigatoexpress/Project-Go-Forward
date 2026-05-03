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


def test_healthz_body_is_strict_json_with_concrete_values(monkeypatch):
    """Regression: the body must be parseable JSON with concrete values, NEVER
    a schema-style description (e.g. ``{ status: string, uptime_s: int }``).
    Locks the contract: Cache-Control: no-store, JSON content-type, types
    are values not type names.
    """
    import json as _json

    monkeypatch.setenv("APP_VERSION", "test-sha")
    client, _main, _db, _logger = create_client(monkeypatch)

    for path in ("/healthz", "/healthz/"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json"), path

        # Strict parse — schema-style bodies (unquoted keys / type-name values)
        # would raise here.
        body = _json.loads(response.text)

        assert isinstance(body["status"], str) and body["status"] == "ok", path
        assert isinstance(body["version"], str) and body["version"], path
        assert isinstance(body["sha"], str) and body["sha"], path
        assert isinstance(body["uptime_s"], int) and body["uptime_s"] >= 0, path
        assert isinstance(body["dependencies"], dict), path
        for dep in ("drive", "secrets", "db", "email"):
            assert isinstance(body["dependencies"][dep], str), (path, dep)
            assert body["dependencies"][dep] != "string", (path, dep)
        assert isinstance(body["warnings"], list), path
        for warning in body["warnings"]:
            assert isinstance(warning, str) and warning != "string", (path, warning)

        # Cache-Control must prevent any proxy/CDN caching of the probe.
        assert "no-store" in response.headers.get("cache-control", "").lower(), path


def test_healthz_body_includes_workers_field(monkeypatch):
    """workers field must be present and numeric — used by smoke probes to audit
    that no accidental multi-worker config crept in via env-var override."""
    monkeypatch.setenv("APP_VERSION", "test-sha")
    client, _main, _db, _logger = create_client(monkeypatch)

    response = client.get("/healthz/")

    assert response.status_code == 200
    body = response.json()
    assert "workers" in body, "workers field missing from healthz body"
    assert isinstance(body["workers"], int), "workers must be an integer"
    assert body["workers"] >= 1


def test_schema_text_body_guard_blocks_placeholder_body(monkeypatch):
    """SchemaTextBodyGuard must intercept a JSON body whose values are
    schema-text type names ("string", "int") and return 500 with code
    SCHEMA_TEXT_BODY instead of leaking the malformed payload to the client."""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse as _JSONResponse
    from fastapi.testclient import TestClient

    _client, main_mod, _db, _logger = create_client(monkeypatch)

    # Build a minimal isolated app that only wires up SchemaTextBodyGuard
    # so we can inject a buggy route without fighting the SPA catch-all.
    mini_app = FastAPI()
    mini_app.add_middleware(main_mod.SchemaTextBodyGuard)

    @mini_app.get("/buggy")
    def _schema_text_route():
        # Simulates the observed schema-text body: real field names, type-name values
        return _JSONResponse(
            {"status": "string", "uptime_s": "int", "sha": "string", "version": "string"}
        )

    @mini_app.get("/clean")
    def _clean_route():
        return _JSONResponse({"status": "ok", "uptime_s": 42, "sha": "abc123"})

    mini_client = TestClient(mini_app, raise_server_exceptions=False)

    # Schema-text body must be replaced with 500 + SCHEMA_TEXT_BODY code
    bad = mini_client.get("/buggy")
    assert bad.status_code == 500, f"expected 500 from guard, got {bad.status_code}: {bad.text}"
    assert bad.json()["code"] == "SCHEMA_TEXT_BODY"

    # Legitimate JSON body must pass through unchanged
    good = mini_client.get("/clean")
    assert good.status_code == 200
    assert good.json()["status"] == "ok"
    assert good.json()["uptime_s"] == 42


def test_schema_text_body_guard_passes_large_json_unchanged(monkeypatch):
    """Guard must not consume or reconstruct large JSON responses
    (content-length > threshold) — they are never schema-text bodies."""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse as _JSONResponse
    from fastapi.testclient import TestClient

    _client, main_mod, _db, _logger = create_client(monkeypatch)

    mini_app = FastAPI()
    mini_app.add_middleware(main_mod.SchemaTextBodyGuard)

    # A large payload that legitimately contains the word "string" inside a longer value
    large_payload = {"items": ["item_" + str(i) for i in range(500)], "type": "not-a-string"}

    @mini_app.get("/large")
    def _large_route():
        return _JSONResponse(large_payload)

    mini_client = TestClient(mini_app, raise_server_exceptions=False)

    resp = mini_client.get("/large")
    assert resp.status_code == 200
    assert resp.json()["items"][0] == "item_0"


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
