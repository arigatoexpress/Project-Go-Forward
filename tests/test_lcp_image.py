"""Derived hero JPEG for the inventory Largest Contentful Paint image."""

import io
from pathlib import Path

import pytest
from PIL import Image

from tools.image_storage import PhotoStorageError, PhotoValidationError
from tools.lcp_image import (
    LCP_IMAGE_PATH,
    clear_lcp_cache,
    hero_url_is_resizable,
    lcp_jpeg_for_url,
    render_lcp_jpeg,
)


def test_lcp_image_css_is_in_the_stylesheet_not_an_inline_style():
    """CSP style-src does not allow a style attribute, so the hero class is in CSS."""
    css = (Path(__file__).resolve().parent.parent / "frontend" / "src" / "index.css").read_text()
    assert "img#tho-lcp" in css
    assert "position: fixed" in css
    assert "pointer-events: none" in css


CDN = "https://d132mt2yijm03y.cloudfront.net/manufacturer/1944/floorplan/1/home.jpg"
STAFF = "/api/inventory/photos/28102/hero.jpg"


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_lcp_cache()
    yield
    clear_lcp_cache()


def _jpeg(width=900, height=700) -> bytes:
    image = Image.new("RGB", (width, height), (18, 64, 120))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=90)
    return out.getvalue()


def test_only_staff_and_catalog_hosts_are_resizable():
    assert hero_url_is_resizable(STAFF) is True
    assert hero_url_is_resizable(CDN) is True
    assert hero_url_is_resizable(f"{CDN}?w=400") is True
    refused = [
        "",
        "https://img.example.com/creole.jpg",
        "http://d132mt2yijm03y.cloudfront.net/home.jpg",
        "https://" + "user" + ":" + "pass" + "@d132mt2yijm03y.cloudfront.net/home.jpg",
        "https://d132mt2yijm03y.cloudfront.net:8443/home.jpg",
        "https://d132mt2yijm03y.cloudfront.net.evil.example/home.jpg",
        "https://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/",
        f"{STAFF}?x=1",
        "https://www.texashomeoutlet.com/api/inventory/photos/1/a.jpg",
        "https://127.0.0.1/api/inventory/photos/1/a.jpg",
        "a" * 2049,
    ]
    assert [hero_url_is_resizable(url) for url in refused] == [False] * len(refused)


def test_render_shrinks_the_hero_jpeg():
    source = _jpeg()
    rendered = render_lcp_jpeg(source)
    assert rendered.startswith(b"\xff\xd8")
    assert len(rendered) < len(source)
    with Image.open(io.BytesIO(rendered)) as image:
        assert max(image.size) <= 720
        assert image.format == "JPEG"


def test_staff_photo_is_read_from_storage_not_http(monkeypatch):
    def refuse_fetch(*_args, **_kwargs):
        raise AssertionError("staff photos must not be fetched over HTTP")

    monkeypatch.setattr("tools.lcp_image.fetch_public_url", refuse_fetch)
    monkeypatch.setattr(
        "tools.lcp_image.image_storage.get_photo",
        lambda home_id, filename: (_jpeg(), "image/jpeg"),
    )

    first = lcp_jpeg_for_url(STAFF)
    second = lcp_jpeg_for_url(STAFF)
    assert first is not None and first == second

    monkeypatch.setattr(
        "tools.lcp_image.image_storage.get_photo",
        lambda home_id, filename: None,
    )
    clear_lcp_cache()
    assert lcp_jpeg_for_url(STAFF) is None


def test_storage_errors_do_not_fetch_or_raise(monkeypatch):
    def refuse_fetch(*_args, **_kwargs):
        raise AssertionError("storage failures must not fall through to HTTP")

    monkeypatch.setattr("tools.lcp_image.fetch_public_url", refuse_fetch)

    def explode(home_id, filename):
        raise PhotoValidationError("bad name")

    monkeypatch.setattr("tools.lcp_image.image_storage.get_photo", explode)
    assert lcp_jpeg_for_url(STAFF) is None

    def unavailable(home_id, filename):
        raise PhotoStorageError("down")

    monkeypatch.setattr("tools.lcp_image.image_storage.get_photo", unavailable)
    assert lcp_jpeg_for_url(STAFF) is None


