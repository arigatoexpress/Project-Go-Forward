"""Coverage swarm c11-a: tools/drive_service.py + tools/drive_floorplan_sync.py.

All Google seams are faked in-process (no network):

* ``tools.drive_service`` resolves ``google.auth`` / ``build`` /
  ``MediaFileUpload`` as module globals -> patched on the module object.
* ``tools.drive_floorplan_sync`` imports ``google.auth`` /
  ``googleapiclient.discovery.build`` / ``googleapiclient.http`` /
  ``google.cloud.storage`` *inside functions*, so those are patched on the
  already-imported real packages (attribute resolution wins over re-import)
  plus ``sys.modules`` / parent-package attrs for the GCS from-import.
* ``main()`` is exercised via ``runpy.run_path(..., run_name="__main__")``
  with a fake Drive service, ``monkeypatch.chdir(tmp_path)`` so the relative
  ``data/floorplans`` cache lands outside the repo, and faked
  ``sys.argv``.
"""

from __future__ import annotations

import logging
import re
import runpy
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import tools.drive_floorplan_sync as sync
import tools.drive_service as drive

SCRIPT = Path(sync.__file__)


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


class _FakeListRequest:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _FakeDriveSvc:
    """Minimal googleapiclient Drive v3 service double (no pagination)."""

    def __init__(self, children_by_parent: dict[str, list[dict]]):
        self._children = children_by_parent

    def files(self):
        children = self._children

        class _Files:
            def list(self, q=None, **_kw):
                parent = re.search(r"'([^']+)' in parents", q).group(1)
                return _FakeListRequest({"files": children.get(parent, [])})

            def get_media(self, fileId=None):
                return _FakeListRequest(b"")

        return _Files()


class _FakeDownloader:
    """Writes a fixed payload on the first chunk, then reports done."""

    def __init__(self, buf, _request):
        self._buf = buf

    def next_chunk(self):
        self._buf.write(b"%PDF-fake")
        return None, True


class _BoomDownloader:
    def __init__(self, _buf, _request):
        pass

    def next_chunk(self):
        raise RuntimeError("media download failed")


def _patch_google_auth_chain(monkeypatch, svc):
    """Fake ADC + discovery.build for both drive modules (in-function imports)."""
    import google.auth
    import googleapiclient.discovery

    monkeypatch.setattr(google.auth, "default", lambda scopes=None: (MagicMock(), "proj"))
    monkeypatch.setattr(googleapiclient.discovery, "build", lambda *a, **k: svc)


def _patch_gcs_client(monkeypatch, bucket):
    """Fake google.cloud.storage.Client (sys.modules + parent package attr)."""
    import google.cloud

    client = MagicMock()
    client.bucket.return_value = bucket
    fake_storage = types.ModuleType("google.cloud.storage")
    fake_storage.Client = lambda *a, **k: client
    monkeypatch.setitem(sys.modules, "google.cloud.storage", fake_storage)
    monkeypatch.setattr(google.cloud, "storage", fake_storage, raising=False)
    return client


def _drive_file(name="Plan A.pdf", size_bytes=9):
    return sync.DriveFile(
        drive_id="drive-1",
        name=name,
        mime_type="application/pdf",
        parent_path=("Cavco",),
        size_bytes=size_bytes,
    )


def _write_cached(file: sync.DriveFile, payload: bytes, monkeypatch, tmp_path):
    monkeypatch.setattr(sync, "LOCAL_CACHE", tmp_path)
    target = tmp_path / "cavco" / file.safe_local_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


# ---------------------------------------------------------------------------
# tools/drive_service.py
# ---------------------------------------------------------------------------


def test_get_drive_service_builds_lazy_service(monkeypatch):
    svc = MagicMock()
    monkeypatch.setattr(drive.google.auth, "default", lambda scopes=None: (MagicMock(), "proj"))
    monkeypatch.setattr(drive, "build", lambda *a, **k: svc)
    assert drive.get_drive_service() is svc


