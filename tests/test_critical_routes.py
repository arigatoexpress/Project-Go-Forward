"""Pages staff and customers are sent to must never 404.

Each route must answer 200, or redirect (301/302/307/308) to something that
does, on the canonical host, the bare apex, and the local test host.
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_api_v1 import create_client

REDIRECTS = {301, 302, 307, 308}
HOSTS = ("www.texashomeoutlet.com", "texashomeoutlet.com", "testserver")
MAX_HOPS = 5

# /privacy and /terms are not on main yet: the legal copy is waiting on owner
# review in draft PRs #368 / #372. strict=True turns this into a failure the
# moment they ship, so the marker has to be removed and the routes stay guarded.
LEGAL_PENDING = pytest.mark.xfail(
    strict=True, reason="legal pages pending owner review (PRs #368 / #372)"
)

CRITICAL_ROUTES = [
    "/",
    "/staff",
    "/photos",
    pytest.param("/privacy", marks=LEGAL_PENDING),
    pytest.param("/terms", marks=LEGAL_PENDING),
]


@pytest.fixture
def client(monkeypatch):
    client, _main, _db, _logger = create_client(monkeypatch)
    return client


def _resolve(client, method: str, path: str, host: str):
    """Follow redirects by hand so each hop's status is checked, not just the last."""
    hops = []
    for _ in range(MAX_HOPS):
        response = client.request(method, path, headers={"host": host}, follow_redirects=False)
        hops.append((host, path, response.status_code))
        if response.status_code not in REDIRECTS:
            return response, hops
        target = urlsplit(response.headers["location"])
        host = target.netloc or host
        path = target.path + (f"?{target.query}" if target.query else "")
    pytest.fail(f"too many redirects: {hops}")


@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("route", CRITICAL_ROUTES)
def test_critical_route_is_200_or_redirect_to_200(client, route, host, method):
    first = client.request(method, route, headers={"host": host}, follow_redirects=False)
    assert first.status_code == 200 or first.status_code in REDIRECTS, (
        f"{method} {host}{route} -> {first.status_code}"
    )
    final, hops = _resolve(client, method, route, host)
    assert final.status_code == 200, f"{method} {route} via {hops} ended {final.status_code}"


def test_photos_is_the_noindex_staff_photo_page(client):
    for path in ("/photos", "/photos/"):
        response = client.get(path, headers={"host": "www.texashomeoutlet.com"})
        assert response.status_code == 200, path
        assert 'name="robots" content="noindex' in response.text, path
        assert response.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize("path", ["/manage-inventory", "/copilot", "/ops-copilot"])
def test_other_staff_menu_tools_are_served_not_404(client, path):
    response = client.get(path, headers={"host": "www.texashomeoutlet.com"})
    assert response.status_code == 200
    assert 'name="robots" content="noindex' in response.text


def test_robots_disallows_staff_photo_and_inventory_tools(client):
    body = client.get("/robots.txt").text
    for path in ("/photos", "/manage-inventory", "/copilot", "/ops-copilot"):
        assert f"Disallow: {path}" in body


def test_staff_photo_api_is_not_shadowed_by_spa_route(client):
    response = client.get("/api/inventory/photos/no-such-home/missing.jpg")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
