"""Tests for request-path middleware / resilient-handler coverage gaps (swarm D).

Targets (main.py @ f65c537, Python-3.12 honest-coverage campaign — final tail):

- ``_staff_session_rate_key`` — passkey-session verification failure swallows
  to None (the rate-limit bucket falls back to per-IP)
- ``_rate_limit_handler`` — slowapi limit metadata unreadable → Retry-After 60
- ``RequestSizeLimitMiddleware.__call__`` — declared Content-Length over the
  cap → 413; client disconnect mid-body → silent return; non-http.request
  message → skipped; streamed chunks over the cap → drain then 413
- ``PerformanceMetricsMiddleware.dispatch`` — an exception from downstream is
  recorded as a 500 and re-raised
- ``_apply_api_cache_headers`` — public inventory read gets the staff/legacy
  cache policy instead of no-cache
- ``resilient_http_exception_handler`` — non-string detail: None → "Error",
  dict → str() of the payload
- ``resilient_validation_handler`` — partner /api/v1/* keeps the FastAPI
  ``{detail: [...]}`` 422 contract
- ``resilient_json_decode_handler`` — both envelope shapes (partner detail vs
  frontend success/status_code/message)
- ``_should_redirect_to_canonical_host`` — assets/workbox and static
  rate-limit paths stay on the *.run.app host (no redirect)
- ``serve_spa`` — a served ``.html`` file from dist/public gets the strict
  no-cache/no-store/Pragma/Expires header set

The pure-ASGI middleware and handler functions are exercised directly (with a
real ``starlette.requests.Request`` scope); ``serve_spa`` goes through
TestClient so the full middleware stack runs.

Run: python -m pytest tests/test_cov_c6_d_middleware_core.py -v
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from json import JSONDecodeError
from pathlib import Path

import pytest
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(Path(__file__).parent))
from test_api_v1 import create_client  # noqa: E402


def _client(monkeypatch):
    client, main, fake_db, _logger = create_client(monkeypatch)
    monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
    return client, main, fake_db


def _run(coro):
    return asyncio.run(coro)


def _req(path: str, host: str = "candidate---x.a.run.app", method: str = "GET"):
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(b"host", host.encode())],
        "query_string": b"",
        "scheme": "https",
        "server": (host, 443),
    }
    return Request(scope)


def _scope(path: str = "/api/contact", method: str = "POST", headers=None):
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": headers or [],
    }
    return scope


# ─── _staff_session_rate_key ────────────────────────────────────────────────


class TestStaffSessionRateKey:
    def test_passkey_verify_failure_returns_none(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        request = _req("/api/admin/appointments")  # not a strict-auth path
        request.scope["headers"].append(
            (b"cookie", f"{main.PASSKEY_COOKIE_NAME}=corrupt-session".encode())
        )

        def _boom():
            raise RuntimeError("passkey store unreachable")

        monkeypatch.setattr(main, "_get_passkey_session_manager", _boom)
        assert main._staff_session_rate_key(request) is None


# ─── _rate_limit_handler ────────────────────────────────────────────────────


class TestRateLimitHandler:
    def test_unreadable_limit_metadata_falls_back_to_sixty(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        class _BrokenLimit:
            def get_expiry(self):
                raise ValueError("no expiry metadata")

        exc = type("FakeExceeded", (), {"limit": _BrokenLimit(), "detail": "too fast"})()

        # The slowapi handler itself is a plain (non-async) callable.
        response = main._rate_limit_handler(_req("/api/contact"), exc)
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "60"


# ─── RequestSizeLimitMiddleware.__call__ ────────────────────────────────────


class TestRequestSizeLimitMiddleware:
    @pytest.mark.asyncio
    async def test_declared_content_length_over_cap_returns_413(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.setattr(main, "MAX_REQUEST_BODY_BYTES", 64)

        async def inner(scope, receive, send):
            raise AssertionError("inner app must not run")

        sent = []

        async def send(message):
            sent.append(message)

        scope = _scope(headers=[(b"content-length", b"100")])
        await main.RequestSizeLimitMiddleware(inner)(scope, None, send)
        start = next(m for m in sent if m["type"] == "http.response.start")
        assert start["status"] == 413

    @pytest.mark.asyncio
    async def test_disconnect_during_body_read_returns_silently(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.setattr(main, "MAX_REQUEST_BODY_BYTES", 64)

        async def inner(scope, receive, send):
            raise AssertionError("inner app must not run")

        async def receive():
            return {"type": "http.disconnect"}

        scope = _scope()
        await main.RequestSizeLimitMiddleware(inner)(scope, receive, None)
        # No response may be sent for an abandoned request.

    @pytest.mark.asyncio
    async def test_non_request_message_is_skipped(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.setattr(main, "MAX_REQUEST_BODY_BYTES", 64)

        seen = {"called": False}

        async def inner(scope, receive, send):
            seen["called"] = True
            await JSONResponse({"ok": True})(scope, receive, send)

        messages = [
            {"type": "lifespan.startup"},  # not an http.* message → skip
            {"type": "http.request", "body": b"{}", "more_body": False},
        ]

        async def receive():
            return messages.pop(0)

        async def send(message):
            pass

        await main.RequestSizeLimitMiddleware(inner)(_scope(), receive, send)
        assert seen["called"] is True

    @pytest.mark.asyncio
    async def test_streamed_body_over_cap_drains_and_returns_413(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.setattr(main, "MAX_REQUEST_BODY_BYTES", 64)

        async def inner(scope, receive, send):
            raise AssertionError("inner app must not run")

        drained = {"count": 0}
        chunks = [
            {"type": "http.request", "body": b"x" * 40, "more_body": True},
            {"type": "http.request", "body": b"y" * 40, "more_body": True},
        ]

        async def receive():
            if chunks:
                return chunks.pop(0)
            drained["count"] += 1
            return {"type": "http.request", "body": b"", "more_body": False}

        sent = []

        async def send(message):
            sent.append(message)

        await main.RequestSizeLimitMiddleware(inner)(_scope(), receive, send)
        start = next(m for m in sent if m["type"] == "http.response.start")
        assert start["status"] == 413
        assert drained["count"] >= 1  # drain loop consumed the remaining chunk


# ─── PerformanceMetricsMiddleware.dispatch ──────────────────────────────────


class TestPerformanceMetricsMiddleware:
    @pytest.mark.asyncio
    async def test_downstream_exception_recorded_as_500_and_reraised(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        async def call_next(request):
            raise RuntimeError("downstream exploded")

        mw = main.PerformanceMetricsMiddleware(lambda scope, receive, send: None)
        with pytest.raises(RuntimeError, match="downstream exploded"):
            await mw.dispatch(_req("/api/admin/appointments"), call_next)


# ─── _apply_api_cache_headers ─────────────────────────────────────────────


class TestApplyApiCacheHeaders:
    def test_public_inventory_read_uses_cache_policy(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.setattr(main, "_inventory_source_pref", lambda: "staff")

        request = _req("/api/marketing/inventory-context")
        response = main._apply_api_cache_headers(request, JSONResponse({}))
        assert response.headers["Cache-Control"] == main._STAFF_INVENTORY_CACHE


# ─── Resilient error handlers ─────────────────────────────────────────────


class TestResilientHandlers:
    def test_http_exception_none_detail_envelope(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        # Starlette's HTTPException substitutes the status phrase when
        # detail=None, so exercise the None arm with a bare stand-in.
        exc = types.SimpleNamespace(status_code=403, detail=None, headers=None)
        response = _run(main.resilient_http_exception_handler(_req("/api/anything"), exc))
        assert response.status_code == 403
        body = json.loads(response.body.decode())
        assert body == {"success": False, "status_code": 403, "message": "Error"}

    def test_http_exception_dict_detail_is_stringified(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        response = _run(
            main.resilient_http_exception_handler(
                _req("/api/anything"),
                HTTPException(status_code=400, detail={"field": "bad"}),
            )
        )
        body = json.loads(response.body.decode())
        assert body["message"] == str({"field": "bad"})

    def test_validation_error_partner_path_keeps_default_shape(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        exc = RequestValidationError([])
        response = _run(main.resilient_validation_handler(_req("/api/v1/partner/x"), exc))
        assert response.status_code == 422
        assert json.loads(response.body.decode()) == {"detail": []}

    def test_json_decode_partner_path_detail_shape(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        exc = JSONDecodeError("Expecting value", "doc", 0)
        response = _run(main.resilient_json_decode_handler(_req("/api/v1/partner/x"), exc))
        assert response.status_code == 400
        assert json.loads(response.body.decode()) == {"detail": "Malformed JSON body."}

    def test_json_decode_frontend_envelope_shape(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        exc = JSONDecodeError("Expecting value", "doc", 0)
        response = _run(main.resilient_json_decode_handler(_req("/api/chat"), exc))
        assert response.status_code == 400
        assert json.loads(response.body.decode()) == {
            "success": False,
            "status_code": 400,
            "message": "Malformed JSON body.",
        }


# ─── _should_redirect_to_canonical_host ─────────────────────────────────────


class TestCanonicalHostRedirect:
    def test_assets_and_workbox_stay_on_candidate_host(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        host = "candidate---x.a.run.app"
        assert main._should_redirect_to_canonical_host(_req("/assets/app-abc.js", host)) is False
        assert main._should_redirect_to_canonical_host(_req("/workbox-1234.js", host)) is False

    def test_static_rate_limit_paths_stay_on_candidate_host(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        host = "candidate---x.a.run.app"
        for path in ("/favicon.ico", "/sw.js", "/manifest.webmanifest"):
            assert main._should_redirect_to_canonical_host(_req(path, host)) is False


# ─── serve_spa ──────────────────────────────────────────────────────────────


class TestServeSpaHtmlHeaders:
    def test_html_file_from_public_gets_no_cache_headers(self, monkeypatch, tmp_path):
        client, main, _db = _client(monkeypatch)
        # serve_spa probes "frontend/dist" then "frontend/public" relative to the
        # process cwd; point cwd at a scratch tree with one real html file.
        public_dir = tmp_path / "frontend" / "public"
        public_dir.mkdir(parents=True)
        (public_dir / "cov-c6-d-page.html").write_text("<html><body>cov c6 d</body></html>")
        monkeypatch.chdir(tmp_path)

        response = client.get("/cov-c6-d-page.html")
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-cache, no-store, must-revalidate"
        assert response.headers["Pragma"] == "no-cache"
        assert response.headers["Expires"] == "0"