def test_get_drive_service_returns_none_when_adc_fails(monkeypatch):
    def _boom(scopes=None):
        raise RuntimeError("no application default credentials")

    monkeypatch.setattr(drive.google.auth, "default", _boom)
    assert drive.get_drive_service() is None


@pytest.mark.parametrize("flag", ["1", "true", "yes"])
def test_upload_to_drive_kill_switch_skips_everything(monkeypatch, flag):
    monkeypatch.setenv("THO_DISABLE_DRIVE_MIRRORING", flag)
    get_service = MagicMock()
    monkeypatch.setattr(drive, "get_drive_service", get_service)
    assert drive.upload_to_drive("/nonexistent/doc.pdf", "doc.pdf") is None
    get_service.assert_not_called()


@pytest.mark.parametrize(
    ("filename", "expected_mime"),
    [
        ("doc.pdf", "application/pdf"),
        ("photo.jpg", "image/jpeg"),
        ("photo.jpeg", "image/jpeg"),
        ("photo.png", "image/png"),
    ],
)
def test_upload_to_drive_success_mimetypes(monkeypatch, tmp_path, filename, expected_mime):
    monkeypatch.delenv("THO_DISABLE_DRIVE_MIRRORING", raising=False)
    svc = MagicMock()
    svc.files.return_value.create.return_value.execute.return_value = {
        "id": "file-1",
        "webViewLink": "https://drive.example/view/file-1",
    }
    monkeypatch.setattr(drive, "get_drive_service", lambda: svc)
    local = tmp_path / filename
    local.write_bytes(b"%PDF-payload")

    link = drive.upload_to_drive(str(local), filename, folder_id="fold-1")

    assert link == "https://drive.example/view/file-1"
    create_kwargs = svc.files.return_value.create.call_args.kwargs
    assert create_kwargs["body"] == {"name": filename, "parents": ["fold-1"]}
    assert create_kwargs["fields"] == "id, webViewLink"
    assert create_kwargs["media_body"].mimetype() == expected_mime


def test_upload_to_drive_success_without_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("THO_DISABLE_DRIVE_MIRRORING", raising=False)
    svc = MagicMock()
    svc.files.return_value.create.return_value.execute.return_value = {"id": "file-2"}
    monkeypatch.setattr(drive, "get_drive_service", lambda: svc)
    local = tmp_path / "doc.pdf"
    local.write_bytes(b"%PDF")

    assert drive.upload_to_drive(str(local), "doc.pdf") is None  # no webViewLink key
    assert svc.files.return_value.create.call_args.kwargs["body"] == {"name": "doc.pdf"}


def test_upload_to_drive_returns_none_without_service(monkeypatch, tmp_path):
    monkeypatch.delenv("THO_DISABLE_DRIVE_MIRRORING", raising=False)
    monkeypatch.setattr(drive, "get_drive_service", lambda: None)
    local = tmp_path / "doc.pdf"
    local.write_bytes(b"%PDF")
    assert drive.upload_to_drive(str(local), "doc.pdf") is None


def test_upload_to_drive_api_error_returns_none(monkeypatch, tmp_path):
    monkeypatch.delenv("THO_DISABLE_DRIVE_MIRRORING", raising=False)
    svc = MagicMock()
    svc.files.return_value.create.return_value.execute.side_effect = RuntimeError("api down")
    monkeypatch.setattr(drive, "get_drive_service", lambda: svc)
    local = tmp_path / "doc.pdf"
    local.write_bytes(b"%PDF")
    assert drive.upload_to_drive(str(local), "doc.pdf") is None


def test_ensure_deal_folder_requires_parent(monkeypatch):
    monkeypatch.setattr(drive, "DRIVE_RECORDS_ROOT_ID", None)
    assert drive.ensure_deal_folder("deal-1") is None


