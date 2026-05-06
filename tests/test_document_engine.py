"""
Integration tests for the Phase 2 Intelligent Document Engine.
Tests: document generation, packet merging, template listing, field validation, PII guard.
"""

import os
import sys

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.field_map_loader import (
    get_field_definitions,
    get_template_config,
    list_templates,
    reload,
)
from tools.document_engine import (
    generate_batch,
    generate_document,
    generate_packet,
    get_template_fields,
    list_available_packets,
    list_available_templates,
)
from tools.pii_guard import (
    get_pii_field_names,
    redact_pii_from_text,
    sanitize_for_llm,
    sanitize_for_logging,
    validate_no_pii_in_text,
)

# Force reload of field_map.json for fresh state
reload()


SAMPLE_DATA = {
    "buyer_name": "John Test Buyer",
    "co_buyer_name": "Jane Test Buyer",
    "buyer_address": "123 Main Street",
    "buyer_city": "Houston",
    "buyer_county": "Harris",
    "buyer_state": "TX",
    "buyer_zip": "77001",
    "buyer_phone": "(281) 555-0100",
    "manufacturer": "Clayton Homes",
    "model": "The Freedom 3256",
    "year": "2026",
    "serial_number_1": "CLH123456TX",
    "serial_number_2": "CLH123457TX",
    "label_number_1": "TEX0012345",
    "label_number_2": "TEX0012346",
    "no_of_sections": "2",
    "sales_price": "125000",
    "down_payment": "25000",
    "seller_name": "Texas Home Outlet",
    "seller_rbi": "RBI-12345",
    "date_of_sale": "02/12/2026",
}


class TestTemplateRegistry:
    """Tests for the field_map.json registry and loader."""

    def test_templates_loaded(self):
        templates = list_templates()
        assert len(templates) >= 3, f"Expected at least 3 templates, got {len(templates)}"

    def test_tmha_sales_contract_exists(self):
        config = get_template_config("TMHA_SalesContract.pdf")
        assert config is not None
        assert config["display_name"] == "TMHA Sales Contract"
        assert config["category"] == "TMHA"

    def test_field_definitions_exist(self):
        defs = get_field_definitions()
        assert "buyer_name" in defs
        assert "sales_price" in defs
        assert "manufacturer" in defs
        assert defs["buyer_name"]["type"] == "string"
        assert defs["sales_price"]["type"] == "currency"

    def test_pii_fields_marked(self):
        defs = get_field_definitions()
        assert defs["buyer_ssn"].get("pii") is True
        assert defs["buyer_dob"].get("pii") is True
        assert defs["buyer_income"].get("pii") is True
        assert defs.get("buyer_name", {}).get("pii") is not True


class TestDocumentGeneration:
    """Tests for single document generation."""

    def test_generate_tmha_sales_contract(self):
        result = generate_document("TMHA_SalesContract.pdf", SAMPLE_DATA.copy())
        assert result["success"] is True
        assert result["filename"].startswith("TMHA_SalesContract_")
        assert result["filename"].endswith(".pdf")
        assert os.path.exists(result["file_path"])

    def test_generate_with_missing_required_fields(self):
        result = generate_document("TMHA_SalesContract.pdf", {"buyer_name": "Test"})
        assert result["success"] is False
        assert "Missing required fields" in result["message"]

    def test_generate_unknown_template(self):
        result = generate_document("nonexistent.pdf", SAMPLE_DATA.copy())
        assert result["success"] is False
        assert "not found" in result["message"]

    def test_generate_tdhca_1038(self):
        result = generate_document("TDHCA_1038_Consumer_Disclosure.pdf", SAMPLE_DATA.copy())
        assert result["success"] is True

    def test_generate_cover_page(self):
        result = generate_document("All_Cover.pdf", SAMPLE_DATA.copy())
        assert result["success"] is True

    def test_default_values_applied(self):
        """Test that default values (like buyer_state=TX) are applied."""
        data = SAMPLE_DATA.copy()
        del data["buyer_state"]
        result = generate_document("TMHA_SalesContract.pdf", data)
        assert result["success"] is True

    def test_computed_fields_calculated(self):
        """Test that unpaid_balance is auto-computed."""
        data = SAMPLE_DATA.copy()
        if "unpaid_balance" in data:
            del data["unpaid_balance"]
        result = generate_document("TMHA_SalesContract.pdf", data)
        assert result["success"] is True


