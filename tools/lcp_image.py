"""Small same-origin JPEG for the inventory hero, which is the LCP element.

The public catalog host does not resize, and a ``?w=`` query would download
the original once per width. This module serves one derived JPEG for the
hero the server already chose. Callers pass that URL in. There is no request
parameter, so a visitor cannot point the fetch at an arbitrary address.

Remote bytes go through ``tools.outbound_url.fetch_public_url`` (public
addresses only, pinned DNS, no redirects, no environment proxy). Staff
uploads are read from listing-photo storage and are not fetched over HTTP.
"""

from __future__ import annotations

import io
import logging
import re
import threading
import time
from urllib.parse import urlsplit

import requests
from PIL import Image, ImageOps

from tools import image_storage
from tools.outbound_url import fetch_public_url

log = logging.getLogger(__name__)

LCP_IMAGE_PATH = "/api/inventory/lcp-image"
_CDN_HOST = "d132mt2yijm03y.cloudfront.net"
_STAFF_PHOTO_RE = re.compile(r"^/api/inventory/photos/([^/]+)/([^/]+)$")
_MAX_SOURCE_BYTES = 8 * 1024 * 1024
_MAX_EDGE = 720
_JPEG_QUALITY = 60
_MAX_PIXELS = 40_000_000
_CACHE_TTL_SECONDS = 300.0
_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, bytes]] = {}


def clear_lcp_cache() -> None:
    """Drop derived bytes. Tests use this so cases do not share a hero."""
    with _cache_lock:
        _cache.clear()


def hero_url_is_resizable(url: str) -> bool:
    """True when this process can read the hero without a visitor-supplied URL."""
    text = str(url or "").strip()
    if not text or len(text) > 2048:
        return False
    if _STAFF_PHOTO_RE.match(urlsplit(text).path) and urlsplit(text).query == "":
        return text.startswith("/api/inventory/photos/")
    parsed = urlsplit(text)
    host = (parsed.hostname or "").lower().rstrip(".")
    return (
        parsed.scheme == "https"
        and host == _CDN_HOST
        and not parsed.username
        and parsed.port in (None, 443)
    )


def render_lcp_jpeg(data: bytes) -> bytes:
    """Downscale ``data`` to a progressive JPEG small enough for a phone hero."""
    if not data or len(data) > _MAX_SOURCE_BYTES:
        raise ValueError("image source is empty or too large")
    with Image.open(io.BytesIO(data)) as image:
        if image.width * image.height > _MAX_PIXELS:
            raise ValueError("image dimensions are too large")
        oriented = ImageOps.exif_transpose(image)
        try:
            frame = oriented.convert("RGB")
            frame.thumbnail((_MAX_EDGE, _MAX_EDGE), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            frame.save(
                out,
                format="JPEG",
                quality=_JPEG_QUALITY,
                optimize=True,
                progressive=True,
            )
            rendered = out.getvalue()
        finally:
            if oriented is not image:
                oriented.close()
    if not rendered:
        raise ValueError("image encode produced no bytes")
    return rendered


def _read_staff_photo(url: str) -> bytes | None:
    match = _STAFF_PHOTO_RE.match(urlsplit(url).path)
    if match is None:
        return None
    home_id, filename = match.group(1), match.group(2)
    try:
        stored = image_storage.get_photo(home_id, filename)
    except (image_storage.PhotoValidationError, image_storage.PhotoStorageError):
        return None
    if stored is None:
        return None
    data, _content_type = stored
    if not data or len(data) > _MAX_SOURCE_BYTES:
        return None
    return data


def _read_cdn_photo(url: str) -> bytes | None:
    try:
        response = fetch_public_url(url, timeout=4)
    except (ValueError, requests.RequestException, OSError):
        return None
    try:
        if response.status_code != 200:
            return None
        data = response.content
    finally:
        response.close()
    if not data or len(data) > _MAX_SOURCE_BYTES:
        return None
    return data


def _read_source(url: str) -> bytes | None:
    if url.startswith("/api/inventory/photos/"):
        return _read_staff_photo(url)
    return _read_cdn_photo(url)


def lcp_jpeg_for_url(url: str) -> bytes | None:
    """Return cached derived JPEG bytes for a resizable hero URL, or None."""
    if not hero_url_is_resizable(url):
        return None
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(url)
        if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]
    raw = _read_source(url)
    if not raw:
        return None
    try:
        jpeg = render_lcp_jpeg(raw)
    except (OSError, ValueError) as exc:
        log.warning("LCP image encode failed", extra={"error_type": type(exc).__name__})
        return None
    with _cache_lock:
        _cache[url] = (time.monotonic(), jpeg)
    return jpeg
