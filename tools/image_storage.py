"""Durable storage for staff-uploaded listing photos.

Mirrors the GCS helper pattern in ``tools/document_tools.py`` but for inventory
photos. Listing photos are not sensitive, but the ``tho-secure-documents``
bucket holds signed contracts, so photos live in their own bucket and are
streamed back through a public backend endpoint (``/api/inventory/photos/...``)
— the bucket itself never needs to be made public.

Storage layout (GCS or local fallback)::

    listing_photos/<home_id>/<filename>

A local-disk fallback under ``data/listing_photos`` keeps the feature working in
local development and tests where GCS credentials are unavailable. GCS is
preferred whenever it is reachable so photos survive Cloud Run restarts.
"""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Listing photos get their own bucket so we never widen access on the
# secure-documents bucket. Falls back to local disk when GCS is unavailable.
_GCS_BUCKET_NAME = os.getenv("GCS_LISTING_PHOTOS_BUCKET", "tho-listing-photos")
_PREFIX = "listing_photos"

_LOCAL_DIR = os.getenv(
    "THO_LISTING_PHOTOS_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "listing_photos"),
)

# Content types we accept, mapped to a canonical file extension.
ALLOWED_CONTENT_TYPES: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

# Magic-byte signatures so we trust the bytes, not just the declared type.
_MAGIC_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

MAX_PHOTO_BYTES = int(os.getenv("THO_MAX_PHOTO_BYTES", str(15 * 1024 * 1024)))  # 15 MB

_EXT_TO_CONTENT_TYPE = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

_gcs_client = None
_gcs_bucket = None
_gcs_unavailable = False

# Short-lived cache for the cross-home grouped listing so the public
# inventory-context endpoint does not list the bucket on every request.
_GROUPED_TTL = float(os.getenv("THO_LISTING_PHOTOS_CACHE_TTL", "60"))
_grouped_cache: dict[str, list[StoredPhoto]] | None = None
_grouped_cache_at: float = 0.0


@dataclass
class StoredPhoto:
    """One stored listing photo."""

    home_id: str
    filename: str
    size_bytes: int
    content_type: str
    backend: str  # "gcs" or "local"

    @property
    def url(self) -> str:
        """Relative URL the frontend / inventory record should reference."""
        return f"/api/inventory/photos/{self.home_id}/{self.filename}"


class PhotoValidationError(ValueError):
    """Raised when an uploaded file is not an acceptable image."""


def detect_content_type(data: bytes, declared: str | None) -> str:
    """Return a trusted content type from magic bytes, falling back to declared.

    WEBP has no fixed-offset signature short enough to list above, so we sniff
    the RIFF/WEBP container explicitly. Anything we cannot confirm falls back to
    the declared type only if it is in the allowlist; otherwise we reject.
    """
    for signature, content_type in _MAGIC_SIGNATURES:
        if data.startswith(signature):
            return content_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    declared = (declared or "").split(";")[0].strip().lower()
    if declared in ALLOWED_CONTENT_TYPES:
        return "image/jpeg" if declared == "image/jpg" else declared
    raise PhotoValidationError(
        "File is not a recognized image (allowed: JPG, PNG, WebP, GIF)."
    )


def safe_home_id(home_id: str) -> str:
    """Normalize a home id into a filesystem/GCS-safe segment."""
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", (home_id or "").strip()).strip("-.")
    if not cleaned:
        raise PhotoValidationError("Missing or invalid home id.")
    return cleaned[:128]


def _build_filename(original_name: str | None, content_type: str) -> str:
    """Generate a unique, safe filename, preserving a friendly stem."""
    stem = ""
    if original_name:
        stem = os.path.splitext(os.path.basename(original_name))[0]
        stem = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-").lower()[:40]
    ext = ALLOWED_CONTENT_TYPES.get(content_type, ".jpg")
    unique = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    return f"{stem + '-' if stem else ''}{unique}{ext}"


def _safe_filename(filename: str) -> str:
    """Reject path traversal and return a bare filename."""
    base = os.path.basename(filename or "")
    if base != filename or ".." in filename or not base:
        raise PhotoValidationError("Invalid photo filename.")
    return base


