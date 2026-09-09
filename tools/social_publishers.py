"""Draft preparation helpers for Ad Studio social creatives.

This module formats reviewed drafts and optional UTM links. It intentionally
contains no social-provider credentials, transports, or publishing adapters.
"""

from __future__ import annotations

import os
import re
import uuid
from typing import Any
from urllib.parse import urlencode, urljoin

TRUTHY = {"1", "true", "yes", "on", "enabled"}


def _enabled(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in TRUTHY


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
        # This compatibility response is returned to the current browser only.
        # No server-side draft record or provider-side object is created.
        "persisted": False,
        "retention": "response_only",
        "publish_blocked_reason": reason,
    }


def prepare_social_post_draft(
    *,
    platform: str,
    content_type: str,
    scheduled_time: str,
    caption: str,
    hashtags: list[str] | None = None,
    video_url: str | None = None,
    campaign: str | None = None,
) -> dict[str, Any]:
    """Create a reviewed draft without invoking any social-provider API."""
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

    return _draft_response(
        platform=platform,
        content_type=content_type,
        caption=full_caption,
        hashtags=hashtags,
        asset_url=asset_url,
        scheduled_time=scheduled_time,
        reason="Draft prepared locally; no social platform request was made.",
    )
