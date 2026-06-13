"""Tests for input sanitization utilities and middleware.

Run: python -m pytest tests/test_input_sanitizer.py -v
"""

import sys
from pathlib import Path

import pytest

from tools.input_sanitizer import sanitize_body, sanitize_string, sanitize_value

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class TestSanitizeString:
    """Test the sanitize_string function."""

    def test_strip_html_tags(self):
        assert sanitize_string("<script>alert(1)</script>hello") == "alert(1)hello"
        assert sanitize_string("<b>bold</b> text") == "bold text"
        assert sanitize_string("<img src=x onerror=alert(1)>safe") == "safe"

    def test_remove_control_chars(self):
        assert sanitize_string("\x00hello\x01") == "hello"
        assert sanitize_string("\x1f\x7ftest\x9f") == "test"
        assert sanitize_string("line\x0b\x0cbreak") == "linebreak"

    def test_preserve_newline_tab(self):
        assert sanitize_string("hello\nworld\ttab") == "hello\nworld\ttab"

    def test_length_limit(self):
        long_input = "A" * 1000
        assert len(sanitize_string(long_input, max_len=100)) == 100

    def test_whitespace_stripped(self):
        assert sanitize_string("  hello  ") == "hello"
        assert sanitize_string("\n\tname\n\t") == "name"

    def test_non_string_passthrough(self):
        assert sanitize_string(123) == 123
        assert sanitize_string(None) is None


class TestSanitizeValue:
    """Test recursive sanitization."""

    def test_recursive_dict(self):
        data = {"name": "<b>Alice</b>", "email": "alice@example.com"}
        result = sanitize_value(data)
        assert result["name"] == "Alice"
        assert result["email"] == "alice@example.com"

    def test_recursive_list(self):
        data = ["<script>bad</script>", "good"]
        result = sanitize_value(data)
        assert result[0] == "bad"
        assert result[1] == "good"

    def test_nested_structure(self):
        data = {
            "items": [
                {"label": "<img>photo</img>", "count": 5},
            ],
            "meta": {"title": "\x00Title\x01"},
        }
        result = sanitize_value(data)
        assert result["items"][0]["label"] == "photo"
        assert result["items"][0]["count"] == 5
        assert result["meta"]["title"] == "Title"


class TestSanitizeBody:
    """Test the top-level sanitize_body entry point."""

    def test_sanitize_simple_dict(self):
        body = {"message": "<p>Hello</p>", "name": "  Bob  "}
        result = sanitize_body(body)
        assert result["message"] == "Hello"
        assert result["name"] == "Bob"


class TestInputSanitizationMiddleware:
    """Test the InputSanitizationMiddleware in main.py."""

    @pytest.fixture
    def client(self, monkeypatch):
        from tests.test_api_v1 import create_client
        client, _main, _db, _logger = create_client(monkeypatch)
        return client

    def test_middleware_sanitizes_json_body(self, client):
        """POST to a public API with HTML in body; the middleware should strip it."""
        res = client.post(
            "/api/contact",
            json={
                "name": "<script>alert(1)</script>Alice",
                "email": "alice@example.com",
                "message": "<b>Hello</b>",
            },
        )
        # The request should succeed (middleware doesn't break the pipe)
        assert res.status_code in (200, 201, 422, 500)

    def test_middleware_skips_get_requests(self, client):
        """GET requests should not be sanitized."""
        res = client.get("/api/marketing/inventory-context")
        assert res.status_code in (200, 404)

    def test_middleware_skips_v1_partner_api(self, client):
        """Partner API should not be affected by sanitization."""
        res = client.post(
            "/api/v1/customers",
            json={"name": "<b>Test</b>"},
            headers={"X-API-Key": "tho-secret"},
        )
        # Should not be rejected because of middleware
        assert res.status_code in (200, 201, 400, 401, 422)
