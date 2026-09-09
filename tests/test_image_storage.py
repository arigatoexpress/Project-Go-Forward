"""Tests for staff listing-photo storage (tools/image_storage.py).

These exercise the local-disk backend (GCS is disabled in conftest), which is
the fallback path used in development and the logic shared with the GCS path.
"""

import importlib
import io

import pytest

image_storage = importlib.import_module("tools.image_storage")

# 1x1 PNG.
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010804000000b51c0c02"  # pragma: allowlist secret - deterministic 1x1 test image
    "0000000b4944415478da6364f80f00010501012718e3660000000049454e44ae426082"  # pragma: allowlist secret - deterministic 1x1 test image
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
    assert content_type == "image/png"
    image_storage.validate_image_bytes(data, content_type)

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
        image_storage.store_photo(
            "43372", b"this is not an image", declared_content_type="text/plain"
        )


def test_rejects_malformed_bytes_even_with_allowed_declared_type():
    with pytest.raises(image_storage.PhotoValidationError, match="valid image"):
        image_storage.store_photo(
            "43372",
            b"this is not a png",
            declared_content_type="image/png",
        )


@pytest.mark.parametrize(
    ("data", "declared"),
    [
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"GIF89a", "image/gif"),
        (b"RIFF\x00\x00\x00\x00WEBP", "image/webp"),
    ],
)
def test_rejects_truncated_image_containers(data, declared):
    with pytest.raises(image_storage.PhotoValidationError, match="valid image"):
        image_storage.store_photo("43372", data, declared_content_type=declared)


def test_rejects_truncated_jpeg_that_pillow_verify_accepts():
    from PIL import Image

    buffer = io.BytesIO()
    Image.effect_noise((20, 20), 100).convert("RGB").save(buffer, format="JPEG")
    truncated = buffer.getvalue()[:-50]

    # This fixture pins the historical gap: Pillow's header verifier accepts
    # the file, while a full pixel decode fails.
    with Image.open(io.BytesIO(truncated)) as image:
        image.verify()

    with pytest.raises(image_storage.PhotoValidationError, match="valid image"):
        image_storage.store_photo("43372", truncated, declared_content_type="image/jpeg")


def test_rejects_truncated_animated_gif_after_first_frame():
    from PIL import Image, ImageSequence

    frames = [Image.effect_noise((40, 40), 100 + index).convert("P") for index in range(3)]
    buffer = io.BytesIO()
    frames[0].save(
        buffer,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        loop=0,
        duration=100,
    )
    truncated = buffer.getvalue()[:-100]

    # The first frame still decodes, but a later frame is corrupt.
    with Image.open(io.BytesIO(truncated)) as image:
        image.load()
    with pytest.raises(OSError):
        with Image.open(io.BytesIO(truncated)) as image:
            for frame in ImageSequence.Iterator(image):
                frame.load()

    with pytest.raises(image_storage.PhotoValidationError, match="valid image"):
        image_storage.store_photo("43372", truncated, declared_content_type="image/gif")


def test_rejects_animation_over_total_decoded_pixel_budget(monkeypatch):
    from PIL import Image

    frames = [Image.effect_noise((20, 20), 100 + index).convert("P") for index in range(3)]
    buffer = io.BytesIO()
    frames[0].save(
        buffer,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        loop=0,
        duration=100,
    )
    monkeypatch.setattr(image_storage, "MAX_DECODED_PIXELS", 1_000)

    with pytest.raises(image_storage.PhotoValidationError, match="too large"):
        image_storage.store_photo("43372", buffer.getvalue(), declared_content_type="image/gif")


def test_validation_still_runs_when_optimization_is_disabled(monkeypatch):
    monkeypatch.setenv("THO_DISABLE_IMAGE_PROCESSING", "1")

    with pytest.raises(image_storage.PhotoValidationError, match="valid image"):
        image_storage.store_photo(
            "43372",
            b"not really a png",
            declared_content_type="image/png",
        )


def test_rejects_empty_and_oversize():
    with pytest.raises(image_storage.PhotoValidationError):
        image_storage.store_photo("43372", b"", declared_content_type="image/png")
    with pytest.raises(image_storage.PhotoValidationError):
        image_storage.store_photo(
            "43372",
            b"\xff\xd8\xff" + b"0" * image_storage.MAX_PHOTO_BYTES,
            declared_content_type="image/jpeg",
        )


