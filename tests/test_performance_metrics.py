"""Tests for the performance metrics middleware and /api/metrics endpoint.

Run: python -m pytest tests/test_performance_metrics.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

sys.path.insert(0, str(Path(__file__).parent))
from test_api_v1 import create_client  # noqa: E402


class TestPerformanceMetricsMiddleware:
    """Test the PerformanceMetricsMiddleware tracks latency and logs correctly."""

    def test_middleware_tracks_latency(self, monkeypatch):
        """API requests should be recorded in the metrics store."""
        client, main, _db, _logger = create_client(monkeypatch)

        response = client.get("/api/marketing/inventory-context")
        assert response.status_code == 200

        metrics = main._metrics_store.get_metrics()
        assert metrics["overall"]["count"] >= 1
        assert metrics["overall"]["p50"] is not None
        assert metrics["overall"]["p95"] is not None
        assert metrics["overall"]["p99"] is not None

        endpoint_keys = list(metrics["endpoints"].keys())
        assert len(endpoint_keys) >= 1
        assert any("GET /api/marketing/inventory-context" in k for k in endpoint_keys)

    def test_middleware_skips_health_checks(self, monkeypatch):
        """Health checks should not be logged or recorded."""
        client, main, _db, fake_logger = create_client(monkeypatch)

        # Clear any existing metrics from setup
        main._metrics_store._global_buffer.clear()
        main._metrics_store._endpoint_buffers.clear()

        response = client.get("/healthz")
        assert response.status_code == 200

        # No request logs for health checks
        request_logs = [e for e in fake_logger.entries if e.get("message") == "request"]
        health_logs = [
            e for e in request_logs if e.get("path") in {"/health", "/healthz", "/healthz/"}
        ]
        assert len(health_logs) == 0

        # Metrics store should have no new entries
        metrics = main._metrics_store.get_metrics()
        assert metrics["overall"]["count"] == 0

    def test_structured_log_contains_required_fields(self, monkeypatch):
        """Each request log must contain the required structured fields."""
        client, main, _db, fake_logger = create_client(monkeypatch)

        response = client.get("/api/marketing/inventory-context")
        assert response.status_code == 200

        request_logs = [e for e in fake_logger.entries if e.get("message") == "request"]
        assert len(request_logs) >= 1
        log = request_logs[-1]

        required_fields = ["method", "path", "status_code", "duration_ms", "timestamp"]
        for field in required_fields:
            assert field in log, f"Missing required field: {field}"

        assert log["method"] == "GET"
        assert log["path"] == "/api/marketing/inventory-context"
        assert log["status_code"] == 200


class TestMetricsEndpoint:
    """Test the /api/metrics admin endpoint."""

    def test_metrics_endpoint_requires_admin_auth(self, monkeypatch):
        """Unauthenticated or invalid requests must be rejected."""
        client, main, _db, _logger = create_client(monkeypatch)

        # No auth
        response = client.get("/api/metrics")
        assert response.status_code == 401

        # Bad auth
        response = client.get("/api/metrics", headers={"X-Admin-Token": "bad-token"})
        assert response.status_code == 401

    def test_metrics_endpoint_returns_valid_data(self, monkeypatch):
        """Admin requests should return overall + per-endpoint percentiles."""
        client, main, _db, _logger = create_client(monkeypatch)
        token = main._create_admin_token()

        # Generate some traffic
        for _ in range(5):
            client.get("/api/marketing/inventory-context")

        response = client.get("/api/metrics", headers={"X-Admin-Token": token})
        assert response.status_code == 200
        data = response.json()

        assert "overall" in data
        assert "endpoints" in data
        overall = data["overall"]
        assert overall["count"] >= 5
        assert overall["p50"] is not None
        assert overall["p95"] is not None
        assert overall["p99"] is not None

        endpoints = data["endpoints"]
        assert len(endpoints) >= 1
        for key, ep in endpoints.items():
            assert "p50" in ep
            assert "p95" in ep
            assert "p99" in ep
            assert "count" in ep
            assert "error_rate" in ep
            assert ep["error_rate"] >= 0.0
