"""Safety tests for the dormant social publish endpoint.

Live publishing must remain impossible until it has purpose-bound owner
approval, durable idempotency, and a fail-closed authority ledger.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.test_api_v1 import create_client  # noqa: E402


class TestMarketingPublishEndpoint:
    def test_publish_requires_admin(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "_verify_admin_token", lambda token: False)

        response = client.post("/api/marketing/publish", json={"filename": "test.mp4"})

        assert response.status_code in (401, 403)

    def test_publish_is_blocked_before_asset_or_provider_when_legacy_gate_is_on(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "_verify_admin_token", lambda token: True)
        monkeypatch.setenv("THO_SOCIAL_PUBLISH_ENABLED", "true")
        monkeypatch.setenv("META_ACCESS_TOKEN", "configured-but-not-authority")
        monkeypatch.setenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", "configured-but-not-authority")
        asset_calls = []
        provider_calls = []
        monkeypatch.setattr(
            main,
            "publish_video_asset",
            lambda path: asset_calls.append(path)
            or {"success": True, "public_url": "https://example.com/video.mp4"},
            raising=False,
        )
        monkeypatch.setattr(
            main,
            "schedule_social_post",
            lambda **kwargs: provider_calls.append(kwargs)
            or {"success": True, "status": "published"},
        )

        response = client.post(
            "/api/marketing/publish",
            json={"filename": "test.mp4", "platform": "instagram_reels"},
            headers={"X-Admin-Token": "test-token"},
        )

        assert response.status_code == 200
        assert response.json() == {
            "success": False,
            "status": "blocked",
            "reason": "purpose_bound_owner_approval_required",
        }
        assert asset_calls == []
        assert provider_calls == []

    def test_publish_rejects_unsupported_platform_before_asset_upload(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "_verify_admin_token", lambda token: True)
        asset_calls = []
        monkeypatch.setattr(
            main,
            "publish_video_asset",
            lambda path: asset_calls.append(path) or {"success": True, "public_url": "unused"},
            raising=False,
        )

        response = client.post(
            "/api/marketing/publish",
            json={"filename": "test.mp4", "platform": "facebook"},
            headers={"X-Admin-Token": "test-token"},
        )

        assert response.status_code == 200
        assert response.json() == {
            "success": False,
            "status": "blocked",
            "reason": "unsupported_platform",
        }
        assert asset_calls == []

    def test_publish_is_post_only_and_never_claims_attempt(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "_verify_admin_token", lambda token: True)

        assert client.get("/api/marketing/publish").status_code in (404, 405)

        response = client.post(
            "/api/marketing/publish",
            json={"filename": "test.mp4"},
            headers={"X-Admin-Token": "test-token"},
        )
        body = response.json()
        assert body["status"] == "blocked"
        assert "post_id" not in body
        assert "publish_attempted" not in body
