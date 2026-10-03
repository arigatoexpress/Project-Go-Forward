"""Explicit, fail-closed live social publishing for Ad Studio.

This module is intentionally separate from ``tools.social_publishers``, which
is the transport-free draft builder used by ``/api/marketing/schedule``.
Nothing in the scheduling path imports this module.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

import requests

logger = logging.getLogger(__name__)

TRUTHY = {"1", "true", "yes", "on", "enabled"}
SUPPORTED_PLATFORMS = frozenset({"tiktok", "instagram_reels"})
TIKTOK_PUBLISH_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
META_GRAPH_BASE = "https://graph.facebook.com"


def _enabled(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in TRUTHY


def social_publish_readiness(platform: str) -> dict[str, Any]:
    """Return live-publish readiness without exposing credential values."""
    if platform not in SUPPORTED_PLATFORMS:
        return {
            "supported": False,
            "configured": False,
            "publish_enabled": _enabled("THO_SOCIAL_PUBLISH_ENABLED"),
        }

    if platform == "tiktok":
        configured = bool((os.environ.get("TIKTOK_ACCESS_TOKEN") or "").strip())
    else:
        configured = all(
            (os.environ.get(name) or "").strip()
            for name in ("META_ACCESS_TOKEN", "INSTAGRAM_BUSINESS_ACCOUNT_ID")
        )

    return {
        "supported": True,
        "configured": configured,
        "publish_enabled": _enabled("THO_SOCIAL_PUBLISH_ENABLED"),
    }


def _publish_tiktok_video(asset_url: str, caption: str) -> str | None:
    response = requests.post(
        TIKTOK_PUBLISH_URL,
        headers={
            "Authorization": f"Bearer {os.environ['TIKTOK_ACCESS_TOKEN']}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json={
            "post_info": {
                "title": caption[:2200],
                "privacy_level": os.environ.get("TIKTOK_PRIVACY_LEVEL", "SELF_ONLY"),
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
                "video_cover_timestamp_ms": 1000,
            },
            "source_info": {"source": "PULL_FROM_URL", "video_url": asset_url},
        },
        timeout=30,
    )
    response.raise_for_status()
    return ((response.json() or {}).get("data") or {}).get("publish_id")


def _publish_instagram_reel(asset_url: str, caption: str) -> str | None:
    token = os.environ["META_ACCESS_TOKEN"]
    ig_user_id = os.environ["INSTAGRAM_BUSINESS_ACCOUNT_ID"]
    version = os.environ.get("META_GRAPH_VERSION", "v24.0")
    base = f"{META_GRAPH_BASE}/{version}/{ig_user_id}"

    media_response = requests.post(
        f"{base}/media",
        data={
            "media_type": "REELS",
            "video_url": asset_url,
            "caption": caption,
            "access_token": token,
        },
        timeout=30,
    )
    media_response.raise_for_status()
    creation_id = (media_response.json() or {}).get("id")
    if not creation_id:
        return None

    status_url = f"{META_GRAPH_BASE}/{version}/{creation_id}"
    max_attempts = int(os.environ.get("META_REEL_POLL_ATTEMPTS", "20"))
    interval = float(os.environ.get("META_REEL_POLL_INTERVAL_SECONDS", "3"))
    for _ in range(max_attempts):
        poll = requests.get(
            status_url,
            params={"fields": "status_code", "access_token": token},
            timeout=30,
        )
        poll.raise_for_status()
        status_code = (poll.json() or {}).get("status_code")
        if status_code == "FINISHED":
            break
        if status_code == "ERROR":
            return None
        time.sleep(interval)
    else:
        return None

    publish_response = requests.post(
        f"{base}/media_publish",
        data={"creation_id": creation_id, "access_token": token},
        timeout=30,
    )
    publish_response.raise_for_status()
    return (publish_response.json() or {}).get("id")


def publish_social_post(
    *,
    platform: str,
    asset_url: str,
    caption: str = "",
    hashtags: list[str] | None = None,
) -> dict[str, Any]:
    """Publish immediately to exactly the requested configured platform."""
    readiness = social_publish_readiness(platform)
    if not readiness["supported"]:
        return {"success": False, "error_code": "unsupported_platform"}
    if not readiness["configured"]:
        return {"success": False, "error_code": "platform_not_configured"}
    if not readiness["publish_enabled"]:
        return {"success": False, "error_code": "publishing_disabled"}
    if not asset_url.startswith("https://"):
        return {"success": False, "error_code": "invalid_asset_url"}

    full_caption = caption.strip()
    if hashtags:
        full_caption = f"{full_caption} {' '.join(hashtags)}".strip()

    try:
        if platform == "tiktok":
            post_id = _publish_tiktok_video(asset_url, full_caption)
        else:
            post_id = _publish_instagram_reel(asset_url, full_caption)
    except Exception as exc:  # provider exceptions may contain secrets/details
        logger.exception("Live social publish failed for %s", platform, exc_info=exc)
        return {"success": False, "error_code": "provider_request_failed"}

    if not post_id:
        return {"success": False, "error_code": "provider_invalid_response"}

    return {
        "success": True,
        "status": "published",
        "post_id": post_id,
        "platform": platform,
        "content_type": "video",
        "video_url": asset_url,
        "publish_attempted": True,
        "live_integration": True,
        "published_at": datetime.now(UTC).isoformat(),
    }
