"""Tests for inventory/docs/e-sign/marketing coverage-gap targets (swarm C).

Targets (Python-3.12 honest-coverage campaign on main @ 46c8afa — see
docs/MAIN_COVERAGE_INVESTIGATION.md):

- ``list_inventory`` — is_new filter skip, unparseable-width fallback, outer
  failure envelope
- ``create_inventory_item`` / ``update_inventory_item`` /
  ``retire_inventory_item`` — failure / empty-payload / failure envelopes
- ``list_templates`` / ``document_readiness`` / ``get_all_field_definitions``
  — success + failure envelopes
- ``download_document`` / ``download_generated_video`` — resolved-path escape
  guards (forced via a scoped ``os.path.abspath`` patch)
- ``_collect_generated_documents`` — non-PDF skip, non-file skip, GCS duplicate
  skip
- ``_document_batch_failure_json_response`` — multi-failure message suffix and
  generic fallback message
- ``_packet_result_fields`` — legacy ``merged_document`` fallback
- ``_is_synthetic_document`` / ``_is_quarantined_generated_document`` /
  ``_document_history_min_created_at`` / ``_is_legacy_unverified_document``
  — pure-helper branches
- ``generate_document_from_deal`` — missing template_name, engine failure,
  outer envelope
- ``_public_esign_error`` — non-dict, credential-missing, default tokens
- ``_esign_request_collection`` / ``_save_esign_request`` /
  ``_load_esign_request`` / ``_list_esign_requests`` / ``_mark_esign_request``
  — Firestore-unavailable and in-memory paths
- ``_request_json_object`` — JSONDecodeError → {}
- ``api_generate_script`` / ``api_trending_ideas`` /
  ``api_prepare_social_draft`` / ``api_content_analytics`` /
  ``api_marketing_gcp_readiness`` — passthroughs and error envelopes
- ``_marketing_error_response`` — envelope shape
- ``sanitize_url`` — scheme-less URL short-circuit (via ``submit_feedback``)
- ``_get_partner_api_key`` / ``_json_safe`` — pure helpers
- ``v1_list_inventory`` / ``v1_service_request_resolve`` /
  ``get_secure_hub_deal`` — partner/customer-portal envelopes via TestClient

Handlers are called directly with a minimal request stand-in where the
suite's ``create_client`` body-schema swap would interfere; partner and
customer-portal routes go through TestClient. Audit helpers are no-op
patched so the Firestore emulator is never needed.

Run: python -m pytest tests/test_cov_c5_c_inventory_docs.py -v
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from json import JSONDecodeError
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(Path(__file__).parent))
from test_api_v1 import FakeQuery, create_client  # noqa: E402


class _Req:
    """Minimal request stand-in: ``await request.json()`` returns the payload."""

    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _client(monkeypatch):
    client, main, fake_db, _logger = create_client(monkeypatch)
    monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
    return client, main, fake_db


def _run(coro):
    return asyncio.run(coro)


def _json(resp):
    """Decode a JSONResponse/FastAPI response into (status, body)."""
    return resp.status_code, json.loads(resp.body.decode())


def _partner_headers() -> dict:
    return {"X-API-Key": "tho-secret"}


# ─── list_inventory ──────────────────────────────────────────────────────────


def _seed_inventory(fake_db):
    fake_db.collections["inventory"] = {
        "skip": {
            "id": "skip",
            "model_name": "Old Home",
            "is_new": False,
            "status": "AVAILABLE",
        },
        "badw": {
            "id": "badw",
            "model_name": "Wide Home",
            "is_new": True,
            "status": "AVAILABLE",
            "width": "not-a-number",
        },
        "wide": {
            "id": "wide",
            "model_name": "Big Home",
            "is_new": True,
            "status": "AVAILABLE",
            "width": 28,
        },
        "narrow": {
            "id": "narrow",
            "model_name": "Small Home",
            "is_new": True,
            "status": "AVAILABLE",
            "width": 16,
        },
    }


class TestListInventory:
    def test_is_new_filter_bad_width_and_width_inference(self, monkeypatch):
        _client_, main, fake_db = _client(monkeypatch)
        _seed_inventory(fake_db)
        # Media helpers are covered elsewhere; keep this test focused on the
        # classification/filter branches.
        monkeypatch.setattr(main, "_load_legacy_inventory_media_index", lambda: {})
        monkeypatch.setattr(
            main, "_apply_inventory_media_fallback", lambda result, item, index: result
        )
        monkeypatch.setattr(main, "annotate_possible_duplicates", lambda homes: homes)

        result = _run(main.list_inventory(is_new=True))

        assert result["success"] is True
        by_id = {item["id"]: item for item in result["inventory"]}
        assert "skip" not in by_id  # is_new mismatch → continue
        assert by_id["badw"]["classification"] == ""  # width parse failure → 0
        assert by_id["wide"]["classification"] == "Double Wide"
        assert by_id["narrow"]["classification"] == "Single Wide"

    def test_failure_envelope(self, monkeypatch):
        _client_, main, fake_db = _client(monkeypatch)

        def boom(**kwargs):
            raise RuntimeError("inventory backend down")

        monkeypatch.setattr(fake_db, "search_inventory", boom)

        result = _run(main.list_inventory())

        assert result == {
            "success": False,
            "error": "Failed to load inventory. Please try again.",
        }


# ─── create/update/retire inventory ──────────────────────────────────────────


class TestInventoryWriteEndpoints:
    def test_create_failure_envelope(self, monkeypatch):
        _client_, main, fake_db = _client(monkeypatch)

        def boom(data):
            raise RuntimeError("write denied")

        monkeypatch.setattr(fake_db, "create_inventory", boom)

        resp = _run(main.create_inventory_item(_Req({"model_name": "New Home"})))

        status, body = _json(resp)
        assert status == 400
        assert body == {"success": False, "error": "Failed to create inventory item."}

    def test_update_empty_payload_rejected(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        resp = _run(main.update_inventory_item("inv-1", _Req({})))

        status, body = _json(resp)
        assert status == 400
        assert body == {"success": False, "error": "No fields to update."}

    def test_retire_failure_envelope(self, monkeypatch):
        _client_, main, fake_db = _client(monkeypatch)

        def boom(inventory_id, data):
            raise RuntimeError("write denied")

        monkeypatch.setattr(fake_db, "update_inventory", boom)

        resp = _run(main.retire_inventory_item("inv-1", _Req({})))

        status, body = _json(resp)
        assert status == 400
        assert body == {"success": False, "error": "Failed to retire inventory item."}


# ─── document endpoints: templates / readiness / fields ──────────────────────


class TestDocumentListingEndpoints:
    def test_list_templates_failure_envelope(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        def boom():
            raise RuntimeError("engine missing")

        monkeypatch.setattr(main, "engine_list_templates", boom)

        result = _run(main.list_templates())

        assert result == {"error": "Failed to load templates. Please try again."}

    def test_document_readiness_failure_envelope(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        def boom():
            raise RuntimeError("engine missing")

        monkeypatch.setattr(main, "engine_list_templates", boom)

        resp = _run(main.document_readiness())

        status, body = _json(resp)
        assert status == 500
        assert body == {"status": "degraded", "error": "Document readiness check failed."}

    def test_get_all_field_definitions_success_and_failure(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        # tools.document_engine_v2 is not stubbed by load_app, so the real
        # field-definition catalog comes back; the success branch just
        # passes it through.
        result = _run(main.get_all_field_definitions())
        assert isinstance(result, dict)
        assert result

        def boom():
            raise RuntimeError("engine missing")

        monkeypatch.setattr(main, "engine_get_all_field_definitions", boom)

        result = _run(main.get_all_field_definitions())

        assert result == {"error": "Failed to load field definitions. Please try again."}


# ─── download_document / download_generated_video path-escape guards ─────────
#
# The ``resolved.startswith(os.path.abspath(OUTPUT_DIR))`` guard is unreachable
# through real paths (``safe_filename`` is a basename), so a scoped abspath
# patch makes the resolved path escape without touching main.py.


class TestDownloadPathGuards:
    def _patched_abspath(self, monkeypatch, main, marker: str):
        real_abspath = os.path.abspath

        def fake_abspath(path):
            if str(path).endswith(marker):
                return "/etc/escaped"
            return real_abspath(path)

        monkeypatch.setattr(main.os.path, "abspath", fake_abspath)

    def test_download_document_invalid_path_guard(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        self._patched_abspath(monkeypatch, main, "escape.pdf")

        resp = _run(main.download_document("escape.pdf"))

        status, body = _json(resp)
        assert status == 403
        assert body == {"error": "Invalid filename"}

    def test_download_generated_video_invalid_path_guard(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        self._patched_abspath(monkeypatch, main, "escape.mp4")

        resp = _run(main.download_generated_video("escape.mp4"))

        assert resp == {"error": "Invalid path"}


# ─── _collect_generated_documents ────────────────────────────────────────────


class TestCollectGeneratedDocuments:
    def test_skips_non_pdf_dirs_and_gcs_duplicates(self, monkeypatch, tmp_path):
        _client_, main, _db = _client(monkeypatch)
        docs_dir = tmp_path / "generated_docs"
        docs_dir.mkdir()
        (docs_dir / "notes.txt").write_text("not a pdf")
        (docs_dir / "folder.pdf").mkdir()  # .pdf name but a directory
        (docs_dir / "real.pdf").write_bytes(b"%PDF-1.4")
        monkeypatch.setattr(main, "OUTPUT_DIR", str(docs_dir))
        monkeypatch.setattr(
            main,
            "list_gcs_documents",
            lambda: [
                {"filename": "real.pdf", "created_at": "2026-06-01T00:00:00"},  # dup
                {"filename": None, "created_at": "2026-06-02T00:00:00"},
                {"filename": "gcs-only.pdf", "created_at": "2026-06-03T00:00:00"},
            ],
        )

        docs, local_count, gcs_count = main._collect_generated_documents()

        assert local_count == 1
        assert gcs_count == 3
        names = [doc["filename"] for doc in docs]
        assert names.count("real.pdf") == 1  # local wins; GCS duplicate skipped
        assert "gcs-only.pdf" in names


# ─── document batch/packet helpers ───────────────────────────────────────────


class TestDocumentBatchAndPacketHelpers:
    def test_batch_failure_multi_document_suffix(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        resp = main._document_batch_failure_json_response(
            {
                "documents": [
                    {"template_name": "Sales Contract", "success": False, "message": "no buyer"},
                    {"filename": " disclosures.pdf", "success": False, "error": "missing fields"},
                ]
            }
        )

        status, body = _json(resp)
        assert status == 400
        assert body["success"] is False
        assert body["message"] == "Sales Contract: no buyer (2 documents failed.)"

    def test_batch_failure_generic_fallback_message(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        resp = main._document_batch_failure_json_response({"total": 0, "successful": 0})

        status, body = _json(resp)
        assert status == 400
        assert body["message"] == (
            "No documents were generated. Review the selected forms and required fields."
        )

    def test_packet_result_fields_legacy_merged_document(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        fields = main._packet_result_fields(
            {
                "merged": "junk-not-a-dict",
                "merged_document": {"filename": "legacy.pdf", "page_count": 2},
            }
        )

        assert fields["filename"] == "legacy.pdf"
        assert fields["page_count"] == 2
        assert fields["download_url"] == "/api/documents/download/legacy.pdf"


# ─── document history flag helpers ───────────────────────────────────────────


class TestDocumentHistoryHelpers:
    def test_is_synthetic_document_non_pdf(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        assert main._is_synthetic_document("notes.txt") is False
        assert main._is_synthetic_document(None) is False
        assert main._is_synthetic_document("smoke_buyer_contract.pdf") is True

    def test_is_quarantined_generated_document_non_pdf(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        assert main._is_quarantined_generated_document("notes.txt") is False
        assert main._is_quarantined_generated_document("twopartycontract_copy.pdf") is True

    def test_min_created_at_default(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.delenv("DOCUMENT_HISTORY_MIN_CREATED_AT", raising=False)

        assert main._document_history_min_created_at() == main._DOCUMENT_HISTORY_DEFAULT_MIN_CREATED_AT

    def test_min_created_at_configured_valid(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.setenv("DOCUMENT_HISTORY_MIN_CREATED_AT", "2026-01-02T03:04:05Z")

        parsed = main._document_history_min_created_at()

        assert parsed == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    def test_min_created_at_configured_invalid_falls_back(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.setenv("DOCUMENT_HISTORY_MIN_CREATED_AT", "not-a-timestamp")

        assert main._document_history_min_created_at() == main._DOCUMENT_HISTORY_DEFAULT_MIN_CREATED_AT

    def test_is_legacy_unverified_document_branches(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        assert main._is_legacy_unverified_document(None) is False  # 3049
        assert main._is_legacy_unverified_document("not-a-date") is False  # 3052-3053
        # Naive pre-gate timestamp → tz assumed UTC → legacy.
        assert main._is_legacy_unverified_document("2020-01-01T00:00:00") is True  # 3054-3055
        # Post-gate timestamp → not legacy.
        assert main._is_legacy_unverified_document("2026-06-01T00:00:00Z") is False


# ─── generate_document_from_deal ─────────────────────────────────────────────


class _FakeDeal:
    """Stand-in for the Deal model: preserves constructor kwargs."""

    def __init__(self, **kwargs):
        self._kwargs = dict(kwargs)

    def model_dump(self):
        return dict(self._kwargs)

    def to_document_data(self):
        return dict(self._kwargs)


def _deal_doc_client(monkeypatch, deal_data, engine_result=None, engine_exc=None):
    _client_, main, _db = _client(monkeypatch)
    monkeypatch.setattr(main._deal_db, "get_deal", lambda deal_id: deal_data, raising=False)
    monkeypatch.setattr(main, "Deal", _FakeDeal)
    monkeypatch.setattr(main, "validate_for_documents", lambda data: {})
    monkeypatch.setattr(main, "_maybe_email_document", lambda **kwargs: None)

    def fake_engine(**kwargs):
        if engine_exc is not None:
            raise engine_exc
        return engine_result

    monkeypatch.setattr(main, "engine_generate_document", fake_engine)
    return main


class TestGenerateDocumentFromDeal:
    def test_missing_template_name(self, monkeypatch):
        main = _deal_doc_client(monkeypatch, deal_data={"id": "d1"})

        result = _run(main.generate_document_from_deal("d1", _Req({})))

        assert result == {"success": False, "error": "template_name is required"}

    def test_engine_failure_envelope(self, monkeypatch):
        main = _deal_doc_client(
            monkeypatch,
            deal_data={"id": "d1"},
            engine_result={"success": False, "message": "template exploded"},
        )

        result = _run(main.generate_document_from_deal("d1", _Req({"template_name": "sales"})))

        assert result == {"success": False, "error": "template exploded"}

    def test_outer_exception_envelope(self, monkeypatch):
        main = _deal_doc_client(
            monkeypatch,
            deal_data={"id": "d1"},
            engine_exc=RuntimeError("synthetic engine crash"),
        )

        result = _run(main.generate_document_from_deal("d1", _Req({"template_name": "sales"})))

        assert result == {
            "success": False,
            "error": "Failed to generate document from deal. Please try again.",
        }


# ─── e-sign helpers ──────────────────────────────────────────────────────────


class TestEsignHelpers:
    def test_public_esign_error_branches(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        assert main._public_esign_error("not-a-dict") == "dispatch_failed"
        assert main._public_esign_error({"error": "file not found"}) == "file_not_found"
        assert main._public_esign_error({"message": "credentials missing"}) == "not_configured"
        assert main._public_esign_error({}) == "dispatch_failed"

    def test_esign_request_collection_db_none(self, monkeypatch):
        _client_, main, fake_db = _client(monkeypatch)
        monkeypatch.setattr(fake_db, "db", None)

        assert main._esign_request_collection() is None

    def test_esign_request_collection_exception(self, monkeypatch):
        _client_, main, fake_db = _client(monkeypatch)

        def boom(name):
            raise RuntimeError("firestore down")

        monkeypatch.setattr(fake_db.db, "collection", boom)

        assert main._esign_request_collection() is None

    def test_save_esign_request_no_collection(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.setattr(main, "_esign_requests", {})
        monkeypatch.setattr(main, "_esign_request_collection", lambda: None)

        main._save_esign_request({"id": "r1", "deal_id": "d1"})

        assert main._esign_requests["r1"]["id"] == "r1"

    def test_save_esign_request_write_failure_warns(self, monkeypatch):
        client, main, _db = _client(monkeypatch)
        monkeypatch.setattr(main, "_esign_requests", {})

        class _BoomRef:
            def set(self, data, timeout=None):
                raise RuntimeError("write denied")

        class _BoomCollection:
            def document(self, doc_id):
                return _BoomRef()

        monkeypatch.setattr(main, "_esign_request_collection", lambda: _BoomCollection())

        main._save_esign_request({"id": "r2", "deal_id": "d1"})

        # In-memory record is saved even when the Firestore write fails.
        assert main._esign_requests["r2"]["id"] == "r2"
        assert client is not None  # keep the client alive for the app fixture

    def test_load_esign_request_fetch_failure_falls_back(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.setattr(main, "_esign_requests", {})

        class _BoomRef:
            def get(self, timeout=None):
                raise RuntimeError("read denied")

        class _BoomCollection:
            def document(self, doc_id):
                return _BoomRef()

        monkeypatch.setattr(main, "_esign_request_collection", lambda: _BoomCollection())

        assert main._load_esign_request("missing") is None

    def test_list_esign_requests_merges_collection(self, monkeypatch):
        _client_, main, fake_db = _client(monkeypatch)
        monkeypatch.setattr(main, "_esign_requests", {})
        fake_db.collections["esign_requests"] = {
            "d1_s1": {"id": "d1_s1", "deal_id": "deal-1", "created_at": "2026-06-02T00:00:00"},
            "d1_s2": {"id": "d1_s2", "deal_id": "deal-1", "created_at": "2026-06-01T00:00:00"},
            "d9_s9": {"id": "d9_s9", "deal_id": "deal-9", "created_at": "2026-06-03T00:00:00"},
        }
        # The shared FakeQuery only implements .get(); the esign list path
        # streams, so give it a stream facade for this test.
        monkeypatch.setattr(
            FakeQuery,
            "stream",
            lambda self, timeout=None: iter(self.get(timeout=timeout)),
            raising=False,
        )

        records = main._list_esign_requests("deal-1")

        assert [record["id"] for record in records] == ["d1_s1", "d1_s2"]

    def test_mark_esign_request_missing_record(self, monkeypatch):
        _client_, main, fake_db = _client(monkeypatch)
        monkeypatch.setattr(main, "_esign_requests", {})
        fake_db.collections["esign_requests"] = {}

        assert main._mark_esign_request("deal-1", "s1", "cancelled", actor="staff") is None


# ─── _request_json_object ────────────────────────────────────────────────────


class TestRequestJsonObject:
    def test_dict_passthrough(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        assert _run(main._request_json_object(_Req({"a": 1}))) == {"a": 1}

    def test_decode_error_returns_empty(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        assert _run(main._request_json_object(_Req(JSONDecodeError("bad", "doc", 0)))) == {}

    def test_non_dict_returns_empty(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        assert _run(main._request_json_object(_Req(["not", "a", "dict"]))) == {}


# ─── marketing endpoints ─────────────────────────────────────────────────────


class TestMarketingEndpoints:
    def test_generate_script_passthrough(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        result = _run(main.api_generate_script(_Req({"home_name": "Creole"})))

        assert result == {"success": True}

    def test_trending_ideas_passthrough(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        assert _run(main.api_trending_ideas()) == []

    def test_prepare_social_draft_non_dict_body(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        resp = _run(main.api_prepare_social_draft(_Req(["not", "a", "dict"])))

        status, body = _json(resp)
        assert status == 400
        assert body == {
            "success": False,
            "error": "The draft request must be a JSON object.",
            "error_code": "invalid_request",
        }

    def test_prepare_social_draft_failure_envelope(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.setattr(
            main, "schedule_social_post", lambda **kwargs: {"success": False, "error": "nope"}
        )

        resp = _run(main.api_prepare_social_draft(_Req({"platform": "tiktok"})))

        status, body = _json(resp)
        assert status == 500
        assert body["error_code"] == "draft_preparation_failed"

    def test_prepare_social_draft_invalid_json_reraises(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        with pytest.raises(JSONDecodeError):
            _run(main.api_prepare_social_draft(_Req(JSONDecodeError("bad", "doc", 0))))

    def test_content_analytics_passthrough(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        assert _run(main.api_content_analytics()) == {}

    def test_gcp_readiness_failure_envelope(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        def boom():
            raise RuntimeError("vertex down")

        monkeypatch.setattr(main, "get_gcp_ai_readiness", boom)

        result = _run(main.api_marketing_gcp_readiness())

        assert result == {
            "success": False,
            "ready": False,
            "error": "Failed to inspect GCP AI readiness",
        }

    def test_marketing_error_response_envelope(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        resp = main._marketing_error_response("boom", status_code=418, error_code="teapot")

        status, body = _json(resp)
        assert status == 418
        assert body == {"success": False, "error": "boom", "error_code": "teapot"}


# ─── sanitize_url (via submit_feedback) ──────────────────────────────────────


class TestSanitizeUrl:
    def test_scheme_less_url_short_circuit(self, monkeypatch, tmp_path):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.setattr(main, "log_user_action", lambda **kwargs: None)
        # Redirect the feedback log into tmp so the repo stays clean.
        monkeypatch.setattr(main, "__file__", str(tmp_path / "main.py"))

        result = _run(
            main.submit_feedback(
                _Req(
                    {
                        "description": "the floor plan page looks broken",
                        "url": "not-a-real-url",
                        "page": "/floorplans",
                    }
                )
            )
        )

        assert result["success"] is True

    def test_full_url_strips_query(self, monkeypatch, tmp_path):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.setattr(main, "log_user_action", lambda **kwargs: None)
        monkeypatch.setattr(main, "__file__", str(tmp_path / "main.py"))

        result = _run(
            main.submit_feedback(
                _Req(
                    {
                        "description": "broken image",
                        "url": "https://example.com/homes/1?tracking=abc&x=2",
                    }
                )
            )
        )

        assert result["success"] is True
        feedback_file = tmp_path / "data" / "feedback.jsonl"
        line = json.loads(feedback_file.read_text().strip().splitlines()[-1])
        assert line["url"] == "https://example.com/homes/1"


# ─── pure partner helpers ────────────────────────────────────────────────────


class TestPartnerHelpers:
    def test_get_partner_api_key_strips(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.setenv("THO_API_KEY", "  tho-secret  ")

        assert main._get_partner_api_key() == "tho-secret"

    def test_get_partner_api_key_missing(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.delenv("THO_API_KEY", raising=False)

        assert main._get_partner_api_key() == ""

    def test_json_safe_datetime_and_plain(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        stamp = datetime(2026, 6, 1, 12, 30, tzinfo=UTC)
        assert main._json_safe(stamp) == "2026-06-01T12:30:00+00:00"
        assert main._json_safe("plain") == "plain"
        assert main._json_safe(None) is None


# ─── partner / customer-portal routes via TestClient ─────────────────────────


class TestPartnerInventoryRoute:
    def test_list_inventory_failure_envelope(self, monkeypatch):
        client, main, fake_db = _client(monkeypatch)

        def boom(**kwargs):
            raise RuntimeError("inventory backend down")

        monkeypatch.setattr(fake_db, "search_inventory", boom)

        resp = client.get("/api/v1/inventory", headers=_partner_headers())

        assert resp.status_code == 500
        assert resp.json() == {"detail": "Failed to list inventory"}


class TestPartnerServiceRequestRoute:
    def test_resolve_update_failure(self, monkeypatch):
        client, main, fake_db = _client(monkeypatch)
        monkeypatch.setattr(fake_db, "update_service_request", lambda *a, **k: False)

        resp = client.post("/api/v1/service-requests/sr-1/resolve", headers=_partner_headers())

        assert resp.status_code == 500
        assert resp.json() == {"detail": "Failed to update service request"}

    def test_resolve_generic_exception(self, monkeypatch):
        client, main, fake_db = _client(monkeypatch)

        def boom(name):
            raise RuntimeError("firestore exploded")

        monkeypatch.setattr(fake_db.db, "collection", boom)

        resp = client.post("/api/v1/service-requests/sr-1/resolve", headers=_partner_headers())

        assert resp.status_code == 500
        assert resp.json() == {"detail": "Internal server error"}


class TestSecureHubDealRoute:
    def _seed_deal(self, fake_db):
        fake_db.collections["deals"]["deal-1"] = {
            "id": "deal-1",
            "status": "funded",
            "buyer_first_name": "Bob",
            "buyer_last_name": "Buyer",
            "buyer_phone": "512-555-1234",
            "inventory_model_name": "Creole 3256",
            "created_at": "2026-05-01T00:00:00",
        }
        fake_db.collections["deal_notes"] = {
            "n1": {
                "deal_id": "deal-1",
                "type": "esign_completed",
                "template_name": "Sales Contract",
                "created_at": "2026-06-01T00:00:00",
            },
            "n2": {
                "deal_id": "deal-1",
                "type": "document_generated",
                "filename": "closing_packet.pdf",
                "created_at": "2026-06-02T00:00:00",
            },
            "n9": {
                "deal_id": "deal-9",
                "type": "document_generated",
                "filename": "other.pdf",
                "created_at": "2026-06-03T00:00:00",
            },
        }

    def test_deal_with_documents_success(self, monkeypatch):
        client, main, fake_db = _client(monkeypatch)
        self._seed_deal(fake_db)
        monkeypatch.setattr(
            FakeQuery,
            "stream",
            lambda self, timeout=None: iter(self.get(timeout=timeout)),
            raising=False,
        )

        resp = client.get("/api/v1/customer/deal/deal-1?phone=1234")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["deal"]["buyer_name"] == "Bob Buyer"
        # Only deal-1 notes, newest first.
        assert [doc["id"] for doc in data["documents"]] == ["n2", "n1"]
        assert data["documents"][0]["status"] == "generated"
        assert data["documents"][1]["status"] == "signed"

    def test_deal_fetch_generic_exception(self, monkeypatch):
        client, main, fake_db = _client(monkeypatch)
        self._seed_deal(fake_db)

        def boom(name):
            raise RuntimeError("firestore exploded")

        monkeypatch.setattr(fake_db.db, "collection", boom)

        resp = client.get("/api/v1/customer/deal/deal-1?phone=1234")

        assert resp.status_code == 500
        assert resp.json() == {"detail": "firestore exploded"}

    def test_wrong_phone_is_indistinguishable_403(self, monkeypatch):
        client, main, fake_db = _client(monkeypatch)
        self._seed_deal(fake_db)

        resp = client.get("/api/v1/customer/deal/deal-1?phone=9999")

        assert resp.status_code == 403
        assert resp.json() == {"detail": main._PORTAL_VERIFY_MSG}

        resp_missing = client.get("/api/v1/customer/deal/nope?phone=1234")

        assert resp_missing.status_code == 403
        assert resp_missing.json() == {"detail": main._PORTAL_VERIFY_MSG}
