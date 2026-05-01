"""
drive_floorplan_sync.py
=======================

Walk Mark Willcott's shared Google Drive folder "THO" and pull manufacturer
floorplans into ``data/floorplans/<manufacturer>/`` plus the
``tho-secure-documents`` GCS bucket under ``floorplans/``.

Usage
-----

Manual CLI invocation::

    python3 tools/drive_floorplan_sync.py --dry-run \\
        --folder-id 0BwMsFgQWT3QvWWoxbWMtQXJfR0U

    python3 tools/drive_floorplan_sync.py --apply \\
        --folder-id 0BwMsFgQWT3QvWWoxbWMtQXJfR0U \\
        --gcs-bucket tho-secure-documents

Headless / Cloud Run Job invocation::

    DRIVE_FOLDER_ID=0BwMsFgQWT3QvWWoxbWMtQXJfR0U \\
    GCS_DOCUMENTS_BUCKET=tho-secure-documents \\
    python3 -c "from tools.drive_floorplan_sync import run_scheduled; print(run_scheduled())"

See ``docs/integration/cloud-scheduler-drive-sync.md`` for the full Cloud Run
Job + Cloud Scheduler manifests.

Authentication
--------------

By default this module uses Application Default Credentials, so:

* Locally:    ``gcloud auth application-default login`` (use the account that
              has read access to the shared "THO" folder — i.e. aribspector).
* On Cloud Run: rely on the runtime service account; grant it Drive read scope
              via Workspace domain-wide delegation OR use a dedicated service
              account that the human admin manually shared the THO folder with.

PII Posture
-----------

* Only manufacturer-named subfolders are walked (allow-list below).
* People-named subfolders (Adriana, Lee, Mario, Mark, Rox, Sergio, Celeste,
  Ady) are explicitly skipped — they contain customer files.
* No file content is parsed for sensitive data here; the caller is responsible
  for not piping these PDFs into any LLM context.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import io
import logging
import os
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from google.cloud import storage


def _missing_google_libs(exc: ImportError) -> SystemExit:
    raise SystemExit(
        "Missing Google client libs. Run: pip install "
        "google-api-python-client google-auth google-cloud-storage"
    ) from exc


LOG = logging.getLogger("drive_floorplan_sync")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: Allow-list of manufacturer-named subfolders. Add new entries here when
#: Mark confirms a new partner. Anything NOT in this list is skipped.
MANUFACTURER_ALLOWLIST: dict[str, str] = {
    # Drive folder name (exact, case-insensitive)  →  canonical manufacturer key
    "cavco": "cavco",
    "champion louisiana": "champion_la",
    "clayton ebuilt information": "clayton_ebuilt",
    "new vision new retailer": "new_vision",
    "skyline from kansas": "skyline_ks",
    "skyline louisiana": "skyline_la",
    "trumh retail partner": "trumh",
    # Add more as Mark confirms; trailing/leading whitespace is tolerated.
}

#: Subfolder names we explicitly refuse to walk — these are people/operations
#: folders that contain customer files and PII.
PEOPLE_FOLDER_DENYLIST: set[str] = {
    "adriana",
    "lee",
    "mario",
    "mark",
    "rox",
    "sergio",
    "celeste",
    "ady",
}

#: File MIME types we consider "floorplan-bearing".
FLOORPLAN_MIME_TYPES: set[str] = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/heic",
}

#: Local cache root (relative to repo root).
LOCAL_CACHE = Path("data/floorplans")

#: GCS object key prefix.
GCS_PREFIX = "floorplans/"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DriveFile:
    """One file inside Mark's shared THO folder."""

    drive_id: str
    name: str
    mime_type: str
    parent_path: tuple[str, ...]  # path of parent-folder names from the root
    size_bytes: int | None

    @property
    def manufacturer_key(self) -> str:
        """First-level subfolder maps to a manufacturer key."""
        if not self.parent_path:
            raise ValueError(f"{self.name!r} is at the THO root, no manufacturer")
        first = self.parent_path[0].strip().lower()
        try:
            return MANUFACTURER_ALLOWLIST[first]
        except KeyError as exc:
            raise ValueError(f"Folder {first!r} is not in the manufacturer allow-list") from exc

    @property
    def safe_local_name(self) -> str:
        """Sanitized filename for the local cache (no path traversal)."""
        clean = re.sub(r"[^A-Za-z0-9._-]+", "_", self.name).strip("._-")
        return clean or self.drive_id

    @property
    def local_path(self) -> Path:
        return LOCAL_CACHE / self.manufacturer_key / self.safe_local_name

    @property
    def gcs_object_key(self) -> str:
        return f"{GCS_PREFIX}{self.manufacturer_key}/{self.safe_local_name}"


