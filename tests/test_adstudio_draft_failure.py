"""Prepare Draft must not look successful when preparation fails.

Script, ideas, and analytics still return an ``error`` field on HTTP 200.
Only ``POST /api/marketing/schedule`` has to make a failure distinguishable.
"""

from __future__ import annotations

import pytest

from tests.test_api_v1 import create_client

DRAFT_NOT_SAVED = "The draft wasn't saved. Please try again."


def _admin_client(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    token = main._create_admin_token()
    return client, main, {"X-Admin-Token": token}


def test_marketing_schedule_success_stays_a_response_only_draft(monkeypatch):
    client, main, headers = _admin_client(monkeypatch)

    def prepare_draft(**kwargs):
        return {
            "success": True,
            "post_id": "DRAFT-IG-ABC123",
            "platform": kwargs.get("platform"),
            "content_type": kwargs.get("content_type"),
            "status": "draft_ready",
            "persisted": False,
            "retention": "response_only",
            "publish_attempted": False,
        }

    monkeypatch.setattr(main, "schedule_social_post", prepare_draft)

    response = client.post(
        "/api/marketing/schedule",
        json={"platform": "instagram_reels", "content_type": "video"},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["post_id"] == "DRAFT-IG-ABC123"
    assert body["persisted"] is False
    assert body["retention"] == "response_only"
    assert "error" not in body


def test_marketing_schedule_exception_is_not_reported_as_success(monkeypatch):
    """A raised preparation error used to come back as HTTP 200 with ``error``."""
    client, main, headers = _admin_client(monkeypatch)

    def explode(**_kwargs):
        raise RuntimeError("draft store unavailable")

    monkeypatch.setattr(main, "schedule_social_post", explode)

    response = client.post(
        "/api/marketing/schedule",
        json={"platform": "tiktok", "content_type": "video", "script_id": "SCR-1"},
        headers=headers,
    )

    assert response.status_code == 500
    body = response.json()
    assert body.get("success") is False
    assert body.get("status_code") == 500
    assert "post_id" not in body
    looks_like_saved_draft = (
        body.get("persisted") is False and body.get("retention") == "response_only"
    )
    assert not looks_like_saved_draft
    message = body.get("message") or body.get("error") or ""
    assert message == DRAFT_NOT_SAVED
    assert "draft store unavailable" not in response.text


@pytest.mark.parametrize(
    ("method", "path", "target"),
    [
        ("post", "/api/marketing/generate-script", "generate_content_script"),
        ("get", "/api/marketing/trending-ideas", "get_trending_content_ideas"),
        ("get", "/api/marketing/analytics", "analyze_content_performance"),
    ],
)
def test_other_ad_studio_calls_still_return_an_error_field_on_http_200(
    monkeypatch, method, path, target
):
    client, main, headers = _admin_client(monkeypatch)

    def explode(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(main, target, explode)
    request = getattr(client, method)
    kwargs = {"headers": headers}
    if method == "post":
        kwargs["json"] = {"platform": "tiktok"}

    response = request(path, **kwargs)

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body.get("error"), str)
    assert body["error"]
    assert "provider unavailable" not in response.text
