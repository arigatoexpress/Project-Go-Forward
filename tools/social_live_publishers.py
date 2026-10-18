"""Explicit, fail-closed live social publishing for Ad Studio.

This module is intentionally separate from ``tools.social_publishers``, which
is the transport-free draft builder used by ``/api/marketing/schedule``.
Nothing in the scheduling path imports this module, and no route calls it.

``publish_social_post`` performs blocking HTTP I/O (``requests``) and
``time.sleep`` polling. Async callers must run it via ``asyncio.to_thread``
or ``starlette.concurrency.run_in_threadpool``. ``publish_social_post_async``
is the async entry point.
"""

from __future__ import annotations

import asyncio
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
TIKTOK_STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
META_GRAPH_BASE = "https://graph.facebook.com"

# Stay under TikTok's 30 status reads/minute and cap worst-case sleep so a
# caller cannot poll for the provider's full moderation window.
_DEFAULT_TIKTOK_POLL_ATTEMPTS = 20
_MAX_TIKTOK_POLL_ATTEMPTS = 30
_DEFAULT_TIKTOK_POLL_INTERVAL_SECONDS = 3.0
_MAX_TIKTOK_POLL_INTERVAL_SECONDS = 5.0


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


def _bounded_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


def _bounded_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


def _tiktok_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ['TIKTOK_ACCESS_TOKEN']}",
        "Content-Type": "application/json; charset=UTF-8",
    }


def _tiktok_data(response: requests.Response) -> dict[str, Any]:
    response.raise_for_status()
    payload = response.json() or {}
    if not isinstance(payload, dict):
        raise RuntimeError("TikTok publish response was not an object")
    error = payload.get("error") or {}
    code = str(error.get("code") or "ok").lower()
    if code != "ok":
        raise RuntimeError("TikTok publish request was rejected")
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        raise RuntimeError("TikTok publish response was missing data")
    return data


def _tiktok_post_id(status_data: dict[str, Any], publish_id: str) -> str:
    # TikTok's documented field name is misspelled; accept the corrected
    # spelling if they ever fix the response.
    public_ids = (
        status_data.get("publicaly_available_post_id")
        or status_data.get("publicly_available_post_id")
        or []
    )
    if isinstance(public_ids, list) and public_ids:
        return str(public_ids[0])
    return str(publish_id)


def _publish_tiktok_video(asset_url: str, caption: str) -> dict[str, Any]:
    """Init a pull-from-URL post and poll until TikTok reports a terminal status.

    ``publish_id`` from init is not a published post. A receipt is returned
    only after ``PUBLISH_COMPLETE``. ``FAILED`` and an exhausted poll return
    an error code and no publish timestamp.
    """
    response = requests.post(
        TIKTOK_PUBLISH_URL,
        headers=_tiktok_headers(),
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
    publish_id = _tiktok_data(response).get("publish_id")
    if not publish_id:
        return {"error_code": "provider_invalid_response"}

    attempts = _bounded_int(
        "TIKTOK_STATUS_POLL_ATTEMPTS",
        _DEFAULT_TIKTOK_POLL_ATTEMPTS,
        minimum=1,
        maximum=_MAX_TIKTOK_POLL_ATTEMPTS,
    )
    interval = _bounded_float(
        "TIKTOK_STATUS_POLL_INTERVAL_SECONDS",
        _DEFAULT_TIKTOK_POLL_INTERVAL_SECONDS,
        minimum=0,
        maximum=_MAX_TIKTOK_POLL_INTERVAL_SECONDS,
    )
    for attempt in range(attempts):
        status_response = requests.post(
            TIKTOK_STATUS_URL,
            headers=_tiktok_headers(),
            json={"publish_id": publish_id},
            timeout=30,
        )
        status_data = _tiktok_data(status_response)
        status = status_data.get("status")
        if status == "PUBLISH_COMPLETE":
            return {"post_id": _tiktok_post_id(status_data, publish_id)}
        if status == "FAILED":
            return {"error_code": "provider_publish_failed"}
        if attempt + 1 < attempts and interval > 0:
            time.sleep(interval)
    return {"error_code": "provider_publish_timeout"}


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
    """Publish immediately to exactly the requested configured platform.

    Blocking HTTP and sleep. Async callers must use ``publish_social_post_async``.
    TikTok is reported as published only after status fetch returns
    ``PUBLISH_COMPLETE``.
    """
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
            outcome = _publish_tiktok_video(asset_url, full_caption)
            post_id = outcome.get("post_id")
            if not post_id:
                return {
                    "success": False,
                    "error_code": outcome.get("error_code") or "provider_invalid_response",
                }
        else:
            post_id = _publish_instagram_reel(asset_url, full_caption)
            if not post_id:
                return {"success": False, "error_code": "provider_invalid_response"}
    except Exception as exc:  # provider exceptions may contain secrets/details
        logger.exception("Live social publish failed for %s", platform, exc_info=exc)
        return {"success": False, "error_code": "provider_request_failed"}

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


async def publish_social_post_async(
    *,
    platform: str,
    asset_url: str,
    caption: str = "",
    hashtags: list[str] | None = None,
) -> dict[str, Any]:
    """Run :func:`publish_social_post` off the event loop."""
    return await asyncio.to_thread(
        publish_social_post,
        platform=platform,
        asset_url=asset_url,
        caption=caption,
        hashtags=hashtags,
    )
