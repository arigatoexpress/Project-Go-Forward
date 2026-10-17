"""Fakes for the dormant social asset uploader.

No Cloud Storage or IAM calls. Signing uses the same service-account email
plus access-token arguments that Cloud Run needs for IAM signBlob.
"""

from __future__ import annotations

import asyncio

import pytest

from tools import social_publish_assets as assets

_ASSET_ENV = (
    "THO_DISABLE_GCS_UPLOADS",
    "GCS_PUBLISH_ASSETS_BUCKET",
    "META_ASSET_PUBLIC",
    "META_ASSET_URL_TTL_SECONDS",
)


class _Credentials:
    def __init__(
        self, token="access-token-for-test", email="signer@tho-ai-agent.iam.gserviceaccount.com"
    ):
        self.token = token
        self.service_account_email = email
        self.refresh_calls = 0

    def refresh(self, _request):
        self.refresh_calls += 1
        if self.token is None:
            self.token = "refreshed-access-token"
        if self.service_account_email == "default":
            self.service_account_email = "signer@tho-ai-agent.iam.gserviceaccount.com"


class _Blob:
    def __init__(self, sign_error: Exception | None = None):
        self.sign_error = sign_error
        self.uploaded = None
        self.made_public = False
        self.public_url = "https://storage.googleapis.com/tho-publish-assets/obj"
        self.signed_calls: list[dict] = []

    def upload_from_filename(self, filename, content_type=None):
        self.uploaded = (filename, content_type)

    def make_public(self):
        self.made_public = True

    def generate_signed_url(self, **kwargs):
        self.signed_calls.append(kwargs)
        if self.sign_error:
            raise self.sign_error
        return "https://storage.googleapis.com/signed-object"


class _Bucket:
    def __init__(self, blob: _Blob):
        self.blob_obj = blob
        self.names: list[str] = []

    def blob(self, name):
        self.names.append(name)
        return self.blob_obj


@pytest.fixture(autouse=True)
def _isolate_asset_env(monkeypatch):
    for name in _ASSET_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(assets, "_gcs_client", None)
    monkeypatch.setattr(assets, "_gcs_bucket", None)
    monkeypatch.setattr(assets, "_gcs_unavailable", False)

    def _unexpected_default(*_args, **_kwargs):
        raise AssertionError("google.auth.default must be stubbed in this test")

    monkeypatch.setattr(assets.google.auth, "default", _unexpected_default)


def _stage_video(monkeypatch, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not-a-real-video")
    monkeypatch.setattr(assets, "_resolve_local_path", lambda _name: str(video))
    return video


def _install_storage(monkeypatch, blob: _Blob, credentials: _Credentials | None = None) -> _Bucket:
    bucket = _Bucket(blob)
    monkeypatch.setattr(assets, "_gcs_bucket", bucket)
    if credentials is not None:
        monkeypatch.setattr(
            assets.google.auth,
            "default",
            lambda scopes=None: (credentials, "tho-ai-agent"),
        )
    return bucket


def test_module_docstring_requires_off_loop_execution():
    doc = assets.__doc__ or ""
    assert "asyncio.to_thread" in doc
    assert "run_in_threadpool" in doc


def test_default_ttl_covers_tiktok_download_window(monkeypatch, tmp_path):
    _stage_video(monkeypatch, tmp_path)
    blob = _Blob()
    credentials = _Credentials(token=None, email="default")
    bucket = _install_storage(monkeypatch, blob, credentials)

    result = assets.publish_video_asset("clip.mp4")

    assert result == {"success": True, "public_url": "https://storage.googleapis.com/signed-object"}
    assert credentials.refresh_calls == 1
    signed = blob.signed_calls[0]
    assert signed["version"] == "v4"
    assert signed["method"] == "GET"
    assert signed["expiration"] == assets._DEFAULT_ASSET_URL_TTL_SECONDS
    assert signed["expiration"] >= 3900
    assert signed["service_account_email"] == "signer@tho-ai-agent.iam.gserviceaccount.com"
    assert signed["access_token"] == "refreshed-access-token"
    assert "private_key" not in signed
    assert bucket.names[0].startswith("publish_assets/")
    assert bucket.names[0].endswith("-clip.mp4")


def test_ttl_override_is_honored_and_clamped(monkeypatch, tmp_path):
    _stage_video(monkeypatch, tmp_path)
    blob = _Blob()
    _install_storage(monkeypatch, blob, _Credentials())
    monkeypatch.setenv("META_ASSET_URL_TTL_SECONDS", "5000")

    assets.publish_video_asset("clip.mp4")
    assert blob.signed_calls[0]["expiration"] == 5000

    blob.signed_calls.clear()
    monkeypatch.setenv("META_ASSET_URL_TTL_SECONDS", "99999999")
    assets.publish_video_asset("clip.mp4")
    assert blob.signed_calls[0]["expiration"] == assets._MAX_ASSET_URL_TTL_SECONDS
    assert blob.signed_calls[0]["expiration"] == 7 * 24 * 60 * 60


def test_invalid_ttl_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("META_ASSET_URL_TTL_SECONDS", "not-a-number")
    assert assets._asset_url_ttl_seconds() == 3900
    monkeypatch.setenv("META_ASSET_URL_TTL_SECONDS", "0")
    assert assets._asset_url_ttl_seconds() == 3900
    monkeypatch.setenv("META_ASSET_URL_TTL_SECONDS", "600")
    assert assets._asset_url_ttl_seconds() == 600


def test_signing_failure_returns_existing_error_code(monkeypatch, tmp_path):
    _stage_video(monkeypatch, tmp_path)
    blob = _Blob(sign_error=AttributeError("you need a private key to sign credentials"))
    _install_storage(monkeypatch, blob, _Credentials())

    result = assets.publish_video_asset("clip.mp4")

    assert result == {"success": False, "error_code": "asset_upload_failed"}
    assert blob.uploaded is not None


def test_placeholder_signing_identity_returns_upload_error(monkeypatch, tmp_path):
    _stage_video(monkeypatch, tmp_path)
    blob = _Blob()

    class _StuckCredentials(_Credentials):
        def refresh(self, _request):
            self.refresh_calls += 1

    credentials = _StuckCredentials(token="access-token-for-test", email="default")
    _install_storage(monkeypatch, blob, credentials)

    result = assets.publish_video_asset("clip.mp4")

    assert result == {"success": False, "error_code": "asset_upload_failed"}
    assert blob.signed_calls == []


def test_public_mode_does_not_sign(monkeypatch, tmp_path):
    _stage_video(monkeypatch, tmp_path)
    blob = _Blob()
    monkeypatch.setattr(assets, "_gcs_bucket", _Bucket(blob))
    monkeypatch.setenv("META_ASSET_PUBLIC", "true")

    result = assets.publish_video_asset("clip.mp4")

    assert result["success"] is True
    assert result["public_url"] == blob.public_url
    assert blob.made_public is True
    assert blob.signed_calls == []


def test_async_wrapper_uses_to_thread(monkeypatch):
    seen = {}

    async def fake_to_thread(func, /, *args, **kwargs):
        seen["func"] = func
        seen["args"] = args
        return {"success": False, "error_code": "local_file_not_found"}

    monkeypatch.setattr(assets.asyncio, "to_thread", fake_to_thread)

    result = asyncio.run(assets.publish_video_asset_async("missing.mp4"))

    assert seen["func"] is assets.publish_video_asset
    assert seen["args"] == ("missing.mp4",)
    assert result["error_code"] == "local_file_not_found"
