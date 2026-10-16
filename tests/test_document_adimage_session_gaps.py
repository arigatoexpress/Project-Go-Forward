"""Tests for three coverage-gap targets (Python-3.12 honest-coverage backlog).

Targets (see the 2026-10 3.12 re-measurement in docs/MAIN_COVERAGE_INVESTIGATION.md):

- ``POST /api/documents/generate`` — the document quality-gate failure
  response and both document-validation 400 envelopes (prefix present /
  prefix added). Invoked directly: the suite's ``create_client`` swaps the
  request body schema for ``DummyBodyModel``, so the failure paths are
  exercised by calling the endpoint coroutine with a plain namespace body
  (they never touch the request object)
- ``GET /api/marketing/images/{filename}`` — traversal rejection, the
  defense-in-depth resolved-path rejection, and the file-served / not-found
  responses (direct calls: TestClient normalizes ``..`` segments, so the
  filename checks are exercised by calling the endpoint coroutine directly)
- ``create_session`` — created / service-unavailable / generic-failure
  envelopes of the ADK session bootstrap (direct calls)

The documents route and the image route are admin-gated; ``download_ad_image``
and ``create_session`` take plain arguments, so both are invoked directly.
Audit helpers are no-op patched so the Firestore emulator is never needed.

Run: python -m pytest tests/test_document_adimage_session_gaps.py -v
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(Path(__file__).parent))
from test_api_v1 import create_client  # noqa: E402

# ─── POST /api/documents/generate ──────────────────────────────────────────


class TestGenerateDocumentEndpointGaps:
    def _call(self, monkeypatch, engine_result):
        _client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
        monkeypatch.setattr(main, "log_user_action", lambda **kwargs: None)
        monkeypatch.setattr(
            main,
            "engine_generate_document",
            lambda **kwargs: engine_result,
            raising=False,
        )
        body = types.SimpleNamespace(
            template_name="TMHA_SalesContract.pdf",
            data={"deal_id": "d-1"},
            customer_email=None,
            customer_name=None,
        )
        # Failure paths never read the request object.
        return asyncio.run(main.generate_document_endpoint(body, None))

    def test_quality_gate_failure_returns_structured_400(self, monkeypatch):
        resp = self._call(
            monkeypatch,
            {
                "success": False,
                "error": "quality_gate_failed",
                "message": "Document quality gate failed: 2 issue(s).",
                "quality_issues": ["buyer_name looks like a placeholder"],
                "missing_fields": ["buyer_phone"],
            },
        )

        assert resp.status_code == 400
        body = json.loads(resp.body)
        assert body["success"] is False
        assert body["error"] == "quality_gate_failed"
        assert body["quality_issues"] == ["buyer_name looks like a placeholder"]
        assert body["missing_fields"] == ["buyer_phone"]

    def test_validation_failure_with_prefix_passes_message_through(self, monkeypatch):
        resp = self._call(
            monkeypatch,
            {
                "success": False,
                "error": "engine_error",
                "message": "Missing required fields: buyer_name, buyer_phone",
            },
        )

        assert resp.status_code == 400
        assert json.loads(resp.body) == {
            "success": False,
            "error": "missing_required_fields",
            "message": "Missing required fields: buyer_name, buyer_phone",
        }

    def test_validation_failure_gets_prefix_added(self, monkeypatch):
        resp = self._call(
            monkeypatch,
            {"success": False, "error": "engine_error", "message": "Validation failed: bad VIN"},
        )

        assert resp.status_code == 400
        assert json.loads(resp.body) == {
            "success": False,
            "error": "missing_required_fields",
            "message": "Missing required fields. Validation failed: bad VIN",
        }

    def test_generic_engine_failure_returns_error_message(self, monkeypatch):
        result = self._call(
            monkeypatch,
            {"success": False, "error": "engine_error", "message": "Template not mapped."},
        )

        assert result == {"success": False, "error": "Template not mapped."}

    def test_engine_exception_returns_generic_failure_envelope(self, monkeypatch):
        _client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
        monkeypatch.setattr(main, "log_user_action", lambda **kwargs: None)

        def _boom(**kwargs):
            raise RuntimeError("synthetic engine detail")

        monkeypatch.setattr(main, "engine_generate_document", _boom, raising=False)
        body = types.SimpleNamespace(
            template_name="TMHA_SalesContract.pdf",
            data={},
            customer_email=None,
            customer_name=None,
        )

        result = asyncio.run(main.generate_document_endpoint(body, None))

        assert result == {
            "success": False,
            "error": "Document generation failed. Please try again.",
        }


# ─── GET /api/marketing/images/{filename} ──────────────────────────────────


class TestDownloadAdImage:
    def test_traversal_filename_rejected_400(self, monkeypatch):
        _client, main, _db, _logger = create_client(monkeypatch)

        resp = asyncio.run(main.download_ad_image("../secrets.png"))

        assert resp.status_code == 400
        assert b"Invalid filename" in resp.body

    def test_nested_path_rejected_400(self, monkeypatch):
        _client, main, _db, _logger = create_client(monkeypatch)

        resp = asyncio.run(main.download_ad_image("sub/dir/ad.png"))

        assert resp.status_code == 400
        assert b"Invalid filename" in resp.body

    def test_resolved_path_outside_dir_rejected_403(self, monkeypatch):
        _client, main, _db, _logger = create_client(monkeypatch)
        # Defense in depth: even a basename-clean filename must not serve when
        # the resolved path escapes the ads directory.
        monkeypatch.setattr(
            main.os.path,
            "join",
            lambda *parts: "/somewhere/outside/ad.png",
        )

        resp = asyncio.run(main.download_ad_image("ad.png"))

        assert resp.status_code == 403
        assert b"Invalid filename" in resp.body

    def test_existing_file_is_served(self, monkeypatch, tmp_path):
        _client, main, _db, _logger = create_client(monkeypatch)
        ads_dir = tmp_path / "generated_ads"
        ads_dir.mkdir()
        (ads_dir / "ad-1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        monkeypatch.setattr(main, "GENERATED_ADS_DIR", str(ads_dir))

        resp = asyncio.run(main.download_ad_image("ad-1.png"))

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/png")

    def test_missing_file_returns_404(self, monkeypatch, tmp_path):
        _client, main, _db, _logger = create_client(monkeypatch)
        ads_dir = tmp_path / "generated_ads"
        ads_dir.mkdir()
        monkeypatch.setattr(main, "GENERATED_ADS_DIR", str(ads_dir))

        resp = asyncio.run(main.download_ad_image("nope.png"))

        assert resp.status_code == 404
        assert b"Image not found" in resp.body


# ─── create_session (ADK runner bootstrap) ─────────────────────────────────


class TestCreateSession:
    def _runner_with(self, monkeypatch, main, error=None):
        async def create_session(**kwargs):
            if error is not None:
                raise error
            return object()

        runner = types.SimpleNamespace(
            session_service=types.SimpleNamespace(create_session=create_session)
        )
        monkeypatch.setattr(main, "_get_runner", lambda: runner)
        return runner

    def test_session_created(self, monkeypatch):
        _client, main, _db, _logger = create_client(monkeypatch)
        self._runner_with(monkeypatch, main)

        result = asyncio.run(main.create_session("tho-app", "user-1", "sess-1"))

        assert result == {"status": "created", "session_id": "sess-1"}

    def test_runtime_error_is_service_unavailable(self, monkeypatch):
        _client, main, _db, _logger = create_client(monkeypatch)
        self._runner_with(monkeypatch, main, error=RuntimeError("runner wedged"))

        result = asyncio.run(main.create_session("tho-app", "user-1", "sess-1"))

        assert result == {"status": "error", "message": "AI service temporarily unavailable."}

    def test_generic_error_is_retry_message(self, monkeypatch):
        _client, main, _db, _logger = create_client(monkeypatch)
        self._runner_with(monkeypatch, main, error=ValueError("bad app name"))

        result = asyncio.run(main.create_session("tho-app", "user-1", "sess-1"))

        assert result == {
            "status": "error",
            "message": "Failed to create session. Please try again.",
        }
