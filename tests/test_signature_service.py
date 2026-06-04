"""Unit tests for the DocuSeal e-signature client (signature_service.py).

These mock all HTTP so they run offline and assert the request shape, response
parsing, graceful not-configured behavior, and constant-time webhook-secret
verification. No live DocuSeal instance is required.
"""

import base64
from unittest.mock import MagicMock, patch

import pytest

import signature_service as sig


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(sig, "DOCUSEAL_BASE_URL", "https://sign.example.com/api")
    monkeypatch.setattr(sig, "DOCUSEAL_API_TOKEN", "test-token")
    monkeypatch.setattr(sig, "DOCUSEAL_WEBHOOK_SECRET", "")


def _resp(status_code=200, json_data=None, text=""):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_data if json_data is not None else {}
    r.text = text
    return r


# ─── is_configured ───────────────────────────────────────────────

def test_not_configured_by_default(monkeypatch):
    monkeypatch.setattr(sig, "DOCUSEAL_API_TOKEN", "")
    assert sig.is_configured() is False
    out = sig.create_submission_from_pdf("Packet", b"%PDF", [{"role": "Buyer", "email": "a@b.com"}])
    assert out["success"] is False
    assert "not configured" in out["error"].lower()


def test_is_configured_true(configured):
    assert sig.is_configured() is True


# ─── create_submission_from_pdf ──────────────────────────────────

def test_create_submission_builds_correct_request(configured):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _resp(200, [{"submission_id": 4242, "slug": "abc", "email": "a@b.com"}])

    with patch.object(sig.requests, "post", side_effect=fake_post):
        out = sig.create_submission_from_pdf(
            "Closing Packet",
            b"%PDF-1.7 data",
            [{"role": "Buyer", "email": "a@b.com"}],
        )

    assert out["success"] is True
    assert out["submission_id"] == 4242
    # Endpoint + auth header
    assert captured["url"] == "https://sign.example.com/api/submissions/pdf"
    assert captured["headers"]["X-Auth-Token"] == "test-token"
    # PDF is base64-encoded in the documents array
    doc = captured["json"]["documents"][0]
    assert doc["file"] == base64.b64encode(b"%PDF-1.7 data").decode("ascii")
    assert captured["json"]["submitters"][0]["email"] == "a@b.com"


def test_create_submission_rejects_empty_pdf(configured):
    out = sig.create_submission_from_pdf("Packet", b"", [{"role": "Buyer", "email": "a@b.com"}])
    assert out["success"] is False
    assert "document" in out["error"].lower()


def test_create_submission_requires_signers(configured):
    out = sig.create_submission_from_pdf("Packet", b"%PDF", [])
    assert out["success"] is False
    assert "signer" in out["error"].lower()


def test_create_submission_handles_api_error(configured):
    with patch.object(sig.requests, "post", return_value=_resp(422, {}, "bad")):
        out = sig.create_submission_from_pdf("Packet", b"%PDF", [{"role": "Buyer", "email": "a@b.com"}])
    assert out["success"] is False
    assert "422" in out["error"]


def test_create_submission_handles_network_error(configured):
    with patch.object(sig.requests, "post", side_effect=sig.requests.RequestException("boom")):
        out = sig.create_submission_from_pdf("Packet", b"%PDF", [{"role": "Buyer", "email": "a@b.com"}])
    assert out["success"] is False
    assert "reach" in out["error"].lower()


# ─── get_submission ──────────────────────────────────────────────

def test_get_submission_returns_status(configured):
    with patch.object(sig.requests, "get", return_value=_resp(200, {"id": 1, "status": "completed"})):
        out = sig.get_submission(1)
    assert out["success"] is True
    assert out["status"] == "completed"


def test_get_submission_404(configured):
    with patch.object(sig.requests, "get", return_value=_resp(404, {})):
        out = sig.get_submission(999)
    assert out["success"] is False
    assert out.get("status_code") == 404


# ─── get_signed_document_urls ────────────────────────────────────

def test_get_signed_document_urls_dedupes():
    submission = {
        "documents": [{"url": "https://x/a.pdf"}],
        "submitters": [
            {"documents": [{"url": "https://x/a.pdf"}, {"url": "https://x/b.pdf"}]},
            {"documents": [{"url": "https://x/b.pdf"}]},
        ],
    }
    urls = sig.get_signed_document_urls(submission)
    assert urls == ["https://x/a.pdf", "https://x/b.pdf"]


def test_get_signed_document_urls_empty():
    assert sig.get_signed_document_urls({}) == []


# ─── verify_webhook_secret (security) ────────────────────────────

def test_verify_webhook_secret_false_when_unconfigured(monkeypatch):
    monkeypatch.setattr(sig, "DOCUSEAL_WEBHOOK_SECRET", "")
    assert sig.verify_webhook_secret("anything") is False


def test_verify_webhook_secret_rejects_missing_and_wrong(monkeypatch):
    monkeypatch.setattr(sig, "DOCUSEAL_WEBHOOK_SECRET", "s3cr3t")
    assert sig.verify_webhook_secret(None) is False
    assert sig.verify_webhook_secret("") is False
    assert sig.verify_webhook_secret("wrong") is False


def test_verify_webhook_secret_accepts_exact_match(monkeypatch):
    monkeypatch.setattr(sig, "DOCUSEAL_WEBHOOK_SECRET", "s3cr3t")
    assert sig.verify_webhook_secret("s3cr3t") is True
