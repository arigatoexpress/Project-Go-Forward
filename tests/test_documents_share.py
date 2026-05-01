"""Tests for the GET /api/documents/share/{filename} endpoint.

Verifies the signed-URL share flow used by email-doc-delivery:
* Happy path produces a V4 signed URL with the requested TTL.
* Filenames containing path-traversal segments (or any slash) are rejected.
* `ttl_hours` outside the supported range is rejected.
* No live Drive or GCS calls are made — `google.cloud.storage.Client` is mocked.

Run: python -m pytest tests/test_documents_share.py -v
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# Reuse the heavyweight stub harness from test_api_v1 to load main.py
# without importing the real Firestore / ADK / etc. dependencies.
from tests.test_api_v1 import load_app  # noqa: E402


def _stub_storage_module(
    monkeypatch, *, blob_exists: bool = True, signed_url: str = "https://signed.example/"
):
    """Install a fake `google.cloud.storage` module before main is imported.

    Returns the (storage_module, blob_mock) tuple so tests can assert on calls.
    """
    storage_module = types.ModuleType("google.cloud.storage")

    blob = MagicMock()
    blob.exists.return_value = blob_exists
    blob.generate_signed_url.return_value = signed_url

    bucket = MagicMock()
    bucket.blob.return_value = blob

    client = MagicMock()
    client.bucket.return_value = bucket

    storage_module.Client = MagicMock(return_value=client)
    monkeypatch.setitem(sys.modules, "google.cloud.storage", storage_module)
    return storage_module, blob


def _make_admin_client(monkeypatch):
    """Spin up a TestClient, mint an admin token, and return both."""
    main, _fake_db, _fake_logger = load_app(monkeypatch, tho_api_key="tho-secret")
    client = TestClient(main.app)
    token = main._create_admin_token()
    return client, main, token


def test_share_document_happy_path(monkeypatch):
    """Valid filename + default TTL returns a signed URL via blob.generate_signed_url."""
    _storage, blob = _stub_storage_module(
        monkeypatch, blob_exists=True, signed_url="https://signed.example/abc?ttl=24h"
    )
    client, _main, token = _make_admin_client(monkeypatch)

    resp = client.get(
        "/api/documents/share/sales_contract_123.pdf",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["filename"] == "sales_contract_123.pdf"
    assert body["signed_url"] == "https://signed.example/abc?ttl=24h"
    assert body["ttl_hours"] == 24
    assert body["expires_in_seconds"] == 24 * 3600
    assert body["gcs_uri"].endswith("/generated_docs/sales_contract_123.pdf")

    # generate_signed_url was invoked with the right shape.
    assert blob.generate_signed_url.called
    kwargs = blob.generate_signed_url.call_args.kwargs
    assert kwargs.get("version") == "v4"
    assert kwargs.get("method") == "GET"
    # expiration is a timedelta — confirm it equals 24 hours.
    expiration = kwargs.get("expiration")
    assert expiration is not None
    assert expiration.total_seconds() == 24 * 3600


def test_share_document_custom_ttl_hours(monkeypatch):
    """`?ttl_hours=48` produces a 48-hour signed URL."""
    _storage, blob = _stub_storage_module(monkeypatch, blob_exists=True)
    client, _main, token = _make_admin_client(monkeypatch)

    resp = client.get(
        "/api/documents/share/closing_packet.pdf?ttl_hours=48",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ttl_hours"] == 48
    expiration = blob.generate_signed_url.call_args.kwargs["expiration"]
    assert expiration.total_seconds() == 48 * 3600


def test_share_document_rejects_dotdot_in_filename(monkeypatch):
    """Filenames containing `..` are rejected before any GCS call is made.

    Note: Starlette's path routing decodes `%2F` into `/` and breaks the path
    parameter match (returns 404 from the SPA catch-all). The validation we
    care about here is the explicit ``..`` substring check inside the handler.
    """
    _storage, blob = _stub_storage_module(monkeypatch)
    client, _main, token = _make_admin_client(monkeypatch)

    resp = client.get(
        "/api/documents/share/..secret.pdf",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 400, resp.text
    assert "Invalid filename" in resp.json()["error"]
    blob.generate_signed_url.assert_not_called()


def test_share_document_rejects_backslash_in_filename(monkeypatch):
    """Backslashes inside the filename are treated as path-traversal."""
    _storage, blob = _stub_storage_module(monkeypatch)
    client, _main, token = _make_admin_client(monkeypatch)

    # %5C decodes to '\\' — survives routing as a literal character.
    resp = client.get(
        "/api/documents/share/subdir%5Cfile.pdf",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 400, resp.text
    assert "Invalid filename" in resp.json()["error"]
    blob.generate_signed_url.assert_not_called()


def test_share_document_rejects_non_pdf_extension(monkeypatch):
    """Only `.pdf` files are shareable."""
    _storage, _blob = _stub_storage_module(monkeypatch)
    client, _main, token = _make_admin_client(monkeypatch)

    resp = client.get(
        "/api/documents/share/credentials.json",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 400
    assert "PDF" in resp.json()["error"]


def test_share_document_rejects_ttl_hours_too_high(monkeypatch):
    """`ttl_hours` greater than the 72-hour cap is rejected."""
    _storage, _blob = _stub_storage_module(monkeypatch)
    client, _main, token = _make_admin_client(monkeypatch)

    resp = client.get(
        "/api/documents/share/sales_contract.pdf?ttl_hours=999",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 400
    assert "ttl_hours" in resp.json()["error"]


def test_share_document_rejects_ttl_hours_zero(monkeypatch):
    """`ttl_hours=0` is rejected (must be >= 1)."""
    _storage, _blob = _stub_storage_module(monkeypatch)
    client, _main, token = _make_admin_client(monkeypatch)

    resp = client.get(
        "/api/documents/share/sales_contract.pdf?ttl_hours=0",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 400


def test_share_document_404_when_blob_missing(monkeypatch):
    """If the GCS blob does not exist, return 404 (no signed URL minted)."""
    _storage, blob = _stub_storage_module(monkeypatch, blob_exists=False)
    client, _main, token = _make_admin_client(monkeypatch)

    resp = client.get(
        "/api/documents/share/missing_doc.pdf",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 404
    blob.generate_signed_url.assert_not_called()


def test_share_document_requires_admin_auth(monkeypatch):
    """Unauthenticated requests get 401 from require_admin."""
    _storage, _blob = _stub_storage_module(monkeypatch)
    client, _main, _token = _make_admin_client(monkeypatch)

    resp = client.get("/api/documents/share/sales_contract.pdf")

    assert resp.status_code == 401