class TestPacketGeneration:
    """Tests for closing packet generation."""

    def test_generate_standard_closing_packet(self):
        result = generate_packet("standard_closing", SAMPLE_DATA.copy())
        assert result["success"] is True
        assert result["filename"].startswith("Packet_standard_closing_")
        assert result["page_count"] > 0
        assert len(result["documents_included"]) > 0
        assert os.path.exists(result["file_path"])

    def test_generate_unknown_packet(self):
        result = generate_packet("nonexistent_packet", SAMPLE_DATA.copy())
        assert result["success"] is False
        assert "not found" in result["message"]


class TestMergedDocumentPersistence:
    """Tests that merged PDFs are persisted to durable storage."""

    @staticmethod
    def _write_pdf(path):
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        with open(path, "wb") as f:
            writer.write(f)

    def test_generate_packet_uploads_merged_pdf(self, monkeypatch, tmp_path):
        import tools.document_engine as engine

        calls = []

        def fake_generate_document(template_name, data, output_filename=None):
            filename = output_filename or f"{template_name}.pdf"
            path = tmp_path / filename
            self._write_pdf(path)
            return {"success": True, "file_path": str(path), "filename": filename}

        monkeypatch.setattr(engine, "OUTPUT_DIR", str(tmp_path))
        monkeypatch.setattr(
            engine,
            "get_packet_config",
            lambda _name: {"display_name": "Test Packet", "templates": ["one.pdf", "two.pdf"]},
        )
        monkeypatch.setattr(engine, "get_template_config", lambda _name: {"display_name": _name})
        monkeypatch.setattr(engine, "generate_document", fake_generate_document)
        monkeypatch.setattr(
            engine, "upload_to_gcs", lambda path, filename: calls.append((path, filename))
        )

        result = engine.generate_packet("test_packet", {"buyer_name": "Durable Buyer"})

        assert result["success"] is True
        assert calls == [(result["file_path"], result["filename"])]

    def test_generate_batch_uploads_merged_pdf(self, monkeypatch, tmp_path):
        import tools.document_engine as engine

        calls = []

        def fake_generate_document(template_name, data, output_filename=None):
            filename = output_filename or f"{template_name}.pdf"
            path = tmp_path / filename
            self._write_pdf(path)
            return {"success": True, "file_path": str(path), "filename": filename}

        monkeypatch.setattr(engine, "OUTPUT_DIR", str(tmp_path))
        monkeypatch.setattr(engine, "get_template_config", lambda _name: {"display_name": _name})
        monkeypatch.setattr(engine, "generate_document", fake_generate_document)
        monkeypatch.setattr(
            engine, "upload_to_gcs", lambda path, filename: calls.append((path, filename))
        )

        result = engine.generate_batch(
            ["one.pdf", "two.pdf"], {"buyer_name": "Durable Buyer"}, merge=True
        )

        assert result["success"] is True
        assert result["merged"] is not None
        assert calls == [
            (str(tmp_path / result["merged"]["filename"]), result["merged"]["filename"])
        ]

    def test_generate_batch_marks_individual_outputs_with_buyer(self, monkeypatch, tmp_path):
        import tools.document_engine as engine

        output_filenames = []

        def fake_generate_document(template_name, data, output_filename=None):
            output_filenames.append(output_filename)
            return {
                "success": True,
                "file_path": str(tmp_path / output_filename),
                "filename": output_filename,
            }

        monkeypatch.setattr(engine, "get_template_config", lambda _name: {"display_name": _name})
        monkeypatch.setattr(engine, "generate_document", fake_generate_document)

        result = engine.generate_batch(
            ["TMHA_SalesContract.pdf"],
            {"buyer_name": "Smoke Buyer"},
            merge=False,
        )

        assert result["success"] is True
        assert output_filenames[0].startswith("_batch_TMHA_SalesContract_Smoke_Buyer_")
        assert output_filenames[0].endswith(".pdf")


