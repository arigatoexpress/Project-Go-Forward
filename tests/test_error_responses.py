"""Tests for the resilient JSON error envelope + Cache-Control headers.

Covers:
  * 404 on unknown /api path returns {success, status_code, message} + no-cache
  * 500 raised inside a handler returns the same wrapper shape + no-cache
  * Non-/api 404 (SPA fallback for a missing static file) does NOT get
    Cache-Control stamped by the API middleware
  * GET /api/marketing/inventory-context carries the long-cache header
  * GET /api/inventory carries no-cache because it is admin-protected
  * GET /api/customers/* and /api/deals/* carry no-cache

Run: python -m pytest tests/test_error_responses.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent))

from test_api_v1 import REPO_ROOT, create_client  # noqa: E402


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


def test_vite_assets_use_immutable_cache(monkeypatch):
    client, _main, _db, _logger = create_client(monkeypatch)
    asset = REPO_ROOT / "frontend" / "dist" / "assets" / "app.deadbeef.js"
    asset.write_text("console.log('asset cache test')\n")

    response = client.get("/assets/app.deadbeef.js")

    assert response.status_code == 200
    assert response.headers.get("Cache-Control") == "public, max-age=31536000, immutable"


def test_static_assets_do_not_consume_global_rate_limit(monkeypatch):
    """SPA bundles must not burn the per-IP API/navigation allowance.

    A single page load can request several fingerprinted JS/CSS chunks plus
    PWA files. If those static files count toward the legacy global limiter,
    fast but normal navigation can start returning JSON 429s instead of app
    HTML/assets.
    """
    client, _main, _db, _logger = create_client(monkeypatch, rate_limit_rpm="2")
    asset = REPO_ROOT / "frontend" / "dist" / "assets" / "app.deadbeef.js"
    asset.write_text("console.log('asset cache test')\n")
    service_worker = REPO_ROOT / "frontend" / "dist" / "registerSW.js"
    service_worker.write_text("console.log('sw registration')\n")
    icon = REPO_ROOT / "frontend" / "dist" / "tex-icon.svg"
    icon.write_text('<svg xmlns="http://www.w3.org/2000/svg" />\n')

    static_paths = [
        "/assets/app.deadbeef.js",
        "/assets/app.deadbeef.js",
        "/registerSW.js",
        "/tex-icon.svg",
    ]
    for path in static_paths:
        assert client.get(path).status_code == 200

    first_dynamic = client.get("/api/this-route-does-not-exist")
    second_dynamic = client.get("/api/this-route-does-not-exist")
    third_dynamic = client.get("/api/this-route-does-not-exist")

    assert first_dynamic.status_code == 404
    assert second_dynamic.status_code == 404
    assert third_dynamic.status_code == 429


def test_spa_shell_routes_do_not_consume_global_rate_limit(monkeypatch):
    """Direct SPA navigations should not spend the API/chat rate budget."""
    client, _main, _db, _logger = create_client(monkeypatch, rate_limit_rpm="2")

    for path in ["/", "/chat", "/documents", "/system"]:
        assert client.get(path).status_code == 200

    first_dynamic = client.get("/api/this-route-does-not-exist")
    second_dynamic = client.get("/api/this-route-does-not-exist")
    third_dynamic = client.get("/api/this-route-does-not-exist")

    assert first_dynamic.status_code == 404
    assert second_dynamic.status_code == 404
    assert third_dynamic.status_code == 429


def test_run_app_document_center_redirects_to_canonical_domain(monkeypatch):
    """Raw Cloud Run URLs should not become the customer/admin URL of record."""
    client, _main, _db, _logger = create_client(monkeypatch)

    response = client.get(
        "/documents?from=email",
        headers={"host": "project-go-forward-trgi34bxuq-uc.a.run.app"},
        follow_redirects=False,
    )

    assert response.status_code == 308
    assert response.headers["location"] == "https://www.texashomeoutlet.com/documents?from=email"


def test_run_app_api_and_health_paths_do_not_redirect(monkeypatch):
    """Health probes and API diagnostics must keep working on Cloud Run hostnames."""
    client, _main, _db, _logger = create_client(monkeypatch)
    host = {"host": "project-go-forward-trgi34bxuq-uc.a.run.app"}

    health = client.get("/healthz/", headers=host, follow_redirects=False)
    api = client.get("/api/documents/templates", headers=host, follow_redirects=False)

    assert health.status_code == 200
    assert api.status_code not in {301, 302, 307, 308}
    assert "location" not in api.headers


def test_run_app_seo_machine_paths_do_not_redirect(monkeypatch):
    client, _main, _db, _logger = create_client(monkeypatch)
    host = {"host": "candidate---project-go-forward-trgi34bxuq-uc.a.run.app"}

    robots = client.get("/robots.txt", headers=host, follow_redirects=False)
    sitemap = client.get("/sitemap.xml", headers=host, follow_redirects=False)

    assert robots.status_code == 200
    assert sitemap.status_code == 200
    assert "Sitemap: https://www.texashomeoutlet.com/sitemap.xml" in robots.text
    assert "https://www.texashomeoutlet.com/" in sitemap.text


def test_direct_html_files_use_no_store(monkeypatch):
    client, _main, _db, _logger = create_client(monkeypatch)
    studio = REPO_ROOT / "frontend" / "dist" / "studio.html"
    studio.write_text("<html><body>studio</body></html>")

    response = client.get("/studio.html")

    assert response.status_code == 200
    assert response.headers.get("Cache-Control") == "no-cache, no-store, must-revalidate"


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


# ─── Cache-Control on inventory surfaces ─────────────────────────────────────


def test_get_api_inventory_uses_no_cache_header(monkeypatch):
    """Admin /api/inventory responses must never be public-cacheable."""
    client, main, _db, _logger = create_client(monkeypatch)

    # The handler is admin-protected, so inject a valid admin token.
    token = main._create_admin_token()

    response = client.get(
        "/api/inventory",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.headers.get("Cache-Control") == "no-cache"


def test_get_api_inventory_item_uses_no_cache_header(monkeypatch):
    """Admin-style /api/inventory/{id} reads inherit no-cache."""
    client, main, _db, _logger = create_client(monkeypatch)

    async def _get_item(item_id: str):
        return {"success": True, "id": item_id}

    _add_route_before_spa_catchall(main.app, "/api/inventory/{item_id}", _get_item)

    response = client.get("/api/inventory/abc123")

    assert response.status_code == 200
    assert response.headers.get("Cache-Control") == "no-cache"


def test_inventory_404_still_returns_no_cache_header(monkeypatch):
    """Admin inventory misses should not be retained by shared/browser caches."""
    client, main, _db, _logger = create_client(monkeypatch)

    async def _missing(item_id: str):
        raise HTTPException(status_code=404, detail="inventory item not found")

    _add_route_before_spa_catchall(main.app, "/api/inventory/{item_id}", _missing)

    response = client.get("/api/inventory/ghost")

    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["status_code"] == 404
    assert response.headers.get("Cache-Control") == "no-cache"


def test_marketing_inventory_context_uses_long_cache_header(monkeypatch):
    """The unauthenticated marketing inventory context remains public-cacheable."""
    client, _main, _db, _logger = create_client(monkeypatch)

    response = client.get("/api/marketing/inventory-context")

    assert response.status_code == 200
    cc = response.headers.get("Cache-Control", "")
    assert "max-age=3600" in cc
    assert "public" in cc
    assert "stale-while-revalidate=60" in cc


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


# ─── Malformed body / validation envelope ────────────────────────────────────


def test_malformed_json_body_returns_wrapper_envelope(monkeypatch):
    """`await request.json()` raising JSONDecodeError previously surfaced as
    Starlette's default `text/plain` 500 ("Internal Server Error"), breaking
    the JSON contract. Confirm the new exception handler wraps it.
    """
    client, _main, _db, _logger = create_client(monkeypatch)

    response = client.post(
        "/api/admin/verify",
        content="not json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body["success"] is False


def test_empty_body_to_admin_verify_returns_envelope(monkeypatch):
    """The admin verify endpoint defensively handles missing/empty bodies
    instead of letting `await request.json()` raise.
    """
    client, _main, _db, _logger = create_client(monkeypatch)

    response = client.post(
        "/api/admin/verify",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    body = response.json()
    assert body.get("success") is False


def test_admin_verify_rejects_non_object_body(monkeypatch):
    """A JSON body that parses but isn't an object (e.g. a string) must
    return a 400 envelope, not a 500.
    """
    client, _main, _db, _logger = create_client(monkeypatch)

    response = client.post(
        "/api/admin/verify",
        json="just a string",
    )

    assert response.status_code == 400
    body = response.json()
    assert body.get("success") is False


# ─── /openapi.json, /docs, /redoc must be hidden in production ──────────────


def test_openapi_docs_disabled_in_cloud_run(monkeypatch):
    """When K_SERVICE is set (Cloud Run), /openapi.json, /docs, /redoc
    must be 404. They expose the full API surface (every admin route, every
    operation ID) to anonymous attackers.
    """
    monkeypatch.setenv("K_SERVICE", "project-go-forward")
    monkeypatch.delenv("EXPOSE_API_DOCS", raising=False)
    client, _main, _db, _logger = create_client(monkeypatch)

    for path in ("/openapi.json", "/docs", "/redoc"):
        response = client.get(path)
        # In Cloud Run, these paths fall through to the SPA catch-all,
        # which serves index.html for non-/api unknown paths. Assert they
        # do NOT serve the OpenAPI/Swagger/ReDoc payload.
        body_lower = response.text.lower()
        assert "openapi" not in body_lower or "<!doctype html>" in body_lower, path
        assert "swagger-ui" not in body_lower, path
        assert "redoc" not in body_lower or "<!doctype html>" in body_lower, path


def test_openapi_docs_available_in_local_dev(monkeypatch):
    """Without K_SERVICE, /openapi.json should still work for developers."""
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("EXPOSE_API_DOCS", raising=False)
    client, _main, _db, _logger = create_client(monkeypatch)

    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body.get("openapi", "").startswith("3.")


def test_openapi_docs_can_be_force_enabled_in_cloud_run(monkeypatch):
    """EXPOSE_API_DOCS=1 lets operators turn docs back on for debugging."""
    monkeypatch.setenv("K_SERVICE", "project-go-forward")
    monkeypatch.setenv("EXPOSE_API_DOCS", "1")
    client, _main, _db, _logger = create_client(monkeypatch)

    response = client.get("/openapi.json")
    assert response.status_code == 200
    body = response.json()
    assert body.get("openapi", "").startswith("3.")


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
