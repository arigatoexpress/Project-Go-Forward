"""Direct tests for docuseal_service.py — the e-sign orchestration client.

Covers: PII redaction of vendor response bodies (email / phone / name
heuristics + truncation), template mapping cache + numeric fallback,
send_for_signature guards (unconfigured, blank-values refusal, unknown
template), success and error legs for both submission paths (including the
ESIGN consent prepend and its never-block-signing failure path),
submission-id extraction shapes, and archive_submission validation.

httpx is replaced with a fake client object on the module namespace, so no
network is touched.

Run: python -m pytest tests/test_docuseal_service.py -v
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import docuseal_service as ds  # noqa: E402

# ---------------------------------------------------------------------------
# Fake httpx
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text

    def json(self):
        return self._json


class FakeAsyncClient:
    """Records post/delete calls; replays a scripted response."""

    next_response = FakeResponse()
    calls: list = []

    def __init__(self, timeout=None):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, headers=None, json=None):
        FakeAsyncClient.calls.append({"method": "post", "url": url, "headers": headers, "json": json})
        if isinstance(FakeAsyncClient.next_response, Exception):
            raise FakeAsyncClient.next_response
        return FakeAsyncClient.next_response

    async def delete(self, url, headers=None):
        FakeAsyncClient.calls.append({"method": "delete", "url": url, "headers": headers})
        if isinstance(FakeAsyncClient.next_response, Exception):
            raise FakeAsyncClient.next_response
        return FakeAsyncClient.next_response


@pytest.fixture
def configured(monkeypatch):
    """Configured credentials + fake httpx; resets per-test call log."""
    monkeypatch.setattr(ds, "API_URL", "https://docuseal.example.com/")
    monkeypatch.setattr(ds, "API_TOKEN", "token-123")
    monkeypatch.setattr(ds, "httpx", SimpleNamespace(AsyncClient=FakeAsyncClient))
    FakeAsyncClient.calls = []
    FakeAsyncClient.next_response = FakeResponse(json_data={"ok": True})
    return FakeAsyncClient


@pytest.fixture
def mapped_templates(monkeypatch):
    monkeypatch.setattr(ds, "_template_cache", {"contract.pdf": {"docuseal_template_id": 42}})


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


class TestRedaction:
    def test_scrubs_email_phone_and_name_patterns(self):
        raw = (
            '{"email": "alice@example.com", "note": "call 281-324-3020", '
            '"name": "Alice Buyer", "submitter_name=John Doe"}'
        )
        redacted = ds._redact_docuseal_body(raw)
        assert "alice@example.com" not in redacted
        assert "281-324-3020" not in redacted
        assert "Alice Buyer" not in redacted
        assert "John Doe" not in redacted
        assert "[REDACTED_EMAIL]" in redacted
        assert "[REDACTED_PHONE]" in redacted
        assert redacted.count("[REDACTED_NAME]") == 2

    def test_truncates_long_bodies(self):
        raw = "x" * 500
        redacted = ds._redact_docuseal_body(raw)
        assert redacted.endswith("...[truncated]")
        assert len(redacted) < 220

    def test_handles_none(self):
        assert ds._redact_docuseal_body(None) == ""


# ---------------------------------------------------------------------------
# Template mapping
# ---------------------------------------------------------------------------


class TestTemplateMapping:
    def test_get_template_id_from_cache(self, mapped_templates):
        assert ds.get_template_id("contract.pdf") == 42
        assert ds.get_template_id("unknown.pdf") is None

    def test_load_templates_reads_mapping_file(self, monkeypatch, tmp_path):
        mapping = tmp_path / "docuseal_templates.json"
        mapping.write_text(json.dumps({"a.pdf": {"docuseal_template_id": 7}}))
        monkeypatch.setattr(ds, "MAPPING_FILE", str(mapping))
        monkeypatch.setattr(ds, "_template_cache", {})
        assert ds.get_template_id("a.pdf") == 7

    def test_load_templates_survives_corrupt_file(self, monkeypatch, tmp_path):
        mapping = tmp_path / "docuseal_templates.json"
        mapping.write_text("{not json")
        monkeypatch.setattr(ds, "MAPPING_FILE", str(mapping))
        monkeypatch.setattr(ds, "_template_cache", {})
        assert ds.get_template_id("a.pdf") is None


# ---------------------------------------------------------------------------
# send_for_signature
# ---------------------------------------------------------------------------


VALUES = {"buyer_name": "Jane", "price": "89900"}


class TestSendForSignature:
    @pytest.mark.asyncio
    async def test_not_configured(self, monkeypatch):
        monkeypatch.setattr(ds, "API_URL", "")
        result = await ds.send_for_signature(
            email="a@b.com", name="Jane", template_name="contract.pdf", values=VALUES
        )
        assert result["status"] == "not_configured"

    @pytest.mark.asyncio
    async def test_refuses_blank_values(self, configured):
        result = await ds.send_for_signature(
            email="a@b.com", name="Jane", template_name="contract.pdf", values=None
        )
        assert result["status"] == "missing_values"

    @pytest.mark.asyncio
    async def test_success_uses_mapped_template(self, configured, mapped_templates):
        result = await ds.send_for_signature(
            email="a@b.com",
            name="Jane",
            template_name="contract.pdf",
            deal_id="deal-1",
            metadata={"source": "test"},
            values=VALUES,
        )
        assert result["success"] is True
        (call,) = FakeAsyncClient.calls
        assert call["url"] == "https://docuseal.example.com/api/submissions"
        assert call["headers"]["X-Auth-Token"] == "token-123"
        payload = call["json"]
        assert payload["template_id"] == 42
        assert payload["submitters"][0]["values"] == VALUES
        assert payload["metadata"]["deal_id"] == "deal-1"
        assert payload["metadata"]["source"] == "test"

    @pytest.mark.asyncio
    async def test_numeric_template_name_fallback(self, configured):
        monkeypatch_cache = ds._template_cache
        ds._template_cache = {}
        try:
            result = await ds.send_for_signature(
                email="a@b.com", name="Jane", template_name="77", values=VALUES
            )
            assert result["success"] is True
            assert FakeAsyncClient.calls[0]["json"]["template_id"] == 77
        finally:
            ds._template_cache = monkeypatch_cache

    @pytest.mark.asyncio
    async def test_unknown_template_errors(self, configured, monkeypatch):
        monkeypatch.setattr(ds, "_template_cache", {})
        result = await ds.send_for_signature(
            email="a@b.com", name="Jane", template_name="ghost.pdf", values=VALUES
        )
        assert result["success"] is False
        assert "not found" in result["error"].lower()
        assert FakeAsyncClient.calls == []

    @pytest.mark.asyncio
    async def test_api_error_returns_request_id(self, configured, mapped_templates):
        FakeAsyncClient.next_response = FakeResponse(
            status_code=422, text="validation: email alice@example.com invalid for 281-324-3020"
        )
        result = await ds.send_for_signature(
            email="a@b.com", name="Jane", template_name="contract.pdf", deal_id="deal-1", values=VALUES
        )
        assert result["success"] is False
        assert result["status_code"] == 422
        assert result["request_id"].startswith("deal-1:")

    @pytest.mark.asyncio
    async def test_network_exception(self, configured, mapped_templates):
        FakeAsyncClient.next_response = ConnectionError("dns down")
        result = await ds.send_for_signature(
            email="a@b.com", name="Jane", template_name="contract.pdf", values=VALUES
        )
        assert result["success"] is False
        assert "dns down" in result["error"]


# ---------------------------------------------------------------------------
# send_file_for_signature
# ---------------------------------------------------------------------------


@pytest.fixture
def pdf_file(tmp_path):
    path = tmp_path / "packet.pdf"
    path.write_bytes(b"%PDF-fake-content")
    return path


@pytest.fixture
def consent_module(monkeypatch):
    module = types.ModuleType("esign_consent")
    module.build_consent_document = lambda: {"name": "ESIGN Consent", "file": "QkFTRTY0"}
    monkeypatch.setitem(sys.modules, "esign_consent", module)
    return module


class TestSendFileForSignature:
    @pytest.mark.asyncio
    async def test_not_configured(self, monkeypatch, pdf_file):
        monkeypatch.setattr(ds, "API_URL", "")
        result = await ds.send_file_for_signature(
            email="a@b.com", name="Jane", file_path=str(pdf_file), display_name="Packet"
        )
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_missing_file(self, configured):
        result = await ds.send_file_for_signature(
            email="a@b.com", name="Jane", file_path="/no/such.pdf", display_name="Packet"
        )
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_success_prepends_consent(self, configured, pdf_file, consent_module):
        result = await ds.send_file_for_signature(
            email="a@b.com",
            name="Jane",
            file_path=str(pdf_file),
            display_name="Closing Packet",
            deal_id="deal-9",
        )
        assert result["success"] is True
        (call,) = FakeAsyncClient.calls
        payload = call["json"]
        # Consent page first, then the real document.
        assert payload["documents"][0]["name"] == "ESIGN Consent"
        assert payload["documents"][1]["name"] == "Closing Packet"
        assert payload["metadata"]["esign_consent_included"] is True
        assert payload["metadata"]["is_custom_file"] is True
        assert payload["metadata"]["deal_id"] == "deal-9"

    @pytest.mark.asyncio
    async def test_consent_generation_failure_does_not_block(
        self, configured, pdf_file, consent_module
    ):
        def broken():
            raise RuntimeError("consent generator down")

        consent_module.build_consent_document = broken
        result = await ds.send_file_for_signature(
            email="a@b.com", name="Jane", file_path=str(pdf_file), display_name="Packet"
        )
        assert result["success"] is True
        payload = FakeAsyncClient.calls[0]["json"]
        assert len(payload["documents"]) == 1
        assert payload["metadata"]["esign_consent_included"] is False

    @pytest.mark.asyncio
    async def test_success_without_consent_flag(self, configured, pdf_file):
        result = await ds.send_file_for_signature(
            email="a@b.com",
            name="Jane",
            file_path=str(pdf_file),
            display_name="Packet",
            include_esign_consent=False,
        )
        assert result["success"] is True
        payload = FakeAsyncClient.calls[0]["json"]
        assert len(payload["documents"]) == 1

    @pytest.mark.asyncio
    async def test_api_error(self, configured, pdf_file, consent_module):
        FakeAsyncClient.next_response = FakeResponse(status_code=500, text="boom")
        result = await ds.send_file_for_signature(
            email="a@b.com", name="Jane", file_path=str(pdf_file), display_name="Packet"
        )
        assert result["success"] is False
        assert result["status_code"] == 500
        assert result["request_id"].endswith(":custom-file")


# ---------------------------------------------------------------------------
# is_configured / submission id extraction / archive
# ---------------------------------------------------------------------------


class TestConfigAndIds:
    def test_is_configured(self, monkeypatch):
        monkeypatch.setattr(ds, "API_URL", "")
        monkeypatch.setattr(ds, "API_TOKEN", "")
        assert ds.is_configured() is False
        monkeypatch.setattr(ds, "API_URL", "https://x")
        assert ds.is_configured() is False
        monkeypatch.setattr(ds, "API_TOKEN", "t")
        assert ds.is_configured() is True

    def test_submission_ids_from_submitters_list(self):
        submission = [{"submission_id": 5}, {"submission_id": "5"}, {"submission_id": 7}, "junk"]
        assert ds.submission_ids_from_response(submission) == ["5", "7"]

    def test_submission_ids_from_single_object(self):
        assert ds.submission_ids_from_response({"id": 9}) == ["9"]

    def test_submission_ids_empty(self):
        assert ds.submission_ids_from_response(None) == []
        assert ds.submission_ids_from_response({}) == []


class TestArchiveSubmission:
    @pytest.mark.asyncio
    async def test_not_configured(self, monkeypatch):
        monkeypatch.setattr(ds, "API_URL", "")
        result = await ds.archive_submission("123")
        assert result["status"] == "not_configured"

    @pytest.mark.asyncio
    async def test_rejects_non_numeric_id(self, configured):
        for bad in ("", "abc", "12;drop", None):
            result = await ds.archive_submission(bad)
            assert result["success"] is False
            assert "Invalid" in result["error"]
        assert FakeAsyncClient.calls == []

    @pytest.mark.asyncio
    async def test_success(self, configured):
        result = await ds.archive_submission(" 123 ")
        assert result["success"] is True
        (call,) = FakeAsyncClient.calls
        assert call["method"] == "delete"
        assert call["url"] == "https://docuseal.example.com/api/submissions/123"

    @pytest.mark.asyncio
    async def test_api_error(self, configured):
        FakeAsyncClient.next_response = FakeResponse(status_code=404, text="gone")
        result = await ds.archive_submission("123")
        assert result["success"] is False
        assert "404" in result["error"]

    @pytest.mark.asyncio
    async def test_network_exception(self, configured):
        FakeAsyncClient.next_response = ConnectionError("timeout")
        result = await ds.archive_submission("123")
        assert result["success"] is False
        assert "timeout" in result["error"]