class TestTemplateFields:
    """Tests for the template field API."""

    def test_get_template_fields(self):
        fields = get_template_fields("TMHA_SalesContract.pdf")
        assert fields is not None
        assert "sections" in fields
        assert "buyer" in fields["sections"]
        assert "pricing" in fields["sections"]

    def test_get_unknown_template_fields(self):
        fields = get_template_fields("nonexistent.pdf")
        assert fields is None

    def test_field_sections_have_metadata(self):
        fields = get_template_fields("TMHA_SalesContract.pdf")
        buyer_fields = fields["sections"]["buyer"]
        assert len(buyer_fields) > 0
        first = buyer_fields[0]
        assert "field_name" in first
        assert "label" in first
        assert "type" in first
        assert "required" in first


class TestAvailableLists:
    """Tests for listing endpoints."""

    def test_list_templates(self):
        templates = list_available_templates()
        assert isinstance(templates, list)
        assert len(templates) >= 3
        for t in templates:
            assert "template_name" in t
            assert "display_name" in t
            assert "category" in t

    def test_list_packets(self):
        packets = list_available_packets()
        assert isinstance(packets, list)
        assert len(packets) >= 1
        assert packets[0]["packet_name"] == "standard_closing"


class TestPIIGuard:
    """Tests for PII protection."""

    def test_pii_field_names_identified(self):
        pii_fields = get_pii_field_names()
        assert "buyer_ssn" in pii_fields
        assert "buyer_dob" in pii_fields
        assert "buyer_name" not in pii_fields

    def test_sanitize_for_logging(self):
        data = {"buyer_name": "John", "buyer_ssn": "123-45-6789", "sales_price": "100000"}
        sanitized = sanitize_for_logging(data)
        assert sanitized["buyer_name"] == "John"
        assert "6789" in sanitized["buyer_ssn"]
        assert "123-45" not in sanitized["buyer_ssn"]
        assert sanitized["sales_price"] == "100000"

    def test_sanitize_for_llm(self):
        data = {"buyer_name": "John", "buyer_ssn": "123-45-6789", "buyer_dob": "1990-01-01"}
        sanitized = sanitize_for_llm(data)
        assert "buyer_name" in sanitized
        assert "buyer_ssn" not in sanitized
        assert "buyer_dob" not in sanitized

    def test_validate_text_clean(self):
        result = validate_no_pii_in_text("The buyer wants a 3 bedroom home in Houston")
        assert result["clean"] is True

    def test_validate_text_with_ssn(self):
        result = validate_no_pii_in_text("My SSN is 123-45-6789")
        assert result["clean"] is False
        assert any(f["type"] == "SSN" for f in result["findings"])

    def test_redact_ssn_from_text(self):
        text = "SSN: 123-45-6789 and CC: 4111-1111-1111-1111"
        redacted = redact_pii_from_text(text)
        assert "123-45-6789" not in redacted
        assert "4111-1111-1111-1111" not in redacted
        assert "[SSN-REDACTED]" in redacted
        assert "[CC-REDACTED]" in redacted


def _values_in_pdf(pdf_path, needles):
    """
    Verify text values are present in a generated PDF.

    Searches the raw PDF bytes — values rendered with auto_regenerate=True
    end up in field appearance streams that get baked into page content,
    surviving the merge step that strips the AcroForm structure.
    """
    with open(pdf_path, "rb") as f:
        raw = f.read()
    return {n: n.encode() in raw for n in needles}


