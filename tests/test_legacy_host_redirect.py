"""Retired tho.sapphirealpha.xyz requests 301 to the canonical storefront.

The match is the Host header only: case-insensitive, with or without a port.
Path and query are preserved, including percent-encoding in the raw
request target. www, the apex twin, Cloud Run revision hosts, and
/healthz/ on those hosts keep their existing responses.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from starlette.requests import Request

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(Path(__file__).parent))
from test_api_v1 import create_client  # noqa: E402

CANONICAL = "https://www.texashomeoutlet.com"
RUN_APP = "candidate---project-go-forward-example.a.run.app"


@pytest.fixture
def client(monkeypatch):
    http, _main, _db, _logger = create_client(monkeypatch)
    return http


def _send(client, method, path, host):
    response = client.request(method, path, headers={"host": host}, follow_redirects=False)
    return response


def test_legacy_host_redirects_with_path_and_query(client):
    cases = [
        ("GET", "/inventory?sort=price&q=oak", "tho.sapphirealpha.xyz"),
        ("GET", "/homes/43372-premier?from=ad", "THO.SapphireAlpha.XYZ"),
        ("HEAD", "/contact?from=legacy", "tho.sapphirealpha.xyz:443"),
        ("POST", "/api/contact?from=legacy", "tho.sapphirealpha.xyz:8443"),
        ("GET", "/?utm=old", "tho.SAPPHIREALPHA.xyz."),
        ("GET", "/admin/login?next=/photos", "Tho.Sapphirealpha.XYZ:443"),
        ("GET", "/healthz/?probe=1", "tho.sapphirealpha.xyz"),
    ]
    for method, path, host in cases:
        response = _send(client, method, path, host)
        assert response.status_code == 301, (method, host, path)
        assert response.headers["location"] == f"{CANONICAL}{path}", (method, host, path)


def _request(path: str, raw_path: bytes, query: bytes, host: str) -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": path,
            "raw_path": raw_path,
            "query_string": query,
            "headers": [(b"host", host.encode("ascii"))],
            "client": ("127.0.0.1", 123),
            "server": (host, 443),
        }
    )


def test_redirect_target_keeps_raw_path_when_scope_path_is_decoded(monkeypatch):
    # ASGI scope["path"] is decoded; raw_path still has the client's bytes.
    _http, loaded, _db, _logger = create_client(monkeypatch)
    request = _request(
        "/foo/bar?baz qux",
        b"/foo%2Fbar%3Fbaz%20qux",
        b"q=oak%20plan",
        "tho.sapphirealpha.xyz",
    )
    assert "%2F" not in request.url.path
    assert loaded._legacy_public_host_target(request) == (
        f"{CANONICAL}/foo%2Fbar%3Fbaz%20qux?q=oak%20plan"
    )

    apex = _request(
        "/foo/bar?baz qux",
        b"/foo%2Fbar%3Fbaz%20qux",
        b"q=oak%20plan",
        "texashomeoutlet.com",
    )
    assert loaded._alternate_public_host_target(apex) == (
        f"{CANONICAL}/foo%2Fbar%3Fbaz%20qux?q=oak%20plan"
    )


def test_redirect_target_preserves_empty_raw_query(monkeypatch):
    """An empty ASGI query string is explicit, not a missing-scope fallback."""
    from types import SimpleNamespace

    from starlette.datastructures import URL

    _http, loaded, _db, _logger = create_client(monkeypatch)
    request = SimpleNamespace(
        scope={"raw_path": b"/foo%3Fbar", "query_string": b""},
        # The decoded URL can reinterpret an encoded %3F as a query delimiter.
        # The raw empty query must take precedence over this fallback.
        url=URL("https://tho.sapphirealpha.xyz/foo?bar"),
    )

    assert loaded._original_target_parts(request) == ("/foo%3Fbar", "")


def test_legacy_host_preserves_percent_encoded_path(client):
    # scope["path"] is decoded. Rebuilding from request.url.path turns
    # %2F into a separator and can turn %3F into a query. The Location
    # must keep the request's original encoding.
    paths = (
        "/foo%3Fbar",
        "/foo%2Fbar",
        "/foo%20bar",
        "/homes/a%2Fb%3Fc%20d?q=oak%20plan",
    )
    for path in paths:
        response = _send(client, "GET", path, "tho.sapphirealpha.xyz")
        assert response.status_code == 301, path
        assert response.headers["location"] == f"{CANONICAL}{path}", path

    posted = _send(client, "POST", "/api/contact%3Fnext", "tho.sapphirealpha.xyz")
    assert posted.status_code == 301
    assert posted.headers["location"] == f"{CANONICAL}/api/contact%3Fnext"


def test_apex_and_run_app_preserve_percent_encoded_path(client):
    path = "/foo%3Fbar%2Fbaz%20qux?q=1%20x"
    apex = _send(client, "GET", path, "texashomeoutlet.com")
    assert apex.status_code == 301
    assert apex.headers["location"] == f"{CANONICAL}{path}"

    run_app = _send(client, "GET", path, RUN_APP)
    assert run_app.status_code == 308
    assert run_app.headers["location"] == f"{CANONICAL}{path}"

    www = _send(client, "GET", "/contact?from=ad%20x", "www.texashomeoutlet.com")
    assert www.status_code == 200
    assert "location" not in www.headers


def test_other_hosts_keep_existing_behavior(client):
    www = _send(client, "GET", "/contact?from=ad", "www.texashomeoutlet.com")
    assert www.status_code == 200
    assert "location" not in www.headers

    apex = _send(client, "GET", "/contact?from=ad", "texashomeoutlet.com")
    assert apex.status_code == 301
    assert apex.headers["location"] == f"{CANONICAL}/contact?from=ad"

    posted = _send(client, "POST", "/api/contact", "texashomeoutlet.com")
    assert posted.status_code not in {301, 302, 307, 308}

    parent = _send(client, "GET", "/contact", "sapphirealpha.xyz")
    assert parent.status_code == 200
    assert "location" not in parent.headers

    lookalike = _send(client, "GET", "/contact", "tho.sapphirealpha.xyz.example")
    assert lookalike.status_code == 200
    assert "location" not in lookalike.headers

    candidate = _send(client, "GET", "/inventory?sort=price", RUN_APP)
    assert candidate.status_code == 308
    assert candidate.headers["location"] == f"{CANONICAL}/inventory?sort=price"

    candidate_staff = _send(client, "GET", "/staff", RUN_APP)
    assert candidate_staff.status_code == 200
    assert "location" not in candidate_staff.headers


def test_healthz_on_non_legacy_hosts_is_unchanged(client):
    hosts = (
        "www.texashomeoutlet.com",
        "testserver",
        "project-go-forward-trgi34bxuq-uc.a.run.app",
        RUN_APP,
    )
    for host in hosts:
        response = _send(client, "GET", "/healthz/", host)
        assert response.status_code == 200, host
        assert response.headers["content-type"].startswith("application/json"), host
        assert response.json()["status"] == "ok", host
        assert "location" not in response.headers

    apex = _send(client, "GET", "/healthz/", "texashomeoutlet.com")
    assert apex.status_code == 301
    assert apex.headers["location"] == f"{CANONICAL}/healthz/"
