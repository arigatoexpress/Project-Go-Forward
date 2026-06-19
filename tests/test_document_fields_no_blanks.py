"""Regression guard: a fully-populated customer must not leave entry data blank.

Client report (Mark Willcott, 2026-06): staff "filled out all the blanks" in the
Document Center entry form, generated a full package, then highlighted fields
that came out blank. Co-owner Celeste specifically flagged the manufactured-home
HUD/serial "label number".

This test pins two things so a regression can never silently reintroduce blanks:

  1. ``enrich_document_data`` composes the buyer- and co-buyer-name and the
     mailing address from their first/last/part fields. The frontend composes
     these in ``toDocumentData``; the backend backstop must do the same so the
     deal-based path (and any non-frontend caller) does not drop the co-buyer
     name or mailing block.
  2. A real generated document carries the previously-blank entry values —
     including the HUD label number and the co-buyer name composed only from
     first/last parts — either as an AcroForm value or as baked overlay text.

The assertion is CI-safe: it reads back both the AcroForm ``/V`` values and the
extracted page text (THO templates fill via AcroForm appearances AND text
overlays, so a value may live in either), with no poppler dependency.
"""

from __future__ import annotations

import os
import sys

from pypdf import PdfReader

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.field_map_loader import get_field_map  # noqa: E402
from tools.document_engine_v2 import generate_document  # noqa: E402
from tools.document_quality import enrich_document_data  # noqa: E402

# Fully-populated, realistic customer (placeholders are rejected by the quality
# gate). Co-buyer name and mailing block are supplied ONLY as parts so the test
# exercises backend composition. installer_* default to THO via enrichment.
FULL_CUSTOMER: dict[str, object] = {
    "buyer_first_name": "Jordan",
    "buyer_last_name": "Brooks",
    "buyer_address": "456 Meadow Lane",
    "buyer_city": "Austin",
    "buyer_county": "Travis",
    "buyer_state": "TX",
    "buyer_zip": "78701",
    "buyer_phone": "512-555-0123",
    "buyer_email": "jordan.brooks@example.com",
    # Co-buyer supplied as PARTS only (no precomposed co_buyer_name):
    "co_buyer_first_name": "Taylor",
    "co_buyer_last_name": "Brooks",
    "co_buyer_phone": "512-555-0144",
    # Mailing supplied as PARTS only (no precomposed composites):
    "mailing_address": "456 Meadow Lane",
    "mailing_city": "Austin",
    "mailing_state": "TX",
    "mailing_zip": "78701",
    "is_new": True,
    "manufacturer": "TRU Homes",
    "model": "The Marvel",
    "year": "2026",
    "serial_number_1": "TRU0987654A",
    "serial_number_2": "TRU0987654B",
    "label_number_1": "TEX0482913",
    "label_number_2": "TEX0482914",
    "no_of_sections": "Double Section",
    "sq_ft": "2128",
    "wind_zone": "2",
    "date_of_manufacture": "2026-01-15",
    "manufacturer_address": "500 Factory Road",
    "manufacturer_city": "Fort Worth",
    "manufacturer_state": "TX",
    "manufacturer_zip": "76101",
    "sales_price": "95000",
    "down_payment": "5000",
    "loan_term": "240",
    "apr": "8.5",
    "payment_start_date": "2026-08-01",
    "creditor_name": "21st Mortgage Corporation",
    "creditor_address": "620 Market Street",
    "creditor_city_state_zip": "Knoxville, TN 37902",
    "creditor_phone": "865-555-0100",
    "salesrep": "Lee Carter",
}


# ─── Backend composition (the bug fix) ──────────────────────────────────────


def test_enrich_composes_co_buyer_name_from_parts():
    """Co-buyer name must compose from first/last like the buyer name does."""
    enriched = enrich_document_data(
        {"co_buyer_first_name": "Taylor", "co_buyer_last_name": "Brooks"}
    )
    assert enriched.get("co_buyer_name") == "Taylor Brooks"


def test_enrich_preserves_precomposed_co_buyer_name():
    enriched = enrich_document_data(
        {
            "co_buyer_name": "Taylor M Brooks",
            "co_buyer_first_name": "Taylor",
            "co_buyer_last_name": "Brooks",
        }
    )
    assert enriched.get("co_buyer_name") == "Taylor M Brooks"


def test_enrich_composes_mailing_block_from_parts():
    enriched = enrich_document_data(
        {
            "mailing_address": "500 Main St",
            "mailing_city": "Houston",
            "mailing_state": "TX",
            "mailing_zip": "77002",
        }
    )
    assert enriched.get("mailing_city_state_zip") == "Houston, TX 77002"
    assert enriched.get("mailing_full_address") == "500 Main St, Houston, TX 77002"


# ─── End-to-end render (the client-visible blanks) ──────────────────────────


def _rendered_values(pdf_path: str) -> str:
    """Concatenate AcroForm /V values and extracted page text.

    THO fills via AcroForm appearance streams AND flattened text overlays, so a
    value may render through either path. Reading both is the CI-safe proxy for
    "what the client sees" (no poppler/rasterization required).
    """
    reader = PdfReader(pdf_path)
    parts: list[str] = []
    for value in (reader.get_fields() or {}).values():
        v = value.get("/V")
        if v is not None:
            parts.append(str(v))
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            pass
    return "\n".join(parts)


def _assert_renders(pdf_path: str, needles: list[str]) -> None:
    haystack = _rendered_values(pdf_path)
    missing = [n for n in needles if n not in haystack]
    assert not missing, f"{os.path.basename(pdf_path)} did not render: {missing}"


def test_statement_of_ownership_renders_label_and_serial(tmp_path, monkeypatch):
    """The Statement of Ownership (Celeste's form) must carry the label number."""
    import tools.document_tools as dt

    monkeypatch.setattr(dt, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(dt, "upload_to_gcs", lambda *a, **k: None)

    result = generate_document("TDHCA_1023-Statement-Ownership.pdf", dict(FULL_CUSTOMER))
    assert result["success"], result.get("message")

    _assert_renders(
        result["file_path"],
        [
            "TEX0482913",  # label_number_1 (HUD label — Celeste's "label number")
            "TEX0482914",  # label_number_2
            "TRU0987654A",  # serial_number_1
            "TRU Homes",  # manufacturer
            "456 Meadow Lane",  # installation address
        ],
    )


def test_sales_contract_renders_label_number(tmp_path, monkeypatch):
    """The TMHA Sales Contract (Mark's baseline) must carry the HUD label."""
    import tools.document_tools as dt

    monkeypatch.setattr(dt, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(dt, "upload_to_gcs", lambda *a, **k: None)

    result = generate_document("TMHA_SalesContract.pdf", dict(FULL_CUSTOMER))
    assert result["success"], result.get("message")
    _assert_renders(result["file_path"], ["TEX0482913", "TRU0987654A"])


def test_consumer_disclosure_renders_composed_co_buyer_name(tmp_path, monkeypatch):
    """Co-buyer name (composed from parts by enrichment) must not be blank."""
    import tools.document_tools as dt

    monkeypatch.setattr(dt, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(dt, "upload_to_gcs", lambda *a, **k: None)

    # Sanity: this template actually maps co_buyer_name.
    cfg = get_field_map()["templates"]["TDHCA_1038_Consumer_Disclosure.pdf"]
    assert "co_buyer_name" in set(cfg.get("field_map", {}).values())

    result = generate_document("TDHCA_1038_Consumer_Disclosure.pdf", dict(FULL_CUSTOMER))
    assert result["success"], result.get("message")
    _assert_renders(result["file_path"], ["Taylor Brooks", "Jordan Brooks"])
