"""Smoke tests — verify core product works end-to-end.

These tests verify the critical user workflow:
  Search customer → fill form → generate PDF → verify data in PDF

Run: python -m pytest tests/test_smoke.py -v
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestDocumentGeneration:
    """Test the core document generation pipeline."""

    def test_templates_available(self):
        """At least 60 templates should be loadable."""
        from tools.document_engine import list_available_templates

        templates = list_available_templates()
        assert len(templates) >= 60, f"Only {len(templates)} templates"

    def test_generate_tdhca_disclosure(self):
        """Generate a TDHCA form and verify output exists."""
        from tools.document_engine import generate_document

        result = generate_document(
            "TDHCA_1038_Consumer_Disclosure.pdf",
            {
                "buyer_name": "Test Buyer",
                "salesrep": "Test Rep",
            },
        )
        assert result["success"], f"Generation failed: {result}"
        assert os.path.exists(result["file_path"]), "PDF not created"
        assert os.path.getsize(result["file_path"]) > 1000, "PDF too small"

    def test_generate_tmha_sales_contract(self):
        """Generate a TMHA sales contract with required fields."""
        from tools.document_engine import generate_document

        result = generate_document(
            "TMHA_SalesContract.pdf",
            {
                "buyer_address": "123 Test St",
                "buyer_city": "Houston",
                "buyer_county": "Harris",
                "buyer_state": "TX",
                "buyer_zip": "77001",
                "manufacturer": "Clayton",
                "model": "Test Model",
                "no_of_sections": "2",
                "serial_number_1": "TEST-001",
                "label_number_1": "TEX482913",
                "sales_price": "50000",
            },
        )
        assert result["success"], f"Generation failed: {result}"

    def test_generate_internal_form(self):
        """Generate an internal form."""
        from tools.document_engine import generate_document

        result = generate_document(
            "Internal_Homestead.pdf",
            {
                "buyer_name": "Test Buyer",
                "buyer_address": "123 Test St",
                "buyer_city": "Houston",
                "buyer_state": "TX",
                "buyer_zip": "77001",
                "manufacturer": "Clayton",
                "model": "Test Model",
                "serial_number_1": "TEST-001",
            },
        )
        assert result["success"], f"Generation failed: {result}"

    def test_xfa_data_in_pdf(self):
        """Verify XFA data is actually embedded in the generated PDF."""
        from tools.document_engine import generate_document

        result = generate_document(
            "TDHCA_1038_Consumer_Disclosure.pdf",
            {
                "buyer_name": "XFA Verification Test",
            },
        )
        assert result["success"]

        from pypdf import PdfReader
        from pypdf.generic import ArrayObject

        reader = PdfReader(result["file_path"])
        acroform = reader.trailer["/Root"].get("/AcroForm", {})
        xfa = acroform.get("/XFA")
        assert xfa, "No XFA in output PDF"

        if isinstance(xfa, ArrayObject):
            for i in range(0, len(xfa), 2):
                if str(xfa[i]) == "datasets":
                    data = xfa[i + 1].get_object().get_data().decode()
                    assert "XFA Verification Test" in data, "Customer name not in XFA data"
                    return
        pytest.fail("Could not find datasets in XFA")

    def test_missing_required_fields_rejected(self):
        """Generating without required fields should fail gracefully."""
        from tools.document_engine import generate_document

        result = generate_document(
            "TMHA_SalesContract.pdf",
            {
                "buyer_name": "Test",
                # Missing: buyer_address, manufacturer, etc.
            },
        )
        assert not result["success"]
        assert "required" in result.get("message", "").lower()


class TestCustomerData:
    """Test customer data integrity and search."""

    def test_customers_loadable(self):
        """Customer JSON should load without errors."""
        path = Path(__file__).parent.parent / "data" / "migrated_customers.json"
        if not path.exists():
            pytest.skip("No customer data")
        customers = json.loads(path.read_text())
        assert len(customers) > 1900

    def test_customer_search_by_name(self):
        """Search should find customers by name."""
        path = Path(__file__).parent.parent / "data" / "migrated_customers.json"
        if not path.exists():
            pytest.skip("No customer data")
        customers = json.loads(path.read_text())
        results = [c for c in customers if "salazar" in c.get("full_name", "").lower()]
        assert len(results) > 0

    def test_no_raw_ssn(self):
        """No raw SSNs should exist in customer data."""
        import re

        path = Path(__file__).parent.parent / "data" / "migrated_customers.json"
        if not path.exists():
            pytest.skip("No customer data")
        customers = json.loads(path.read_text())
        raw_pattern = re.compile(r"^\d{3}-\d{2}-\d{4}$")
        for c in customers:
            ssn = c.get("ssn_masked", "")
            if ssn:
                assert not raw_pattern.match(ssn), f"Raw SSN for {c['full_name']}"


class TestStartup:
    """Tests that verify the app can start without crashing."""

    def test_imports_work(self):
        """All critical imports should succeed."""
        from tools.document_tools import DOCUMENTS_DIR

        assert os.path.isdir(DOCUMENTS_DIR), f"Templates dir missing: {DOCUMENTS_DIR}"

    def test_field_map_valid(self):
        """field_map.json should be valid and have templates."""
        path = Path(__file__).parent.parent / "config" / "field_map.json"
        data = json.loads(path.read_text())
        assert "templates" in data
        assert len(data["templates"]) >= 60

    def test_frontend_built(self):
        """Frontend dist should exist with index.html."""
        dist = Path(__file__).parent.parent / "frontend" / "dist" / "index.html"
        assert dist.exists(), "Frontend not built — run: cd frontend && npm run build"
