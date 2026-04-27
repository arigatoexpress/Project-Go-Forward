"""
drive_floorplan_sync.py
=======================

Walk Mark Willcott's shared Google Drive folder "THO" and pull manufacturer
floorplans into ``data/floorplans/<manufacturer>/`` plus the
``tho-secure-documents`` GCS bucket under ``floorplans/``.

Usage
-----

    python3 tools/drive_floorplan_sync.py --dry-run \\
        --folder-id 0BwMsFgQWT3QvWWoxbWMtQXJfR0U

    python3 tools/drive_floorplan_sync.py --apply \\
        --folder-id 0BwMsFgQWT3QvWWoxbWMtQXJfR0U \\
        --gcs-bucket tho-secure-documents

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
import io
import logging
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


def upload_to_gcs(bucket: storage.Bucket, file: DriveFile) -> str:
    blob = bucket.blob(file.gcs_object_key)
    blob.upload_from_filename(str(file.local_path), content_type=file.mime_type)
    LOG.info("Uploaded gs://%s/%s", bucket.name, file.gcs_object_key)
    return f"gs://{bucket.name}/{file.gcs_object_key}"


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

    svc = _drive_service()
    bucket = None
    if args.apply:
        try:
            from google.cloud import storage
        except ImportError as exc:  # pragma: no cover - runtime environment only
            raise _missing_google_libs(exc) from exc

        gcs_client = storage.Client()
        bucket = gcs_client.bucket(args.gcs_bucket)

    counts: dict[str, int] = {}
    bytes_total = 0
    for file in walk_tho_folder(svc, args.folder_id):
        counts[file.manufacturer_key] = counts.get(file.manufacturer_key, 0) + 1
        bytes_total += file.size_bytes or 0
        if args.dry_run:
            print(f"DRY  {file.manufacturer_key:18s}  {file.name}  ({file.size_bytes or '?'} B)")
            continue
        download_to_cache(svc, file)
        if bucket is not None:
            upload_to_gcs(bucket, file)

    print()
    print(f"Files seen: {sum(counts.values())}  (~{bytes_total / 1e6:.1f} MB)")
    for k, v in sorted(counts.items()):
        print(f"  {k:20s}  {v} files")

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
