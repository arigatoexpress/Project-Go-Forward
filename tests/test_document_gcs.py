"""Tests for document generation and optional GCS persistence.

Verifies PDF generation, XFA filling, and edge cases. Live GCS checks are
opt-in so normal pytest runs cannot pollute the production document bucket.

Run: python -m pytest tests/test_document_gcs.py -v
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestDocumentTools:
    """Test document_tools module functions."""

    def test_output_dir_exists(self):
        from tools.document_tools import OUTPUT_DIR
        assert os.path.isdir(OUTPUT_DIR) or OUTPUT_DIR.startswith("/tmp")

    def test_documents_dir_has_templates(self):
        from tools.document_tools import DOCUMENTS_DIR
        if os.path.isdir(DOCUMENTS_DIR):
            pdfs = [f for f in os.listdir(DOCUMENTS_DIR) if f.endswith(".pdf")]
            assert len(pdfs) >= 60

    def test_fill_pdf_form_creates_file(self):
        from tools.document_tools import fill_pdf_form, DOCUMENTS_DIR
        template = os.path.join(DOCUMENTS_DIR, "TDHCA_1038_Consumer_Disclosure.pdf")
        if not os.path.exists(template):
            pytest.skip("Template not available locally")

        output = fill_pdf_form(template, {"buyer_name": "Test Fill"}, "test_fill_output.pdf")
        assert os.path.exists(output)
        assert os.path.getsize(output) > 1000
        # Cleanup
        os.remove(output)

    def test_fill_pdf_form_summary_fallback(self):
        """If template has no forms, should create summary PDF."""
        from tools.document_tools import OUTPUT_DIR
        # Use a known simple PDF that might not have forms
        from tools.document_tools import create_summary_pdf
        out_path = os.path.join(OUTPUT_DIR, "test_summary.pdf")
        create_summary_pdf({"buyer_name": "Summary Test", "price": "50000"}, out_path)
        assert os.path.exists(out_path)
        assert os.path.getsize(out_path) > 500
        os.remove(out_path)

    def test_fill_pdf_form_creates_missing_output_dir(self, monkeypatch, tmp_path):
        """Fresh worktrees may not have the ignored generated-docs directory."""
        from reportlab.pdfgen import canvas
        import tools.document_tools as document_tools

        template_path = tmp_path / "blank-template.pdf"
        c = canvas.Canvas(str(template_path))
        c.drawString(72, 720, "Blank template")
        c.save()

        output_dir = tmp_path / "missing" / "generated_docs"
        monkeypatch.setattr(document_tools, "OUTPUT_DIR", str(output_dir))

        output = document_tools.fill_pdf_form(
            str(template_path),
            {"buyer_name": "Test Fill"},
            "created_from_missing_dir.pdf",
        )

        assert output_dir.is_dir()
        assert os.path.exists(output)
        assert os.path.getsize(output) > 500


class TestGCSFunctions:
    """Test GCS upload/download/list functions."""

    pytestmark = pytest.mark.skipif(
        os.getenv("RUN_LIVE_GCS_TESTS") != "1",
        reason="Live GCS checks are opt-in; set RUN_LIVE_GCS_TESTS=1.",
    )

    def test_gcs_bucket_accessible(self):
        """GCS bucket should be accessible (may fail in CI without credentials)."""
        from tools.document_tools import _get_gcs_bucket
        bucket = _get_gcs_bucket()
        if bucket is None:
            pytest.skip("GCS not available (no credentials)")
        assert bucket.name == "tho-secure-documents"

    def test_gcs_list_documents(self):
        """List should return documents or empty list."""
        from tools.document_tools import list_gcs_documents
        docs = list_gcs_documents()
        assert isinstance(docs, list)
        # Should have at least one doc from our earlier E2E test
        if docs:
            assert "filename" in docs[0]
            assert docs[0]["filename"].endswith(".pdf")

    def test_gcs_download_nonexistent(self):
        """Downloading a nonexistent file should return False."""
        from tools.document_tools import download_from_gcs
        result = download_from_gcs("totally_fake_file_999.pdf", "/tmp/fake_download.pdf")
        assert result is False


class TestDocumentEngine:
    """Test the document_engine module."""

    def test_list_templates(self):
        from tools.document_engine import list_available_templates
        templates = list_available_templates()
        assert len(templates) >= 60
        # Each template should have required fields
        for t in templates[:5]:
            assert "template_name" in t
            assert "category" in t

    def test_generate_with_valid_data(self):
        from tools.document_engine import generate_document
        result = generate_document("TDHCA_1038_Consumer_Disclosure.pdf", {
            "buyer_name": "Test Engine",
            "buyer_address": "123 Test",
            "buyer_city": "Houston",
            "buyer_county": "Harris",
            "buyer_state": "TX",
            "buyer_zip": "77001",
            "manufacturer": "Clayton",
            "model": "Breeze",
            "serial_number_1": "TEST-ENG-001",
            "sales_price": "50000",
        })
        assert result["success"], f"Failed: {result}"
        # Cleanup
        if os.path.exists(result.get("file_path", "")):
            os.remove(result["file_path"])

    def test_generate_missing_required_fields(self):
        from tools.document_engine import generate_document
        result = generate_document("TMHA_SalesContract.pdf", {
            "buyer_name": "Incomplete",
        })
        assert not result["success"]
        assert "required" in result.get("message", "").lower()

    def test_generate_nonexistent_template(self):
        from tools.document_engine import generate_document
        result = generate_document("NONEXISTENT_TEMPLATE.pdf", {"buyer_name": "Test"})
        assert not result["success"]

    def test_template_field_definitions(self):
        """Field map should provide field definitions for each template."""
        from config.field_map_loader import get_template_config
        config = get_template_config("TMHA_SalesContract.pdf")
        assert config is not None
        assert "sections" in config or "fields" in config or "required_fields" in config