def test_ensure_deal_folder_returns_existing(monkeypatch):
    svc = MagicMock()
    svc.files.return_value.list.return_value.execute.return_value = {"files": [{"id": "fold-x"}]}
    monkeypatch.setattr(drive, "DRIVE_RECORDS_ROOT_ID", "root-1")
    monkeypatch.setattr(drive, "get_drive_service", lambda: svc)

    assert drive.ensure_deal_folder("deal-1") == "fold-x"
    query = svc.files.return_value.list.call_args.kwargs["q"]
    assert "name = 'deal-1'" in query
    assert "'root-1' in parents" in query


def test_ensure_deal_folder_creates_when_missing(monkeypatch):
    svc = MagicMock()
    svc.files.return_value.list.return_value.execute.return_value = {"files": []}
    svc.files.return_value.create.return_value.execute.return_value = {"id": "new-fold"}
    monkeypatch.setattr(drive, "get_drive_service", lambda: svc)

    assert drive.ensure_deal_folder("deal-2", parent_folder_id="root-2") == "new-fold"
    body = svc.files.return_value.create.call_args.kwargs["body"]
    assert body == {
        "name": "deal-2",
        "mimeType": "application/vnd.google-apps.folder",
        "parents": ["root-2"],
    }


def test_ensure_deal_folder_returns_none_without_service(monkeypatch):
    monkeypatch.setattr(drive, "DRIVE_RECORDS_ROOT_ID", "root-1")
    monkeypatch.setattr(drive, "get_drive_service", lambda: None)
    assert drive.ensure_deal_folder("deal-1") is None


def test_ensure_deal_folder_api_error_returns_none(monkeypatch):
    svc = MagicMock()
    svc.files.return_value.list.return_value.execute.side_effect = RuntimeError("boom")
    monkeypatch.setattr(drive, "DRIVE_RECORDS_ROOT_ID", "root-1")
    monkeypatch.setattr(drive, "get_drive_service", lambda: svc)
    assert drive.ensure_deal_folder("deal-1") is None


# ---------------------------------------------------------------------------
# tools/drive_floorplan_sync.py — Drive walking / download
# ---------------------------------------------------------------------------


def test_drive_service_constructs_with_readonly_scope(monkeypatch):
    import google.auth
    import googleapiclient.discovery

    svc = MagicMock()
    seen = {}

    def fake_default(scopes=None):
        seen["scopes"] = scopes
        return MagicMock(), "proj"

    monkeypatch.setattr(google.auth, "default", fake_default)
    monkeypatch.setattr(googleapiclient.discovery, "build", lambda *a, **k: svc)

    assert sync._drive_service() is svc
    assert seen["scopes"] == ["https://www.googleapis.com/auth/drive.readonly"]


def test_list_children_paginates(monkeypatch):
    svc = MagicMock()
    child_a = {"id": "a", "name": "A.pdf", "mimeType": "application/pdf"}
    child_b = {"id": "b", "name": "B.pdf", "mimeType": "application/pdf"}
    request = svc.files.return_value.list.return_value
    request.execute.side_effect = [
        {"files": [child_a], "nextPageToken": "tok-2"},
        {"files": [child_b]},
    ]

    assert list(sync._list_children(svc, "root-1")) == [child_a, child_b]

    calls = svc.files.return_value.list.call_args_list
    assert calls[0].kwargs["pageToken"] is None
    assert calls[1].kwargs["pageToken"] == "tok-2"
    assert calls[0].kwargs["pageSize"] == 100
    assert calls[0].kwargs["supportsAllDrives"] is True
    assert "'root-1' in parents" in calls[0].kwargs["q"]


def test_list_children_empty_folder_yields_nothing():
    svc = MagicMock()
    svc.files.return_value.list.return_value.execute.return_value = {"files": []}
    assert list(sync._list_children(svc, "empty")) == []