def test_detects_content_type_from_magic_bytes():
    assert image_storage.detect_content_type(PNG_BYTES, None) == "image/png"
    assert image_storage.detect_content_type(b"\xff\xd8\xff\xe0", None) == "image/jpeg"
    assert image_storage.detect_content_type(b"RIFF\x00\x00\x00\x00WEBP....", None) == "image/webp"


def test_cloud_run_never_falls_back_to_ephemeral_local_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("K_SERVICE", "project-go-forward")
    monkeypatch.setattr(image_storage, "_gcs_unavailable", True)

    with pytest.raises(image_storage.PhotoStorageError, match="Durable"):
        image_storage.store_photo("43372", PNG_BYTES, declared_content_type="image/png")

    assert list(tmp_path.rglob("*")) == []


@pytest.mark.parametrize(
    "operation",
    [
        lambda: image_storage.list_photos("43372"),
        lambda: image_storage.get_photo("43372", "front.png"),
    ],
)
def test_cloud_run_reads_never_consult_ephemeral_local_storage(monkeypatch, operation):
    monkeypatch.setenv("K_SERVICE", "project-go-forward")

    with pytest.raises(image_storage.PhotoStorageError, match="Durable"):
        operation()


def test_cloud_run_gcs_upload_failure_does_not_write_local(tmp_path, monkeypatch):
    class FailingBlob:
        cache_control = None

        def upload_from_string(self, *_args, **_kwargs):
            raise RuntimeError("provider detail must not escape")

    class FailingBucket:
        def blob(self, _name):
            return FailingBlob()

    monkeypatch.setenv("K_SERVICE", "project-go-forward")
    monkeypatch.setattr(image_storage, "_get_bucket", lambda: FailingBucket())

    with pytest.raises(image_storage.PhotoStorageError, match="Durable"):
        image_storage.store_photo("43372", PNG_BYTES, declared_content_type="image/png")

    assert list(tmp_path.rglob("*")) == []


def test_cloud_run_upload_reconciles_commit_then_response_loss(monkeypatch):
    objects = set()

    class AmbiguousBlob:
        cache_control = None

        def __init__(self, name):
            self.name = name

        def upload_from_string(self, *_args, **_kwargs):
            objects.add(self.name)
            raise RuntimeError("response lost after provider commit")

        def exists(self):
            return self.name in objects

    class Bucket:
        def blob(self, name):
            return AmbiguousBlob(name)

    monkeypatch.setenv("K_SERVICE", "project-go-forward")
    monkeypatch.setattr(image_storage, "_get_bucket", lambda: Bucket())

    first = image_storage.store_photo("43372", PNG_BYTES, declared_content_type="image/png")
    second = image_storage.store_photo("43372", PNG_BYTES, declared_content_type="image/png")

    assert first.filename == second.filename
    assert len(objects) == 1
    assert objects == {f"listing_photos/43372/{first.filename}"}


def test_cloud_run_order_write_fails_when_gcs_is_unavailable(monkeypatch):
    filename = _store("99009")
    monkeypatch.setenv("K_SERVICE", "project-go-forward")

    with pytest.raises(image_storage.PhotoStorageError, match="Durable"):
        image_storage.set_photo_order("99009", [filename])


def test_cloud_run_order_fails_before_write_when_gcs_list_fails(monkeypatch, caplog):
    uploads = []

    class Blob:
        def upload_from_string(self, *args, **kwargs):
            uploads.append((args, kwargs))

    class FailingListBucket:
        def list_blobs(self, **_kwargs):
            raise RuntimeError("sensitive provider detail")

        def blob(self, _name):
            return Blob()

    monkeypatch.setenv("K_SERVICE", "project-go-forward")
    monkeypatch.setattr(image_storage, "_get_bucket", lambda: FailingListBucket())

    with pytest.raises(image_storage.PhotoStorageError, match="Durable"):
        image_storage.set_photo_order("43372", ["front.jpg"])

    assert uploads == []
    assert "sensitive provider detail" not in caplog.text


