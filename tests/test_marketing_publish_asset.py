"""Tests for public asset publishing (tools/marketing_assets.py).

Exercises the GCS upload + signed/public URL paths with mocked buckets so no
real network call is made. The local-fallback path is also verified.
"""

from __future__ import annotations

import importlib
import os
from unittest.mock import MagicMock

import pytest

marketing_assets = importlib.import_module("tools.marketing_assets")


@pytest.fixture(autouse=True)
def _reset_module_cache(monkeypatch):
    """Reset the module-level GCS cache before each test."""
    monkeypatch.setattr(marketing_assets, "_gcs_client", None, raising=False)
    monkeypatch.setattr(marketing_assets, "_gcs_bucket", None, raising=False)
    monkeypatch.setattr(marketing_assets, "_gcs_unavailable", False, raising=False)


def _fake_bucket(signed_url: str = "https://signed.example/v4/test") -> MagicMock:
    """Return a mock bucket whose blob generates a signed URL."""
    bucket = MagicMock()
    blob = MagicMock()
    blob.generate_signed_url.return_value = signed_url
    blob.public_url = "https://storage.googleapis.com/tho-publish-assets/test"
    bucket.blob.return_value = blob
    return bucket


def _make_video_file(tmp_path):
    """Create a tiny dummy MP4 file for upload tests."""
    path = tmp_path / "test_video.mp4"
    # Minimal MP4 box header (ftyp + moov) so content_type sniffing would work
    # if we ever add it; for now just needs to exist.
    path.write_bytes(b"\x00\x00\x00\x20ftypisom\x00\x00\x00\x00moov")
    return str(path)


class TestPublishVideoAsset:
    """End-to-end tests for publish_video_asset."""

    def test_local_file_not_found(self, monkeypatch):
        monkeypatch.setattr(marketing_assets, "_get_bucket", lambda: _fake_bucket())
        result = marketing_assets.publish_video_asset("/nonexistent/fake.mp4")
        assert result["success"] is False
        assert "not_found" in result["reason"].lower() or "local_file_not_found" in result["reason"]

    def test_gcs_disabled_returns_local_fallback(self, tmp_path, monkeypatch):
        """When GCS is unavailable, fail closed with a clear reason."""
        monkeypatch.setenv("THO_DISABLE_GCS_UPLOADS", "1")
        monkeypatch.setattr(marketing_assets, "_gcs_unavailable", True)
        video_path = _make_video_file(tmp_path)
        result = marketing_assets.publish_video_asset(video_path)
        assert result["success"] is False
        assert "gcs_unavailable" in result["reason"]

    def test_uploads_with_random_name(self, tmp_path, monkeypatch):
        bucket = _fake_bucket()
        monkeypatch.setattr(marketing_assets, "_get_bucket", lambda: bucket)
        video_path = _make_video_file(tmp_path)
        result = marketing_assets.publish_video_asset(video_path)
        assert result["success"] is True
        assert result["public_url"] == "https://signed.example/v4/test"
        # Blob name should be random / unguessable
        blob_name = bucket.blob.call_args[0][0]
        assert blob_name.startswith("publish_assets/")
        assert "test_video.mp4" in blob_name
        # The random part should be a UUID prefix
        random_part = blob_name.replace("publish_assets/", "").replace("-test_video.mp4", "")
        assert len(random_part) >= 8  # uuid.hex is 32 chars

    def test_signed_url_mode(self, tmp_path, monkeypatch):
        bucket = _fake_bucket(signed_url="https://signed.example/abc123")
        monkeypatch.setattr(marketing_assets, "_get_bucket", lambda: bucket)
        monkeypatch.setenv("META_ASSET_PUBLIC", "false")
        video_path = _make_video_file(tmp_path)
        result = marketing_assets.publish_video_asset(video_path)
        assert result["success"] is True
        assert result["public_url"] == "https://signed.example/abc123"
        blob = bucket.blob.return_value
        blob.generate_signed_url.assert_called_once()
        kwargs = blob.generate_signed_url.call_args.kwargs
        assert kwargs.get("version") == "v4"
        assert kwargs.get("method") == "GET"
        assert kwargs.get("expiration") == 600  # default TTL

    def test_public_mode_returns_public_url(self, tmp_path, monkeypatch):
        bucket = _fake_bucket()
        monkeypatch.setattr(marketing_assets, "_get_bucket", lambda: bucket)
        monkeypatch.setenv("META_ASSET_PUBLIC", "true")
        video_path = _make_video_file(tmp_path)
        result = marketing_assets.publish_video_asset(video_path)
        assert result["success"] is True
        assert result["public_url"] == "https://storage.googleapis.com/tho-publish-assets/test"
        bucket.blob.return_value.make_public.assert_called_once()

    def test_public_mode_failure_returns_reason(self, tmp_path, monkeypatch):
        bucket = _fake_bucket()
        blob = bucket.blob.return_value
        blob.make_public.side_effect = RuntimeError("ACL denied")
        monkeypatch.setattr(marketing_assets, "_get_bucket", lambda: bucket)
        monkeypatch.setenv("META_ASSET_PUBLIC", "true")
        video_path = _make_video_file(tmp_path)
        result = marketing_assets.publish_video_asset(video_path)
        assert result["success"] is False
        assert "public_acl_failed" in result["reason"]

    def test_upload_failure_returns_reason(self, tmp_path, monkeypatch):
        bucket = _fake_bucket()
        bucket.blob.return_value.upload_from_filename.side_effect = RuntimeError("Network broken")
        monkeypatch.setattr(marketing_assets, "_get_bucket", lambda: bucket)
        video_path = _make_video_file(tmp_path)
        result = marketing_assets.publish_video_asset(video_path)
        assert result["success"] is False
        assert "upload_failed" in result["reason"]

    def test_path_traversal_rejected(self, tmp_path, monkeypatch):
        bucket = _fake_bucket()
        monkeypatch.setattr(marketing_assets, "_get_bucket", lambda: bucket)
        # Pass a path traversal string
        result = marketing_assets.publish_video_asset("../../../etc/passwd")
        assert result["success"] is False
        assert "invalid_filename" in result["reason"] or "not_found" in result["reason"].lower()

    def test_resolves_bare_filename_from_generated_videos_dir(self, tmp_path, monkeypatch):
        """A bare filename should be resolved against GENERATED_VIDEOS_DIR."""
        from tools import video_generator

        monkeypatch.setattr(video_generator, "GENERATED_VIDEOS_DIR", str(tmp_path))
        bucket = _fake_bucket()
        monkeypatch.setattr(marketing_assets, "_get_bucket", lambda: bucket)
        video_path = _make_video_file(tmp_path)
        basename = os.path.basename(video_path)
        result = marketing_assets.publish_video_asset(basename)
        assert result["success"] is True
        assert result["public_url"] == "https://signed.example/v4/test"
