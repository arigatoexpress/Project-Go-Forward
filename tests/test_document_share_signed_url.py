"""Tests for the document share endpoint and its signed-URL helper.

Targets flagged by the Python-3.12 honest coverage re-measurement (see
docs/MAIN_COVERAGE_INVESTIGATION.md):

- ``_generate_document_signed_url`` (main.py:8689) — local-key signing plus
  the Cloud Run ADC fallback (google.auth default → refresh → IAM
  delegation signing)
- ``GET /api/documents/share/{filename}`` (``share_document``) — basename
  validation, TTL bounds, GCS existence check, V4 signed-URL minting

``google.cloud.storage`` and ``google.auth`` are stubbed in both
``sys.modules`` and on the already-imported parent packages (from-imports
resolve package attributes first — the same ordering trap as
``tools.image_storage``). Traversal variants (slashes, ``..``) are invoked
directly because TestClient normalizes dotted paths before routing; early
validation branches never touch the request object, so a bare namespace
suffices. Audit logging is no-op-patched except where the endpoint's own
log line is under test.

Run: python -m pytest tests/test_document_share_signed_url.py -v
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(Path(__file__).parent))
from test_api_v1 import create_client  # noqa: E402


def _admin_headers(main) -> dict:
    return {"X-Admin-Token": main._create_admin_token()}


# ─── Fakes ────────────────────────────────────────────────────────────────


class _FakeBlob:
    def __init__(self, bucket_name, blob_name, exists=True, url="https://signed/x"):
        self.bucket_name = bucket_name
        self.blob_name = blob_name
        self._exists = exists
        self._url = url
        self.calls: list = []
        self.error: Exception | None = None

    def exists(self):
        if self.error:
            raise self.error
        return self._exists

    def generate_signed_url(self, version=None, expiration=None, method=None, **kwargs):
        self.calls.append(
            {"version": version, "expiration": expiration, "method": method, **kwargs}
        )
        # The first (local-key) attempt fails with the private-key
        # AttributeError; the IAM-delegation retry succeeds.
        if self.error and "service_account_email" not in kwargs:
            raise self.error
        return self._url


def _stub_storage(monkeypatch, blob: _FakeBlob, client_log: list) -> None:
    """Install a fake google.cloud.storage (both import paths)."""
    storage_module = types.ModuleType("google.cloud.storage")

    class _Bucket:
        def __init__(self, name):
            self.name = name

        def blob(self, blob_name):
            return blob

    class _Client:
        def __init__(self):
            client_log.append("client-created")

        def bucket(self, name):
            return _Bucket(name)

    storage_module.Client = _Client
    monkeypatch.setitem(sys.modules, "google.cloud.storage", storage_module)
    import google.cloud

    monkeypatch.setattr(google.cloud, "storage", storage_module, raising=False)


def _stub_google_auth(monkeypatch, credentials) -> None:
    """Install a fake google.auth chain for the ADC fallback path."""
    auth_module = types.ModuleType("google.auth")
    auth_module.default = lambda scopes=None: (credentials, "test-project")
    transport_module = types.ModuleType("google.auth.transport")
    requests_module = types.ModuleType("google.auth.transport.requests")

    class GoogleAuthRequest:
        pass

    requests_module.Request = GoogleAuthRequest
    transport_module.requests = requests_module
    auth_module.transport = transport_module
    monkeypatch.setitem(sys.modules, "google.auth", auth_module)
    monkeypatch.setitem(sys.modules, "google.auth.transport", transport_module)
    monkeypatch.setitem(sys.modules, "google.auth.transport.requests", requests_module)
    # `import google.auth` rebinds the REAL parent package and attribute
    # access (`google.auth.default`) resolves the package attribute, not
    # sys.modules — override it too or the real ADC lookup runs.
    import google

    monkeypatch.setattr(google, "auth", auth_module, raising=False)


class _Credentials:
    def __init__(self, token="access-tok", email="signer@tho.iam.gserviceaccount.com"):
        self.token = token
        self.service_account_email = email
        self.refreshed = False

    def refresh(self, request):
        self.refreshed = True


# ─── _generate_document_signed_url (direct) ───────────────────────────────


class TestGenerateDocumentSignedUrl:
    def test_local_key_signing_short_circuits(self, monkeypatch):
        _client, main, _db, _logger = create_client(monkeypatch)
        blob = _FakeBlob("bucket", "obj", url="https://signed/local")

        url = main._generate_document_signed_url(
            blob, expiration="EXP", method="GET"
        )

        assert url == "https://signed/local"
        assert blob.calls == [{"version": "v4", "expiration": "EXP", "method": "GET"}]

    def test_adc_fallback_signs_with_iam_credentials(self, monkeypatch):
        _client, main, _db, _logger = create_client(monkeypatch)
        credentials = _Credentials()
        _stub_google_auth(monkeypatch, credentials)
        blob = _FakeBlob("bucket", "obj", url="https://signed/adc")
        blob.error = AttributeError("could not serialize the private key")

        url = main._generate_document_signed_url(
            blob, expiration="EXP", method="GET"
        )

        assert url == "https://signed/adc"
        assert credentials.refreshed is True
        # First call raised the private-key AttributeError; the fallback
        # retried with the IAM-delegation kwargs.
        assert blob.calls[1] == {
            "version": "v4",
            "expiration": "EXP",
            "method": "GET",
            "service_account_email": "signer@tho.iam.gserviceaccount.com",
            "access_token": "access-tok",
        }

    def test_sign_credentials_wording_also_triggers_fallback(self, monkeypatch):
        _client, main, _db, _logger = create_client(monkeypatch)
        credentials = _Credentials()
        _stub_google_auth(monkeypatch, credentials)
        blob = _FakeBlob("bucket", "obj", url="https://signed/adc2")
        blob.error = AttributeError("Unable to sign credentials without a key")

        url = main._generate_document_signed_url(blob, expiration="EXP", method="GET")

        assert url == "https://signed/adc2"
        assert len(blob.calls) == 2

    def test_unrelated_attribute_error_reraises(self, monkeypatch):
        _client, main, _db, _logger = create_client(monkeypatch)
        blob = _FakeBlob("bucket", "obj")
        blob.error = AttributeError("some unrelated attr")

        with pytest.raises(AttributeError, match="some unrelated attr"):
            main._generate_document_signed_url(blob, expiration="EXP", method="GET")

    def test_missing_signing_identity_raises_runtime_error(self, monkeypatch):
        _client, main, _db, _logger = create_client(monkeypatch)
        # Token missing entirely.
        credentials = _Credentials(token=None)
        _stub_google_auth(monkeypatch, credentials)
        blob = _FakeBlob("bucket", "obj")
        blob.error = AttributeError("could not serialize the private key")

        with pytest.raises(RuntimeError, match="signing identity"):
            main._generate_document_signed_url(blob, expiration="EXP", method="GET")

    def test_placeholder_service_account_email_rejected(self, monkeypatch):
        _client, main, _db, _logger = create_client(monkeypatch)
        # Cloud Run's default compute identity reports email "default",
        # which cannot sign — must not be used for URL signing.
        credentials = _Credentials(email="default")
        _stub_google_auth(monkeypatch, credentials)
        blob = _FakeBlob("bucket", "obj")
        blob.error = AttributeError("could not serialize the private key")

        with pytest.raises(RuntimeError, match="signing identity"):
            main._generate_document_signed_url(blob, expiration="EXP", method="GET")


# ─── GET /api/documents/share/{filename} ──────────────────────────────────


class TestShareDocument:
    def test_happy_path_mints_signed_url(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        logged: list = []
        monkeypatch.setattr(main, "log_user_action", lambda **kwargs: None)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: logged.append(kwargs))
        blob = _FakeBlob("tho-secure-documents", "generated_docs/contract.pdf")
        _stub_storage(monkeypatch, blob, [])

        resp = client.get(
            "/api/documents/share/contract.pdf",
            headers=_admin_headers(main),
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["filename"] == "contract.pdf"
        assert body["gcs_uri"] == "gs://tho-secure-documents/generated_docs/contract.pdf"
        assert body["signed_url"] == "https://signed/x"
        assert body["ttl_hours"] == 24  # module default
        assert body["expires_in_seconds"] == 24 * 3600
        assert blob.calls[0]["version"] == "v4"
        assert blob.calls[0]["expiration"].total_seconds() == 24 * 3600
        assert logged and logged[0]["action"] == "documents.share"
        assert logged[0]["target_id"] == "generated_docs/contract.pdf"

    def test_custom_ttl_within_bounds(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
        monkeypatch.setattr(main, "log_user_action", lambda **kwargs: None)
        blob = _FakeBlob("bucket", "generated_docs/x.pdf")
        _stub_storage(monkeypatch, blob, [])

        resp = client.get(
            "/api/documents/share/x.pdf?ttl_hours=48",
            headers=_admin_headers(main),
        )

        assert resp.status_code == 200
        assert resp.json()["ttl_hours"] == 48
        assert resp.json()["expires_in_seconds"] == 48 * 3600

    def test_ttl_out_of_bounds_rejected(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
        blob = _FakeBlob("bucket", "generated_docs/x.pdf")
        _stub_storage(monkeypatch, blob, [])

        for bad_ttl in ("0", "999", "-3"):
            resp = client.get(
                f"/api/documents/share/x.pdf?ttl_hours={bad_ttl}",
                headers=_admin_headers(main),
            )
            assert resp.status_code == 400, bad_ttl
            assert "ttl_hours" in resp.json()["error"]

    def test_non_pdf_extension_rejected(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        blob = _FakeBlob("bucket", "obj")
        _stub_storage(monkeypatch, blob, [])

        resp = client.get(
            "/api/documents/share/notes.txt",
            headers=_admin_headers(main),
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "Only PDF files are shareable."

    def test_backslash_in_filename_rejected(self, monkeypatch):
        client, _main, _db, _logger = create_client(monkeypatch)
        blob = _FakeBlob("bucket", "obj")
        _stub_storage(monkeypatch, blob, [])

        # %5C survives routing as a single path segment.
        resp = client.get(
            "/api/documents/share/evil%5Cfile.pdf",
            headers={"X-Admin-Token": _main._create_admin_token()},
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "Invalid filename"

    def test_traversal_and_slash_variants_rejected_directly(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        blob = _FakeBlob("bucket", "obj")
        _stub_storage(monkeypatch, blob, [])
        dummy_request = types.SimpleNamespace(headers={})

        # TestClient normalizes dotted paths client-side, so exercise these
        # by calling the endpoint function directly (early returns never
        # touch the request object).
        for bad_name in ("../secret.pdf", "sub/dir.pdf", "..", "a\\b.pdf"):
            response = asyncio.run(main.share_document(bad_name, dummy_request))
            assert response.status_code == 400, bad_name
            assert response.body == b'{"error":"Invalid filename"}', bad_name

    def test_non_integer_ttl_rejected_directly(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        blob = _FakeBlob("bucket", "obj")
        _stub_storage(monkeypatch, blob, [])
        dummy_request = types.SimpleNamespace(headers={})

        response = asyncio.run(main.share_document("x.pdf", dummy_request, ttl_hours="abc"))

        assert response.status_code == 400
        assert "ttl_hours" in response.body.decode()

    def test_missing_blob_returns_404(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
        blob = _FakeBlob("bucket", "generated_docs/ghost.pdf", exists=False)
        _stub_storage(monkeypatch, blob, [])

        resp = client.get(
            "/api/documents/share/ghost.pdf",
            headers=_admin_headers(main),
        )

        assert resp.status_code == 404
        assert resp.json()["error"] == "File not found"

    def test_gcs_unavailable_returns_503(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
        # Make `from google.cloud import storage` raise ImportError: with no
        # package attribute, the from-import falls back to importing the
        # submodule, and a None sys.modules entry halts that with ImportError.
        import google.cloud

        monkeypatch.delattr(google.cloud, "storage", raising=False)
        monkeypatch.setitem(sys.modules, "google.cloud.storage", None)

        resp = client.get(
            "/api/documents/share/contract.pdf",
            headers=_admin_headers(main),
        )

        assert resp.status_code == 503
        assert resp.json()["error"] == "GCS client not available"

    def test_signing_failure_returns_500(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
        blob = _FakeBlob("bucket", "generated_docs/broken.pdf")
        blob.error = RuntimeError("backend boom")
        _stub_storage(monkeypatch, blob, [])

        resp = client.get(
            "/api/documents/share/broken.pdf",
            headers=_admin_headers(main),
        )

        assert resp.status_code == 500
        assert "RuntimeError" in resp.json()["error"]
