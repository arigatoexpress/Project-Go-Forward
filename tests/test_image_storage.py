"""Tests for staff listing-photo storage (tools/image_storage.py).

These exercise the local-disk backend (GCS is disabled in conftest), which is
the fallback path used in development and the logic shared with the GCS path.
"""

import importlib

import pytest

image_storage = importlib.import_module("tools.image_storage")

# 1x1 PNG.
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000154a24f5f0000000049454e44ae426082"
)


@pytest.fixture(autouse=True)
def _isolate_storage(tmp_path, monkeypatch):
    """Point storage at a temp dir and reset the module-level cache per test."""
    monkeypatch.setattr(image_storage, "_LOCAL_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(image_storage, "_gcs_unavailable", True, raising=False)
    image_storage._invalidate_grouped_cache()
    yield
    image_storage._invalidate_grouped_cache()


def test_store_list_get_delete_roundtrip():
    photo = image_storage.store_photo(
        "43372", PNG_BYTES, original_name="Front Photo.png", declared_content_type="image/png"
    )
    assert photo.backend == "local"
    assert photo.url == f"/api/inventory/photos/43372/{photo.filename}"
    assert photo.filename.endswith(".png")

    listed = image_storage.list_photos("43372")
    assert [p.filename for p in listed] == [photo.filename]

    fetched = image_storage.get_photo("43372", photo.filename)
    assert fetched is not None
    data, content_type = fetched
    assert data == PNG_BYTES
    assert content_type == "image/png"

    assert image_storage.delete_photo("43372", photo.filename) is True
    assert image_storage.get_photo("43372", photo.filename) is None


def test_list_all_grouped_keys_by_home_and_invalidates():
    image_storage.store_photo("43372", PNG_BYTES, declared_content_type="image/png")
    image_storage.store_photo("28102", PNG_BYTES, declared_content_type="image/png")

    grouped = image_storage.list_all_grouped(use_cache=False)
    assert set(grouped) == {"43372", "28102"}
    assert len(grouped["43372"]) == 1


def test_rejects_non_image_bytes():
    with pytest.raises(image_storage.PhotoValidationError):
        image_storage.store_photo("43372", b"this is not an image", declared_content_type="text/plain")


def test_rejects_empty_and_oversize():
    with pytest.raises(image_storage.PhotoValidationError):
        image_storage.store_photo("43372", b"", declared_content_type="image/png")
    with pytest.raises(image_storage.PhotoValidationError):
        image_storage.store_photo(
            "43372", b"\xff\xd8\xff" + b"0" * image_storage.MAX_PHOTO_BYTES,
            declared_content_type="image/jpeg",
        )


def test_detects_content_type_from_magic_bytes():
    assert image_storage.detect_content_type(PNG_BYTES, None) == "image/png"
    assert image_storage.detect_content_type(b"\xff\xd8\xff\xe0", None) == "image/jpeg"
    assert image_storage.detect_content_type(b"RIFF\x00\x00\x00\x00WEBP....", None) == "image/webp"


def test_blocks_path_traversal_in_filename():
    with pytest.raises(image_storage.PhotoValidationError):
        image_storage.get_photo("43372", "../../etc/passwd")
    with pytest.raises(image_storage.PhotoValidationError):
        image_storage.delete_photo("43372", "../secret.png")


def test_rejects_blank_home_id():
    with pytest.raises(image_storage.PhotoValidationError):
        image_storage.safe_home_id("   ")


def _store(home):
    return image_storage.store_photo(home, PNG_BYTES, declared_content_type="image/png").filename


def test_set_photo_order_makes_first_the_hero():
    a, b, c = _store("99001"), _store("99001"), _store("99001")
    # Default order is by filename (upload order); reorder to put c first.
    ordered = image_storage.set_photo_order("99001", [c, a])
    assert [p.filename for p in ordered][:2] == [c, a]
    # b was omitted from the order list but must still be present (appended).
    assert b in {p.filename for p in ordered}
    # The public grouped view honors the same order (hero = c).
    grouped = image_storage.list_all_grouped(use_cache=False)
    assert grouped["99001"][0].filename == c


def test_order_ignores_unknown_filenames():
    a = _store("99002")
    ordered = image_storage.set_photo_order("99002", ["does-not-exist.jpg", a])
    assert [p.filename for p in ordered] == [a]


def test_deleting_main_photo_prunes_order():
    a, b = _store("99003"), _store("99003")
    image_storage.set_photo_order("99003", [b, a])
    assert image_storage.delete_photo("99003", b) is True
    remaining = image_storage.list_photos("99003")
    assert [p.filename for p in remaining] == [a]


def test_order_sidecar_not_listed_as_photo():
    a = _store("99004")
    image_storage.set_photo_order("99004", [a])
    names = [p.filename for p in image_storage.list_photos("99004")]
    assert names == [a]
    assert image_storage._ORDER_FILE not in names


def test_process_image_leaves_gif_untouched():
    data = b"GIF89a" + b"\x00" * 32
    assert image_storage.process_image(data, "image/gif") == data


def test_process_image_disabled_returns_original(monkeypatch):
    monkeypatch.setenv("THO_DISABLE_IMAGE_PROCESSING", "1")
    assert image_storage.process_image(b"anything", "image/jpeg") == b"anything"


def test_process_image_downscales_large_photo():
    pytest.importorskip("PIL")
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (3000, 1500), "blue").save(buf, format="JPEG")
    out = image_storage.process_image(buf.getvalue(), "image/jpeg")
    with Image.open(io.BytesIO(out)) as im:
        assert max(im.size) <= image_storage.MAX_IMAGE_DIM


