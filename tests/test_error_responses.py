"""Tests for the resilient JSON error envelope + Cache-Control headers.

Covers:
  * 404 on unknown /api path returns {success, status_code, message} + no-cache
  * 500 raised inside a handler returns the same wrapper shape + no-cache
  * Non-/api 404 (SPA fallback for a missing static file) does NOT get
    Cache-Control stamped by the API middleware
  * GET /api/inventory carries the long-cache header
  * GET /api/customers/* and /api/deals/* carry no-cache

Run: python -m pytest tests/test_error_responses.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent))

from test_api_v1 import create_client  # noqa: E402


def _add_route_before_spa_catchall(app, path: str, handler, methods=("GET",)):
    """Insert a route at the front of the router so it wins over the
    /{full_path:path} SPA catch-all that main.py registers at import time.

    FastAPI matches routes top-to-bottom; appending after the catch-all means
    the catch-all wins. We splice the new route in at index 0 instead.
    """
    app.add_api_route(path, handler, methods=list(methods))
    # The route we just added is now at the END of the list. Move it to the
    # front so it takes precedence over the SPA catch-all.
    new_route = app.router.routes.pop()
    app.router.routes.insert(0, new_route)


# ─── Wrapper shape ───────────────────────────────────────────────────────────


def test_unknown_api_path_returns_wrapper_envelope(monkeypatch):
    client, _main, _db, _logger = create_client(monkeypatch)

    response = client.get("/api/this-route-does-not-exist")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body == {
        "success": False,
        "status_code": 404,
        "message": "Not Found",
    }


def test_unknown_api_path_carries_no_cache_header(monkeypatch):
    client, _main, _db, _logger = create_client(monkeypatch)

    response = client.get("/api/nope")

    assert response.headers.get("Cache-Control") == "no-cache"


def test_500_via_test_only_endpoint_uses_wrapper_envelope(monkeypatch):
    """Mount a transient endpoint that always raises HTTPException(500)."""
    client, main, _db, _logger = create_client(monkeypatch)

    async def _boom():
        raise HTTPException(status_code=500, detail="kaboom")

    _add_route_before_spa_catchall(main.app, "/api/__test_boom__", _boom)

    response = client.get("/api/__test_boom__")

    assert response.status_code == 500
    body = response.json()
    assert body["success"] is False
    assert body["status_code"] == 500
    assert body["message"] == "kaboom"
    assert response.headers.get("Cache-Control") == "no-cache"


def test_400_inherits_wrapper_envelope(monkeypatch):
    """Sanity: 4xx other than 404 also gets wrapped."""
    client, main, _db, _logger = create_client(monkeypatch)

    async def _bad():
        raise HTTPException(status_code=400, detail="bad input")

    _add_route_before_spa_catchall(main.app, "/api/__test_bad__", _bad)

    response = client.get("/api/__test_bad__")

    assert response.status_code == 400
    body = response.json()
    assert body == {
        "success": False,
        "status_code": 400,
        "message": "bad input",
    }


# ─── Cache-Control on the public inventory read-side ─────────────────────────


def test_get_api_inventory_uses_long_cache_header(monkeypatch):
    """Successful /api/inventory GET advertises 1h cache + SWR window."""
    client, main, _db, _logger = create_client(monkeypatch)

    # The handler is admin-protected, so inject a valid admin token.
    token = main._create_admin_token()

    response = client.get(
        "/api/inventory",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    cc = response.headers.get("Cache-Control", "")
    assert "max-age=3600" in cc
    assert "public" in cc
    assert "stale-while-revalidate=60" in cc


def test_get_api_inventory_item_uses_long_cache_header(monkeypatch):
    """Stand up a /api/inventory/{id} stub on the same app and check headers."""
    client, main, _db, _logger = create_client(monkeypatch)

    async def _get_item(item_id: str):
        return {"success": True, "id": item_id}

    _add_route_before_spa_catchall(main.app, "/api/inventory/{item_id}", _get_item)

    response = client.get("/api/inventory/abc123")

    assert response.status_code == 200
    cc = response.headers.get("Cache-Control", "")
    assert "max-age=3600" in cc
    assert "stale-while-revalidate=60" in cc


def test_inventory_404_still_returns_long_cache_header(monkeypatch):
    """A 404 on the public inventory surface should still be cacheable so we
    don't hammer Firestore for known-missing IDs."""
    client, main, _db, _logger = create_client(monkeypatch)

    async def _missing(item_id: str):
        raise HTTPException(status_code=404, detail="inventory item not found")

    _add_route_before_spa_catchall(main.app, "/api/inventory/{item_id}", _missing)

    response = client.get("/api/inventory/ghost")

    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["status_code"] == 404
    cc = response.headers.get("Cache-Control", "")
    assert "max-age=3600" in cc


# ─── No-cache on the dynamic CRM surface ─────────────────────────────────────


def test_get_api_customers_uses_no_cache(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch)
    token = main._create_admin_token()

    response = client.get(
        "/api/customers/search?q=test",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Whether the search has results or not, the cache header must be no-cache.
    assert response.headers.get("Cache-Control") == "no-cache"


def test_get_api_deals_uses_no_cache(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch)
    token = main._create_admin_token()

    response = client.get(
        "/api/deals",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.headers.get("Cache-Control") == "no-cache"


# ─── Non-/api paths must not be stamped by the API cache middleware ──────────


def test_non_api_path_does_not_get_api_cache_header(monkeypatch):
    """The SPA fallback sets its own Cache-Control; the API middleware must
    NOT overwrite or stamp anything outside /api/*."""
    client, _main, _db, _logger = create_client(monkeypatch)

    response = client.get("/some-spa-route")

    # SPA fallback sets its own no-cache/no-store header on index.html.
    # The important assertion is: the value is the SPA's chosen header
    # (no-cache, no-store, must-revalidate), NOT the bare "no-cache" string
    # that the API middleware would have stamped.
    cc = response.headers.get("Cache-Control", "")
    assert cc != "no-cache"
    # Either the SPA's full directive or no header at all is acceptable here.
    if cc:
        assert "no-store" in cc or "must-revalidate" in cc
