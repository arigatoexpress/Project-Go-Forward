"""
Google Drive Service — Handles deal-specific document mirroring to Google Drive.
Used by Phase 2 Intelligent Document Engine for regulatory compliance storage.
"""

import logging
import os
from typing import Optional
try:
    import google.auth
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
except Exception:  # pragma: no cover
    # Provide minimal stubs for testing when google libraries are unavailable
    class _DummyCreds:
        pass
    class _DummyAuth:
        @staticmethod
        def default(scopes=None):
            return (_DummyCreds(), None)
    google = type('google', (), {'auth': _DummyAuth})
    def build(*args, **kwargs):
        return None
    class MediaFileUpload:
        def __init__(self, *args, **kwargs):
            pass

logger = logging.getLogger(__name__)

# Primary Drive folder ID for generated documents (can be overridden by env)
DRIVE_RECORDS_ROOT_ID = os.getenv("DRIVE_RECORDS_ROOT_ID")

def get_drive_service():
    """Lazy-load Google Drive service using Application Default Credentials."""
    try:
        # We need drive.file for creating files we own, and drive for broader folder management if needed
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/drive.file"])
        return build("drive", "v3", credentials=creds, cache_discovery=False)
    except Exception as e:
        logger.warning(f"Google Drive service unavailable: {e}")
        return None

def upload_to_drive(local_path: str, filename: str, folder_id: Optional[str] = None) -> Optional[str]:
    """
    Upload a file to Google Drive.

    Args:
        local_path: Path to the local file.
        filename: Name for the file on Drive.
        folder_id: Optional destination folder ID.

    Returns:
        The webViewLink to the uploaded file or None on failure.
    """
    if os.getenv("THO_DISABLE_DRIVE_MIRRORING", "").lower() in {"1", "true", "yes"}:
        logger.info("Drive mirroring disabled; skipping %s", filename)
        return None

    service = get_drive_service()
    if not service:
        return None

    try:
        file_metadata = {'name': filename}
        if folder_id:
            file_metadata['parents'] = [folder_id]

        # Determine mimetype
        mimetype = 'application/pdf'
        if filename.endswith('.jpg') or filename.endswith('.jpeg'):
            mimetype = 'image/jpeg'
        elif filename.endswith('.png'):
            mimetype = 'image/png'

        media = MediaFileUpload(local_path, mimetype=mimetype, resumable=True)
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink'
        ).execute()

        logger.info(f"Successfully mirrored to Drive: {filename} (ID: {file.get('id')})")
        return file.get('webViewLink')
    except Exception as e:
        logger.error(f"Drive upload failed for {filename}: {e}")
        return None

def ensure_deal_folder(deal_id: str, parent_folder_id: Optional[str] = None) -> Optional[str]:
    """
    Ensure a folder exists for a specific deal on Drive.

    Args:
        deal_id: The unique deal identifier (used as folder name).
        parent_folder_id: Parent folder ID (defaults to DRIVE_RECORDS_ROOT_ID).

    Returns:
        The folder ID of the existing or newly created folder.
    """
    parent_id = parent_folder_id or DRIVE_RECORDS_ROOT_ID
    if not parent_id:
        logger.warning("No DRIVE_RECORDS_ROOT_ID configured; cannot create deal folder")
        return None

    service = get_drive_service()
    if not service:
        return None

    try:
        # Check if folder already exists
        query = f"name = '{deal_id}' and '{parent_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        results = service.files().list(q=query, fields="files(id)").execute()
        files = results.get('files', [])

        if files:
            return files[0].get('id')

        # Create folder
        file_metadata = {
            'name': deal_id,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [parent_id]
        }
        file = service.files().create(body=file_metadata, fields='id').execute()
        logger.info(f"Created new deal folder on Drive: {deal_id} (ID: {file.get('id')})")
        return file.get('id')
    except Exception as e:
        logger.error(f"Failed to ensure deal folder for {deal_id}: {e}")
        return None