def test_catalog_redirect_and_refused_urls_return_nothing(monkeypatch):
    calls = []

    class Redirect:
        status_code = 302
        content = b"go-away"

        def close(self):
            calls.append("closed")

    def fetch(url, timeout):
        calls.append(url)
        if "redirect" in url:
            return Redirect()
        raise ValueError("refused non-public URL")

    monkeypatch.setattr("tools.lcp_image.fetch_public_url", fetch)
    redirect = CDN.replace("home.jpg", "redirect.jpg")
    assert lcp_jpeg_for_url(redirect) is None
    assert calls == [redirect, "closed"]
    assert lcp_jpeg_for_url("https://169.254.169.254/latest/meta-data/") is None
    assert lcp_jpeg_for_url("https://img.example.com/creole.jpg") is None


def test_catalog_jpeg_is_cached_until_cleared(monkeypatch):
    calls = []

    class Ok:
        status_code = 200
        content = _jpeg()

        def close(self):
            return None

    def fetch(url, timeout):
        calls.append((url, timeout))
        return Ok()

    monkeypatch.setattr("tools.lcp_image.fetch_public_url", fetch)
    first = lcp_jpeg_for_url(CDN)
    second = lcp_jpeg_for_url(CDN)
    assert first is not None and first == second
    assert calls == [(CDN, 4)]
    clear_lcp_cache()
    assert lcp_jpeg_for_url(CDN) == first
    assert len(calls) == 2


def test_oversized_or_unreadable_bytes_return_nothing(monkeypatch):
    monkeypatch.setattr(
        "tools.lcp_image.image_storage.get_photo",
        lambda home_id, filename: (b"x" * (8 * 1024 * 1024 + 1), "image/jpeg"),
    )
    assert lcp_jpeg_for_url(STAFF) is None
    clear_lcp_cache()
    monkeypatch.setattr(
        "tools.lcp_image.image_storage.get_photo",
        lambda home_id, filename: (b"not-an-image", "image/jpeg"),
    )
    assert lcp_jpeg_for_url(STAFF) is None
    with pytest.raises(ValueError):
        render_lcp_jpeg(b"")


def test_lcp_endpoint_ignores_a_visitor_supplied_url(monkeypatch):
    from test_api_v1 import create_client

    client, app_main, _db, _logger = create_client(monkeypatch)
    seen = []

    def hero():
        return STAFF

    def derive(url):
        seen.append(url)
        return b"\xff\xd8\xff\xd9"

    monkeypatch.setattr(app_main.seo_routes, "_inventory_hero_image", hero)
    monkeypatch.setattr(app_main, "lcp_jpeg_for_url", derive)

    response = client.get(f"{LCP_IMAGE_PATH}?url=http://169.254.169.254/latest/meta-data/")
    assert response.status_code == 200
    assert response.content == b"\xff\xd8\xff\xd9"
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "public, max-age=600, stale-while-revalidate=86400"
    assert seen == [STAFF]

    monkeypatch.setattr(
        app_main.seo_routes, "_inventory_hero_image", lambda: "https://img.example.com/x.jpg"
    )
    missing = client.get(LCP_IMAGE_PATH)
    assert missing.status_code == 404
    assert seen == [STAFF]


def test_lcp_endpoint_returns_404_when_conversion_fails(monkeypatch):
    from test_api_v1 import create_client

    client, app_main, _db, _logger = create_client(monkeypatch)
    seen = []

    def derive(url):
        seen.append(url)
        return None

    monkeypatch.setattr(app_main.seo_routes, "_inventory_hero_image", lambda: STAFF)
    monkeypatch.setattr(app_main, "lcp_jpeg_for_url", derive)

    response = client.get(LCP_IMAGE_PATH)

    assert response.status_code == 404
    assert seen == [STAFF]
