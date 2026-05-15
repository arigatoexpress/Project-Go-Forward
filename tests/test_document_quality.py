from tools.document_engine_v2 import generate_batch, generate_document
from tools.document_quality import enrich_document_data, validate_document_quality


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

    assert any(issue.code == "placeholder_identifier" and issue.field == "label_number_1" for issue in issues)


def test_quality_gate_blocks_unmapped_note_security_templates():
    result = generate_batch(["TMHA-TwoPartyContract.pdf"], BASE_DATA, merge=True)

    assert result["success"] is False
    assert result["error"] == "quality_gate_failed"
    assert result["successful"] == 0
    assert result["quality_issues"][0]["code"] == "template_not_production_ready"
    assert result["quality_issues"][0]["template"] == "TMHA-TwoPartyContract.pdf"


def test_quality_enrichment_adds_seller_and_financing_aliases():
    enriched = enrich_document_data(BASE_DATA)

    assert enriched["seller_name"] == "Texas Home Outlet"
    assert enriched["seller_address"] == "10685 FM 1960 East"
    assert enriched["max_financed"] == "50,000.00"
    assert enriched["unpaid_balance"] == "50,000.00"
    assert enriched["interest_rate"] == "7.5"
