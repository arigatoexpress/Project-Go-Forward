"""Fakes for the dormant live publishers.

These tests never open a socket. TikTok is published only after status fetch
reports PUBLISH_COMPLETE.
"""

from __future__ import annotations

import asyncio

import pytest

from tools import social_live_publishers as publishers

_PUBLISH_ENV = (
    "THO_SOCIAL_PUBLISH_ENABLED",
    "TIKTOK_ACCESS_TOKEN",
    "TIKTOK_PRIVACY_LEVEL",
    "TIKTOK_STATUS_POLL_ATTEMPTS",
    "TIKTOK_STATUS_POLL_INTERVAL_SECONDS",
    "META_ACCESS_TOKEN",
    "META_GRAPH_VERSION",
    "META_REEL_POLL_ATTEMPTS",
    "META_REEL_POLL_INTERVAL_SECONDS",
    "INSTAGRAM_BUSINESS_ACCOUNT_ID",
)


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise publishers.requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _isolate_publish_env(monkeypatch):
    for name in _PUBLISH_ENV:
        monkeypatch.delenv(name, raising=False)

    def _unexpected(*_args, **_kwargs):
        raise AssertionError("social provider HTTP must be stubbed in this test")

    monkeypatch.setattr(publishers.requests, "post", _unexpected)
    monkeypatch.setattr(publishers.requests, "get", _unexpected)


def _enable_tiktok(monkeypatch, *, attempts="3", interval="0"):
    monkeypatch.setenv("THO_SOCIAL_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "tiktok-test-token")
    monkeypatch.setenv("TIKTOK_STATUS_POLL_ATTEMPTS", attempts)
    monkeypatch.setenv("TIKTOK_STATUS_POLL_INTERVAL_SECONDS", interval)


def test_module_docstring_requires_off_loop_execution():
    doc = publishers.__doc__ or ""
    assert "asyncio.to_thread" in doc
    assert "run_in_threadpool" in doc


def test_disabled_publish_does_not_call_tiktok(monkeypatch):
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "tiktok-test-token")

    result = publishers.publish_social_post(
        platform="tiktok",
        asset_url="https://cdn.example/clip.mp4",
        caption="Tour",
    )

    assert result == {"success": False, "error_code": "publishing_disabled"}


def test_tiktok_init_alone_is_not_published(monkeypatch):
    _enable_tiktok(monkeypatch, attempts="2", interval="0")
    urls = []

    def fake_post(url, **_kwargs):
        urls.append(url)
        if url == publishers.TIKTOK_PUBLISH_URL:
            return _Response({"data": {"publish_id": "pub-1"}, "error": {"code": "ok"}})
        if url == publishers.TIKTOK_STATUS_URL:
            return _Response({"data": {"status": "PROCESSING_DOWNLOAD"}, "error": {"code": "ok"}})
        raise AssertionError(url)

    monkeypatch.setattr(publishers.requests, "post", fake_post)

    result = publishers.publish_social_post(
        platform="tiktok",
        asset_url="https://cdn.example/clip.mp4",
    )

    assert result["success"] is False
    assert result["error_code"] == "provider_publish_timeout"
    assert result.get("status") != "published"
    assert "published_at" not in result
    assert urls == [
        publishers.TIKTOK_PUBLISH_URL,
        publishers.TIKTOK_STATUS_URL,
        publishers.TIKTOK_STATUS_URL,
    ]


def test_tiktok_failed_status_is_not_published(monkeypatch):
    _enable_tiktok(monkeypatch, attempts="3", interval="0")

    def fake_post(url, **_kwargs):
        if url == publishers.TIKTOK_PUBLISH_URL:
            return _Response({"data": {"publish_id": "pub-2"}, "error": {"code": "ok"}})
        return _Response(
            {
                "data": {"status": "FAILED", "fail_reason": "video_pull_failed"},
                "error": {"code": "ok"},
            }
        )

    monkeypatch.setattr(publishers.requests, "post", fake_post)

    result = publishers.publish_social_post(
        platform="tiktok",
        asset_url="https://cdn.example/clip.mp4",
    )

    assert result == {"success": False, "error_code": "provider_publish_failed"}
    assert "fail_reason" not in result
    assert "published_at" not in result


