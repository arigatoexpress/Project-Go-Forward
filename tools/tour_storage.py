"""Durable storage for staff-entered Matterport tours.

Staff can attach one Matterport model id to a home. The public inventory feed
overlays that id the same way staff photos are overlaid, so the tour shows up
no matter which inventory source is live.

Only a canonical 11-character Matterport model id is stored. Callers may pass
a bare id or an https://my.matterport.com/show URL; anything else is rejected.
The public URL is always rebuilt from the id. The raw text staff pasted is
never written.

Storage layout (GCS or local fallback)::

    listing_tours/<home_id>.json

Local disk under ``data/listing_tours`` is for development and tests. Cloud
Run mutations require GCS and fail closed, matching listing photos. Tours use
the existing listing-photo bucket (prefix ``listing_tours/``) so no new
permission is required.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from tools.image_storage import PhotoValidationError, safe_home_id

log = logging.getLogger(__name__)

# Same bucket as listing photos, different prefix. The runtime account that
# can write photos can write tours without a new IAM grant.
_GCS_BUCKET_NAME = os.getenv("GCS_LISTING_PHOTOS_BUCKET", "tho-listing-photos")
_PREFIX = "listing_tours"

_LOCAL_DIR = os.getenv(
    "THO_LISTING_TOURS_DIR",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "listing_tours"
    ),
)

# Matterport model ids in this catalog are 11 letters and digits.
_MATTERPORT_ID_RE = re.compile(r"^[A-Za-z0-9]{11}$")
_MATTERPORT_HOST = "my.matterport.com"
_SHOW_PATHS = frozenset({"/show", "/show/"})
# Bare ids and my.matterport.com/show URLs. No quotes, angle brackets, spaces,
# backslashes, or userinfo.
_INPUT_RE = re.compile(r"^[A-Za-z0-9:/?&=._%~#-]+$")
_MAX_INPUT_CHARS = 500

MATTERPORT_URL_BASE = "https://my.matterport.com/show/?m="

INVALID_TOUR_MESSAGE = "Paste a Matterport tour link from my.matterport.com, or the tour ID."
INVALID_HOME_MESSAGE = "Missing or invalid home id."
TOUR_STORAGE_MESSAGE = "The 3D tour could not be saved. Please try again."
TOUR_READ_MESSAGE = "The 3D tour could not be loaded. Please try again."

_GROUPED_TTL = float(os.getenv("THO_LISTING_TOURS_CACHE_TTL", "60"))
_grouped_cache: dict[str, str] | None = None
_grouped_cache_at: float = 0.0
_lock = threading.Lock()

_gcs_client = None
_gcs_bucket = None
_gcs_unavailable = False


class TourValidationError(ValueError):
    """Raised when staff input is not a Matterport id or show URL."""


class TourStorageError(RuntimeError):
    """Raised when a durable tour read or write cannot be completed."""


@dataclass(frozen=True)
class StoredTour:
    """One staff-entered Matterport model id."""

    home_id: str
    matterport_id: str
    backend: str  # "gcs" or "local"

    @property
    def matterport_url(self) -> str:
        return canonical_matterport_url(self.matterport_id)


def canonical_matterport_url(matterport_id: str) -> str:
    """Rebuild the public show URL from a validated id."""
    model_id = require_matterport_id(matterport_id)
    return f"{MATTERPORT_URL_BASE}{model_id}&play=1"


def require_matterport_id(value: str) -> str:
    """Return the id when it is exactly a Matterport model id."""
    if isinstance(value, str) and _MATTERPORT_ID_RE.fullmatch(value):
        return value
    raise TourValidationError(INVALID_TOUR_MESSAGE)


def normalize_matterport_input(value: object) -> str:
    """Accept a bare Matterport id or a my.matterport.com show URL.

    Returns the canonical model id. Rejects every other shape, including
    other hosts, non-https schemes, userinfo, and markup.
    """
    if not isinstance(value, str):
        raise TourValidationError(INVALID_TOUR_MESSAGE)
    text = value.strip()
    if not text or len(text) > _MAX_INPUT_CHARS or not _INPUT_RE.fullmatch(text):
        raise TourValidationError(INVALID_TOUR_MESSAGE)
    if _MATTERPORT_ID_RE.fullmatch(text):
        return text
    if not text.lower().startswith("https://"):
        raise TourValidationError(INVALID_TOUR_MESSAGE)

    parts = urlsplit(text)
    host = (parts.hostname or "").lower().rstrip(".")
    if (
        parts.scheme.lower() != "https"
        or parts.username
        or parts.password
        or host != _MATTERPORT_HOST
        or parts.port not in (None, 443)
        or parts.path not in _SHOW_PATHS
    ):
        raise TourValidationError(INVALID_TOUR_MESSAGE)
    ids = parse_qs(parts.query, keep_blank_values=False).get("m") or []
    if len(ids) != 1:
        raise TourValidationError(INVALID_TOUR_MESSAGE)
    return require_matterport_id(ids[0])


def _requires_durable_storage() -> bool:
    """Cloud Run's writable filesystem is ephemeral and must never be success."""
    return bool((os.environ.get("K_SERVICE") or "").strip())


