import json
import re

from tools.document_engine_v2 import generate_batch, generate_document
from tools.document_quality import (
    enrich_document_data,
    validate_document_quality,
    validate_required_document_data,
)

BASE_DATA = {
    "buyer_name": "Quality Buyer",
    "buyer_address": "123 Closing Ln",
    "buyer_city": "Austin",
    "buyer_county": "Travis",
    "buyer_state": "TX",
    "buyer_zip": "78701",
    "manufacturer": "TRU Homes",
    "model": "Delight",
    "serial_number_1": "TRU-REAL-001",
    "label_number_1": "NTA7654321",
    "sales_price": "55000",
    "down_payment": "5000",
    "loan_term": "240",
    "apr": "7.5",
}


def test_quality_gate_rejects_placeholder_serials_before_generation():
    data = {**BASE_DATA, "serial_number_1": "12345678"}

    result = generate_document("TMHA_SalesContract.pdf", data)

    assert result["success"] is False
    assert result["error"] == "quality_gate_failed"
    assert result["quality_issues"][0]["code"] == "placeholder_identifier"
    assert result["quality_issues"][0]["field"] == "serial_number_1"


def test_quality_gate_rejects_placeholder_hud_labels():
    issues = validate_document_quality({**BASE_DATA, "label_number_1": "nta1234567"})

    assert any(
        issue.code == "placeholder_identifier" and issue.field == "label_number_1"
        for issue in issues
    )


def test_quality_gate_blocks_unmapped_note_security_templates():
    result = generate_batch(["TMHA-TwoPartyContract.pdf"], BASE_DATA, merge=True)

    assert result["success"] is False
    assert result["error"] == "quality_gate_failed"
    assert result["successful"] == 0
    assert result["quality_issues"][0]["code"] == "template_not_production_ready"
    assert result["quality_issues"][0]["template"] == "TMHA-TwoPartyContract.pdf"


def test_quality_enrichment_adds_seller_and_financing_aliases():
    enriched = enrich_document_data(BASE_DATA)

    assert enriched["seller_name"] == "Texas Home Outlet, Inc."
    assert enriched["seller_address"] == "10685 FM 1960 East"
    assert enriched["seller_rbi"] == "35248"
    assert enriched["seller_city"] == "Huffman"
    assert enriched["seller_state"] == "TX"
    assert enriched["seller_zip"] == "77336"
    assert enriched["manufacturer_model_hud"] == "TRU Homes Delight / NTA7654321"
    assert enriched["manufacturer_model_serial"] == "TRU Homes Delight / TRU-REAL-001"
    assert enriched["max_financed"] == "50,000.00"
    assert enriched["unpaid_balance"] == "50,000.00"
    assert enriched["interest_rate"] == "7.5"


def test_quality_enrichment_repairs_blank_seller_defaults():
    enriched = enrich_document_data(
        {
            **BASE_DATA,
            "seller_name": "",
            "seller_rbi": "",
            "seller_address": "",
        }
    )

    assert enriched["seller_name"] == "Texas Home Outlet, Inc."
    assert enriched["seller_rbi"] == "35248"
    assert enriched["seller_address"] == "10685 FM 1960 East"


def test_quality_enrichment_adds_manufacturer_address_aliases():
    enriched = enrich_document_data(
        {
            **BASE_DATA,
            "manufacturer_address": "500 Factory Road",
            "manufacturer_city": "Fort Worth",
            "manufacturer_state": "TX",
            "manufacturer_zip": "76101",
        }
    )

    assert enriched["manufacturer_city_state_zip"] == "Fort Worth, TX 76101"
    assert enriched["manufacturer_full_address"] == "500 Factory Road, Fort Worth, TX 76101"


def test_required_field_gate_reports_missing_template_data_before_generation():
    issues = validate_required_document_data(
        {
            "buyer_name": "Lee Partial",
            "manufacturer": "TRU Homes",
            "model": "Delight",
            "serial_number_1": "TRU-REAL-001",
        },
        ["buyer_address", "buyer_city", "buyer_county", "buyer_state", "buyer_zip", "sales_price"],
    )

    assert [issue.code for issue in issues] == [
        "missing_required_field",
        "missing_required_field",
        "missing_required_field",
        "missing_required_field",
        "missing_required_field",
        "missing_required_field",
    ]
    assert [issue.field for issue in issues] == [
        "buyer_address",
        "buyer_city",
        "buyer_county",
        "buyer_state",
        "buyer_zip",
        "sales_price",
    ]


