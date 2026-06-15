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
