"""Unit tests for the DocuSeal e-sign scaffolding.

Tests cover:
  1. /api/docuseal/send returns 501 when DOCUSEAL_API_URL is unset, 200 when set
  2. /api/docuseal/webhook rejects invalid HMAC signatures (401)
  3. GCS mirror path logic stores signed PDF at signed_documents/<deal_id>/<file>

These tests mirror the core logic from the main.py endpoints without requiring
the full GCP/ADK dependency stack, following the pattern of test_admin_auth.py.

Run: python -m pytest tests/test_docuseal.py -v
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ─── Minimal replica of the two DocuSeal endpoints ───────────────────────────
#
# We extract the endpoint logic into a small standalone FastAPI app so these
# tests run without the full main.py dependency stack (google-cloud, ADK, etc.).
# The logic is a verbatim copy of the scaffolded code in main.py; any change to
# main.py should be reflected here to keep the tests meaningful.


def _build_docuseal_app(
    api_url: str = "",
    api_token: str = "",
    webhook_secret: str = "",
):
    """Return a minimal FastAPI app with only the two DocuSeal endpoints."""
    app = FastAPI()

    @app.post("/api/docuseal/send")
    async def docuseal_send(request: Request):
        if not api_url or not api_token:
            return JSONResponse(
                {
                    "success": False,
                    "status": "not_configured",
                    "message": (
                        "DocuSeal not configured — set DOCUSEAL_API_URL + "
                        "DOCUSEAL_API_TOKEN env vars to enable."
                    ),
                },
                status_code=501,
            )

        import httpx

        data = await request.json()
        deal_id = data.get("deal_id")
        template_name = data.get("template_name")
        signer_email = data.get("signer_email")
        signer_name = data.get("signer_name")

        if not all([deal_id, template_name, signer_email, signer_name]):
            raise HTTPException(status_code=422, detail="Missing required fields")

        payload = {
            "template_id": template_name,
            "send_email": True,
            "submitters": [
                {"role": "Buyer", "email": signer_email, "name": signer_name, "values": {}}
            ],
            "metadata": {"deal_id": deal_id, "template_name": template_name},
        }

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{api_url.rstrip('/')}/api/submissions",
                headers={"X-Auth-Token": api_token, "Content-Type": "application/json"},
                json=payload,
            )
        resp.raise_for_status()
        return {"success": True, "submission": resp.json()}

    @app.post("/api/docuseal/webhook")
    async def docuseal_webhook(request: Request):
        if not webhook_secret:
            return {"status": "ignored", "reason": "webhook_secret_not_configured"}

        body = await request.body()
        incoming_sig = request.headers.get("X-Docuseal-Signature", "")
        expected_sig = hmac.new(webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(incoming_sig, expected_sig):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

        event = json.loads(body)
        event_type = event.get("event_type") or event.get("type", "")

        if event_type not in ("form.completed", "submission.completed"):
            return {"status": "ignored", "event_type": event_type}

        data = event.get("data", event)
        deal_id = (data.get("metadata") or {}).get("deal_id")
        submission_id = data.get("id")
        documents = data.get("documents") or []
        document_url = documents[0].get("url") if documents else None

        if not document_url or not deal_id:
            return {"status": "ignored", "reason": "missing_document_url_or_deal_id"}

        import httpx

        async with httpx.AsyncClient(timeout=60) as client:
            dl = await client.get(document_url)
        dl.raise_for_status()

        filename = f"signed_{submission_id}.pdf"
        gcs_path = f"signed_documents/{deal_id}/{filename}"
        tmp_path = os.path.join("/tmp", filename)
        with open(tmp_path, "wb") as f:
            f.write(dl.content)

        gcs_uri = None
        try:
            from google.cloud import storage as _gcs  # type: ignore

            _client = _gcs.Client()
            bucket_name = os.getenv("GCS_DOCUMENTS_BUCKET", "tho-secure-documents")
            _bucket = _client.bucket(bucket_name)
            _bucket.blob(gcs_path).upload_from_filename(tmp_path, content_type="application/pdf")
            gcs_uri = f"gs://{bucket_name}/{gcs_path}"
        except Exception:
            pass

        return {"status": "ok", "gcs_path": gcs_uri or gcs_path}

    return app


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ─── Test 1: /api/docuseal/send — 501 vs 200 ─────────────────────────────────


class TestDocuSealSend:
    def test_returns_501_when_not_configured(self):
        client = TestClient(_build_docuseal_app(), raise_server_exceptions=False)
        resp = client.post(
            "/api/docuseal/send",
            json={
                "deal_id": "deal-001",
                "template_name": "TMHA_SalesContract.pdf",
                "signer_email": "buyer@example.com",
                "signer_name": "Test Buyer",
            },
        )
        assert resp.status_code == 501
        body = resp.json()
        assert body["success"] is False
        assert body["status"] == "not_configured"
        assert "DOCUSEAL_API_URL" in body["message"]

    def test_returns_200_when_configured(self):
        client = TestClient(
            _build_docuseal_app(
                api_url="http://docuseal.internal",
                api_token="test-token-abc",
            ),
            raise_server_exceptions=False,
        )
        fake_submission = {"id": 42, "status": "pending", "url": "http://docuseal.internal/s/xyz"}

        mock_response = MagicMock()
        mock_response.json.return_value = fake_submission
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_http):
            resp = client.post(
                "/api/docuseal/send",
                json={
                    "deal_id": "deal-001",
                    "template_name": "TMHA_SalesContract.pdf",
                    "signer_email": "buyer@example.com",
                    "signer_name": "Test Buyer",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["submission"]["id"] == 42


# ─── Test 2: webhook HMAC signature validation ───────────────────────────────


class TestDocuSealWebhookSignature:
    def test_rejects_invalid_signature(self):
        client = TestClient(
            _build_docuseal_app(webhook_secret="correct-secret"),
            raise_server_exceptions=False,
        )
        payload = json.dumps({"event_type": "form.completed", "data": {}}).encode()
        resp = client.post(
            "/api/docuseal/webhook",
            content=payload,
            headers={"Content-Type": "application/json", "X-Docuseal-Signature": "badhex"},
        )
        assert resp.status_code == 401
        assert "Invalid webhook signature" in resp.json()["detail"]

    def test_accepts_valid_signature(self):
        secret = "correct-secret"
        client = TestClient(
            _build_docuseal_app(webhook_secret=secret),
            raise_server_exceptions=False,
        )
        payload = json.dumps({"event_type": "submission.created", "data": {}}).encode()
        resp = client.post(
            "/api/docuseal/webhook",
            content=payload,
            headers={
                "Content-Type": "application/json",
                "X-Docuseal-Signature": _sign(secret, payload),
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"


# ─── Test 3: GCS mirror path ─────────────────────────────────────────────────


class TestDocuSealGCSMirror:
    def test_signed_pdf_stored_at_correct_gcs_path(self, monkeypatch):
        """form.completed event → PDF mirrored to signed_documents/<deal_id>/signed_<id>.pdf."""
        secret = "webhook-secret-abc"
        monkeypatch.setenv("GCS_DOCUMENTS_BUCKET", "tho-secure-documents")

        client = TestClient(
            _build_docuseal_app(webhook_secret=secret),
            raise_server_exceptions=False,
        )

        event = {
            "event_type": "form.completed",
            "data": {
                "id": 99,
                "metadata": {"deal_id": "deal-777"},
                "documents": [{"url": "http://docuseal.internal/doc/99.pdf"}],
            },
        }
        payload = json.dumps(event).encode()
        sig = _sign(secret, payload)

        uploaded_blobs: list[str] = []

        class FakeBlob:
            def __init__(self, path):
                self._path = path

            def upload_from_filename(self, local_path, content_type=None):
                uploaded_blobs.append(self._path)

        class FakeBucket:
            def blob(self, path):
                return FakeBlob(path)

        fake_gcs_client = MagicMock()
        fake_gcs_client.bucket.return_value = FakeBucket()

        fake_dl = MagicMock()
        fake_dl.content = b"%PDF-1.4 signed"
        fake_dl.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=fake_dl)

        # Stub google.cloud.storage.Client so no real GCP call is made
        fake_gcs_module = MagicMock()
        fake_gcs_module.Client.return_value = fake_gcs_client

        with (
            patch("httpx.AsyncClient", return_value=mock_http),
            patch.dict(
                sys.modules,
                {
                    "google.cloud.storage": fake_gcs_module,
                    "google.cloud": MagicMock(storage=fake_gcs_module),
                },
            ),
        ):
            resp = client.post(
                "/api/docuseal/webhook",
                content=payload,
                headers={"Content-Type": "application/json", "X-Docuseal-Signature": sig},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert len(uploaded_blobs) == 1
        assert uploaded_blobs[0] == "signed_documents/deal-777/signed_99.pdf"
        assert "signed_documents/deal-777" in body["gcs_path"]