def test_manufacturer_key_root_file_raises():
    file = sync.DriveFile(
        drive_id="x",
        name="loose.pdf",
        mime_type="application/pdf",
        parent_path=(),
        size_bytes=1,
    )
    with pytest.raises(ValueError, match="no manufacturer"):
        _ = file.manufacturer_key


def test_manufacturer_key_unknown_folder_raises():
    file = sync.DriveFile(
        drive_id="x",
        name="plan.pdf",
        mime_type="application/pdf",
        parent_path=("Unknown Mfr",),
        size_bytes=1,
    )
    with pytest.raises(ValueError, match="allow-list"):
        _ = file.manufacturer_key


def test_walk_skips_non_manufacturer_folder_and_non_floorplan_mime(monkeypatch):
    def fake_list_children(_svc, parent_id):
        yield from {
            "root": [
                {"id": "misc", "name": "Misc", "mimeType": "application/vnd.google-apps.folder"},
                {"id": "cavco", "name": "Cavco", "mimeType": "application/vnd.google-apps.folder"},
            ],
            "misc": [],
            "cavco": [{"id": "n", "name": "notes.txt", "mimeType": "text/plain"}],
        }.get(parent_id, [])

    monkeypatch.setattr(sync, "_list_children", fake_list_children)
    assert list(sync.walk_tho_folder(object(), "root")) == []


def test_download_to_cache_fresh_download(monkeypatch, tmp_path):
    monkeypatch.setattr(sync, "LOCAL_CACHE", tmp_path)
    monkeypatch.setattr("googleapiclient.http.MediaIoBaseDownload", _FakeDownloader)
    file = _drive_file(size_bytes=9)
    svc = MagicMock()

    out = sync.download_to_cache(svc, file)

    assert out == tmp_path / "cavco" / "Plan_A.pdf"
    assert out.read_bytes() == b"%PDF-fake"
    svc.files.return_value.get_media.assert_called_once_with(fileId="drive-1")


def test_download_to_cache_hit_skips_download(monkeypatch, tmp_path):
    file = _drive_file(size_bytes=4)
    target = _write_cached(file, b"data", monkeypatch, tmp_path)

    def _boom(*_a, **_k):
        raise AssertionError("downloader must not be constructed on cache hit")

    monkeypatch.setattr("googleapiclient.http.MediaIoBaseDownload", _boom)

    out = sync.download_to_cache(MagicMock(), file)
    assert out == target
    assert target.read_bytes() == b"data"


def test_download_to_cache_size_mismatch_redownloads(monkeypatch, tmp_path):
    file = _drive_file(size_bytes=4)
    target = _write_cached(file, b"stale-content", monkeypatch, tmp_path)
    monkeypatch.setattr("googleapiclient.http.MediaIoBaseDownload", _FakeDownloader)

    out = sync.download_to_cache(MagicMock(), file)
    assert out == target
    assert out.read_bytes() == b"%PDF-fake"


# ---------------------------------------------------------------------------
# tools/drive_floorplan_sync.py — md5 helpers / GCS precheck
# ---------------------------------------------------------------------------


def test_decode_blob_md5_empty_returns_none():
    assert sync._decode_blob_md5(None) is None
    assert sync._decode_blob_md5("") is None


def test_decode_blob_md5_garbage_returns_none():
    assert sync._decode_blob_md5("!!!not-valid-base64!!!") is None


def test_upload_to_gcs_precheck_failure_still_uploads(monkeypatch, tmp_path):
    file = _drive_file()
    _write_cached(file, b"data", monkeypatch, tmp_path)
    blob = MagicMock()
    blob.exists.side_effect = RuntimeError("gcs metadata down")
    bucket = MagicMock()
    bucket.name = "tho-secure-documents"
    bucket.blob.return_value = blob

    uri, uploaded = sync.upload_to_gcs(bucket, file)

    assert uploaded is True
    assert uri == "gs://tho-secure-documents/floorplans/cavco/Plan_A.pdf"
    blob.upload_from_filename.assert_called_once()


