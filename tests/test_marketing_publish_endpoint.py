"""Safety and error contracts for Ad Studio draft and live publish routes."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.test_api_v1 import create_client  # noqa: E402


def _admin_client(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch)
    return client, main, {"X-Admin-Token": main._create_admin_token()}


def test_schedule_never_invokes_live_publisher(monkeypatch):
    client, main, admin_headers = _admin_client(monkeypatch)
    from tools.social_publishers import prepare_social_post_draft

    monkeypatch.setenv("THO_SOCIAL_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "configured-for-test")
    publish_calls = []
    monkeypatch.setattr(
        main,
        "publish_social_post",
        lambda **kwargs: publish_calls.append(kwargs) or {"success": True},
    )
    monkeypatch.setattr(
        main,
        "schedule_social_post",
        lambda **kwargs: prepare_social_post_draft(
            platform=kwargs["platform"],
            content_type=kwargs["content_type"],
            scheduled_time=kwargs.get("post_time") or "2026-08-31T12:00:00",
            caption=kwargs.get("caption") or "",
            hashtags=kwargs.get("hashtags"),
            video_url=kwargs.get("video_url"),
            campaign=kwargs.get("campaign"),
        ),
    )

    response = client.post(
        "/api/marketing/schedule",
        json={
            "platform": "tiktok",
            "content_type": "video",
            "caption": "Reviewed draft",
            "video_url": "/api/marketing/videos/review.mp4",
        },
        headers=admin_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["status"] == "draft_ready"
    assert body["publish_attempted"] is False
    assert publish_calls == []


def test_publish_honors_selected_platform(monkeypatch):
    client, main, admin_headers = _admin_client(monkeypatch)
    publish_calls = []
    monkeypatch.setattr(
        main,
        "social_publish_readiness",
        lambda platform: {
            "supported": True,
            "configured": True,
            "publish_enabled": True,
        },
    )
    monkeypatch.setattr(
        main,
        "publish_video_asset",
        lambda filename: {"success": True, "public_url": "https://cdn.example.com/ad.mp4"},
    )
    monkeypatch.setattr(
        main,
        "publish_social_post",
        lambda **kwargs: publish_calls.append(kwargs)
        or {
            "success": True,
            "status": "published",
            "post_id": "tiktok-post-1",
            "platform": kwargs["platform"],
        },
    )

    response = client.post(
        "/api/marketing/publish",
        json={
            "platform": "tiktok",
            "filename": "approved.mp4",
            "caption": "Post this to TikTok",
            "hashtags": ["#TexasHomes"],
        },
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json()["platform"] == "tiktok"
    assert publish_calls == [
        {
            "platform": "tiktok",
            "asset_url": "https://cdn.example.com/ad.mp4",
            "caption": "Post this to TikTok",
            "hashtags": ["#TexasHomes"],
        }
    ]


def test_publish_rejects_unconfigured_platform_with_non_2xx(monkeypatch):
    client, main, admin_headers = _admin_client(monkeypatch)
    monkeypatch.setattr(
        main,
        "social_publish_readiness",
        lambda platform: {
            "supported": True,
            "configured": False,
            "publish_enabled": True,
        },
    )

    response = client.post(
        "/api/marketing/publish",
        json={"platform": "instagram_reels", "filename": "approved.mp4"},
        headers=admin_headers,
    )

    assert response.status_code == 503
    assert response.json() == {
        "success": False,
        "error": "Instagram Reels publishing is not configured.",
        "error_code": "platform_not_configured",
    }


def test_publish_rejects_unsupported_platform(monkeypatch):
    client, _main, admin_headers = _admin_client(monkeypatch)

    response = client.post(
        "/api/marketing/publish",
        json={"platform": "facebook", "filename": "approved.mp4"},
        headers=admin_headers,
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "unsupported_platform"


def test_schedule_failure_is_non_2xx_and_does_not_leak_exception(monkeypatch):
    client, main, admin_headers = _admin_client(monkeypatch)

    def fail_draft(**_kwargs):
        raise RuntimeError("secret provider detail")

    monkeypatch.setattr(main, "schedule_social_post", fail_draft)

    response = client.post(
        "/api/marketing/schedule",
        json={"platform": "tiktok", "content_type": "video"},
        headers=admin_headers,
    )

    assert response.status_code == 500
    assert response.json()["success"] is False
    assert "secret provider detail" not in response.text
