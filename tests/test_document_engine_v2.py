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
    "date_of_manufacture": "2026-01-15",
    "manufacturer_address": "500 Factory Road",
    "manufacturer_city": "Fort Worth",
    "manufacturer_state": "TX",
    "manufacturer_zip": "76101",
    "serial_number_1": "TRU-987654",
    "label_number_1": "TEX482913",
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
    assert full_new["production_ready"] is True
    assert full_new["blocked_templates"] == []
    assert "Production-Ready Documents" in full_new["display_name"]
    assert "TMHA-TwoPartyContract.pdf" not in full_new["templates"]
    assert "TMHA-TwoPartyContract191220.pdf" not in full_new["templates"]


def test_v2_maps_manufacturer_location_from_split_fields(monkeypatch, tmp_path):
    import tools.document_engine_v2 as engine_module

    captured = {}

    def _capture_fill(_template_path, pdf_data, output_filename):
        captured.update(pdf_data)
        output_path = tmp_path / output_filename
        output_path.write_bytes(b"%PDF-1.4\n%%EOF")
        return str(output_path)

    monkeypatch.setattr(engine_module, "fill_pdf_form", _capture_fill)

    result = engine_module.generate_document(
        "TDHCA_1023-Statement-Ownership.pdf",
        {
            **SAMPLE_DATA,
            "manufacturer_address": "500 Factory Road",
            "manufacturer_city": "Fort Worth",
            "manufacturer_state": "TX",
            "manufacturer_zip": "76101",
        },
    )

    assert result["success"] is True
    assert captured["topmostSubform[0].Page1[0].Manufacturer_Address[0]"] == "500 Factory Road"
    assert (
        captured["topmostSubform[0].Page1[0].Manufacturer_City_State_Zip[0]"]
        == "Fort Worth, TX 76101"
    )


def test_v2_maps_full_packet_manufacturer_identity_fields(monkeypatch, tmp_path):
    import tools.document_engine_v2 as engine_module

    captures = {}

    def _capture_fill(template_path, pdf_data, output_filename):
        captures[os.path.basename(template_path)] = dict(pdf_data)
        output_path = tmp_path / output_filename
        output_path.write_bytes(b"%PDF-1.4\n%%EOF")
        return str(output_path)

    monkeypatch.setattr(engine_module, "fill_pdf_form", _capture_fill)

    templates = [
        "TMHA_LimitedWarranty.pdf",
        "Internal_AoA.pdf",
        "Internal_Homestead.pdf",
        "State_ImportantHealth.pdf",
        "TDHCA_1054_Habitability_Warranty.pdf",
        "TDHCA_WarrantyUsedHome.pdf",
        "TMHA-SalesContractDepositAgreement.pdf",
    ]
    result = engine_module.generate_batch(templates, SAMPLE_DATA, merge=False)

    assert result["success"] is True
    assert (
        captures["TMHA_LimitedWarranty.pdf"]["topmostSubform[0].Page3[0].Manufacturer_Address[0]"]
        == "500 Factory Road"
    )
    assert (
        captures["TMHA_LimitedWarranty.pdf"][
            "topmostSubform[0].Page3[0].Manufacturer_City_State_Zip[0]"
        ]
        == "Fort Worth, TX 76101"
    )
    assert (
        captures["Internal_AoA.pdf"]["topmostSubform[0].Page2[0].Manufacturer_Model_HUD[0]"]
        == "TRU Homes The Marvel / TEX482913"
    )
    assert (
        captures["Internal_Homestead.pdf"][
            "topmostSubform[0].Page2[0].Manufacturer_Model_Serial[0]"
        ]
        == "TRU Homes The Marvel / TRU-987654"
    )
    assert (
        captures["State_ImportantHealth.pdf"]["topmostSubform[0].Page1[0].Manufacturer_Address[0]"]
        == "500 Factory Road"
    )
    assert (
        captures["TDHCA_1054_Habitability_Warranty.pdf"][
            "topmostSubform[0].Page1[0].Manu_Address[0]"
        ]
        == "500 Factory Road"
    )
    assert (
        captures["TDHCA_WarrantyUsedHome.pdf"]["topmostSubform[0].Page1[0].Manu_City_State_Zip[0]"]
        == "Fort Worth, TX 76101"
    )
    assert (
        captures["TMHA-SalesContractDepositAgreement.pdf"][
            "topmostSubform[0].Page1[0].Seller_Phone[0]"
        ]
        == "(281) 324-3020"
    )


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