def listing_home_id(home_id: str) -> str:
    try:
        return safe_home_id(home_id)
    except PhotoValidationError as exc:
        raise TourValidationError(INVALID_HOME_MESSAGE) from exc


def _get_bucket():
    """Lazy-load the shared listing-photo bucket, caching the unavailable state."""
    global _gcs_client, _gcs_bucket, _gcs_unavailable
    if os.getenv("THO_DISABLE_GCS_UPLOADS", "").lower() in {"1", "true", "yes"}:
        return None
    if _gcs_unavailable:
        return None
    if _gcs_bucket is None:
        try:
            from google.cloud import storage

            _gcs_client = storage.Client()
            _gcs_bucket = _gcs_client.bucket(_GCS_BUCKET_NAME)
        except Exception as exc:  # pragma: no cover - depends on env
            log.warning("Listing-tour GCS unavailable", extra={"error_type": type(exc).__name__})
            _gcs_unavailable = not _requires_durable_storage()
            return None
    return _gcs_bucket


def _invalidate_cache() -> None:
    global _grouped_cache, _grouped_cache_at
    _grouped_cache = None
    _grouped_cache_at = 0.0


def _blob_name(home: str) -> str:
    return f"{_PREFIX}/{home}.json"


def _local_path(home: str) -> str:
    return os.path.join(_LOCAL_DIR, f"{home}.json")


def _payload(model_id: str) -> bytes:
    return json.dumps({"matterport_id": model_id}, separators=(",", ":")).encode("utf-8")


def _parse_payload(data: bytes) -> str | None:
    """Return a canonical id, or None when the file is missing or untrusted."""
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    model_id = parsed.get("matterport_id")
    if isinstance(model_id, str) and _MATTERPORT_ID_RE.fullmatch(model_id):
        return model_id
    return None


def _write_local(home: str, payload: bytes) -> None:
    path = _local_path(home)
    os.makedirs(_LOCAL_DIR, exist_ok=True)
    temporary = f"{path}.tmp"
    with open(temporary, "wb") as handle:
        handle.write(payload)
    os.replace(temporary, path)


def _read_local(home: str) -> str | None:
    path = _local_path(home)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as handle:
            return _parse_payload(handle.read())
    except OSError:
        return None


def set_tour(home_id: str, matterport_id: str) -> StoredTour:
    """Persist one canonical Matterport id for ``home_id``."""
    home = listing_home_id(home_id)
    model_id = require_matterport_id(matterport_id)
    payload = _payload(model_id)
    bucket = _get_bucket()
    if bucket is not None:
        try:
            bucket.blob(_blob_name(home)).upload_from_string(
                payload, content_type="application/json"
            )
        except Exception as exc:  # pragma: no cover - depends on env
            log.error("Listing-tour GCS write failed", extra={"error_type": type(exc).__name__})
            if _requires_durable_storage():
                raise TourStorageError(TOUR_STORAGE_MESSAGE) from exc
        else:
            _invalidate_cache()
            return StoredTour(home, model_id, "gcs")
    if _requires_durable_storage():
        raise TourStorageError(TOUR_STORAGE_MESSAGE)
    with _lock:
        _write_local(home, payload)
    _invalidate_cache()
    return StoredTour(home, model_id, "local")


