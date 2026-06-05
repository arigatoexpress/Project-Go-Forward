"""Regression guard: full closing packets must never contain a blank page.

Clients reported "pages 40+ were blank" in downloaded closing packets. Root
cause class: when filled AcroForm/XFA pages are merged page-by-page, the
document-level form structure is dropped, so any field whose value was not
baked into a page-level appearance stream renders blank in non-Acrobat
viewers. Unit tests that only check generation *logic* miss this because the
data layer still reports values.

This test generates the real full packets with synthetic data and asserts
that every page carries actual renderable content, using a dependency-free
structural heuristic (no poppler/rasterization required, so it runs in CI and
in the Cloud Run container). For pixel-level ground truth during local/manual
QA, use ``scripts/pdf_packet_qa.py --render`` instead.
"""

from __future__ import annotations

import os
import sys

import pytest
from pypdf import PdfReader

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.document_engine_v2 import generate_batch  # noqa: E402
from config.field_map_loader import get_field_map  # noqa: E402

# Realistic, non-placeholder data (the quality gate rejects "Sample"/"Test").
PACKET_DATA = {
    "buyer_name": "Jordan Avery Brooks",
    "co_buyer_name": "Taylor Morgan Brooks",
    "buyer_address": "456 Meadow Lane",
    "buyer_city": "Austin",
    "buyer_county": "Travis",
    "buyer_state": "TX",
    "buyer_zip": "78701",
    "buyer_phone": "512-555-0123",
    "manufacturer": "TRU Homes",
    "model": "The Marvel",
    "date_of_manufacture": "2026-01-15",
    "manufacturer_address": "500 Factory Road",
    "manufacturer_city": "Fort Worth",
    "manufacturer_state": "TX",
    "manufacturer_zip": "76101",
    "serial_number_1": "TRU-987654",
    "label_number_1": "TEX482913",
    "no_of_sections": "Double Section",
    "sales_price": "95000",
    "down_payment": "5000",
    "loan_term": "240",
    "apr": "8.5",
    "dealer_name": "Texas Home Outlet",
}


def _page_has_content(page) -> bool:
    """Dependency-free check that a page would render *something* visible.

    A page counts as non-blank if ANY of these hold:
      * it extracts non-trivial text;
      * it references an image/form XObject in its resources;
      * its content stream has a meaningful number of drawing operators;
      * it carries a widget annotation with a value AND a baked appearance.
    """
    try:
        text = (page.extract_text() or "").strip()
    except Exception:
        text = ""
    if len(text) >= 15:
        return True

    resources = page.get("/Resources")
    if resources is not None:
        resources = resources.get_object()
        xobjects = resources.get("/XObject")
        if xobjects is not None and len(xobjects.get_object()):
            return True

    # Non-trivial vector content (lines, boxes, etc.) in the content stream.
    try:
        data = page.get_contents()
        if data is not None and len(data.get_data()) > 80:
            return True
    except Exception:
        pass

    # A filled widget with a baked appearance stream renders even after merge.
    for annot in page.get("/Annots") or []:
        obj = annot.get_object()
        if obj.get("/Subtype") == "/Widget" and obj.get("/V") not in (None, ""):
            if "/AP" in obj:
                return True

    return False


def _blank_pages(pdf_path: str) -> list[int]:
    reader = PdfReader(pdf_path)
    return [i for i, page in enumerate(reader.pages, start=1) if not _page_has_content(page)]


@pytest.mark.parametrize("packet_name", ["full_closing_new", "full_closing_used"])
def test_full_packet_has_no_blank_pages(packet_name):
    templates = get_field_map()["packets"][packet_name]["templates"]
    result = generate_batch(templates, dict(PACKET_DATA), merge=True)

    assert result["success"], f"{packet_name} failed to generate: {result.get('message')}"
    assert result["successful"] == result["total"], (
        f"{packet_name}: only {result['successful']}/{result['total']} documents generated; "
        f"failed: {[d['template_name'] for d in result['documents'] if not d['success']]}"
    )

    merged = result.get("merged")
    assert merged is not None, f"{packet_name}: merge step did not produce a packet"

    blanks = _blank_pages(merged["__path__"]) if "__path__" in merged else None
    # The merge dict exposes a download filename, not a local path, so resolve
    # the freshest merged artifact from the output directory.
    if blanks is None:
        import glob
        from tools.document_tools import OUTPUT_DIR

        candidates = sorted(
            glob.glob(os.path.join(OUTPUT_DIR, "Documents_*.pdf")),
            key=os.path.getmtime,
        )
        assert candidates, f"{packet_name}: no merged PDF found on disk"
        blanks = _blank_pages(candidates[-1])

    assert not blanks, f"{packet_name}: blank pages detected at {blanks} (clients see these as empty)"
