"""Tests for the Report Issue endpoint."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_api_v1 import create_client  # noqa: E402


def test_feedback_requires_description(monkeypatch):
    client, _main, _db, _logger = create_client(monkeypatch)

    response = client.post("/api/feedback", json={"description": ""})

    assert response.status_code == 400
    assert response.headers.get("Cache-Control") == "no-cache"
    assert response.json()["message"] == "Description is required"


def test_feedback_redacts_sensitive_text_and_strips_url_queries(monkeypatch, tmp_path):
    client, main, _db, _logger = create_client(monkeypatch)
    monkeypatch.setattr(main, "__file__", str(tmp_path / "main.py"))

    response = client.post(
        "/api/feedback",
        json={
            "description": (
                "<b>Call 281-415-4111, SSN 123-45-6789, "
                "api_key=super-secret-value, token:tok_123</b>"
            ),
            "page": "Document Center",
            "url": "https://example.test/admin?token=super-secret-value#frag",
            "userAgent": "pytest",
            "screenSize": "1440x900",
        },
    )

    assert response.status_code == 200
    assert response.headers.get("Cache-Control") == "no-cache"

    saved = (tmp_path / "data" / "feedback.jsonl").read_text().strip()
    feedback = json.loads(saved)
    assert feedback["url"] == "https://example.test/admin"
    assert "281-415-4111" not in feedback["description"]
    assert "123-45-6789" not in feedback["description"]
    assert "super-secret-value" not in feedback["description"]
    assert "tok_123" not in feedback["description"]
    assert "[PHONE-REDACTED]" in feedback["description"]
    assert "[SSN-REDACTED]" in feedback["description"]
    assert "[SECRET-REDACTED]" in feedback["description"]
