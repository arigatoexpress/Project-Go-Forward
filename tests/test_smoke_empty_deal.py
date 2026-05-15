"""Regression tests for the "empty Joe Blo" Document Center bug.

Mark reported on 2026-04-27 that the Document Center generated an empty
docs package for a fictitious customer ("Joe Blo") with no real data, and
the response looked like a successful generation. PRs #13/#16 smoothed
customer lookup, but no test pinned the desired behavior. These tests
lock it down so the regression cannot reappear silently.

Cases:
  a. ``test_empty_deal_returns_400_not_empty_pdf``
     POST a minimally-empty deal payload to ``/api/documents/generate``
     and assert: response is HTTP 400 with a structured error envelope,
     NOT a 200 with an empty PDF download URL.
  b. ``test_partial_deal_only_renders_provided_fields``
     POST a deal with name only, no SSN/address/dates. The generated
     PDF must contain the supplied name and leave missing fields empty
     (never garbage placeholders or "None"/"null"/leftover sample data).
  c. ``test_doc_packet_skips_unfilled_templates``
     For a multi-PDF packet, templates with insufficient data must be
     skipped (not emitted as empty), and the response must list which
     templates were skipped.

Run: ``python -m pytest tests/test_smoke_empty_deal.py -v``
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ─────────────────────────────────────────────────────────────────────────────
# Test helpers — adapted from tests/test_deal_document_validation.py
# ─────────────────────────────────────────────────────────────────────────────


def _stub_admin_token(monkeypatch, main_module) -> str:
    """Bypass admin auth so the tests focus on document validation."""
    monkeypatch.setattr(main_module, "_verify_admin_token", lambda token: True)
    return "test-admin-token"


def _build_test_client(monkeypatch):
    """Import ``main`` with Firestore + the document engine patched out.

    Mirrors the strategy from ``test_deal_document_validation.py``. Static
    files / firestore are stubbed so the import succeeds in CI.
    """
    frontend_dist = REPO_ROOT / "frontend" / "dist"
    (frontend_dist / "assets").mkdir(parents=True, exist_ok=True)
    index_html = frontend_dist / "index.html"
    if not index_html.exists():
        index_html.write_text("<html><body>test</body></html>")

    fake_db = types.SimpleNamespace(get_deal=lambda deal_id: None)

    class _FakeFirestoreClient:
        def __init__(self, *args, **kwargs):
            pass

        def collection(self, *args, **kwargs):  # pragma: no cover - unused here
            raise RuntimeError("Tests must not exercise live Firestore queries")

    from google.cloud import firestore as _firestore_module

    monkeypatch.setattr(_firestore_module, "Client", _FakeFirestoreClient)

    fake_firestore_client_module = types.ModuleType("database.firestore_client")
    fake_firestore_client_module.get_database = lambda: fake_db
    fake_firestore_client_module.THODatabase = type("THODatabase", (), {})
    monkeypatch.setitem(sys.modules, "database.firestore_client", fake_firestore_client_module)

    sys.modules.pop("main", None)
    main_module = importlib.import_module("main")
    monkeypatch.setattr(main_module, "_db", fake_db)
    monkeypatch.setattr(main_module, "_deal_db", fake_db)

    token = _stub_admin_token(monkeypatch, main_module)
    client = TestClient(main_module.app)
    return client, main_module, token


# ─────────────────────────────────────────────────────────────────────────────
# (a) Empty deal → 400 with structured error envelope
# ─────────────────────────────────────────────────────────────────────────────


class TestEmptyDealReturns400:
    """The Document Center must reject empty deals explicitly, not silently
    produce a near-empty PDF that looks like a real document."""

    def test_empty_deal_returns_400_not_empty_pdf(self, monkeypatch):
        """``POST /api/documents/generate`` with an empty data dict must
        return HTTP 400 with the structured ``missing_required_fields``
        envelope. It must NOT return a 200 with a download URL.
        """
        client, main_module, token = _build_test_client(monkeypatch)

        # Engine should never be allowed to "succeed" on an empty payload —
        # if it does, the test should fail loudly. A real engine call here
        # is fine because the production engine itself rejects missing
        # required fields; we just guard against accidental "success".
        original_engine = main_module.engine_generate_document

        def _guarded_engine(*, template_name: str, data: dict):
            result = original_engine(template_name=template_name, data=data)
            if result.get("success"):
                raise AssertionError(
                    "engine_generate_document returned success for an empty " f"payload: {result!r}"
                )
            return result

        monkeypatch.setattr(main_module, "engine_generate_document", _guarded_engine)

        response = client.post(
            "/api/documents/generate",
            headers={"X-Admin-Token": token},
            json={
                "template_name": "TMHA_SalesContract.pdf",
                "data": {},  # the "Joe Blo" empty payload
            },
        )

        assert (
            response.status_code == 400
        ), f"Expected 400 for empty deal, got {response.status_code}: {response.text}"
        body = response.json()
        assert body.get("success") is False
        assert (
            body.get("error") == "missing_required_fields"
        ), f"Expected structured error envelope, got: {body!r}"
        # The structured response must NOT include any download_url —
        # that would be the exact regression we are guarding against.
        assert "download_url" not in body, f"Empty deal must not expose a download URL: {body!r}"
        assert "filename" not in body
        # Helpful detail for the operator.
        assert "message" in body and "Missing required fields" in body["message"]


# ─────────────────────────────────────────────────────────────────────────────
# (b) Partial deal renders only what was provided
# ─────────────────────────────────────────────────────────────────────────────


class TestPartialDealOnlyRendersProvided:
    """A deal that provides only a buyer name should generate a PDF where
    the name appears where expected, and missing fields render as empty
    strings or explicit blank markers — never garbage like 'None', 'null',
    or leftover sample data from a previous fill."""

    def _xfa_datasets_text(self, file_path: str) -> str:
        """Return the XFA datasets payload (XML) as text, or '' if absent."""
        from pypdf import PdfReader
        from pypdf.generic import ArrayObject

        reader = PdfReader(file_path)
        acroform = reader.trailer["/Root"].get("/AcroForm", {})
        xfa = acroform.get("/XFA")
        if not xfa:
            return ""
        if isinstance(xfa, ArrayObject):
            for i in range(0, len(xfa), 2):
                if str(xfa[i]) == "datasets":
                    return xfa[i + 1].get_object().get_data().decode("utf-8", errors="replace")
        return ""

    def test_partial_deal_only_renders_provided_fields(self, tmp_path, monkeypatch):
        """Direct call to the document engine with only ``buyer_name`` and
        ``seller_name``. Expect: success, the supplied names appear in the
        PDF, and no garbage placeholder strings leak into other fields.
        """
        from tools import document_engine
        from tools.document_engine import generate_document

        # Engine writes alongside other generated docs; redirect to tmp_path
        # so the test does not pollute ``data/generated_docs``.
        monkeypatch.setattr(document_engine, "OUTPUT_DIR", str(tmp_path))
        # GCS upload is a no-op in this test.
        monkeypatch.setattr(document_engine, "upload_to_gcs", lambda *args, **kw: None)

        # Pick a template whose required_fields are minimal so we can prove
        # the "only provided fields render" property without conflating
        # with required-field validation.
        result = generate_document(
            "TDHCA_1038_Consumer_Disclosure.pdf",
            {
                "buyer_name": "Smoke Buyer",
                "seller_name": "Texas Home Outlet",
            },
        )

        assert result["success"], f"Generation failed: {result}"
        assert Path(result["file_path"]).exists(), "PDF not created on disk"
        assert Path(result["file_path"]).stat().st_size > 1000, "PDF too small to be real"

        xfa_text = self._xfa_datasets_text(result["file_path"])
        assert "Smoke Buyer" in xfa_text, "Provided buyer_name missing from XFA"
        assert "Texas Home Outlet" in xfa_text, "Provided seller_name missing from XFA"

        # Garbage placeholder check — none of these strings should leak
        # into a generated PDF for fields the caller did NOT provide.
        garbage_markers = (
            "None",  # Python repr of None
            "null",  # JSON-style null
            "undefined",  # JS undefined
            "<built-in",  # repr leak of a builtin
            "object at 0x",  # repr leak of a Python object
            "{{",  # unrendered Jinja-style placeholder
            "}}",  # unrendered Jinja-style placeholder
            "Joe Blo",  # the canonical sample-data name from the bug
        )
        for marker in garbage_markers:
            assert marker not in xfa_text, (
                f"Garbage placeholder {marker!r} leaked into generated PDF — "
                "missing fields must render empty, never as placeholders or "
                "leftover sample data."
            )

    def test_format_value_returns_empty_for_blank_inputs(self):
        """Unit-level guard: ``_format_value`` must never return a
        garbage string for ``None`` / empty input — it must return ''."""
        from tools.document_engine import _format_value

        for bad_input in (None, "", "   "):
            for field_def in (
                {"type": "string"},
                {"type": "currency"},
                {"type": "date"},
            ):
                rendered = _format_value(bad_input, field_def)
                # Whitespace strings are rendered as-is by the formatter;
                # we only assert the None/empty case here.
                if bad_input in (None, ""):
                    assert rendered == "", (
                        f"_format_value({bad_input!r}, {field_def!r}) leaked "
                        f"non-empty value: {rendered!r}"
                    )
                # Critically, no value ever becomes a garbage placeholder.
                assert "None" not in rendered
                assert "null" not in rendered


# ─────────────────────────────────────────────────────────────────────────────
# (c) Multi-PDF packet skips templates with insufficient data
# ─────────────────────────────────────────────────────────────────────────────


class TestPacketSkipsUnfilledTemplates:
    """A packet generated from a thin deal must skip templates that lack
    required data, never emit them as empty PDFs, and report which
    templates were skipped so the operator can react."""

    @staticmethod
    def _write_pdf(path: Path) -> None:
        """Write a tiny valid PDF (the engine merger needs a real PDF)."""
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        with open(path, "wb") as f:
            writer.write(f)

    def test_doc_packet_skips_unfilled_templates(self, monkeypatch, tmp_path):
        """Build a fake 3-template packet: one template succeeds, two
        fail validation. The packet response must:
          * include only the successful template in ``documents_included``
          * list both failures in ``documents_skipped`` with reasons
          * still report ``success=True`` because at least one made it
        """
        from tools import document_engine

        monkeypatch.setattr(document_engine, "OUTPUT_DIR", str(tmp_path))
        monkeypatch.setattr(document_engine, "upload_to_gcs", lambda *a, **kw: None)
        monkeypatch.setattr(
            document_engine,
            "get_packet_config",
            lambda _name: {
                "display_name": "Smoke Packet",
                "templates": ["good.pdf", "needs_more_a.pdf", "needs_more_b.pdf"],
                "include_cover_page": False,
            },
        )
        monkeypatch.setattr(
            document_engine,
            "get_template_config",
            lambda name: {"display_name": name},
        )

        def fake_generate_document(template_name, data, output_filename=None):
            if template_name == "good.pdf":
                filename = output_filename or "good.pdf"
                pdf_path = tmp_path / filename
                self._write_pdf(pdf_path)
                return {
                    "success": True,
                    "file_path": str(pdf_path),
                    "filename": filename,
                    "message": "ok",
                }
            return {
                "success": False,
                "message": f"Missing required fields: buyer_address, manufacturer (template={template_name})",
            }

        monkeypatch.setattr(document_engine, "generate_document", fake_generate_document)

        result = document_engine.generate_packet(
            "smoke_packet",
            {"buyer_name": "Smoke Buyer"},
        )

        assert result["success"] is True, f"Packet failed unexpectedly: {result}"
        assert result["documents_included"] == ["good.pdf"]
        skipped = result.get("documents_skipped", [])
        assert isinstance(skipped, list), "documents_skipped must be a list"
        skipped_templates = sorted(item["template"] for item in skipped)
        assert skipped_templates == [
            "needs_more_a.pdf",
            "needs_more_b.pdf",
        ], f"Expected both bad templates skipped, got {skipped_templates!r}"
        for entry in skipped:
            assert (
                "reason" in entry and entry["reason"]
            ), f"Skipped template {entry!r} must include a non-empty reason"
            assert "Missing required fields" in entry["reason"]

    def test_packet_endpoint_surfaces_documents_skipped(self, monkeypatch):
        """``POST /api/documents/generate-packet`` must propagate the
        ``documents_skipped`` field to the API response so the frontend
        and operator can display which templates were dropped."""
        client, main_module, token = _build_test_client(monkeypatch)

        def fake_engine(*, packet_name: str, data: dict):
            return {
                "success": True,
                "filename": "packet.pdf",
                "message": "ok",
                "page_count": 1,
                "documents_included": ["TMHA_SalesContract.pdf"],
                "documents_skipped": [
                    {
                        "template": "TDHCA_1056_Rescission_Waiver.pdf",
                        "reason": "Missing required fields: buyer_address",
                    }
                ],
            }

        monkeypatch.setattr(main_module, "engine_generate_packet", fake_engine)

        response = client.post(
            "/api/documents/generate-packet",
            headers={"X-Admin-Token": token},
            json={"packet_name": "standard_closing", "data": {"buyer_name": "Smoke"}},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True
        assert body["documents_included"] == ["TMHA_SalesContract.pdf"]
        assert body["documents_skipped"] == [
            {
                "template": "TDHCA_1056_Rescission_Waiver.pdf",
                "reason": "Missing required fields: buyer_address",
            }
        ]

    def test_packet_endpoint_accepts_v2_engine_response_shape(self, monkeypatch):
        """The API must normalize document_engine_v2 packet results.

        The v2 engine returns a ``merged`` object plus per-template document
        rows, while the legacy endpoint used top-level ``filename`` and
        ``documents_included`` fields. Production callers need the endpoint
        to support both shapes.
        """
        client, main_module, token = _build_test_client(monkeypatch)

        def fake_engine(*, packet_name: str, data: dict):
            return {
                "success": True,
                "documents": [
                    {"template_name": "TMHA_SalesContract.pdf", "success": True},
                    {"template_name": "TDHCA_1038_Consumer_Disclosure.pdf", "success": True},
                ],
                "merged": {
                    "filename": "Documents_Smoke_Buyer_20260515.pdf",
                    "download_url": "/api/documents/download/Documents_Smoke_Buyer_20260515.pdf",
                    "page_count": 4,
                },
            }

        monkeypatch.setattr(main_module, "engine_generate_packet", fake_engine)

        response = client.post(
            "/api/documents/generate-packet",
            headers={"X-Admin-Token": token},
            json={"packet_name": "standard_closing", "data": {"buyer_name": "Smoke Buyer"}},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True
        assert body["filename"] == "Documents_Smoke_Buyer_20260515.pdf"
        assert body["download_url"] == "/api/documents/download/Documents_Smoke_Buyer_20260515.pdf"
        assert body["page_count"] == 4
        assert body["documents_included"] == [
            "TMHA_SalesContract.pdf",
            "TDHCA_1038_Consumer_Disclosure.pdf",
        ]
        assert body["documents_skipped"] == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
