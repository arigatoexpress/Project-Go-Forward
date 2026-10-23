"""Retired tho.sapphirealpha.xyz requests 301 to the canonical storefront.

The match is the Host header only: case-insensitive, with or without a port.
Path and query are preserved. www, the apex twin, Cloud Run revision hosts,
and /healthz/ on those hosts keep their existing responses.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

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
