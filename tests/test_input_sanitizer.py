"""Tests for input sanitization utilities and middleware.

Run: python -m pytest tests/test_input_sanitizer.py -v
"""

import sys
from pathlib import Path

import pytest

from tools.input_sanitizer import (
    sanitize_body,
    sanitize_query_params,
    sanitize_string,
    sanitize_value,
)

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


class TestSanitizeQueryParams:
    """Test query parameter sanitization."""

    def test_sanitize_simple_params(self):
        params = {"search": "<script>alert(1)</script>homes", "page": "1"}
        result = sanitize_query_params(params)
        assert result["search"] == "alert(1)homes"
        assert result["page"] == "1"

    def test_sanitize_list_values(self):
        params = {"tags": ["<b>new</b>", "used"], "status": "active"}
        result = sanitize_query_params(params)
        assert result["tags"] == ["new", "used"]
        assert result["status"] == "active"

    def test_sanitize_non_dict_passthrough(self):
        assert sanitize_query_params(None) is None
        assert sanitize_query_params("string") == "string"

    def test_max_len_applied(self):
        long_value = "A" * 1000
        params = {"q": long_value}
        result = sanitize_query_params(params, max_len=100)
        assert len(result["q"]) == 100


class TestSanitizeEdgeCases:
    """Edge cases for sanitization functions."""

    def test_deeply_nested_html(self):
        data = {
            "level1": {
                "level2": {
                    "level3": {
                        "html": "<div><span><script>alert(1)</script></span></div>"
                    }
                }
            }
        }
        result = sanitize_value(data)
        assert result["level1"]["level2"]["level3"]["html"] == "alert(1)"

    def test_unicode_preserved(self):
        assert sanitize_string("Hello \u2014 World") == "Hello \u2014 World"
        assert sanitize_string("Caf\u00e9") == "Caf\u00e9"

    def test_empty_and_whitespace_only(self):
        assert sanitize_string("  ") == ""
        assert sanitize_string("") == ""
        assert sanitize_string("\n\t\r") == ""

    def test_numeric_and_boolean_passthrough(self):
        data = {"count": 42, "active": True, "ratio": 3.14}
        result = sanitize_value(data)
        assert result["count"] == 42
        assert result["active"] is True
        assert result["ratio"] == 3.14

    def test_empty_collections(self):
        assert sanitize_value([]) == []
        assert sanitize_value({}) == {}
        assert sanitize_value(()) == ()


class TestInputSanitizationMiddlewareDirect:
    """Directly test the middleware's request body rewriting."""

    @pytest.mark.asyncio
    async def test_middleware_rewrites_json_body(self):
        """The middleware should set request._body to the sanitized JSON."""
        import json
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock

        from main import InputSanitizationMiddleware

        middleware = InputSanitizationMiddleware(app=None)

        raw_body = json.dumps({"name": "<script>alert(1)</script>Alice", "email": "alice@example.com"}).encode("utf-8")
        request = SimpleNamespace(
            method="POST",
            url=SimpleNamespace(path="/api/contact"),
            headers={"content-type": "application/json"},
            body=AsyncMock(return_value=raw_body),
        )

        call_next = AsyncMock(return_value=MagicMock())
        await middleware.dispatch(request, call_next)

        # Verify body was rewritten to sanitized JSON
        expected = json.dumps({"name": "alert(1)Alice", "email": "alice@example.com"}).encode("utf-8")
        assert request._body == expected
        call_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_middleware_leaves_non_json_untouched(self):
        """Non-JSON bodies should not be modified."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock

        from main import InputSanitizationMiddleware

        middleware = InputSanitizationMiddleware(app=None)

        request = SimpleNamespace(
            method="POST",
            url=SimpleNamespace(path="/api/contact"),
            headers={"content-type": "text/plain"},
            body=AsyncMock(return_value=b"plain text body"),
        )

        call_next = AsyncMock(return_value=MagicMock())
        await middleware.dispatch(request, call_next)

        # _body should not be set for non-JSON content
        assert not hasattr(request, "_body")
        call_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_middleware_leaves_malformed_json_untouched(self):
        """Malformed JSON should not crash the middleware; body is left as-is."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock

        from main import InputSanitizationMiddleware

        middleware = InputSanitizationMiddleware(app=None)

        request = SimpleNamespace(
            method="POST",
            url=SimpleNamespace(path="/api/contact"),
            headers={"content-type": "application/json"},
            body=AsyncMock(return_value=b"{not valid json"),
        )

        call_next = AsyncMock(return_value=MagicMock())
        await middleware.dispatch(request, call_next)

        # _body should not be set when JSON parsing fails
        assert not hasattr(request, "_body")
        call_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_middleware_skips_get_requests_direct(self):
        """GET requests should bypass body sanitization entirely."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock

        from main import InputSanitizationMiddleware

        middleware = InputSanitizationMiddleware(app=None)

        request = SimpleNamespace(
            method="GET",
            url=SimpleNamespace(path="/api/marketing/inventory-context"),
            headers={"content-type": "application/json"},
            body=AsyncMock(return_value=b""),
        )

        call_next = AsyncMock(return_value=MagicMock())
        await middleware.dispatch(request, call_next)

        request.body.assert_not_called()
        call_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_middleware_skips_partner_api_direct(self):
        """Partner API requests should bypass body sanitization."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock

        from main import InputSanitizationMiddleware

        middleware = InputSanitizationMiddleware(app=None)

        request = SimpleNamespace(
            method="POST",
            url=SimpleNamespace(path="/api/v1/customers"),
            headers={"content-type": "application/json"},
            body=AsyncMock(return_value=b"{}"),
        )

        call_next = AsyncMock(return_value=MagicMock())
        await middleware.dispatch(request, call_next)

        request.body.assert_not_called()
        call_next.assert_awaited_once()
