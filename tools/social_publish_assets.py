"""Create bounded public URLs for explicitly approved social video assets."""

from __future__ import annotations

import logging
import os
import uuid

logger = logging.getLogger(__name__)

_PREFIX = "publish_assets"
_gcs_client = None
_gcs_bucket = None
_gcs_unavailable = False


def _get_bucket():
    global _gcs_client, _gcs_bucket, _gcs_unavailable
    if _gcs_unavailable:
        return None
    if (os.environ.get("THO_DISABLE_GCS_UPLOADS") or "").lower() in {"1", "true", "yes"}:
        return None
    if _gcs_bucket is None:
        try:
            from google.cloud import storage

            _gcs_client = storage.Client()
            bucket_name = os.environ.get("GCS_PUBLISH_ASSETS_BUCKET", "tho-publish-assets")
            _gcs_bucket = _gcs_client.bucket(bucket_name)
        except Exception as exc:
            logger.warning("Social publish asset storage unavailable", exc_info=exc)
            _gcs_unavailable = True
            return None
    return _gcs_bucket


def _resolve_local_path(local_path_or_filename: str) -> str | None:
    from tools.video_generator import GENERATED_VIDEOS_DIR

    if not local_path_or_filename:
        return None
    base = os.path.realpath(GENERATED_VIDEOS_DIR)
    candidate = os.path.realpath(os.path.join(base, os.path.basename(local_path_or_filename)))
    if os.path.commonpath((base, candidate)) == base and os.path.isfile(candidate):
        return candidate
    return None


def publish_video_asset(local_path_or_filename: str) -> dict:
    """Upload one generated video and return a signed/public HTTPS URL."""
    local_path = _resolve_local_path(local_path_or_filename)
    if not local_path:
        return {"success": False, "error_code": "local_file_not_found"}

    safe_basename = os.path.basename(local_path_or_filename)
    if safe_basename != os.path.basename(local_path):
        return {"success": False, "error_code": "invalid_filename"}

    bucket = _get_bucket()
    if bucket is None:
        return {"success": False, "error_code": "asset_storage_unavailable"}

    blob = bucket.blob(f"{_PREFIX}/{uuid.uuid4().hex}-{safe_basename}")
    try:
        blob.upload_from_filename(local_path, content_type="video/mp4")
        if (os.environ.get("META_ASSET_PUBLIC") or "").lower() in {"1", "true", "yes"}:
            blob.make_public()
            public_url = blob.public_url
        else:
            ttl_seconds = int(os.environ.get("META_ASSET_URL_TTL_SECONDS", "600"))
            public_url = blob.generate_signed_url(
                version="v4",
                expiration=ttl_seconds,
                method="GET",
            )
    except Exception as exc:
        logger.exception("Social publish asset upload failed", exc_info=exc)
        return {"success": False, "error_code": "asset_upload_failed"}

    return {"success": True, "public_url": public_url}
