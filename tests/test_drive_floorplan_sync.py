"""Unit tests for tools/drive_floorplan_sync.py."""

import base64
import hashlib
from unittest.mock import MagicMock

import pytest

import tools.drive_floorplan_sync as sync


def _patch_children(monkeypatch, children_by_parent):
    def fake_list_children(_svc, parent_id):
        yield from children_by_parent.get(parent_id, [])

    monkeypatch.setattr(sync, "_list_children", fake_list_children)


def test_walk_skips_root_level_floorplan_file(monkeypatch):
    _patch_children(
        monkeypatch,
        {
            "root": [
                {
                    "id": "loose-pdf",
                    "name": "Loose Floorplan.pdf",
                    "mimeType": "application/pdf",
                    "size": "123",
                }
            ]
        },
    )

    assert list(sync.walk_tho_folder(object(), "root")) == []


def test_walk_descends_only_allowed_root_manufacturer_folders(monkeypatch):
    _patch_children(
        monkeypatch,
        {
            "root": [
                {
                    "id": "cavco-folder",
                    "name": "Cavco",
                    "mimeType": "application/vnd.google-apps.folder",
                },
                {
                    "id": "mark-folder",
                    "name": "Mark",
                    "mimeType": "application/vnd.google-apps.folder",
                },
            ],
            "cavco-folder": [
                {
                    "id": "plan-1",
                    "name": "../Plan A.pdf",
                    "mimeType": "application/pdf",
                    "size": "456",
                }
            ],
            "mark-folder": [
                {
                    "id": "customer-file",
                    "name": "Customer.pdf",
                    "mimeType": "application/pdf",
                    "size": "999",
                }
            ],
        },
    )

    files = list(sync.walk_tho_folder(object(), "root"))

    assert len(files) == 1
    assert files[0].manufacturer_key == "cavco"
    assert files[0].safe_local_name == "Plan_A.pdf"


# ---------------------------------------------------------------------------
# Idempotency: upload_to_gcs skips re-uploads when md5 matches
# ---------------------------------------------------------------------------


def _make_drive_file(name="Plan A.pdf"):
    return sync.DriveFile(
        drive_id="drive-1",
        name=name,
        mime_type="application/pdf",
        parent_path=("Cavco",),
        size_bytes=4,
    )


def test_upload_to_gcs_skips_when_md5_matches(monkeypatch, tmp_path):
    file = _make_drive_file()
    monkeypatch.setattr(sync, "LOCAL_CACHE", tmp_path)
    target = tmp_path / "cavco" / "Plan_A.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = b"data"
    target.write_bytes(payload)

    md5_b64 = base64.b64encode(hashlib.md5(payload, usedforsecurity=False).digest()).decode()

    blob = MagicMock()
    blob.exists.return_value = True
    blob.md5_hash = md5_b64
    bucket = MagicMock()
    bucket.name = "tho-secure-documents"
    bucket.blob.return_value = blob

    uri, uploaded = sync.upload_to_gcs(bucket, file)

    assert uploaded is False
    assert uri.endswith("/floorplans/cavco/Plan_A.pdf")
    blob.upload_from_filename.assert_not_called()


def test_upload_to_gcs_uploads_when_md5_differs(monkeypatch, tmp_path):
    file = _make_drive_file()
    monkeypatch.setattr(sync, "LOCAL_CACHE", tmp_path)
    target = tmp_path / "cavco" / "Plan_A.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"new-payload")

    blob = MagicMock()
    blob.exists.return_value = True
    blob.md5_hash = base64.b64encode(b"x" * 16).decode()  # mismatched
    bucket = MagicMock()
    bucket.name = "tho-secure-documents"
    bucket.blob.return_value = blob

    _uri, uploaded = sync.upload_to_gcs(bucket, file)

    assert uploaded is True
    blob.upload_from_filename.assert_called_once()


def test_upload_to_gcs_uploads_when_blob_missing(monkeypatch, tmp_path):
    file = _make_drive_file()
    monkeypatch.setattr(sync, "LOCAL_CACHE", tmp_path)
    target = tmp_path / "cavco" / "Plan_A.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"data")

    blob = MagicMock()
    blob.exists.return_value = False
    bucket = MagicMock()
    bucket.name = "tho-secure-documents"
    bucket.blob.return_value = blob

    _uri, uploaded = sync.upload_to_gcs(bucket, file)

    assert uploaded is True
    blob.upload_from_filename.assert_called_once()


# ---------------------------------------------------------------------------
# run_scheduled
# ---------------------------------------------------------------------------


def test_run_scheduled_requires_drive_folder_id(monkeypatch):
    monkeypatch.delenv("DRIVE_FOLDER_ID", raising=False)
    with pytest.raises(SystemExit, match="DRIVE_FOLDER_ID"):
        sync.run_scheduled()


def test_run_scheduled_returns_stats_dict(monkeypatch, tmp_path):
    """Mocks Drive walk + GCS bucket; verifies the stats dict shape."""
    monkeypatch.setenv("DRIVE_FOLDER_ID", "fake-folder-id")
    monkeypatch.setenv("GCS_DOCUMENTS_BUCKET", "tho-secure-documents")
    monkeypatch.setenv("DRIVE_SYNC_DRY_RUN", "1")  # stay headless — no GCS client needed

    monkeypatch.setattr(sync, "_drive_service", lambda: object())

    fake_file = sync.DriveFile(
        drive_id="d1",
        name="Plan.pdf",
        mime_type="application/pdf",
        parent_path=("Cavco",),
        size_bytes=100,
    )
    monkeypatch.setattr(sync, "walk_tho_folder", lambda _svc, _fid: iter([fake_file]))

    stats = sync.run_scheduled()

    assert stats["folder_id"] == "fake-folder-id"
    assert stats["gcs_bucket"] == "tho-secure-documents"
    assert stats["files_seen"] == 1
    assert stats["folders_walked"] == 1
    assert stats["bytes_total"] == 100
    assert stats["files_downloaded"] == 0  # dry-run
    assert stats["files_uploaded"] == 0
    assert stats["errors"] == []
    assert stats["dry_run"] is True
    assert stats["manufacturer_counts"] == {"cavco": 1}


def test_run_scheduled_captures_per_file_errors(monkeypatch):
    """download/upload failures are captured in stats['errors'] and don't crash."""
    monkeypatch.setenv("DRIVE_FOLDER_ID", "fake-folder-id")
    monkeypatch.setenv("DRIVE_SYNC_DRY_RUN", "0")

    monkeypatch.setattr(sync, "_drive_service", lambda: object())

    fake_file = sync.DriveFile(
        drive_id="d1",
        name="Plan.pdf",
        mime_type="application/pdf",
        parent_path=("Cavco",),
        size_bytes=100,
    )
    monkeypatch.setattr(sync, "walk_tho_folder", lambda _svc, _fid: iter([fake_file]))

    def boom(*_args, **_kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(sync, "download_to_cache", boom)

    # Stub out the GCS storage import so we never hit real cloud APIs.
    fake_storage = MagicMock()
    fake_storage.Client.return_value.bucket.return_value = MagicMock(name="bucket")
    monkeypatch.setitem(__import__("sys").modules, "google.cloud.storage", fake_storage)

    stats = sync.run_scheduled()

    assert stats["files_seen"] == 1
    assert stats["files_downloaded"] == 0
    assert len(stats["errors"]) == 1
    assert stats["errors"][0]["stage"] == "download"
    assert "RuntimeError" in stats["errors"][0]["error"]
