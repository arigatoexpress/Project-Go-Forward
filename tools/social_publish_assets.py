"""Create bounded public URLs for explicitly approved social video assets.

``publish_video_asset`` performs blocking Cloud Storage uploads and signed-URL
signing. Async callers must run it via ``asyncio.to_thread`` or
``starlette.concurrency.run_in_threadpool``. ``publish_video_asset_async`` is
the async entry point. This module is not registered on any route.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid

import google.auth
from google.auth.transport.requests import Request as GoogleAuthRequest

logger = logging.getLogger(__name__)

_PREFIX = "publish_assets"
_gcs_client = None
_gcs_bucket = None
_gcs_unavailable = False

# TikTok PULL_FROM_URL may download for up to one hour. 3900s leaves a short
# margin past that window. V4 signed URLs cannot exceed 7 days.
_DEFAULT_ASSET_URL_TTL_SECONDS = 3900
_MAX_ASSET_URL_TTL_SECONDS = 7 * 24 * 60 * 60
_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


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


def _asset_url_ttl_seconds() -> int:
    """Return the signed URL lifetime, defaulting past TikTok's download window."""
    raw = os.environ.get("META_ASSET_URL_TTL_SECONDS")
    if raw is None or not raw.strip():
        return _DEFAULT_ASSET_URL_TTL_SECONDS
    try:
        ttl_seconds = int(raw)
    except ValueError:
        return _DEFAULT_ASSET_URL_TTL_SECONDS
    if ttl_seconds < 1:
        return _DEFAULT_ASSET_URL_TTL_SECONDS
    return min(ttl_seconds, _MAX_ASSET_URL_TTL_SECONDS)


def _signed_get_url(blob, ttl_seconds: int) -> str:
    """Mint a V4 GET URL with IAM signBlob credentials.

    Cloud Run metadata credentials cannot sign with a private key. Passing the
    refreshed access token and service account email makes
    ``Blob.generate_signed_url`` call IAM ``signBlob`` instead.
    """
    credentials, _project_id = google.auth.default(scopes=[_CLOUD_PLATFORM_SCOPE])
    credentials.refresh(GoogleAuthRequest())
    access_token = getattr(credentials, "token", None)
    service_account_email = getattr(credentials, "service_account_email", None)
    if not access_token or not service_account_email or service_account_email == "default":
        raise RuntimeError("Could not resolve service account signing identity")
    return blob.generate_signed_url(
        version="v4",
        expiration=ttl_seconds,
        method="GET",
        service_account_email=service_account_email,
        access_token=access_token,
    )


def publish_video_asset(local_path_or_filename: str) -> dict:
    """Upload one generated video and return a signed/public HTTPS URL.

    Blocking Cloud Storage I/O. Async callers must use
    ``publish_video_asset_async``.
    """
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
            public_url = _signed_get_url(blob, _asset_url_ttl_seconds())
    except Exception as exc:
        logger.exception("Social publish asset upload failed", exc_info=exc)
        return {"success": False, "error_code": "asset_upload_failed"}

    return {"success": True, "public_url": public_url}


async def publish_video_asset_async(local_path_or_filename: str) -> dict:
    """Run :func:`publish_video_asset` off the event loop."""
    return await asyncio.to_thread(publish_video_asset, local_path_or_filename)