def _content_type_for(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    return _EXT_TO_CONTENT_TYPE.get(ext, "application/octet-stream")


def _get_bucket():
    """Lazy-load the GCS bucket, caching the unavailable state."""
    global _gcs_client, _gcs_bucket, _gcs_unavailable
    if _gcs_unavailable:
        return None
    if os.getenv("THO_DISABLE_GCS_UPLOADS", "").lower() in {"1", "true", "yes"}:
        return None
    if _gcs_bucket is None:
        try:
            from google.cloud import storage

            _gcs_client = storage.Client()
            _gcs_bucket = _gcs_client.bucket(_GCS_BUCKET_NAME)
        except Exception as exc:  # pragma: no cover - depends on env
            log.warning("Listing-photo GCS unavailable, using local disk: %s", exc)
            _gcs_unavailable = True
            return None
    return _gcs_bucket


def _invalidate_grouped_cache() -> None:
    global _grouped_cache, _grouped_cache_at
    _grouped_cache = None
    _grouped_cache_at = 0.0


def _blob_path(home_id: str, filename: str) -> str:
    return f"{_PREFIX}/{home_id}/{filename}"


def _local_path(home_id: str, filename: str) -> str:
    return os.path.join(_LOCAL_DIR, home_id, filename)


def store_photo(
    home_id: str,
    data: bytes,
    *,
    original_name: str | None = None,
    declared_content_type: str | None = None,
) -> StoredPhoto:
    """Validate and persist one photo for ``home_id``. Returns a StoredPhoto.

    Tries GCS first; falls back to local disk if GCS is unreachable so the
    feature still works in development.
    """
    if not data:
        raise PhotoValidationError("Uploaded file is empty.")
    if len(data) > MAX_PHOTO_BYTES:
        raise PhotoValidationError(
            f"Photo is too large ({len(data) // (1024 * 1024)} MB). "
            f"Max is {MAX_PHOTO_BYTES // (1024 * 1024)} MB."
        )
    home = safe_home_id(home_id)
    content_type = detect_content_type(data, declared_content_type)
    filename = _build_filename(original_name, content_type)

    bucket = _get_bucket()
    if bucket is not None:
        try:
            blob = bucket.blob(_blob_path(home, filename))
            blob.cache_control = "public, max-age=86400"
            blob.upload_from_string(data, content_type=content_type)
            _invalidate_grouped_cache()
            return StoredPhoto(home, filename, len(data), content_type, "gcs")
        except Exception as exc:  # pragma: no cover - depends on env
            log.error("Listing-photo GCS upload failed, using local disk: %s", exc)

    path = _local_path(home, filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    _invalidate_grouped_cache()
    return StoredPhoto(home, filename, len(data), content_type, "local")


def list_photos(home_id: str) -> list[StoredPhoto]:
    """List stored photos for ``home_id`` from GCS and/or local disk."""
    home = safe_home_id(home_id)
    seen: dict[str, StoredPhoto] = {}

    bucket = _get_bucket()
    if bucket is not None:
        try:
            for blob in bucket.list_blobs(prefix=f"{_PREFIX}/{home}/"):
                name = blob.name.split("/")[-1]
                if not name:
                    continue
                seen[name] = StoredPhoto(
                    home, name, blob.size or 0, _content_type_for(name), "gcs"
                )
        except Exception as exc:  # pragma: no cover - depends on env
            log.error("Listing-photo GCS list failed: %s", exc)

    local_dir = os.path.join(_LOCAL_DIR, home)
    if os.path.isdir(local_dir):
        for name in os.listdir(local_dir):
            full = os.path.join(local_dir, name)
            if name in seen or not os.path.isfile(full):
                continue
            seen[name] = StoredPhoto(
                home, name, os.path.getsize(full), _content_type_for(name), "local"
            )
    return sorted(seen.values(), key=lambda p: p.filename)


def get_photo(home_id: str, filename: str) -> tuple[bytes, str] | None:
    """Return ``(bytes, content_type)`` for a stored photo, or None."""
    home = safe_home_id(home_id)
    name = _safe_filename(filename)

    bucket = _get_bucket()
    if bucket is not None:
        try:
            blob = bucket.blob(_blob_path(home, name))
            if blob.exists():
                return blob.download_as_bytes(), (blob.content_type or _content_type_for(name))
        except Exception as exc:  # pragma: no cover - depends on env
            log.error("Listing-photo GCS fetch failed: %s", exc)

    path = _local_path(home, name)
    if os.path.isfile(path):
        with open(path, "rb") as fh:
            return fh.read(), _content_type_for(name)
    return None


def delete_photo(home_id: str, filename: str) -> bool:
    """Delete a stored photo from GCS and/or local disk. True if anything went."""
    home = safe_home_id(home_id)
    name = _safe_filename(filename)
    deleted = False

    bucket = _get_bucket()
    if bucket is not None:
        try:
            blob = bucket.blob(_blob_path(home, name))
            if blob.exists():
                blob.delete()
                deleted = True
        except Exception as exc:  # pragma: no cover - depends on env
            log.error("Listing-photo GCS delete failed: %s", exc)

    path = _local_path(home, name)
    if os.path.isfile(path):
        os.remove(path)
        deleted = True
    if deleted:
        _invalidate_grouped_cache()
    return deleted


def list_all_grouped(*, use_cache: bool = True) -> dict[str, list[StoredPhoto]]:
    """Return ``{home_id: [StoredPhoto, ...]}`` for every uploaded photo.

    Uses a single GCS list (plus a local-disk walk) and caches the result for a
    short TTL so the public inventory endpoint can overlay staff photos without
    a per-home round trip.
    """
    global _grouped_cache, _grouped_cache_at
    now = time.time()
    if use_cache and _grouped_cache is not None and (now - _grouped_cache_at) < _GROUPED_TTL:
        return _grouped_cache

    grouped: dict[str, dict[str, StoredPhoto]] = {}

    bucket = _get_bucket()
    if bucket is not None:
        try:
            for blob in bucket.list_blobs(prefix=f"{_PREFIX}/"):
                parts = blob.name.split("/")
                if len(parts) < 3 or not parts[-1]:
                    continue
                home, name = parts[1], parts[-1]
                grouped.setdefault(home, {})[name] = StoredPhoto(
                    home, name, blob.size or 0, _content_type_for(name), "gcs"
                )
        except Exception as exc:  # pragma: no cover - depends on env
            log.error("Listing-photo GCS grouped list failed: %s", exc)

    if os.path.isdir(_LOCAL_DIR):
        for home in os.listdir(_LOCAL_DIR):
            home_dir = os.path.join(_LOCAL_DIR, home)
            if not os.path.isdir(home_dir):
                continue
            for name in os.listdir(home_dir):
                full = os.path.join(home_dir, name)
                if name in grouped.get(home, {}) or not os.path.isfile(full):
                    continue
                grouped.setdefault(home, {})[name] = StoredPhoto(
                    home, name, os.path.getsize(full), _content_type_for(name), "local"
                )

    result = {
        home: sorted(photos.values(), key=lambda p: p.filename)
        for home, photos in grouped.items()
    }
    _grouped_cache = result
    _grouped_cache_at = now
    return result