def get_tour(home_id: str) -> StoredTour | None:
    """Return the stored tour, or None when this home has none."""
    home = listing_home_id(home_id)
    bucket = _get_bucket()
    if bucket is not None:
        try:
            blob = bucket.blob(_blob_name(home))
            if blob.exists():
                model_id = _parse_payload(blob.download_as_bytes())
                return StoredTour(home, model_id, "gcs") if model_id else None
        except Exception as exc:  # pragma: no cover - depends on env
            log.error("Listing-tour GCS read failed", extra={"error_type": type(exc).__name__})
            if _requires_durable_storage():
                raise TourStorageError(TOUR_READ_MESSAGE) from exc
        if _requires_durable_storage():
            return None
    elif _requires_durable_storage():
        raise TourStorageError(TOUR_READ_MESSAGE)
    model_id = _read_local(home)
    if not model_id:
        return None
    return StoredTour(home, model_id, "local")


def clear_tour(home_id: str) -> bool:
    """Remove a stored tour. True when a record was deleted."""
    home = listing_home_id(home_id)
    deleted = False
    bucket = _get_bucket()
    if bucket is None and _requires_durable_storage():
        raise TourStorageError(TOUR_STORAGE_MESSAGE)
    if bucket is not None:
        try:
            blob = bucket.blob(_blob_name(home))
            if blob.exists():
                blob.delete()
                deleted = True
        except Exception as exc:  # pragma: no cover - depends on env
            log.error("Listing-tour GCS delete failed", extra={"error_type": type(exc).__name__})
            if _requires_durable_storage():
                raise TourStorageError(TOUR_STORAGE_MESSAGE) from exc
        if _requires_durable_storage():
            if deleted:
                _invalidate_cache()
            return deleted
    path = _local_path(home)
    if os.path.isfile(path):
        os.remove(path)
        deleted = True
    if deleted:
        _invalidate_cache()
    return deleted


def list_all(*, use_cache: bool = True) -> dict[str, str]:
    """Return ``{home_id: matterport_id}`` for every valid stored tour.

    A storage outage returns an empty map so the public inventory page can
    still render. Staff read and write endpoints fail closed instead.
    """
    global _grouped_cache, _grouped_cache_at
    now = time.time()
    if use_cache and _grouped_cache is not None and (now - _grouped_cache_at) < _GROUPED_TTL:
        return _grouped_cache

    grouped: dict[str, str] = {}
    bucket = _get_bucket()
    if bucket is not None:
        try:
            for blob in bucket.list_blobs(prefix=f"{_PREFIX}/"):
                name = blob.name.split("/")[-1]
                if not name.endswith(".json"):
                    continue
                home = name[: -len(".json")]
                model_id = _parse_payload(blob.download_as_bytes())
                if home and model_id:
                    grouped[home] = model_id
        except Exception as exc:  # pragma: no cover - depends on env
            log.error(
                "Listing-tour GCS list failed",
                extra={"error_type": type(exc).__name__},
            )
            if _requires_durable_storage():
                return {}
    elif _requires_durable_storage():
        return {}

    if not _requires_durable_storage() and os.path.isdir(_LOCAL_DIR):
        for name in os.listdir(_LOCAL_DIR):
            if not name.endswith(".json") or name.endswith(".tmp"):
                continue
            home = name[: -len(".json")]
            if home in grouped:
                continue
            model_id = _read_local(home)
            if model_id:
                grouped[home] = model_id

    _grouped_cache = grouped
    _grouped_cache_at = now
    return grouped
