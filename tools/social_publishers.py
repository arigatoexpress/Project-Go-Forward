"""Fail-closed social publishing adapters for Ad Studio.

These adapters prepare the real API payloads for TikTok Content Posting and
Meta/Instagram publishing, but they do not send anything unless the explicit
``THO_SOCIAL_PUBLISH_ENABLED`` gate is enabled and the required tokens are
configured. Ad Studio can therefore show staff exactly what is missing without
pretending a simulated post was published.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import urlencode, urljoin

import requests

TRUTHY = {"1", "true", "yes", "on", "enabled"}
TIKTOK_PUBLISH_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
META_GRAPH_BASE = "https://graph.facebook.com"


def _enabled(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in TRUTHY


def _configured(*names: str) -> dict[str, bool]:
    return {name: bool((os.environ.get(name) or "").strip()) for name in names}


def _canonical_origin() -> str | None:
    """Resolve the canonical public site origin (no trailing slash).

    Precedence: PUBLIC_SITE_URL env var, then config.yaml business.website.
    Returns None when neither is set so callers can no-op cleanly.
    """
    origin = (os.environ.get("PUBLIC_SITE_URL") or "").strip()
    if not origin:
        try:
            from config_loader import get_business

            origin = (get_business().get("website") or "").strip()
        except Exception:
            origin = ""
    return origin.rstrip("/") or None


def _campaign_slug(*parts: str | None) -> str:
    """Slugify a home/plan name into a UTM-safe campaign token.

    Lowercase; keep [a-z0-9]; collapse every other run to a single hyphen; trim
    leading/trailing hyphens; cap at 60 chars. Returns "ad-studio" when no
    usable text is provided so utm_campaign is never empty.
    """
    raw = " ".join(p for p in parts if p and p.strip())
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")[:60].strip("-")
    return slug or "ad-studio"


def _utm_cta_link(platform: str, campaign: str | None) -> str | None:
    """Build the UTM-tagged outbound CTA link, or None to no-op.

    Strict no-op (returns None) unless THO_UTM_CTA_ENABLED is truthy AND a
    canonical origin is resolvable. utm_source defaults to the platform,
    utm_medium to "social"; both overridable via env for ad-account
    conventions. Never hardcodes a tracking id.
    """
    if not _enabled("THO_UTM_CTA_ENABLED"):
        return None
    origin = _canonical_origin()
    if not origin:
        return None
    source = (os.environ.get("THO_UTM_SOURCE") or platform or "social").strip()
    medium = (os.environ.get("THO_UTM_MEDIUM") or "social").strip()
    params = urlencode(
        {
            "utm_source": source,
            "utm_medium": medium,
            "utm_campaign": _campaign_slug(campaign),
        }
    )
    return f"{origin}/?{params}"


def _absolute_asset_url(url: str | None) -> str | None:
    if not url:
        return None
    if url.startswith(("http://", "https://")):
        return url
    origin = (os.environ.get("PUBLIC_SITE_URL") or "").rstrip("/")
    if not origin:
        return url
    return urljoin(f"{origin}/", url.lstrip("/"))


def social_readiness() -> dict[str, Any]:
    """Return platform connection status without exposing secrets."""
    publish_enabled = _enabled("THO_SOCIAL_PUBLISH_ENABLED")
    tiktok = _configured("TIKTOK_ACCESS_TOKEN")
    instagram = _configured("META_ACCESS_TOKEN", "INSTAGRAM_BUSINESS_ACCOUNT_ID")
    public_site_url = bool((os.environ.get("PUBLIC_SITE_URL") or "").strip())

    return {
        "success": True,
        "publish_enabled": publish_enabled,
        "public_site_url_configured": public_site_url,
        "platforms": {
            "tiktok": {
                "configured": all(tiktok.values()) and public_site_url,
                "publish_enabled": publish_enabled,
                "required_env": [name for name, ok in tiktok.items() if not ok]
                + ([] if public_site_url else ["PUBLIC_SITE_URL"]),
                "api": "TikTok Content Posting API",
            },
            "instagram_reels": {
                "configured": all(instagram.values()) and public_site_url,
                "publish_enabled": publish_enabled,
                "required_env": [name for name, ok in instagram.items() if not ok]
                + ([] if public_site_url else ["PUBLIC_SITE_URL"]),
                "api": "Meta Instagram Content Publishing API",
            },
        },
    }


def _draft_response(
    *,
    platform: str,
    content_type: str,
    caption: str,
    hashtags: list[str],
    asset_url: str | None,
    scheduled_time: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "success": True,
        "post_id": f"DRAFT-{platform.upper()[:2]}-{uuid.uuid4().hex[:6].upper()}",
        "platform": platform,
        "content_type": content_type,
        "scheduled_time": scheduled_time,
        "caption": caption,
        "hashtags": hashtags,
        "video_url": asset_url,
        "status": "draft_ready",
        "live_integration": False,
        "publish_attempted": False,
        "publish_blocked_reason": reason,
        "social_readiness": social_readiness(),
    }


def _publish_tiktok_video(asset_url: str, caption: str) -> dict[str, Any]:
    token = os.environ["TIKTOK_ACCESS_TOKEN"]
    payload = {
        "post_info": {
            "title": caption[:2200],
            "privacy_level": os.environ.get("TIKTOK_PRIVACY_LEVEL", "SELF_ONLY"),
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
            "video_cover_timestamp_ms": 1000,
        },
        "source_info": {
            "source": "PULL_FROM_URL",
            "video_url": asset_url,
        },
    }
    response = requests.post(
        TIKTOK_PUBLISH_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    body = response.json()
    publish_id = (body.get("data") or {}).get("publish_id") or f"TT-{uuid.uuid4().hex[:8]}"
    return {"success": True, "post_id": publish_id, "api_response": body}


def _publish_instagram_reel(asset_url: str, caption: str) -> dict[str, Any]:
    token = os.environ["META_ACCESS_TOKEN"]
    ig_user_id = os.environ["INSTAGRAM_BUSINESS_ACCOUNT_ID"]
    version = os.environ.get("META_GRAPH_VERSION", "v24.0")
    base = f"{META_GRAPH_BASE}/{version}/{ig_user_id}"

    media_resp = requests.post(
        f"{base}/media",
        data={
            "media_type": "REELS",
            "video_url": asset_url,
            "caption": caption,
            "access_token": token,
        },
        timeout=30,
    )
    media_resp.raise_for_status()
    creation_id = media_resp.json().get("id")
    if not creation_id:
        return {
            "success": False,
            "error": "Instagram media container response did not include an id.",
        }

    publish_resp = requests.post(
        f"{base}/media_publish",
        data={"creation_id": creation_id, "access_token": token},
        timeout=30,
    )
    publish_resp.raise_for_status()
    body = publish_resp.json()
    return {
        "success": True,
        "post_id": body.get("id") or creation_id,
        "creation_id": creation_id,
        "api_response": body,
    }


def prepare_or_publish_social_post(
    *,
    platform: str,
    content_type: str,
    scheduled_time: str,
    caption: str,
    hashtags: list[str] | None = None,
    video_url: str | None = None,
    campaign: str | None = None,
) -> dict[str, Any]:
    """Create a safe draft or publish through the configured platform adapter."""
    hashtags = hashtags or []
    asset_url = _absolute_asset_url(video_url)
    full_caption = (caption or "").strip()
    if hashtags:
        full_caption = f"{full_caption} {' '.join(hashtags)}".strip()
    # Opt-in UTM-tagged CTA link appended to the caption. No-op unless
    # THO_UTM_CTA_ENABLED is set and a canonical origin resolves, so default
    # behavior is byte-for-byte unchanged for the live site.
    cta_link = _utm_cta_link(platform, campaign)
    if cta_link:
        full_caption = f"{full_caption}\n{cta_link}".strip()

    if platform not in {"tiktok", "instagram_reels"}:
        return _draft_response(
            platform=platform,
            content_type=content_type,
            caption=full_caption,
            hashtags=hashtags,
            asset_url=asset_url,
            scheduled_time=scheduled_time,
            reason="Platform is queued locally; live adapter is not implemented yet.",
        )

    if content_type == "video" and not asset_url:
        return _draft_response(
            platform=platform,
            content_type=content_type,
            caption=full_caption,
            hashtags=hashtags,
            asset_url=asset_url,
            scheduled_time=scheduled_time,
            reason="A public video URL is required before this can be published.",
        )

    readiness = social_readiness()
    platform_ready = readiness["platforms"].get(platform, {})
    if not platform_ready.get("configured"):
        missing = ", ".join(platform_ready.get("required_env") or [])
        return _draft_response(
            platform=platform,
            content_type=content_type,
            caption=full_caption,
            hashtags=hashtags,
            asset_url=asset_url,
            scheduled_time=scheduled_time,
            reason=f"Missing social publishing configuration: {missing or 'unknown'}",
        )

    if not readiness["publish_enabled"]:
        return _draft_response(
            platform=platform,
            content_type=content_type,
            caption=full_caption,
            hashtags=hashtags,
            asset_url=asset_url,
            scheduled_time=scheduled_time,
            reason="THO_SOCIAL_PUBLISH_ENABLED is not enabled, so this remains a reviewed draft.",
        )

    if platform == "tiktok":
        publish_result = _publish_tiktok_video(asset_url or "", full_caption)
    else:
        publish_result = _publish_instagram_reel(asset_url or "", full_caption)

    if not publish_result.get("success"):
        return {
            **_draft_response(
                platform=platform,
                content_type=content_type,
                caption=full_caption,
                hashtags=hashtags,
                asset_url=asset_url,
                scheduled_time=scheduled_time,
                reason=publish_result.get("error", "Live publish failed."),
            ),
            "success": False,
        }

    return {
        "success": True,
        "post_id": publish_result["post_id"],
        "platform": platform,
        "content_type": content_type,
        "scheduled_time": scheduled_time,
        "caption": full_caption,
        "hashtags": hashtags,
        "video_url": asset_url,
        "status": "published",
        "live_integration": True,
        "publish_attempted": True,
        "api_debug": publish_result.get("api_response"),
        "published_at": datetime.now().isoformat(),
    }
