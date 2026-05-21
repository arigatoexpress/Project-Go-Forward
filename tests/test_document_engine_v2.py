import os
import sys
from decimal import Decimal

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.document_engine_v2 import (
    generate_batch,
    generate_document,
    list_available_packets,
    list_available_templates,
)

SAMPLE_DATA = {
    "buyer_name": "V2 Test Buyer",
    "buyer_address": "456 Test Ave",
    "buyer_city": "Austin",
    "buyer_county": "Travis",
    "buyer_state": "TX",
    "buyer_zip": "78701",
    "buyer_phone": "555-0123",
    "manufacturer": "TRU Homes",
    "model": "The Marvel",
    "serial_number_1": "TRU-987654",
    "sales_price": "95000",
    "down_payment": "5000",
    "loan_term": "240",
    "apr": "8.5",
}


def test_v2_list_templates():
    templates = list_available_templates()
    assert len(templates) >= 63
    # Check that both v2 and legacy templates are present
    assert any(t["template_name"] == "TMHA_SalesContract.pdf" and t["is_v2"] for t in templates)
    assert any(
        t["template_name"] == "TDHCA_1054_Habitability_Warranty.pdf" and not t["is_v2"]
        for t in templates
    )


def test_v2_list_packets():
    packets = list_available_packets()
    assert len(packets) >= 1
    assert any(p["packet_name"] == "standard_closing" for p in packets)


def test_v2_packets_surface_production_readiness():
    packets = {p["packet_name"]: p for p in list_available_packets()}

    assert packets["standard_closing"]["production_ready"] is True
    assert packets["standard_closing"]["blocked_templates"] == []

    full_new = packets["full_closing_new"]
    blocked_names = {item["template_name"] for item in full_new["blocked_templates"]}
    assert full_new["production_ready"] is False
    assert blocked_names == {
        "TMHA-TwoPartyContract.pdf",
        "TMHA-TwoPartyContract191220.pdf",
    }
    assert all("lender/note" in item["message"] for item in full_new["blocked_templates"])


def test_v2_generate_unified_template():
    # TMHA_SalesContract is in unified_schema.json
    result = generate_document("TMHA_SalesContract.pdf", SAMPLE_DATA)
    assert result["success"] is True
    assert result["intelligence"]["mapping"]["source"] == "unified_schema_v2"
    # Verify we got more than just the 7 v2 fields (thanks to hybrid fallback)
    assert result["intelligence"]["mapping"]["total_fields"] > 7


def test_v2_generate_legacy_template():
    # Habitability Warranty is NOT in unified_schema.json
    result = generate_document("TDHCA_1054_Habitability_Warranty.pdf", SAMPLE_DATA)
    assert result["success"] is True
    assert result["intelligence"]["mapping"]["source"] == "legacy_fallback"
    assert result["intelligence"]["mapping"]["total_fields"] > 0


def test_v2_generate_batch():
    templates = ["TMHA_SalesContract.pdf", "TDHCA_1054_Habitability_Warranty.pdf"]
    result = generate_batch(templates, SAMPLE_DATA, merge=True)
    assert result["success"] is True
    assert len(result["documents"]) == 2
    assert all(doc["display_name"] for doc in result["documents"])
    assert result["merged"] is not None
    assert result["successful"] == 2


def test_v2_validation_error():
    # Missing template-required fields should fail before Pydantic/pdf filling.
    result = generate_document("TMHA_SalesContract.pdf", {"sales_price": 100})
    assert result["success"] is False
    assert result["error"] == "missing_required_fields"
    assert "Missing required fields" in result["message"]


def test_v2_financial_computations():
    # Test that computed fields are present in intelligence
    result = generate_document("TMHA_SalesContract.pdf", SAMPLE_DATA)
    intelligence = result["intelligence"]["computed"]
    assert "monthly_payment" in intelligence
    assert "loan_amount" in intelligence
    assert intelligence["loan_amount"] == 90000  # 95000 - 5000


def test_v2_blank_optional_financial_fields_default_instead_of_failing():
    data = {
        **SAMPLE_DATA,
        "down_payment": "",
        "loan_term": "",
        "apr": "",
    }
    result = generate_document("TMHA_SalesContract.pdf", data)
    assert result["success"] is True
    intelligence = result["intelligence"]["computed"]
    assert intelligence["sales_price"] == Decimal("95000")
    assert intelligence["down_payment"] == Decimal("0")
    assert intelligence["loan_term"] == 240
    assert intelligence["apr"] == Decimal("7.5")


def test_v2_blank_required_sales_price_fails_closed():
    data = {
        **SAMPLE_DATA,
        "sales_price": "",
    }
    result = generate_document("TMHA_SalesContract.pdf", data)
    assert result["success"] is False
    assert result["error"] == "missing_required_fields"
    assert "sales_price" in result["missing_fields"]
