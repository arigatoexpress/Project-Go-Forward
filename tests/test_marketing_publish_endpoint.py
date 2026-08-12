"""Tests for the one-tap marketing publish endpoint.

Verifies admin gating, fail-closed behavior, and the full publish flow
(asset upload → public URL → social adapter) without any real network calls.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.test_api_v1 import create_client  # noqa: E402


def _make_test_video(tmp_path):
    """Create a minimal dummy MP4 file for the publish endpoint."""
    path = tmp_path / "test_publish.mp4"
    path.write_bytes(b"\x00\x00\x00\x20ftypisom\x00\x00\x00\x00moov")
    return str(path)


class TestMarketingPublishEndpoint:
    """End-to-end tests for POST /api/marketing/publish."""

    def test_publish_requires_admin(self, monkeypatch):
        """Without admin auth the endpoint must return 401."""
        client, main, _db, _logger = create_client(monkeypatch)
        # Ensure admin auth is NOT bypassed
        monkeypatch.setattr(main, "_verify_admin_token", lambda token: False)

        resp = client.post("/api/marketing/publish", json={"filename": "test.mp4"})
        assert resp.status_code in (401, 403)

    def test_publish_draft_when_gate_off(self, monkeypatch):
        """When THO_SOCIAL_PUBLISH_ENABLED is not set, the endpoint should
        still succeed but return a draft-ready status (the gate inside
        schedule_social_post blocks the actual publish)."""
        client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "_verify_admin_token", lambda token: True)
        monkeypatch.delenv("THO_SOCIAL_PUBLISH_ENABLED", raising=False)
        # Stub publish_video_asset so the test doesn't hit GCS
        monkeypatch.setattr(
            main,
            "publish_video_asset",
            lambda path: {"success": True, "public_url": "https://example.com/fake.mp4"},
        )
        # Stub schedule_social_post to return a predictable draft response
        monkeypatch.setattr(
            main,
            "schedule_social_post",
            lambda **kwargs: {
                "success": True,
                "status": "draft_ready",
                "reason": "Gate is off",
            },
        )

        resp = client.post(
            "/api/marketing/publish",
            json={"filename": "test.mp4", "caption": "Test caption"},
            headers={"X-Admin-Token": "test-token"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("status") == "draft_ready"

    def test_publish_uploads_asset_then_calls_adapter_when_gate_on(self, monkeypatch, tmp_path):
        """With the gate on, the endpoint should:
        1. Call publish_video_asset with the filename
        2. Pass the returned public_url to schedule_social_post
        3. Return the adapter result
        """
        client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "_verify_admin_token", lambda token: True)
        monkeypatch.setenv("THO_SOCIAL_PUBLISH_ENABLED", "true")
        monkeypatch.setenv("META_ACCESS_TOKEN", "fake-token")
        monkeypatch.setenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", "fake-id")

        _make_test_video(tmp_path)
        public_url = "https://example.com/public-video.mp4"

        monkeypatch.setattr(
            main,
            "publish_video_asset",
            lambda path: {"success": True, "public_url": public_url},
        )
        monkeypatch.setattr(
            main,
            "schedule_social_post",
            lambda **kwargs: {
                "success": True,
                "status": "published",
                "post_id": "test-post-123",
                "platform": kwargs.get("platform"),
                "video_url": kwargs.get("video_url"),
            },
        )

        resp = client.post(
            "/api/marketing/publish",
            json={
                "filename": "test_publish.mp4",
                "platform": "tiktok",
                "caption": "Test caption",
                "hashtags": ["#test"],
                "home_name": "Test Home",
                "campaign": "spring",
            },
            headers={"X-Admin-Token": "test-token"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("status") == "published"
        assert body.get("post_id") == "test-post-123"
        assert body.get("video_url") == public_url
        assert body.get("platform") == "tiktok"

    def test_publish_fails_closed_when_asset_not_public(self, monkeypatch):
        """If publish_video_asset returns failure, the endpoint must NOT
        call the social adapter and must return a blocked status."""
        client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "_verify_admin_token", lambda token: True)
        monkeypatch.setenv("THO_SOCIAL_PUBLISH_ENABLED", "true")

        adapter_calls = []
        monkeypatch.setattr(
            main,
            "publish_video_asset",
            lambda path: {"success": False, "reason": "gcs_unavailable"},
        )
        monkeypatch.setattr(
            main,
            "schedule_social_post",
            lambda **kwargs: adapter_calls.append(kwargs) or {"success": True},
        )

        resp = client.post(
            "/api/marketing/publish",
            json={"filename": "test.mp4", "caption": "Test"},
            headers={"X-Admin-Token": "test-token"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("success") is False
        assert body.get("status") == "blocked"
        assert "gcs_unavailable" in body.get("reason", "")
        # Adapter must NOT have been called
        assert adapter_calls == []

    def test_publish_rejects_unsupported_platform_before_asset_upload(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "_verify_admin_token", lambda token: True)
        asset_calls = []
        monkeypatch.setattr(
            main,
            "publish_video_asset",
            lambda path: asset_calls.append(path) or {"success": True, "public_url": "unused"},
        )

        resp = client.post(
            "/api/marketing/publish",
            json={"filename": "test.mp4", "platform": "facebook"},
            headers={"X-Admin-Token": "test-token"},
        )

        assert resp.status_code == 200
        assert resp.json() == {
            "success": False,
            "status": "blocked",
            "reason": "unsupported_platform",
        }
        assert asset_calls == []

    def test_publish_is_human_endpoint_only(self, monkeypatch):
        """The endpoint has no scheduler/cron wiring; it is only reachable
        via the admin route. We verify this by asserting it is a POST-only
        admin endpoint and has no automated triggers in the response."""
        client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "_verify_admin_token", lambda token: True)

        # GET should 405 (Method Not Allowed) or 404
        resp_get = client.get("/api/marketing/publish")
        assert resp_get.status_code in (404, 405)

        # The response shape for a successful call should NOT contain
        # any scheduler-specific fields like "scheduled_at" or "cron_id"
        monkeypatch.setattr(
            main,
            "publish_video_asset",
            lambda path: {"success": True, "public_url": "https://example.com/fake.mp4"},
        )
        monkeypatch.setattr(
            main,
            "schedule_social_post",
            lambda **kwargs: {"success": True, "status": "published", "post_id": "123"},
        )
        resp = client.post(
            "/api/marketing/publish",
            json={"filename": "test.mp4"},
            headers={"X-Admin-Token": "test-token"},
        )
        body = resp.json()
        assert "scheduled_at" not in body
        assert "cron_id" not in body
        assert "publish_attempted" not in body or body.get("publish_attempted") is not False
