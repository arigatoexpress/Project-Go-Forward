"""CSP must be byte-identical to the baseline when no analytics ID is set, and
widen ONLY (and exactly) for validly-configured vendor IDs.

style-src is ``'self'`` plus Google Fonts stylesheets: no ``'unsafe-inline'``,
nonce, or hash (COD-153), so the header stays static and cache-safe.
"""

from test_api_v1 import create_client

_BASELINE_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com; "
    "img-src 'self' https://d132mt2yijm03y.cloudfront.net https: data:; "
    "frame-src https://my.matterport.com; "
    "connect-src 'self'; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    "frame-ancestors 'none'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "script-src-attr 'none'; "
    "worker-src 'self'; "
    "manifest-src 'self'"
)
_HSTS = "max-age=63072000; includeSubDomains; preload"

_ANALYTICS_ENV = ("GA4_MEASUREMENT_ID", "GTM_CONTAINER_ID", "META_PIXEL_ID", "TIKTOK_PIXEL_ID")


def _csp(client):
    return client.get("/healthz/").headers["content-security-policy"]


def test_csp_byte_identical_when_analytics_unconfigured(monkeypatch):
    for var in _ANALYTICS_ENV:
        monkeypatch.delenv(var, raising=False)
    client, *_ = create_client(monkeypatch)
    assert _csp(client) == _BASELINE_CSP


def test_csp_unchanged_for_malformed_id(monkeypatch):
    for var in _ANALYTICS_ENV:
        monkeypatch.delenv(var, raising=False)
    client, *_ = create_client(monkeypatch)
    monkeypatch.setenv("GA4_MEASUREMENT_ID", "garbage!!!")  # fails the format regex
    assert _csp(client) == _BASELINE_CSP


def test_csp_widens_only_for_configured_ga4(monkeypatch):
    for var in _ANALYTICS_ENV:
        monkeypatch.delenv(var, raising=False)
    client, *_ = create_client(monkeypatch)
    monkeypatch.setenv("GA4_MEASUREMENT_ID", "G-ABC1234XYZ")
    csp = _csp(client)
    assert "https://www.googletagmanager.com" in csp
    assert "https://www.google-analytics.com" in csp
    # unconfigured vendors must NOT be added
    assert "facebook" not in csp
    assert "tiktok" not in csp


def test_csp_widens_for_meta_and_tiktok(monkeypatch):
    for var in _ANALYTICS_ENV:
        monkeypatch.delenv(var, raising=False)
    client, *_ = create_client(monkeypatch)
    monkeypatch.setenv("META_PIXEL_ID", "1234567890123456")
    monkeypatch.setenv("TIKTOK_PIXEL_ID", "CABCDEF1234567890GHIJK")
    csp = _csp(client)
    assert "https://connect.facebook.net" in csp
    assert "https://analytics.tiktok.com" in csp


def _directive(csp: str, name: str) -> str:
    prefix = f"{name} "
    for part in csp.split("; "):
        if part == name or part.startswith(prefix):
            return part
    raise AssertionError(f"{name} missing from CSP: {csp}")


def test_style_src_omits_unsafe_inline(monkeypatch):
    """COD-153: style-src is static (no nonce, no hash, no unsafe-inline)."""
    for var in _ANALYTICS_ENV:
        monkeypatch.delenv(var, raising=False)
    client, *_ = create_client(monkeypatch)
    style_src = _directive(_csp(client), "style-src")
    assert style_src == "style-src 'self' https://fonts.googleapis.com"
    assert "'unsafe-inline'" not in style_src
    assert "nonce-" not in style_src
    assert "'sha" not in style_src


def test_baseline_headers_are_strict(monkeypatch):
    """Locked response headers for an HTTP test request (no upgrade directive)."""
    for var in _ANALYTICS_ENV:
        monkeypatch.delenv(var, raising=False)
    client, *_ = create_client(monkeypatch)
    headers = client.get("/healthz/").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["x-xss-protection"] == "0"
    assert headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert headers["strict-transport-security"] == _HSTS
    assert headers["cross-origin-opener-policy"] == "same-origin-allow-popups"
    assert headers["x-permitted-cross-domain-policies"] == "none"
    assert headers["x-dns-prefetch-control"] == "off"
    policy = headers["permissions-policy"]
    assert "camera=()" in policy
    assert "microphone=()" in policy
    assert "geolocation=()" in policy
    assert "publickey-credentials-get=(self)" in policy
    assert "web-share=(self)" in policy
    assert "payment=()" in policy
    assert "server" not in headers
    assert "x-powered-by" not in headers
    assert "upgrade-insecure-requests" not in headers["content-security-policy"]


def test_https_requests_upgrade_insecure_content(monkeypatch):
    for var in _ANALYTICS_ENV:
        monkeypatch.delenv(var, raising=False)
    client, *_ = create_client(monkeypatch)
    response = client.get("/healthz/", headers={"X-Forwarded-Proto": "https"})
    assert response.headers["content-security-policy"] == (
        _BASELINE_CSP + "; upgrade-insecure-requests"
    )


def test_direct_https_requests_upgrade_insecure_content(monkeypatch):
    for var in _ANALYTICS_ENV:
        monkeypatch.delenv(var, raising=False)
    client, *_ = create_client(monkeypatch)
    response = client.get(
        "https://testserver/healthz/",
        # The direct HTTPS URL takes precedence over a contradictory proxy header.
        headers={"X-Forwarded-Proto": "http"},
    )
    assert response.request.url.scheme == "https"
    assert response.headers["content-security-policy"] == (
        _BASELINE_CSP + "; upgrade-insecure-requests"
    )


def test_security_middleware_strips_server_disclosure(monkeypatch):
    import asyncio

    from starlette.requests import Request
    from starlette.responses import Response

    client, main, *_ = create_client(monkeypatch)
    del client
    middleware = main.SecurityHeadersMiddleware(app=None)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/healthz/",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("test", 80),
            "client": ("127.0.0.1", 123),
        }
    )

    async def call_next(_request):
        response = Response("ok")
        response.headers["Server"] = "uvicorn/0.34.0"
        response.headers["X-Powered-By"] = "Express"
        return response

    response = asyncio.run(middleware.dispatch(request, call_next))
    assert "server" not in response.headers
    assert "x-powered-by" not in response.headers
    assert response.headers["x-content-type-options"] == "nosniff"


def test_style_src_unchanged_when_analytics_widens_script_src(monkeypatch):
    for var in _ANALYTICS_ENV:
        monkeypatch.delenv(var, raising=False)
    client, *_ = create_client(monkeypatch)
    monkeypatch.setenv("GA4_MEASUREMENT_ID", "G-ABC1234XYZ")
    style_src = _directive(_csp(client), "style-src")
    assert style_src == "style-src 'self' https://fonts.googleapis.com"
    script_src = _directive(_csp(client), "script-src")
    assert "https://www.googletagmanager.com" in script_src
    assert "'unsafe-inline'" not in script_src