def test_cloud_run_corrupt_order_is_ignored_and_repaired(monkeypatch):
    class PhotoBlob:
        def __init__(self, name):
            self.name = name
            self.size = 10

    class OrderBlob:
        def __init__(self):
            self.payload = b"not-json"

        def exists(self):
            return True

        def download_as_bytes(self):
            return self.payload

        def upload_from_string(self, payload, **_kwargs):
            self.payload = payload

    class Bucket:
        def __init__(self):
            self.order = OrderBlob()

        def list_blobs(self, **_kwargs):
            return [
                PhotoBlob("listing-photos/43372/front.jpg"),
                PhotoBlob("listing-photos/43372/side.jpg"),
            ]

        def blob(self, name):
            assert name.endswith(image_storage._ORDER_FILE)
            return self.order

    bucket = Bucket()
    monkeypatch.setenv("K_SERVICE", "project-go-forward")
    monkeypatch.setattr(image_storage, "_get_bucket", lambda: bucket)

    assert [p.filename for p in image_storage.list_photos("43372")] == ["front.jpg", "side.jpg"]
    reordered = image_storage.set_photo_order("43372", ["side.jpg", "front.jpg"])

    assert [p.filename for p in reordered] == ["side.jpg", "front.jpg"]
    assert bucket.order.payload == b'["side.jpg", "front.jpg"]'


def test_cloud_run_order_provider_read_failure_still_fails_closed(monkeypatch, caplog):
    class PhotoBlob:
        def __init__(self, name):
            self.name = name
            self.size = 10

    class OrderBlob:
        def exists(self):
            return True

        def download_as_bytes(self):
            raise RuntimeError("sensitive provider detail")

    class Bucket:
        def list_blobs(self, **_kwargs):
            return [
                PhotoBlob("listing-photos/43372/front.jpg"),
                PhotoBlob("listing-photos/43372/side.jpg"),
            ]

        def blob(self, _name):
            return OrderBlob()

    monkeypatch.setenv("K_SERVICE", "project-go-forward")
    monkeypatch.setattr(image_storage, "_get_bucket", lambda: Bucket())

    with pytest.raises(image_storage.PhotoStorageError, match="Durable"):
        image_storage.list_photos("43372")

    assert "sensitive provider detail" not in caplog.text


def test_cloud_run_delete_fails_when_gcs_is_unavailable(monkeypatch):
    filename = _store("99010")
    monkeypatch.setenv("K_SERVICE", "project-go-forward")

    with pytest.raises(image_storage.PhotoStorageError, match="Durable"):
        image_storage.delete_photo("99010", filename)


def test_cloud_run_delete_commit_survives_order_cleanup_outage(monkeypatch):
    state = {"deleted": False}

    class PhotoBlob:
        def exists(self):
            return True

        def delete(self):
            state["deleted"] = True

    class OrderBlob:
        def exists(self):
            return True

        def download_as_bytes(self):
            raise RuntimeError("order provider unavailable")

    class Bucket:
        def blob(self, name):
            if name.endswith(image_storage._ORDER_FILE):
                return OrderBlob()
            return PhotoBlob()

    monkeypatch.setenv("K_SERVICE", "project-go-forward")
    monkeypatch.setattr(image_storage, "_get_bucket", lambda: Bucket())
    image_storage._grouped_cache = {"43372": []}

    assert image_storage.delete_photo("43372", "front.jpg") is True
    assert state["deleted"] is True
    assert image_storage._grouped_cache is None


def test_blocks_path_traversal_in_filename():
    with pytest.raises(image_storage.PhotoValidationError):
        image_storage.get_photo("43372", "../../etc/passwd")
    with pytest.raises(image_storage.PhotoValidationError):
        image_storage.delete_photo("43372", "../secret.png")


def test_rejects_blank_home_id():
    with pytest.raises(image_storage.PhotoValidationError):
        image_storage.safe_home_id("   ")


def _store(home):
    _store.counter += 1
    return image_storage.store_photo(
        home,
        PNG_BYTES,
        original_name=f"photo-{_store.counter}.png",
        declared_content_type="image/png",
    ).filename


_store.counter = 0


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