def test_process_image_applies_exif_orientation():
    pytest.importorskip("PIL")
    import io

    from PIL import Image

    img = Image.new("RGB", (200, 100), "red")  # landscape on disk
    exif = img.getexif()
    exif[274] = 6  # orientation tag: display rotated 90° → becomes portrait
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes())
    out = image_storage.process_image(buf.getvalue(), "image/jpeg")
    with Image.open(io.BytesIO(out)) as im:
        assert im.size == (100, 200)  # auto-rotated
        assert im.getexif().get(274) in (None, 1)  # orientation stripped/normalized


def test_store_photo_downscales_on_upload():
    pytest.importorskip("PIL")
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4000, 2000), "green").save(buf, format="JPEG")
    photo = image_storage.store_photo("99005", buf.getvalue(), declared_content_type="image/jpeg")
    fetched = image_storage.get_photo("99005", photo.filename)
    assert fetched is not None
    with Image.open(io.BytesIO(fetched[0])) as im:
        assert max(im.size) <= image_storage.MAX_IMAGE_DIM


def test_store_photo_sanitizes_malicious_filename():
    """Malicious filenames (path traversal, HTML, control chars) are sanitized before storage."""
    photo = image_storage.store_photo(
        "99006",
        PNG_BYTES,
        original_name="../../../etc/passwd<script>alert(1)</script>\x00.png",
        declared_content_type="image/png",
    )
    assert photo.backend == "local"
    # Filename should not contain path traversal, HTML, or control chars
    assert ".." not in photo.filename
    assert "<" not in photo.filename
    assert ">" not in photo.filename
    assert photo.filename.endswith(".png")
    # The stem should be a sanitized version with no path traversal or slashes
    assert "/" not in photo.filename
    assert photo.filename.startswith("etc_passwdalert-1-")
    # Verify it can be retrieved and deleted normally
    fetched = image_storage.get_photo("99006", photo.filename)
    assert fetched is not None
    assert image_storage.delete_photo("99006", photo.filename) is True
    assert image_storage.get_photo("99006", photo.filename) is None


def test_store_photo_sanitizes_filename_with_html_and_unicode():
    """HTML is stripped and Unicode is preserved in the safe filename stem."""
    photo = image_storage.store_photo(
        "99007",
        PNG_BYTES,
        original_name="<b>Café</b>_ドキュメント.png",
        declared_content_type="image/png",
    )
    assert photo.filename.endswith(".png")
    # HTML stripped, unicode preserved in the regex-sanitized stem
    assert "<b>" not in photo.filename
    assert "</b>" not in photo.filename
    # The stem should contain the cleaned-up version (Café and ドキュメント survive regex)
    assert "caf" in photo.filename.lower() or "ドキュメント" in photo.filename
    assert image_storage.delete_photo("99007", photo.filename) is True


def test_store_photo_with_none_and_empty_filename():
    """None or empty original_name should still produce a valid unique filename."""
    photo_none = image_storage.store_photo(
        "99008", PNG_BYTES, original_name=None, declared_content_type="image/png"
    )
    assert photo_none.filename.endswith(".png")
    assert len(photo_none.filename) > 10  # should have uuid/time component

    photo_empty = image_storage.store_photo(
        "99008", PNG_BYTES, original_name="", declared_content_type="image/png"
    )
    assert photo_empty.filename.endswith(".png")
    assert len(photo_empty.filename) > 10

    # Cleanup
    image_storage.delete_photo("99008", photo_none.filename)
    image_storage.delete_photo("99008", photo_empty.filename)
