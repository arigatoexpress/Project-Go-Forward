"""Tests for Ad Studio's local-only social draft builder."""

from __future__ import annotations

import pytest

from tools import social_publishers

_DRAFT_ENV_VARS = (
    "THO_UTM_CTA_ENABLED",
    "THO_UTM_SOURCE",
    "THO_UTM_MEDIUM",
    "PUBLIC_SITE_URL",
)


@pytest.fixture(autouse=True)
def _isolate_draft_env(monkeypatch):
    for variable in _DRAFT_ENV_VARS:
        monkeypatch.delenv(variable, raising=False)


def test_prepare_social_post_draft_preserves_reviewed_content(monkeypatch):
    monkeypatch.setenv("PUBLIC_SITE_URL", "https://example.com")

    result = social_publishers.prepare_social_post_draft(
        platform="instagram_reels",
        content_type="video",
        scheduled_time="2026-07-01T12:00:00",
        caption="New listing tour",
        hashtags=["#texashomes"],
        video_url="/api/marketing/videos/clip.mp4",
    )

    assert result["success"] is True
    assert result["status"] == "draft_ready"
    assert result["live_integration"] is False
    assert result["publish_attempted"] is False
    assert result["persisted"] is False
    assert result["retention"] == "response_only"
    assert result["post_id"].startswith("DRAFT-")
    assert result["caption"] == "New listing tour #texashomes"
    assert result["video_url"] == "https://example.com/api/marketing/videos/clip.mp4"
    assert result["publish_blocked_reason"] == (
        "Draft prepared locally; no social platform request was made."
    )
    assert "social_readiness" not in result


def test_prepare_social_post_draft_needs_no_provider_configuration(monkeypatch):
    monkeypatch.setenv("PUBLIC_SITE_URL", "https://example.com")

    result = social_publishers.prepare_social_post_draft(
        platform="tiktok",
        content_type="video",
        scheduled_time="2026-07-01T12:00:00",
        caption="New listing tour",
        video_url="https://cdn.example.com/clip.mp4",
    )

    assert result["status"] == "draft_ready"
    assert result["publish_attempted"] is False


def test_utm_cta_link_none_when_gate_unset(monkeypatch):
    monkeypatch.setenv("PUBLIC_SITE_URL", "https://example.com")

    assert social_publishers._utm_cta_link("tiktok", "Spring Sale") is None


def test_utm_cta_link_none_when_enabled_but_no_origin(monkeypatch):
    monkeypatch.setenv("THO_UTM_CTA_ENABLED", "true")
    monkeypatch.setattr(social_publishers, "_canonical_origin", lambda: None)

    assert social_publishers._utm_cta_link("tiktok", "Spring Sale") is None


def test_utm_cta_link_tagged_url_when_enabled_with_origin(monkeypatch):
    monkeypatch.setenv("THO_UTM_CTA_ENABLED", "1")
    monkeypatch.setenv("PUBLIC_SITE_URL", "https://example.com/")

    link = social_publishers._utm_cta_link("tiktok", "Spring Sale 2026")

    assert link is not None
    assert link.startswith("https://example.com/?")
    assert "utm_source=tiktok" in link
    assert "utm_medium=social" in link
    assert "utm_campaign=spring-sale-2026" in link


def test_utm_cta_link_respects_source_and_medium_overrides(monkeypatch):
    monkeypatch.setenv("THO_UTM_CTA_ENABLED", "yes")
    monkeypatch.setenv("PUBLIC_SITE_URL", "https://example.com")
    monkeypatch.setenv("THO_UTM_SOURCE", "ig")
    monkeypatch.setenv("THO_UTM_MEDIUM", "paid_social")

    link = social_publishers._utm_cta_link("instagram_reels", None)

    assert link is not None
    assert "utm_source=ig" in link
    assert "utm_medium=paid_social" in link
    assert "utm_campaign=ad-studio" in link
