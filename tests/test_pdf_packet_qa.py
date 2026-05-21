from scripts.pdf_packet_qa import PLACEHOLDER_PATTERNS, _missing_expected_texts


def test_pdf_packet_qa_detects_fake_hud_serial_values():
    text = "HUD nta1234567 Serial 12345678"

    assert PLACEHOLDER_PATTERNS["fake_hud_or_serial"].search(text)


def test_pdf_packet_qa_detects_note_placeholder_artifacts():
    text = "The Purpose of this Loan is IRREGULA 01234 6789 RREGULAR"

    assert PLACEHOLDER_PATTERNS["note_placeholder"].search(text)


def test_pdf_packet_qa_detects_missing_expected_text():
    text = "Texas Home Outlet, Inc.\nTRU Homes\nFort Worth, TX 76101"

    assert _missing_expected_texts(text, ["TRU Homes", "Texas Home Outlet, Inc."]) == []
    assert _missing_expected_texts(text, ["New Customer 052026"]) == ["New Customer 052026"]