# ---------------------------------------------------------------------------
# tools/drive_floorplan_sync.py — _run_sync
# ---------------------------------------------------------------------------


def _sync_fixture(monkeypatch, tmp_path, file):
    monkeypatch.setattr(sync, "LOCAL_CACHE", tmp_path)
    monkeypatch.setattr(sync, "_drive_service", lambda: MagicMock())
    monkeypatch.setattr(sync, "walk_tho_folder", lambda _s, _f: iter([file]))
    monkeypatch.setattr("googleapiclient.http.MediaIoBaseDownload", _FakeDownloader)


def test_run_sync_apply_uploads_new_blob(monkeypatch, tmp_path):
    file = _drive_file()
    _sync_fixture(monkeypatch, tmp_path, file)
    blob = MagicMock()
    blob.exists.return_value = False
    bucket = MagicMock()
    bucket.name = "tho-secure-documents"
    bucket.blob.return_value = blob
    _patch_gcs_client(monkeypatch, bucket)

    stats = sync._run_sync("folder-1", "tho-secure-documents", dry_run=False)

    assert stats["files_seen"] == 1
    assert stats["folders_walked"] == 1
    assert stats["files_downloaded"] == 1
    assert stats["files_uploaded"] == 1
    assert stats["files_skipped_md5"] == 0
    assert stats["bytes_total"] == 9
    assert stats["manufacturer_counts"] == {"cavco": 1}
    assert stats["errors"] == []
    assert stats["dry_run"] is False
    blob.upload_from_filename.assert_called_once()


def test_run_sync_apply_skips_upload_on_md5_match(monkeypatch, tmp_path):
    import base64
    import hashlib

    payload = b"%PDF-fake"
    file = _drive_file(size_bytes=len(payload))
    _sync_fixture(monkeypatch, tmp_path, file)
    blob = MagicMock()
    blob.exists.return_value = True
    blob.md5_hash = base64.b64encode(hashlib.md5(payload, usedforsecurity=False).digest()).decode()
    bucket = MagicMock()
    bucket.name = "tho-secure-documents"
    bucket.blob.return_value = blob
    _patch_gcs_client(monkeypatch, bucket)

    stats = sync._run_sync("folder-1", "tho-secure-documents", dry_run=False)

    assert stats["files_uploaded"] == 0
    assert stats["files_skipped_md5"] == 1
    assert stats["errors"] == []
    blob.upload_from_filename.assert_not_called()


def test_run_sync_captures_upload_errors(monkeypatch, tmp_path):
    file = _drive_file()
    _sync_fixture(monkeypatch, tmp_path, file)
    blob = MagicMock()
    blob.exists.return_value = False
    blob.upload_from_filename.side_effect = RuntimeError("gcs write failed")
    bucket = MagicMock()
    bucket.name = "tho-secure-documents"
    bucket.blob.return_value = blob
    _patch_gcs_client(monkeypatch, bucket)

    stats = sync._run_sync("folder-1", "tho-secure-documents", dry_run=False)

    assert stats["files_downloaded"] == 1
    assert stats["files_uploaded"] == 0
    assert len(stats["errors"]) == 1
    assert stats["errors"][0]["stage"] == "upload"
    assert stats["errors"][0]["file"] == file.gcs_object_key


def test_run_sync_apply_without_gcs_bucket_skips_upload(monkeypatch, tmp_path):
    file = _drive_file()
    _sync_fixture(monkeypatch, tmp_path, file)

    stats = sync._run_sync("folder-1", None, dry_run=False)

    assert stats["files_downloaded"] == 1
    assert stats["files_uploaded"] == 0
    assert stats["errors"] == []
    assert stats["gcs_bucket"] is None


# ---------------------------------------------------------------------------
# tools/drive_floorplan_sync.py — run_scheduled extras
# ---------------------------------------------------------------------------