def test_tiktok_publish_complete_returns_public_post_id(monkeypatch):
    _enable_tiktok(monkeypatch, attempts="3", interval="3")
    slept = []
    statuses = iter(("PROCESSING_DOWNLOAD", "PUBLISH_COMPLETE"))
    monkeypatch.setattr(publishers.time, "sleep", slept.append)

    def fake_post(url, **kwargs):
        if url == publishers.TIKTOK_PUBLISH_URL:
            assert kwargs["headers"]["Authorization"] == "Bearer tiktok-test-token"
            assert kwargs["json"]["source_info"]["source"] == "PULL_FROM_URL"
            return _Response({"data": {"publish_id": "pub-3"}, "error": {"code": "ok"}})
        assert kwargs["json"] == {"publish_id": "pub-3"}
        status = next(statuses)
        payload = {"status": status}
        if status == "PUBLISH_COMPLETE":
            payload["publicaly_available_post_id"] = [789]
        return _Response({"data": payload, "error": {"code": "ok"}})

    monkeypatch.setattr(publishers.requests, "post", fake_post)

    result = publishers.publish_social_post(
        platform="tiktok",
        asset_url="https://cdn.example/clip.mp4",
        caption="Open house",
        hashtags=["#homes"],
    )

    assert result["success"] is True
    assert result["status"] == "published"
    assert result["post_id"] == "789"
    assert result["published_at"]
    assert slept == [3.0]


def test_tiktok_publish_complete_without_public_id_uses_publish_id(monkeypatch):
    _enable_tiktok(monkeypatch)

    def fake_post(url, **_kwargs):
        if url == publishers.TIKTOK_PUBLISH_URL:
            return _Response({"data": {"publish_id": "pub-4"}, "error": {"code": "ok"}})
        return _Response(
            {
                "data": {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": []},
                "error": {"code": "ok"},
            }
        )

    monkeypatch.setattr(publishers.requests, "post", fake_post)

    result = publishers.publish_social_post(
        platform="tiktok",
        asset_url="https://cdn.example/clip.mp4",
    )

    assert result["status"] == "published"
    assert result["post_id"] == "pub-4"


def test_tiktok_missing_publish_id_does_not_poll(monkeypatch):
    _enable_tiktok(monkeypatch)
    urls = []

    def fake_post(url, **_kwargs):
        urls.append(url)
        return _Response({"data": {}, "error": {"code": "ok"}})

    monkeypatch.setattr(publishers.requests, "post", fake_post)

    result = publishers.publish_social_post(
        platform="tiktok",
        asset_url="https://cdn.example/clip.mp4",
    )

    assert result == {"success": False, "error_code": "provider_invalid_response"}
    assert urls == [publishers.TIKTOK_PUBLISH_URL]


def test_instagram_reports_published_only_after_media_publish(monkeypatch):
    monkeypatch.setenv("THO_SOCIAL_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("META_ACCESS_TOKEN", "meta-test-token")
    monkeypatch.setenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", "1789")
    monkeypatch.setenv("META_REEL_POLL_INTERVAL_SECONDS", "0")
    posts = []

    def fake_post(url, **_kwargs):
        posts.append(url)
        if url.endswith("/media"):
            return _Response({"id": "creation-1"})
        if url.endswith("/media_publish"):
            return _Response({"id": "ig-post-1"})
        raise AssertionError(url)

    monkeypatch.setattr(publishers.requests, "post", fake_post)
    monkeypatch.setattr(
        publishers.requests,
        "get",
        lambda *_args, **_kwargs: _Response({"status_code": "FINISHED"}),
    )

    result = publishers.publish_social_post(
        platform="instagram_reels",
        asset_url="https://cdn.example/clip.mp4",
        caption="Reel",
    )

    assert result["success"] is True
    assert result["status"] == "published"
    assert result["post_id"] == "ig-post-1"
    assert any(url.endswith("/media_publish") for url in posts)


def test_async_wrapper_uses_to_thread(monkeypatch):
    seen = {}

    async def fake_to_thread(func, /, *args, **kwargs):
        seen["func"] = func
        seen["kwargs"] = kwargs
        return {"success": False, "error_code": "publishing_disabled"}

    monkeypatch.setattr(publishers.asyncio, "to_thread", fake_to_thread)

    result = asyncio.run(
        publishers.publish_social_post_async(
            platform="tiktok",
            asset_url="https://cdn.example/clip.mp4",
            caption="Tour",
        )
    )

    assert seen["func"] is publishers.publish_social_post
    assert seen["kwargs"]["platform"] == "tiktok"
    assert seen["kwargs"]["caption"] == "Tour"
    assert result["error_code"] == "publishing_disabled"