# ---------------------------------------------------------------------------
# Drive walking
# ---------------------------------------------------------------------------


def _drive_service():
    try:
        import google.auth
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover - runtime environment only
        raise _missing_google_libs(exc) from exc

    creds, _project = google.auth.default(scopes=["https://www.googleapis.com/auth/drive.readonly"])
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _list_children(svc, parent_id: str) -> Iterator[dict]:
    """Yield all immediate children of a Drive folder."""
    page_token = None
    query = f"'{parent_id}' in parents and trashed = false"
    while True:
        resp = (
            svc.files()
            .list(
                q=query,
                spaces="drive",
                fields=("nextPageToken," "files(id,name,mimeType,size,parents)"),
                pageToken=page_token,
                pageSize=100,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        yield from resp.get("files", [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break


def walk_tho_folder(svc, root_folder_id: str, _path: tuple[str, ...] = ()) -> Iterator[DriveFile]:
    """Recursively yield manufacturer-floorplan files. Honors deny/allow lists."""
    for child in _list_children(svc, root_folder_id):
        name = child["name"]
        mime = child["mimeType"]
        is_folder = mime == "application/vnd.google-apps.folder"
        new_path = _path + (name,)
        first_segment = new_path[0].strip().lower()

        if is_folder:
            # At root level, gate on allow/deny lists.
            if not _path:
                if first_segment in PEOPLE_FOLDER_DENYLIST:
                    LOG.info("Skipping people-folder %r", name)
                    continue
                if first_segment not in MANUFACTURER_ALLOWLIST:
                    LOG.info("Skipping non-manufacturer folder %r", name)
                    continue
            # Below root: descend regardless (manufacturer subfolders may have
            # nested year/line subfolders).
            yield from walk_tho_folder(svc, child["id"], new_path)
            continue

        if not _path:
            LOG.info("Skipping root-level file %r", name)
            continue

        # Leaf file — only floorplan-bearing types.
        if mime not in FLOORPLAN_MIME_TYPES:
            LOG.debug("Skipping non-floorplan mime %s for %s", mime, name)
            continue

        size = int(child["size"]) if child.get("size") else None
        yield DriveFile(
            drive_id=child["id"],
            name=name,
            mime_type=mime,
            parent_path=_path,
            size_bytes=size,
        )


# ---------------------------------------------------------------------------
# Download / upload
# ---------------------------------------------------------------------------


def download_to_cache(svc, file: DriveFile) -> Path:
    try:
        from googleapiclient.http import MediaIoBaseDownload
    except ImportError as exc:  # pragma: no cover - runtime environment only
        raise _missing_google_libs(exc) from exc

    file.local_path.parent.mkdir(parents=True, exist_ok=True)
    if (
        file.local_path.exists()
        and file.size_bytes
        and file.local_path.stat().st_size == file.size_bytes
    ):
        LOG.debug("Cache hit %s", file.local_path)
        return file.local_path

    LOG.info("Downloading %s (%s)", file.name, file.manufacturer_key)
    request = svc.files().get_media(fileId=file.drive_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _status, done = downloader.next_chunk()
    file.local_path.write_bytes(buf.getvalue())
    return file.local_path


def _local_md5_hex(path: Path) -> str:
    """Compute the MD5 of a local file as a lowercase hex string."""
    h = hashlib.md5(usedforsecurity=False)  # noqa: S324 - matches GCS blob.md5_hash
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _decode_blob_md5(blob_md5: str | None) -> str | None:
    """Convert ``blob.md5_hash`` (base64) to lowercase hex, or None on failure.

    GCS exposes the MD5 of an uploaded object as a base64-encoded string.
    Returning ``None`` means we cannot compare reliably and should re-upload.
    """
    if not blob_md5:
        return None
    try:
        return binascii.hexlify(base64.b64decode(blob_md5)).decode("ascii").lower()
    except (binascii.Error, ValueError):
        return None


def upload_to_gcs(bucket: storage.Bucket, file: DriveFile) -> tuple[str, bool]:
    """Upload ``file`` to GCS if its md5 differs from the existing blob.

    Returns ``(gs_uri, uploaded)`` where ``uploaded`` is False when the blob
    already exists in GCS with a matching md5 (idempotent re-run).
    """
    blob = bucket.blob(file.gcs_object_key)
    local_md5 = _local_md5_hex(file.local_path)
    try:
        if blob.exists():
            blob.reload()  # populate md5_hash
            remote_md5 = _decode_blob_md5(getattr(blob, "md5_hash", None))
            if remote_md5 and remote_md5 == local_md5:
                LOG.debug(
                    "Skipping gs://%s/%s — md5 match (%s)",
                    bucket.name,
                    file.gcs_object_key,
                    local_md5,
                )
                return f"gs://{bucket.name}/{file.gcs_object_key}", False
    except Exception:  # noqa: BLE001
        # Fall through to upload — better to re-upload than silently skip.
        LOG.exception("md5 precheck failed for gs://%s/%s", bucket.name, file.gcs_object_key)

    blob.upload_from_filename(str(file.local_path), content_type=file.mime_type)
    LOG.info("Uploaded gs://%s/%s", bucket.name, file.gcs_object_key)
    return f"gs://{bucket.name}/{file.gcs_object_key}", True


# ---------------------------------------------------------------------------
# Shared sync routine (used by both CLI --apply and run_scheduled)
# ---------------------------------------------------------------------------


def _run_sync(folder_id: str, gcs_bucket_name: str | None, *, dry_run: bool = False) -> dict:
    """Walk Drive, optionally download + upload to GCS, return stats.

    Returns a dict with shape::

        {
            "folder_id": "...",
            "gcs_bucket": "tho-secure-documents",
            "folders_walked": int,         # manufacturer keys touched
            "files_seen": int,
            "files_downloaded": int,
            "files_uploaded": int,         # excludes md5-skipped
            "files_skipped_md5": int,
            "bytes_total": int,            # sum of declared file sizes
            "errors": list[dict],          # [{"file": "...", "stage": "...", "error": "..."}]
            "dry_run": bool,
        }
    """
    svc = _drive_service()
    bucket = None
    if not dry_run and gcs_bucket_name:
        try:
            from google.cloud import storage as _storage
        except ImportError as exc:  # pragma: no cover - runtime environment only
            raise _missing_google_libs(exc) from exc

        gcs_client = _storage.Client()
        bucket = gcs_client.bucket(gcs_bucket_name)

    counts: dict[str, int] = {}
    bytes_total = 0
    files_downloaded = 0
    files_uploaded = 0
    files_skipped_md5 = 0
    errors: list[dict] = []

    for file in walk_tho_folder(svc, folder_id):
        counts[file.manufacturer_key] = counts.get(file.manufacturer_key, 0) + 1
        bytes_total += file.size_bytes or 0
        if dry_run:
            continue
        try:
            download_to_cache(svc, file)
            files_downloaded += 1
        except Exception as exc:  # noqa: BLE001
            LOG.exception("download failed for %s", file.name)
            errors.append(
                {
                    "file": file.gcs_object_key,
                    "stage": "download",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        if bucket is not None:
            try:
                _uri, uploaded = upload_to_gcs(bucket, file)
                if uploaded:
                    files_uploaded += 1
                else:
                    files_skipped_md5 += 1
            except Exception as exc:  # noqa: BLE001
                LOG.exception("upload failed for %s", file.gcs_object_key)
                errors.append(
                    {
                        "file": file.gcs_object_key,
                        "stage": "upload",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )

    return {
        "folder_id": folder_id,
        "gcs_bucket": gcs_bucket_name,
        "folders_walked": len(counts),
        "files_seen": sum(counts.values()),
        "files_downloaded": files_downloaded,
        "files_uploaded": files_uploaded,
        "files_skipped_md5": files_skipped_md5,
        "bytes_total": bytes_total,
        "manufacturer_counts": dict(sorted(counts.items())),
        "errors": errors,
        "dry_run": dry_run,
    }


# ---------------------------------------------------------------------------
# Headless / Cloud Run Job entrypoint
# ---------------------------------------------------------------------------


def run_scheduled() -> dict:
    """Entrypoint for scheduled Cloud Run Job. Returns sync stats.

    Reads configuration from environment variables:

    * ``DRIVE_FOLDER_ID`` (required) — Drive folder ID for the THO root.
    * ``GCS_DOCUMENTS_BUCKET`` (default ``tho-secure-documents``) — destination bucket.
    * ``DRIVE_SYNC_DRY_RUN`` (default ``"0"``) — set to ``"1"`` to walk only.

    Errors during individual file download/upload are captured in the returned
    ``errors`` list; only configuration errors raise ``SystemExit``.
    """
    if not logging.getLogger().handlers:
        # Cloud Run Jobs have no preconfigured handler — keep it structured.
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )

    folder_id = (os.environ.get("DRIVE_FOLDER_ID") or "").strip()
    if not folder_id:
        raise SystemExit("DRIVE_FOLDER_ID required for scheduled run")

    gcs_bucket_name = (os.environ.get("GCS_DOCUMENTS_BUCKET") or "tho-secure-documents").strip()
    dry_run = (os.environ.get("DRIVE_SYNC_DRY_RUN") or "0").strip() == "1"

    stats = _run_sync(folder_id, gcs_bucket_name, dry_run=dry_run)
    LOG.info(
        "drive_sync_complete files_seen=%d uploaded=%d skipped_md5=%d errors=%d",
        stats["files_seen"],
        stats["files_uploaded"],
        stats["files_skipped_md5"],
        len(stats["errors"]),
    )

    # Soft-import Sentry for error reporting if available; never hard-fail on absence.
    if stats["errors"]:
        try:  # pragma: no cover - optional dep
            import sentry_sdk

            for err in stats["errors"]:
                sentry_sdk.capture_message(
                    f"drive_sync error: {err['stage']} {err['file']} {err['error']}",
                    level="error",
                )
        except ImportError:
            pass

    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync Mark's THO Drive folder → local + GCS")
    parser.add_argument("--folder-id", required=True, help="Drive folder ID for 'THO' root")
    parser.add_argument("--gcs-bucket", default="tho-secure-documents", help="Destination bucket")
    parser.add_argument(
        "--dry-run", action="store_true", help="List actions but do not download/upload"
    )
    parser.add_argument("--apply", action="store_true", help="Actually download and upload")
    parser.add_argument("-v", "--verbose", action="count", default=0)
    args = parser.parse_args(argv)

    if not (args.dry_run or args.apply):
        parser.error("Pass --dry-run or --apply.")

    logging.basicConfig(
        level=logging.DEBUG
        if args.verbose >= 2
        else (logging.INFO if args.verbose else logging.WARNING),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.dry_run:
        # Preserve historic stdout shape for the dry-run case (per-file lines).
        svc = _drive_service()
        counts: dict[str, int] = {}
        bytes_total = 0
        for file in walk_tho_folder(svc, args.folder_id):
            counts[file.manufacturer_key] = counts.get(file.manufacturer_key, 0) + 1
            bytes_total += file.size_bytes or 0
            print(f"DRY  {file.manufacturer_key:18s}  {file.name}  ({file.size_bytes or '?'} B)")
        print()
        print(f"Files seen: {sum(counts.values())}  (~{bytes_total / 1e6:.1f} MB)")
        for k, v in sorted(counts.items()):
            print(f"  {k:20s}  {v} files")
        return 0

    stats = _run_sync(args.folder_id, args.gcs_bucket, dry_run=False)

    print()
    print(
        f"Files seen: {stats['files_seen']}  "
        f"(~{stats['bytes_total'] / 1e6:.1f} MB)  "
        f"uploaded={stats['files_uploaded']}  "
        f"skipped_md5={stats['files_skipped_md5']}  "
        f"errors={len(stats['errors'])}"
    )
    for k, v in stats["manufacturer_counts"].items():
        print(f"  {k:20s}  {v} files")
    for err in stats["errors"]:
        print(f"  ERROR {err['stage']:10s} {err['file']}: {err['error']}")

    return 0 if not stats["errors"] else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
