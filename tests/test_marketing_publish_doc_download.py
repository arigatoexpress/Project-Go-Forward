"""Tests for the secure document download endpoint.

Target flagged by the Python-3.12 honest coverage re-measurement (see
docs/MAIN_COVERAGE_INVESTIGATION.md):

- ``GET /api/v1/customer/deal/{id}/download/{note}`` — signed URL minting for
  secure closing documents (phone-last-4 verified)

Note: ``POST /api/marketing/publish`` was removed from this file — main went
draft-only via PR #317 and the live-publish endpoint no longer exists. The
draft-only contract is pinned by tests/test_social_safety_surface.py.

``google.cloud.storage`` is stubbed in both ``sys.modules`` and as an attribute
of the already-imported ``google.cloud`` package (``from google.cloud import
storage`` resolves the package attribute first, so a sys.modules-only stub is
shadowed — same ordering trap as ``tools.image_storage``). Firestore is the
shared in-memory fake; audit helpers are no-op-patched (emulator absent;
covered elsewhere).

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
