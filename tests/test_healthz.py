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
    assert isinstance(body["uptime_s"], int)
    assert body["uptime_s"] >= 0


def test_healthz_trailing_slash_returns_structured_probe(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "test-sha")
    client, _main, _db, _logger = create_client(monkeypatch)

    response = client.get("/healthz/")

    assert response.status_code == 200
    assert response.json()["version"] == "test-sha"


def test_healthz_is_not_rate_limited(monkeypatch):
    client, _main, _db, _logger = create_client(monkeypatch, rate_limit_rpm="1")

    responses = [client.get("/healthz") for _ in range(3)]

    assert [response.status_code for response in responses] == [200, 200, 200]