def test_run_scheduled_configures_logging_when_root_has_no_handlers(monkeypatch):
    monkeypatch.setattr(logging.getLogger(), "handlers", [])
    monkeypatch.setenv("DRIVE_FOLDER_ID", "fake-folder-id")
    monkeypatch.setenv("DRIVE_SYNC_DRY_RUN", "1")
    monkeypatch.setattr(sync, "_drive_service", lambda: object())
    monkeypatch.setattr(sync, "walk_tho_folder", lambda _s, _f: iter([]))

    sync.run_scheduled()

    assert logging.getLogger().handlers  # basicConfig installed a handler


def test_run_scheduled_swallows_missing_sentry_sdk(monkeypatch):
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)  # forces ImportError
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
    monkeypatch.setattr(sync, "walk_tho_folder", lambda _s, _f: iter([fake_file]))

    def _boom(*_a, **_k):
        raise RuntimeError("network down")

    monkeypatch.setattr(sync, "download_to_cache", _boom)
    _patch_gcs_client(monkeypatch, MagicMock(name="bucket"))

    stats = sync.run_scheduled()

    assert len(stats["errors"]) == 1  # sentry absence must not mask sync errors


# ---------------------------------------------------------------------------
# tools/drive_floorplan_sync.py — CLI main() via runpy (__main__ in-process)
# ---------------------------------------------------------------------------

_CAVCO_TREE = {
    "root": [
        {"id": "cav", "name": "Cavco", "mimeType": "application/vnd.google-apps.folder"},
    ],
    "cav": [
        {"id": "p1", "name": "Plan.pdf", "mimeType": "application/pdf", "size": "2048"},
    ],
}


def _run_main(monkeypatch, tmp_path, argv):
    monkeypatch.chdir(tmp_path)  # keep data/floorplans out of the repo
    svc = _FakeDriveSvc(_CAVCO_TREE)
    _patch_google_auth_chain(monkeypatch, svc)
    monkeypatch.setattr(sys, "argv", ["drive_floorplan_sync.py", *argv])
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    return exc_info.value.code, svc


def test_main_dry_run_lists_files(monkeypatch, tmp_path, capsys):
    code, _svc = _run_main(monkeypatch, tmp_path, ["--folder-id", "root", "--dry-run"])

    assert code == 0
    out = capsys.readouterr().out
    assert "DRY" in out and "cavco" in out
    assert "Files seen: 1" in out
    assert "2.0 KB" not in out  # size printed raw: 2048 B
    assert "2048 B" in out


def test_main_apply_downloads_and_uploads(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("googleapiclient.http.MediaIoBaseDownload", _FakeDownloader)
    blob = MagicMock()
    blob.exists.return_value = False
    bucket = MagicMock()
    bucket.name = "tho-secure-documents"
    bucket.blob.return_value = blob
    _patch_gcs_client(monkeypatch, bucket)

    code, _svc = _run_main(monkeypatch, tmp_path, ["--folder-id", "root", "--apply"])

    assert code == 0
    out = capsys.readouterr().out
    assert "uploaded=1" in out
    assert "errors=0" in out
    blob.upload_from_filename.assert_called_once()
    cached = tmp_path / "data" / "floorplans" / "cavco" / "Plan.pdf"
    assert cached.read_bytes() == b"%PDF-fake"


def test_main_apply_exits_nonzero_on_errors(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("googleapiclient.http.MediaIoBaseDownload", _BoomDownloader)
    _patch_gcs_client(monkeypatch, MagicMock(name="bucket"))

    code, _svc = _run_main(monkeypatch, tmp_path, ["--folder-id", "root", "--apply"])

    assert code == 1
    out = capsys.readouterr().out
    assert "ERROR" in out
    assert "download" in out


def test_main_requires_dry_run_or_apply(monkeypatch, tmp_path):
    code, _svc = _run_main(monkeypatch, tmp_path, ["--folder-id", "root"])
    assert code == 2  # argparse parser.error