def test_batch_generation_fails_closed_when_required_sales_contract_data_missing():
    result = generate_batch(
        ["TMHA_SalesContract.pdf"],
        {
            "buyer_name": "Lee Partial",
            "manufacturer": "TRU Homes",
            "model": "Delight",
            "serial_number_1": "TRU-REAL-001",
        },
        merge=True,
    )

    assert result["success"] is False
    assert result["error"] == "missing_required_fields"
    assert result["successful"] == 0
    assert "buyer_address" in result["missing_fields"]
    assert "sales_price" in result["missing_fields"]
    assert result["quality_issues"][0]["code"] == "missing_required_field"


def test_manufacturer_location_is_required_when_template_maps_it():
    result = generate_batch(
        ["TDHCA_1023-Statement-Ownership.pdf"],
        BASE_DATA,
        merge=True,
    )

    assert result["success"] is False
    assert result["error"] == "missing_required_fields"
    assert "manufacturer_address" in result["missing_fields"]
    assert "manufacturer_city_state_zip" in result["missing_fields"]


def test_partial_manufacturer_location_does_not_satisfy_required_alias():
    result = generate_batch(
        ["TDHCA_1023-Statement-Ownership.pdf"],
        {
            **BASE_DATA,
            "manufacturer_address": "500 Factory Road",
            "manufacturer_state": "TX",
            "manufacturer_zip": "76101",
        },
        merge=True,
    )

    assert result["success"] is False
    assert result["error"] == "missing_required_fields"
    assert "manufacturer_city_state_zip" in result["missing_fields"]


def test_partial_buyer_name_and_city_do_not_satisfy_composite_requirements():
    issues = validate_required_document_data(
        {
            **BASE_DATA,
            "buyer_name": "",
            "buyer_first_name": "Lee",
            "buyer_address": "123 Closing Ln",
            "buyer_city": "Austin",
            "buyer_state": "TX",
            "buyer_zip": "",
        },
        ["buyer_name", "buyer_city_state_zip", "buyer_full_address"],
    )

    assert [issue.field for issue in issues] == [
        "buyer_name",
        "buyer_city_state_zip",
        "buyer_full_address",
    ]


def test_direct_buyer_address_aliases_allow_partial_stale_components():
    issues = validate_required_document_data(
        {
            **BASE_DATA,
            "buyer_full_address": "987 Contract Ave, Dallas, TX 75201",
            "buyer_city_state_zip": "Dallas, TX 75201",
            "buyer_address": "",
            "buyer_city": "Dallas",
            "buyer_state": "TX",
            "buyer_zip": "",
        },
        ["buyer_city_state_zip", "buyer_full_address"],
    )

    assert issues == []


def test_direct_manufacturer_full_address_allows_partial_stale_components():
    issues = validate_required_document_data(
        {
            **BASE_DATA,
            "manufacturer_full_address": "500 Factory Road, Fort Worth, TX 76101",
            "manufacturer_city_state_zip": "Fort Worth, TX 76101",
            "manufacturer_address": "",
            "manufacturer_city": "Fort Worth",
            "manufacturer_state": "TX",
            "manufacturer_zip": "",
        },
        ["manufacturer_city_state_zip", "manufacturer_full_address"],
    )

    assert issues == []


def test_direct_full_address_must_include_street_and_city_state_zip():
    issues = validate_required_document_data(
        {
            **BASE_DATA,
            "buyer_full_address": "Dallas, TX 75201",
            "buyer_address": "",
            "buyer_city": "",
            "buyer_state": "",
            "buyer_zip": "",
        },
        ["buyer_full_address"],
    )

    assert [issue.field for issue in issues] == ["buyer_full_address"]


def test_production_packet_manufacturer_identity_fields_are_mapped():
    with open("config/pdf_field_inventory.json") as f:
        inventory = json.load(f)
    with open("config/field_map.json") as f:
        field_map = json.load(f)

    def leaf(path: str) -> str:
        matches = re.findall(r"\.([^\.\[]+)\[\d+\]", path)
        return matches[-1] if matches else path

    allowed_unmapped = {
        "Manufacturer_License",
        "Manu_License",
    }
    missing = []
    for packet_name in (
        "standard_closing",
        "used_home_closing",
        "full_closing_new",
        "full_closing_used",
    ):
        for template_name in field_map["packets"][packet_name]["templates"]:
            source_fields = set(inventory.get(template_name, []))
            mapped_fields = {
                leaf(pdf_field)
                for pdf_field in field_map["templates"].get(template_name, {}).get("field_map", {})
            }
            for field in source_fields:
                if not re.search(r"manuf|make|mfg|factory|builder", field, re.I):
                    continue
                if field in allowed_unmapped or "License" in field:
                    continue
                if field not in mapped_fields:
                    missing.append(f"{packet_name}:{template_name}:{field}")

    assert missing == []
