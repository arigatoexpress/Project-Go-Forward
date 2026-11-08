"""HTTP that reaches the app is one 301 to https on the same host.

Google Frontend still answers public HTTP with its own 302 before Cloud Run.
These tests cover the hop this app controls: a trusted proxy header of
``http`` (``X-Forwarded-Proto``, or the RFC 7239 ``Forwarded`` proto when
that header is absent) becomes a single 301 to the https URL of the same
host, path, and query. An https request is not redirected again.

Apex→www and the other host rules stay on the HTTPS follow-up.
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
WWW = "www.texashomeoutlet.com"
APEX = "texashomeoutlet.com"
RUN_APP = "candidate---project-go-forward-example.a.run.app"


@pytest.fixture
def client(monkeypatch):
    http, _main, _db, _logger = create_client(monkeypatch)
    return http


@pytest.fixture
def loaded(monkeypatch):
    _http, main, _db, _logger = create_client(monkeypatch)
    return main


def _scope_request(
    *,
    method: str = "GET",
    scheme: str = "http",
    host: str = WWW,
    path: str = "/contact",
    raw_path: bytes | None = None,
    query: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> Request:
    if raw_path is None:
        raw_path = path.encode("ascii")
    if headers is None:
        headers = [(b"host", host.encode("ascii")), (b"x-forwarded-proto", b"http")]
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": scheme,
            "path": path,
            "raw_path": raw_path,
            "query_string": query,
            "headers": headers,
            "client": ("127.0.0.1", 123),
            "server": (host, 80),
        }
    )


def _get(client, path, host, extra_headers=None):
    headers = {"host": host}
    if extra_headers:
        headers.update(extra_headers)
    return client.get(path, headers=headers, follow_redirects=False)


def test_x_forwarded_proto_http_is_301_to_same_host(client):
    response = _get(client, "/inventory?q=oak%20plan", WWW, {"x-forwarded-proto": "http"})
    assert response.status_code == 301
    assert response.status_code != 302
    assert response.headers["location"] == f"{CANONICAL}/inventory?q=oak%20plan"
    assert "http://" not in response.headers["location"]
    assert b"http://" not in response.content


def test_forwarded_proto_http_is_301_when_x_forwarded_proto_is_absent(client):
    response = _get(
        client,
        "/contact?from=ad",
        WWW,
        {"forwarded": "for=192.0.2.60;proto=http;by=203.0.113.43"},
    )
    assert response.status_code == 301
    assert response.headers["location"] == f"{CANONICAL}/contact?from=ad"


def test_apex_http_stays_on_the_apex_and_https_apex_still_goes_to_www(client):
    """Scheme hop keeps the host. The existing apex→www 301 is the next hop."""
    insecure = _get(client, "/contact?from=ad", APEX, {"x-forwarded-proto": "http"})
    assert insecure.status_code == 301
    assert insecure.headers["location"] == "https://texashomeoutlet.com/contact?from=ad"

    secure_apex = _get(client, "/contact?from=ad", APEX, {"x-forwarded-proto": "https"})
    assert secure_apex.status_code == 301
    assert secure_apex.headers["location"] == f"{CANONICAL}/contact?from=ad"

    secure_www = _get(client, "/contact?from=ad", WWW, {"x-forwarded-proto": "https"})
    assert secure_www.status_code == 200
    assert "location" not in secure_www.headers


def test_https_requests_are_not_redirected_in_a_loop(client):
    """Cloud Run delivers HTTPS as cleartext HTTP plus X-Forwarded-Proto https.

    A direct https URL also wins over a contradictory http proto header.
    Neither case may 301 back to itself.
    """
    behind_proxy = _get(client, "/healthz/?probe=1", WWW, {"x-forwarded-proto": "https"})
    assert behind_proxy.status_code == 200
    assert "location" not in behind_proxy.headers

    direct = client.get(
        "https://testserver/healthz/?probe=1",
        headers={"host": WWW, "x-forwarded-proto": "http"},
        follow_redirects=False,
    )
    assert direct.status_code == 200
    assert "location" not in direct.headers

    omitted = _get(client, "/healthz/?probe=1", WWW)
    assert omitted.status_code == 200
    assert "location" not in omitted.headers


def test_follow_up_of_the_301_is_not_the_same_redirect(client):
    first = _get(client, "/healthz/?probe=1", WWW, {"x-forwarded-proto": "HTTP"})
    assert first.status_code == 301
    assert first.headers["location"] == f"{CANONICAL}/healthz/?probe=1"

    second = _get(client, "/healthz/?probe=1", WWW, {"x-forwarded-proto": "https"})
    assert second.status_code == 200
    assert "location" not in second.headers


def test_run_app_http_upgrades_scheme_without_changing_host(client):
    response = _get(client, "/healthz/", RUN_APP, {"x-forwarded-proto": "http"})
    assert response.status_code == 301
    assert response.headers["location"] == f"https://{RUN_APP}/healthz/"

    follow_up = _get(client, "/healthz/", RUN_APP, {"x-forwarded-proto": "https"})
    assert follow_up.status_code == 200
    assert "location" not in follow_up.headers


def test_insecure_staff_path_keeps_path_until_the_https_request(client):
    insecure = _get(client, "/admin", WWW, {"x-forwarded-proto": "http"})
    assert insecure.status_code == 301
    assert insecure.headers["location"] == f"{CANONICAL}/admin"

    secure = _get(client, "/admin", WWW, {"x-forwarded-proto": "https"})
    assert secure.status_code == 308
    assert secure.headers["location"] == f"{CANONICAL}/staff"


def test_head_redirects_and_post_does_not(client):
    head = client.request(
        "HEAD",
        "/contact",
        headers={"host": WWW, "x-forwarded-proto": "http"},
        follow_redirects=False,
    )
    assert head.status_code == 301
    assert head.headers["location"] == f"{CANONICAL}/contact"

    posted = client.post(
        "/api/contact",
        headers={"host": WWW, "x-forwarded-proto": "http"},
        follow_redirects=False,
    )
    assert posted.status_code not in {301, 302, 307, 308}


def test_percent_encoded_path_is_preserved(client):
    path = "/foo%2Fbar%3Fbaz%20qux?q=oak%20plan"
    response = _get(client, path, WWW, {"x-forwarded-proto": "http"})
    assert response.status_code == 301
    assert response.headers["location"] == f"{CANONICAL}{path}"


def test_host_port_is_not_copied_onto_the_https_url(client):
    response = _get(
        client,
        "/",
        "WWW.TexasHomeOutlet.COM:80",
        {"x-forwarded-proto": " http "},
    )
    assert response.status_code == 301
    assert response.headers["location"] == f"{CANONICAL}/"


def test_unit_targets_and_non_looping_https(loaded):
    insecure = _scope_request(
        path="/café",
        raw_path=b"/foo%2Fbar",
        query=b"q=oak%20plan",
    )
    # scope path is decoded; the redirect must use the raw request target.
    assert insecure.url.path != "/foo%2Fbar"
    assert loaded._insecure_scheme_redirect_target(insecure) == (
        f"{CANONICAL}/foo%2Fbar?q=oak%20plan"
    )

    https_scope = _scope_request(
        scheme="https",
        headers=[(b"host", WWW.encode()), (b"x-forwarded-proto", b"http")],
    )
    assert loaded._insecure_scheme_redirect_target(https_scope) is None

    cloud_run = _scope_request(
        scheme="http",
        headers=[(b"host", WWW.encode()), (b"x-forwarded-proto", b"https")],
    )
    assert loaded._insecure_scheme_redirect_target(cloud_run) is None

    omitted = _scope_request(headers=[(b"host", WWW.encode())])
    assert loaded._proxy_reported_scheme(omitted) == ""
    assert loaded._insecure_scheme_redirect_target(omitted) is None

    posted = _scope_request(method="POST")
    assert loaded._insecure_scheme_redirect_target(posted) is None

    head = _scope_request(method="HEAD", path="/", raw_path=b"/")
    assert loaded._insecure_scheme_redirect_target(head) == f"{CANONICAL}/"


@pytest.mark.parametrize(
    ("forwarded", "expected"),
    [
        ("proto=http", "http"),
        ('for=192.0.2.60;proto="http";by=203.0.113.43', "http"),
        ("Proto=HTTPS", "https"),
        ("proto=http, proto=https", "http"),
        ("proto=https, proto=http", "https"),
        ("for=192.0.2.43", ""),
        ("proto=ftp", "ftp"),
        (' proto = "http" ', "http"),
    ],
)
def test_forwarded_header_proto_uses_the_leftmost_element(loaded, forwarded, expected):
    request = _scope_request(
        headers=[(b"host", WWW.encode()), (b"forwarded", forwarded.encode("ascii"))]
    )
    assert loaded._forwarded_header_proto(request) == expected


def test_x_forwarded_proto_wins_over_a_contradictory_forwarded_header(loaded):
    """Cloud Run's X-Forwarded-Proto is authoritative when both are present."""
    spoofed_secure = _scope_request(
        headers=[
            (b"host", WWW.encode()),
            (b"x-forwarded-proto", b"http"),
            (b"forwarded", b"proto=https"),
        ]
    )
    assert loaded._proxy_reported_scheme(spoofed_secure) == "http"
    assert loaded._insecure_scheme_redirect_target(spoofed_secure) == f"{CANONICAL}/contact"

    real_secure = _scope_request(
        headers=[
            (b"host", WWW.encode()),
            (b"x-forwarded-proto", b"https, http"),
            (b"forwarded", b"proto=http"),
        ]
    )
    assert loaded._proxy_reported_scheme(real_secure) == "https"
    assert loaded._insecure_scheme_redirect_target(real_secure) is None


def test_unsafe_host_is_not_an_open_redirect(loaded):
    request = _scope_request(
        host="evil.com",
        headers=[(b"host", b"evil.com/path"), (b"x-forwarded-proto", b"http")],
    )
    assert loaded._insecure_scheme_redirect_target(request) is None