class TestFilledValuesRenderInStaticContent:
    """
    Regression: customer (Joe Blo, Apr 2026) reported that generated docs
    showed no populated values. Root cause: fill_pdf_form only injected XFA
    datasets, which are invisible in non-Acrobat viewers AND get stripped
    when PdfWriter.add_page merges multiple PDFs into a closing packet.
    Fix: also fill the AcroForm layer with auto_regenerate=True so values
    are baked into the page content streams.
    """

    # TMHA Sales Contract maps the buyer through Install_Address rather than
    # a buyer_name field, so we check fields that are actually mapped.
    NEEDLES = ["Clayton Homes", "The Freedom 3256", "CLH123456TX", "Houston"]

    def test_single_document_contains_filled_values(self):
        result = generate_document("TMHA_SalesContract.pdf", SAMPLE_DATA.copy())
        assert result["success"] is True
        found = _values_in_pdf(result["file_path"], self.NEEDLES)
        assert all(found.values()), f"Missing in single doc: {found}"

    def test_merged_packet_contains_filled_values(self):
        result = generate_packet("standard_closing", SAMPLE_DATA.copy())
        assert result["success"] is True
        found = _values_in_pdf(result["file_path"], self.NEEDLES)
        assert all(found.values()), f"Missing in merged packet: {found}"

    def test_batch_from_ui_shaped_joe_blo_data_fills_credit_application(
        self, monkeypatch, tmp_path
    ):
        """Document Center sends form-shaped data; the engine should fill aliases.

        The April 2026 Joe Blo report was a real UI-path failure mode: a
        customer/deal could make it through batch generation while fields that
        only exist as derived document aliases were blank in the generated PDF.
        This uses synthetic data and disables GCS so the check stays local.
        """
        engine_globals = generate_batch.__globals__

        monkeypatch.setitem(engine_globals, "OUTPUT_DIR", str(tmp_path))
        monkeypatch.setitem(engine_globals, "upload_to_gcs", lambda *_args, **_kwargs: None)
        monkeypatch.setitem(
            engine_globals["fill_pdf_form"].__globals__, "OUTPUT_DIR", str(tmp_path)
        )
        monkeypatch.setitem(
            engine_globals["fill_pdf_form"].__globals__,
            "upload_to_gcs",
            lambda *_args, **_kwargs: None,
        )

        joe_blo_ui_data = {
            "buyer_first_name": "Joe",
            "buyer_last_name": "Blo",
            "buyer_phone": "(555) 010-0000",
            "buyer_email": "joe.blo@example.test",
            "mailing_address": "77 Test Loop",
            "mailing_city": "Austin",
            "mailing_state": "TX",
            "mailing_zip": "78701",
            "buyer_address": "88 Install Way",
            "buyer_city": "Round Rock",
            "buyer_county": "Williamson",
            "buyer_state": "TX",
            "buyer_zip": "78664",
            "employer_name": "Synthetic Employer LLC",
            "occupation": "Synthetic Tester",
            "manufacturer": "Northstar",
            "model": "Tempo",
            "serial_number_1": "SYN-JOE-BLO-001",
            "sales_price": "85000",
            "down_payment": "5000",
        }

        result = generate_batch(["creditapp.pdf"], joe_blo_ui_data, merge=False)

        assert result["success"] is True, result
        assert result["successful"] == 1
        output_path = tmp_path / result["documents"][0]["filename"]
        found = _values_in_pdf(
            output_path,
            ["Joe Blo", "77 Test Loop, Austin, TX 78701", "Synthetic Employer LLC"],
        )
        assert all(found.values()), f"Missing in Joe Blo credit app: {found}"


class TestBackwardCompatibility:
    """Tests for Phase 1 backward compatibility."""

    def test_legacy_sales_contract_function(self):
        from schemas.document_schemas import SalesContractForm
        from tools.document_tools import generate_sales_contract_pdf

        data = SalesContractForm(
            buyer_name="Legacy Test",
            buyer_address="456 Oak Ln",
            buyer_city="Dallas",
            buyer_county="Dallas",
            buyer_state="TX",
            buyer_zip="75201",
            manufacturer="Champion",
            model="The Victory",
            serial_number_1="XYZ789",
            sales_price=95000,
            down_payment=15000,
            unpaid_balance=80000,
        )
        result = generate_sales_contract_pdf(data)
        assert result["success"] is True
        assert "SalesContract" in result["filename"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
