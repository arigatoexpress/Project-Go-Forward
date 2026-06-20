"""TDHCA 1023 Section-1 dimension enrichment (item 5).

Section 1 of a SINGLE-section home is the whole home, so its width/length can be
filled from the home's overall dimensions. For multi-section homes the overall
size is split across sections, so the per-section cells must stay blank (manual
entry) — filling them would risk a wrong value on a state ownership form.
"""

from tools.document_quality import enrich_document_data


def test_single_section_fills_section1_dims():
    out = enrich_document_data(
        {"no_of_sections": "1", "width": "16", "length": "76"}
    )
    assert out["section_1_width"] == "16"
    assert out["section_1_length"] == "76"


def test_single_section_word_form_also_fills():
    out = enrich_document_data(
        {"no_of_sections": "single", "width": "14", "length": "60"}
    )
    assert out["section_1_width"] == "14"
    assert out["section_1_length"] == "60"


def test_multi_section_leaves_section1_blank():
    out = enrich_document_data(
        {"no_of_sections": "2", "width": "28", "length": "56"}
    )
    # A double-wide's overall 28' width is NOT section 1's width — leave blank.
    assert not out.get("section_1_width")
    assert not out.get("section_1_length")


def test_no_dimensions_no_fill():
    out = enrich_document_data({"no_of_sections": "1"})
    assert not out.get("section_1_width")
    assert not out.get("section_1_length")


def test_existing_section1_dims_not_overwritten():
    out = enrich_document_data(
        {"no_of_sections": "1", "width": "16", "length": "76", "section_1_width": "15"}
    )
    assert out["section_1_width"] == "15"  # caller-provided value wins
    assert out["section_1_length"] == "76"
