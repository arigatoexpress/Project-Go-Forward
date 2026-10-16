"""Tests for two endpoints with zero prior coverage.

Targets flagged by the Python-3.12 honest coverage re-measurement (see
docs/MAIN_COVERAGE_INVESTIGATION.md):

- ``POST /api/marketing/publish``                    — Ad Studio live social publish
- ``GET /api/v1/customer/deal/{id}/download/{note}`` — signed URL minting for
  secure closing documents (phone-last-4 verified)

Marketing-publish dependencies (``social_publish_readiness``,
``publish_video_asset``, ``publish_social_post``) are monkeypatched on the
``main`` module globals. ``google.cloud.storage`` is stubbed in both
``sys.modules`` and as an attribute of the already-imported ``google.cloud``
package (``from google.cloud import storage`` resolves the package attribute
first, so a sys.modules-only stub is shadowed — same ordering trap as
``tools.image_storage``). Firestore is the shared in-memory fake; audit
helpers are no-op-patched (emulator absent; covered elsewhere).

Run: python -m pytest tests/test_marketing_publish_doc_download.py -v
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(Path(__file__).parent))
from test_api_v1 import create_client  # noqa: E402


def _admin_headers(main) -> dict:
    return {"X-Admin-Token": main._create_admin_token()}


# ─── POST /api/marketing/publish ──────────────────────────────────────────


def _patch_publish_deps(monkeypatch, main, calls: dict, **overrides):
    """Replace the live-publish toolchain with recorders/overrides."""

    def social_publish_readiness(platform):
        calls.setdefault("readiness", []).append(platform)
        return overrides.get(
            "readiness",
            {"configured": True, "publish_enabled": True},
        )

    def publish_video_asset(filename):
        calls.setdefault("assets", []).append(filename)
        return overrides.get(
            "asset",
            {"success": True, "public_url": "https://cdn.example.com/video.mp4"},
        )

    def publish_social_post(platform, asset_url, caption, hashtags):
        calls.setdefault("posts", []).append(
            {"platform": platform, "asset_url": asset_url, "caption": caption, "hashtags": hashtags}
        )
        return overrides.get(
            "post",
            {"success": True, "post_id": "tt-123", "platform": platform},
        )

    monkeypatch.setattr(main, "social_publish_readiness", social_publish_readiness)
    monkeypatch.setattr(main, "publish_video_asset", publish_video_asset)
    monkeypatch.setattr(main, "publish_social_post", publish_social_post)


class TestApiMarketingPublish:
    def test_success_publishes_configured_platform(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
        monkeypatch.setattr(main, "log_user_action", lambda **kwargs: None)
        calls: dict = {}
        _patch_publish_deps(monkeypatch, main, calls)

        resp = client.post(
            "/api/marketing/publish",
            json={
                "platform": "tiktok",
                "filename": "video.mp4",
                "caption": "Tour this home!",
                "hashtags": ["#texas", "#mobilehomes"],
            },
            headers=_admin_headers(main),
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["post_id"] == "tt-123"
        assert calls["readiness"] == ["tiktok"]
        assert calls["assets"] == ["video.mp4"]
        assert calls["posts"] == [
            {
                "platform": "tiktok",
                "asset_url": "https://cdn.example.com/video.mp4",
                "caption": "Tour this home!",
                "hashtags": ["#texas", "#mobilehomes"],
            }
        ]

    def test_rejects_non_object_body(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        calls: dict = {}
        _patch_publish_deps(monkeypatch, main, calls)

        resp = client.post(
            "/api/marketing/publish",
            json=["not", "an", "object"],
            headers=_admin_headers(main),
        )

        assert resp.status_code == 400
        assert resp.json()["error_code"] == "invalid_request"
        assert calls == {}  # rejected before touching the toolchain

    def test_rejects_unsupported_platform(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        calls: dict = {}
        _patch_publish_deps(monkeypatch, main, calls)

        resp = client.post(
            "/api/marketing/publish",
            json={"platform": "facebook", "filename": "video.mp4"},
            headers=_admin_headers(main),
        )

        assert resp.status_code == 400
        assert resp.json()["error_code"] == "unsupported_platform"

    def test_platform_not_configured_returns_503(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        calls: dict = {}
        _patch_publish_deps(
            monkeypatch, main, calls, readiness={"configured": False, "publish_enabled": False}
        )

        resp = client.post(
            "/api/marketing/publish",
            json={"platform": "tiktok", "filename": "video.mp4"},
            headers=_admin_headers(main),
        )

        assert resp.status_code == 503
        assert resp.json()["error_code"] == "platform_not_configured"

    def test_publishing_disabled_returns_503(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        calls: dict = {}
        _patch_publish_deps(
            monkeypatch, main, calls, readiness={"configured": True, "publish_enabled": False}
        )

        resp = client.post(
            "/api/marketing/publish",
            json={"platform": "instagram_reels", "filename": "video.mp4"},
            headers=_admin_headers(main),
        )

        assert resp.status_code == 503
        assert resp.json()["error_code"] == "publishing_disabled"

    def test_missing_video_rejected(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        calls: dict = {}
        _patch_publish_deps(monkeypatch, main, calls)

        resp = client.post(
            "/api/marketing/publish",
            json={"platform": "tiktok", "caption": "hi"},
            headers=_admin_headers(main),
        )

        assert resp.status_code == 400
        assert resp.json()["error_code"] == "missing_video"

    def test_invalid_caption_or_hashtags_rejected(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        calls: dict = {}
        _patch_publish_deps(monkeypatch, main, calls)

        resp = client.post(
            "/api/marketing/publish",
            json={"platform": "tiktok", "filename": "v.mp4", "caption": 7, "hashtags": ["ok"]},
            headers=_admin_headers(main),
        )

        assert resp.status_code == 400
        assert resp.json()["error_code"] == "invalid_content"

    def test_unknown_local_video_returns_404(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        calls: dict = {}
        _patch_publish_deps(
            monkeypatch, main, calls, asset={"success": False, "error_code": "local_file_not_found"}
        )

        resp = client.post(
            "/api/marketing/publish",
            json={"platform": "tiktok", "filename": "ghost.mp4"},
            headers=_admin_headers(main),
        )

        assert resp.status_code == 404
        assert resp.json()["error_code"] == "video_not_found"

    def test_asset_preparation_failure_returns_503(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        calls: dict = {}
        _patch_publish_deps(
            monkeypatch, main, calls, asset={"success": False, "error_code": "gcs_unreachable"}
        )

        resp = client.post(
            "/api/marketing/publish",
            json={"platform": "tiktok", "filename": "video.mp4"},
            headers=_admin_headers(main),
        )

        assert resp.status_code == 503
        assert resp.json()["error_code"] == "asset_publish_failed"

    def test_social_platform_failure_returns_502(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        calls: dict = {}
        _patch_publish_deps(
            monkeypatch, main, calls, post={"success": False, "error": "token expired"}
        )

        resp = client.post(
            "/api/marketing/publish",
            json={"platform": "tiktok", "filename": "video.mp4"},
            headers=_admin_headers(main),
        )

        assert resp.status_code == 502
        assert resp.json()["error_code"] == "social_publish_failed"

    def test_invalid_json_body_returns_400(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        calls: dict = {}
        _patch_publish_deps(monkeypatch, main, calls)

        resp = client.post(
            "/api/marketing/publish",
            content="{not json",
            headers={**_admin_headers(main), "Content-Type": "application/json"},
        )

        assert resp.status_code == 400
        assert resp.json()["error_code"] == "invalid_json"

    def test_unexpected_error_returns_500(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)

        def _boom(**kwargs):
            raise RuntimeError("unforeseen")

        monkeypatch.setattr(main, "social_publish_readiness", _boom)
        monkeypatch.setattr(main, "publish_video_asset", lambda filename: {"success": True})
        monkeypatch.setattr(main, "publish_social_post", lambda **kwargs: {"success": True})

        resp = client.post(
            "/api/marketing/publish",
            json={"platform": "tiktok", "filename": "video.mp4"},
            headers=_admin_headers(main),
        )

        assert resp.status_code == 500
        assert resp.json()["error_code"] == "publish_failed"


# ─── GET /api/v1/customer/deal/{deal_id}/download/{note_id} ───────────────


def _seed_secure_deal(db) -> None:
    db.collections["deals"]["deal-1"] = {
        "id": "deal-1",
        "status": "funded",
        "buyer_first_name": "Bob",
        "buyer_last_name": "Buyer",
        "buyer_phone": "512-555-6789",
    }


def _seed_note(db, note_id: str, **fields) -> None:
    db.collections.setdefault("deal_notes", {})[note_id] = {"id": note_id, **fields}


def _stub_google_storage(monkeypatch) -> dict:
    """Replace google.cloud.storage with an in-memory signed-URL minter."""
    calls: dict = {"signed": []}
    storage_module = types.ModuleType("google.cloud.storage")

    class _Blob:
        def __init__(self, bucket_name, blob_name):
            self.bucket_name = bucket_name
            self.blob_name = blob_name

        def generate_signed_url(self, version=None, expiration=None, method=None):
            calls["signed"].append(
                {"version": version, "expiration": expiration, "method": method}
            )
            return f"https://storage.example.com/{self.bucket_name}/{self.blob_name}?sig=xyz"

    class _Bucket:
        def __init__(self, name):
            self.name = name

        def blob(self, blob_name):
            return _Bucket.last_blob_cls(self.name, blob_name)

    # Allow tests to swap in a raising blob class.
    _Bucket.last_blob_cls = _Blob

    class _Client:
        def bucket(self, name):
            return _Bucket(name)

    storage_module.Client = _Client
    storage_module._Bucket = _Bucket
    monkeypatch.setitem(sys.modules, "google.cloud.storage", storage_module)
    import google.cloud

    monkeypatch.setattr(google.cloud, "storage", storage_module, raising=False)
    return calls


class TestDownloadSecureDocument:
    def test_signed_url_happy_path(self, monkeypatch):
        client, _main, db, _logger = create_client(monkeypatch)
        _seed_secure_deal(db)
        _seed_note(db, "note-1", deal_id="deal-1", gcs_path="gs://tho-docs/closing/packet.pdf")
        calls = _stub_google_storage(monkeypatch)

        resp = client.get(
            "/api/v1/customer/deal/deal-1/download/note-1",
            params={"phone": "6789"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["url"].startswith("https://storage.example.com/tho-docs/closing/packet.pdf")
        assert len(calls["signed"]) == 1
        assert calls["signed"][0]["version"] == "v4"
        assert calls["signed"][0]["method"] == "GET"
        assert calls["signed"][0]["expiration"].total_seconds() == 15 * 60

    def test_wrong_phone_rejected_before_minting(self, monkeypatch):
        client, _main, db, _logger = create_client(monkeypatch)
        _seed_secure_deal(db)
        _seed_note(db, "note-1", deal_id="deal-1", gcs_path="gs://tho-docs/packet.pdf")
        calls = _stub_google_storage(monkeypatch)

        resp = client.get(
            "/api/v1/customer/deal/deal-1/download/note-1",
            params={"phone": "0000"},
        )

        assert resp.status_code == 403
        assert "Verification failed" in resp.json()["detail"]
        assert calls["signed"] == []

    def test_missing_deal_rejected(self, monkeypatch):
        client, _main, _db, _logger = create_client(monkeypatch)
        _stub_google_storage(monkeypatch)

        resp = client.get(
            "/api/v1/customer/deal/deal-999/download/note-1",
            params={"phone": "6789"},
        )

        assert resp.status_code == 403
        assert "Verification failed" in resp.json()["detail"]

    def test_missing_note_returns_404(self, monkeypatch):
        client, _main, db, _logger = create_client(monkeypatch)
        _seed_secure_deal(db)
        _stub_google_storage(monkeypatch)

        resp = client.get(
            "/api/v1/customer/deal/deal-1/download/note-999",
            params={"phone": "6789"},
        )

        assert resp.status_code == 404
        assert resp.json()["detail"] == "Document not found"

    def test_note_for_another_deal_returns_404(self, monkeypatch):
        client, _main, db, _logger = create_client(monkeypatch)
        _seed_secure_deal(db)
        db.collections["deals"]["deal-2"] = {"id": "deal-2", "buyer_phone": "512-555-6789"}
        # note belongs to deal-2 but is requested via deal-1
        _seed_note(db, "note-x", deal_id="deal-2", gcs_path="gs://tho-docs/packet.pdf")
        _stub_google_storage(monkeypatch)

        resp = client.get(
            "/api/v1/customer/deal/deal-1/download/note-x",
            params={"phone": "6789"},
        )

        assert resp.status_code == 404
        assert resp.json()["detail"] == "Document not found"

    def test_note_without_gcs_path_returns_400(self, monkeypatch):
        client, _main, db, _logger = create_client(monkeypatch)
        _seed_secure_deal(db)
        _seed_note(db, "note-1", deal_id="deal-1")  # no gcs_path / gcs_uri at all
        _stub_google_storage(monkeypatch)

        resp = client.get(
            "/api/v1/customer/deal/deal-1/download/note-1",
            params={"phone": "6789"},
        )

        assert resp.status_code == 400
        assert "storage location unknown" in resp.json()["detail"]

    def test_non_gs_uri_returns_400(self, monkeypatch):
        client, _main, db, _logger = create_client(monkeypatch)
        _seed_secure_deal(db)
        _seed_note(db, "note-1", deal_id="deal-1", gcs_uri="https://example.com/not-gs.pdf")
        _stub_google_storage(monkeypatch)

        resp = client.get(
            "/api/v1/customer/deal/deal-1/download/note-1",
            params={"phone": "6789"},
        )

        assert resp.status_code == 400
        assert "storage location unknown" in resp.json()["detail"]

    def test_signed_url_failure_returns_500(self, monkeypatch):
        client, _main, db, _logger = create_client(monkeypatch)
        _seed_secure_deal(db)
        _seed_note(db, "note-1", deal_id="deal-1", gcs_path="gs://tho-docs/packet.pdf")
        _stub_google_storage(monkeypatch)

        class _ExplodingBlob:
            def __init__(self, bucket_name, blob_name):
                pass

            def generate_signed_url(self, version=None, expiration=None, method=None):
                raise RuntimeError("signing credentials missing")

        sys.modules["google.cloud.storage"]._Bucket.last_blob_cls = _ExplodingBlob

        resp = client.get(
            "/api/v1/customer/deal/deal-1/download/note-1",
            params={"phone": "6789"},
        )

        assert resp.status_code == 500
        assert "signing credentials missing" in resp.json()["detail"]
