"""SSRF guard for inventory- and request-supplied photo fetches."""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import tools.outbound_url as outbound_url
from tools.outbound_url import fetch_public_url, is_public_fetch_url

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
        raise AssertionError("photo fetch must not run")

    monkeypatch.setattr(outbound_url, "_http_get", boom)
    image, source = marketing_tools._load_source_photo(
        home_photo_url="http://169.254.169.254/latest/meta-data/"
    )
    assert image is None
    assert source == "refused_url"


def test_fetch_refuses_before_http(monkeypatch):
    def boom(url, timeout):
        raise AssertionError("photo fetch must not run")

    monkeypatch.setattr(outbound_url, "_http_get", boom)
    with pytest.raises(ValueError, match="refused"):
        fetch_public_url("http://169.254.169.254/latest/meta-data/", timeout=5)


def test_fetch_pins_validated_ip_and_clears_it(monkeypatch):
    calls = []

    def fallback(host, port, *args, **kwargs):
        calls.append(str(host))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.51.100.2", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fallback)

    def fake_http(url, timeout):
        infos = socket.getaddrinfo("cdn.example", 443)
        other = socket.getaddrinfo("other.example", 80)
        assert infos[0][4][0] == "93.184.216.34"
        assert other[0][4][0] == "198.51.100.2"
        assert timeout == 12
        return SimpleNamespace(status_code=200, content=b"ok")

    monkeypatch.setattr(outbound_url, "_http_get", fake_http)
    response = fetch_public_url(
        "https://cdn.example/a.jpg", timeout=12, resolver=_resolver(_PUBLIC)
    )
    assert response.content == b"ok"
    assert calls == ["other.example"]
    socket.getaddrinfo("cdn.example", 443)
    assert calls == ["other.example", "cdn.example"]


def test_http_get_skips_env_proxy_and_redirects(monkeypatch):
    captured = {}

    class FakeSession:
        def __init__(self):
            self.trust_env = True

        def get(self, url, timeout=None, **kwargs):
            captured["trust_env"] = self.trust_env
            captured["timeout"] = timeout
            captured["kwargs"] = kwargs
            return SimpleNamespace(status_code=200)

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(outbound_url.requests, "Session", FakeSession)
    outbound_url._http_get("https://cdn.example/a.jpg", 9)
    assert captured["trust_env"] is False
    assert captured["kwargs"]["allow_redirects"] is False
    assert captured["timeout"] == 9
    assert captured["closed"] is True


def test_path_loaded_video_generator_skips_tools_package_init():
    """The production video smoke loads this file without the tools package."""
    repo = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import importlib.util, sys\n"
            "from pathlib import Path\n"
            "repo = Path(sys.argv[1]).resolve()\n"
            "sys.path = [p for p in sys.path if Path(p or '.').resolve() != repo]\n"
            "spec = importlib.util.spec_from_file_location(\n"
            "    'check_production_video',\n"
            "    repo / 'scripts' / 'check_production_video.py',\n"
            ")\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(mod)\n"
            "try:\n"
            "    import tools\n"
            "except ModuleNotFoundError:\n"
            "    pass\n"
            "else:\n"
            "    raise SystemExit('tools still importable')\n"
            "loaded = mod.load_video_generator()\n"
            "assert callable(loaded.fetch_public_url)\n"
            "assert 'tools.document_engine' not in sys.modules\n"
            "assert 'tools.marketing_tools' not in sys.modules\n",
            str(repo),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
