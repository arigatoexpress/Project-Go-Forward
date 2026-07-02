"""Public asset publishing for social-media creatives.

Meta's ingestion servers need a fetchable HTTPS URL for videos/images they
will publish. This module uploads an approved generated creative to a
separate GCS bucket (never the secure-documents or listing-photos buckets)
and returns a signed URL with bounded TTL (default) or a public-random URL
(when ``META_ASSET_PUBLIC=true``).

Local development falls back cleanly to a ``not_publicly_reachable`` signal
so callers can stay in ``draft_ready`` mode instead of pretending a public URL
exists.
"""

from __future__ import annotations

import logging
import os
import uuid

log = logging.getLogger(__name__)

# Dedicated bucket; never widen existing buckets for this use-case.
_GCS_BUCKET_NAME = os.getenv("GCS_PUBLISH_ASSETS_BUCKET", "tho-publish-assets")
_PREFIX = "publish_assets"

# Signed URL TTL (seconds). Default 10 minutes — longer than the Meta
# async poll budget so the URL survives ingestion.
_SIGNED_TTL_SECONDS = int(os.getenv("META_ASSET_URL_TTL_SECONDS", "600"))

_gcs_client = None
_gcs_bucket = None
_gcs_unavailable = False


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
            log.warning("GCS publish-assets bucket unavailable: %s", exc)
            _gcs_unavailable = True
            return None
    return _gcs_bucket


def _resolve_local_path(local_path_or_filename: str) -> str | None:
    """Return an absolute path to a generated video file, **constrained to
    ``GENERATED_VIDEOS_DIR``**.

    Only ever serves files from inside the generated-creatives directory: any
    directory components in the input are stripped to a basename and resolved
    against that dir, with a realpath containment check. This is a hard security
    boundary — without it, an admin-supplied absolute path (e.g. ``/etc/passwd``)
    or ``..`` traversal to an arbitrary server file would be uploaded to the
    PUBLIC publish bucket. A basename-equality check alone does not catch this
    (``basename('/etc/passwd') == 'passwd'``), so we resolve+contain instead.
    """
    from tools.video_generator import GENERATED_VIDEOS_DIR

    base = os.path.realpath(GENERATED_VIDEOS_DIR)
    candidate = os.path.realpath(os.path.join(base, os.path.basename(local_path_or_filename)))
    # candidate must live inside base AND be a real file (rejects the dir itself,
    # empty input, and anything that escapes the generated-creatives directory).
    if os.path.commonpath((base, candidate)) == base and os.path.isfile(candidate):
        return candidate
    return None


def publish_video_asset(local_path_or_filename: str) -> dict:
    """Upload an approved creative and return a public/signed HTTPS URL.

    Returns::

        {"success": True, "public_url": "https://..."}

    or on failure::

        {"success": False, "reason": "..."}

    The local-dev fallback (GCS disabled or unreachable) returns a clear
    failure so callers never fabricate a URL that Meta cannot fetch.
    """
    local_path = _resolve_local_path(local_path_or_filename)
    if not local_path:
        return {
            "success": False,
            "reason": f"local_file_not_found: {local_path_or_filename}",
        }

    # Reject path traversal
    safe_basename = os.path.basename(local_path_or_filename)
    if safe_basename != os.path.basename(local_path):
        return {"success": False, "reason": "invalid_filename"}

    bucket = _get_bucket()
    if bucket is None:
        return {
            "success": False,
            "reason": "gcs_unavailable: cannot create public URL in local/dev mode",
        }

    # Random object name so the URL is unguessable even in public mode.
    random_name = f"{_PREFIX}/{uuid.uuid4().hex}-{safe_basename}"

    try:
        blob = bucket.blob(random_name)
        blob.upload_from_filename(local_path, content_type="video/mp4")
    except Exception as exc:  # pragma: no cover
        log.error("GCS upload failed for %s: %s", local_path, exc)
        return {"success": False, "reason": f"upload_failed: {exc}"}

    public_mode = os.getenv("META_ASSET_PUBLIC", "").lower() in {"1", "true", "yes"}
    if public_mode:
        try:
            blob.make_public()
            return {"success": True, "public_url": blob.public_url}
        except Exception as exc:  # pragma: no cover
            log.error("Failed to make blob public: %s", exc)
            return {"success": False, "reason": f"public_acl_failed: {exc}"}

    # Default: signed URL with bounded TTL.
    try:
        url = blob.generate_signed_url(
            version="v4",
            expiration=_SIGNED_TTL_SECONDS,
            method="GET",
        )
        return {"success": True, "public_url": url}
    except Exception as exc:  # pragma: no cover
        log.error("Signed URL generation failed: %s", exc)
        return {"success": False, "reason": f"signed_url_failed: {exc}"}
