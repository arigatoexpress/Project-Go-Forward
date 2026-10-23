"""SSRF guard for inventory- and request-supplied photo fetches."""

from __future__ import annotations

import socket

import pytest

from tools.outbound_url import is_public_fetch_url

_PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
_PRIVATE = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 0))]
_METADATA = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))]


def _resolver(rows):
    def resolve(host, port, *args, **kwargs):
        return rows

    return resolve


def test_https_public_host_is_allowed():
    assert is_public_fetch_url("https://cdn.example/photo.jpg", resolver=_resolver(_PUBLIC))


def test_http_public_host_is_allowed():
    assert is_public_fetch_url("http://cdn.example/photo.jpg", resolver=_resolver(_PUBLIC))


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://cdn.example/",
        "https://localhost/a.jpg",
        "https://metadata.google.internal/computeMetadata/v1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1/",
        "http://10.0.0.8/",
        "http://[::1]/",
        "http://2130706433/",
        "https://cdn.example/a.jpg",
    ],
)
def test_unsafe_urls_are_refused(url):
    resolver = _resolver(_METADATA if "cdn.example" in url else _PUBLIC)
    assert is_public_fetch_url(url, resolver=resolver) is False


def test_userinfo_url_is_refused():
    userinfo = "user" + ":" + "pw"
    url = f"https://{userinfo}@cdn.example/a.jpg"
    assert is_public_fetch_url(url, resolver=_resolver(_PUBLIC)) is False


def test_dns_failure_refuses():
    def resolve(host, port, *args, **kwargs):
        raise socket.gaierror("no such host")

    assert is_public_fetch_url("https://missing.example/a.jpg", resolver=resolve) is False


def test_private_resolution_refuses():
    assert is_public_fetch_url("https://cdn.example/a.jpg", resolver=_resolver(_PRIVATE)) is False


def test_mapped_loopback_refuses():
    assert is_public_fetch_url("http://[::ffff:127.0.0.1]/", resolver=_resolver(_PUBLIC)) is False


def test_flyer_photo_does_not_fetch_metadata(monkeypatch):
    import tools.marketing_tools as marketing_tools

    def boom(*args, **kwargs):
        raise AssertionError("requests.get must not run")

    monkeypatch.setattr(marketing_tools.requests, "get", boom)
    image, source = marketing_tools._load_source_photo(
        home_photo_url="http://169.254.169.254/latest/meta-data/"
    )
    assert image is None
    assert source == "refused_url"
