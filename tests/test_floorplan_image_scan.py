"""Content-based floorplan detection.

Filename/namespace rules cannot see two real cases in the live catalog:
  * seeded heroes at tho-inventory-assets/inventory/<id>/hero.jpg, and
  * bare names like "1.jpg" inside the manufacturer floorplan namespace.

Confirmed by eye on production: the site's FEATURED home (28102) and the card
image for 28527 were both floorplan line drawings. A drawing is near-white and
effectively grayscale; a photograph is neither. These tests pin that signal.
"""

import io
import json

import pytest

from tools.floorplan_image_scan import (
    analyze_image_bytes,
    build_manifest,
    is_drawing,
)

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw  # noqa: E402


def _floorplan_png() -> bytes:
    """A white canvas with black line work — a floorplan diagram."""
    img = Image.new("RGB", (240, 120), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([10, 10, 230, 110], outline=(0, 0, 0), width=3)
    d.line([120, 10, 120, 110], fill=(0, 0, 0), width=3)
    d.line([120, 60, 230, 60], fill=(0, 0, 0), width=3)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _photo_png() -> bytes:
    """A saturated, non-white image — stands in for a real photograph."""
    img = Image.new("RGB", (240, 120))
    px = img.load()
    for x in range(240):
        for y in range(120):
            px[x, y] = ((x * 7) % 200 + 30, (y * 5) % 180 + 40, (x + y) % 150 + 60)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_detects_a_line_drawing():
    stats = analyze_image_bytes(_floorplan_png())
    assert stats["white_ratio"] > 0.45
    assert stats["saturation"] < 0.10
    assert is_drawing(stats) is True


def test_does_not_flag_a_photograph():
    stats = analyze_image_bytes(_photo_png())
    assert is_drawing(stats) is False


def test_unreadable_bytes_are_never_flagged():
    # Fail OPEN here on purpose: a fetch error must not hide a real photo.
    assert analyze_image_bytes(b"not an image") is None
    assert is_drawing(None) is False


def test_build_manifest_only_lists_drawings(tmp_path):
    blobs = {
        "https://cdn.example.com/a/hero.jpg": _floorplan_png(),
        "https://cdn.example.com/b/hero.jpg": _photo_png(),
        "https://cdn.example.com/c/dead.jpg": None,  # 403 / unreachable
    }
    out = tmp_path / "manifest.json"
    result = build_manifest(
        list(blobs),
        fetch=lambda url: blobs[url],
        out_path=out,
    )
    assert result["urls"] == ["https://cdn.example.com/a/hero.jpg"]
    assert result["scanned"] == 3
    assert result["unreadable"] == 1

    written = json.loads(out.read_text())
    assert written["urls"] == ["https://cdn.example.com/a/hero.jpg"]
