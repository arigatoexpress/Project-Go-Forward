"""Field-level fill completeness for generated documents.

Complements tests/test_packet_no_blank_pages.py (which guards blank *pages*).
This guards blank *fields*: the bug class where a packet generates but required
information never renders — e.g. a serial/label line left empty deep in a 50+
page closing packet.

Two layers:

* ``test_serial_label_combined_is_enriched`` — pure unit guard for the specific
  regression we fixed: the v2 enrichment path must compute
  ``serial_label_combined`` (the v1 engine did, the v2 path silently did not, so
  TDHCA_1067's Label/Serial field rendered blank). No external deps.

* ``test_packet_fields_render`` — full end-to-end check via the shared
  ``scripts/validate_document_fills`` validator. Generates each packet, renders
  it with poppler's ``pdftotext`` (ground truth for what a viewer shows), and
  asserts required fields render, values survive the merge, and no page is
  blank. Skipped automatically when ``pdftotext`` is unavailable.
"""

from __future__ import annotations

import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.field_map_loader import get_field_map  # noqa: E402
from tools.document_quality import enrich_document_data  # noqa: E402


def test_serial_label_combined_is_enriched():
    """The v2 enrichment must build serial_label_combined from serial/label."""
    enriched = enrich_document_data(
        {
            "serial_number_1": "TRU0987654A",
            "label_number_1": "TEX0482913",
        }
    )
    combined = enriched.get("serial_label_combined")
    assert combined, "serial_label_combined was not computed by enrich_document_data"
    assert "TRU0987654A" in combined
    assert "TEX0482913" in combined


def test_serial_label_combined_respects_existing_value():
    """An explicit serial_label_combined is not overwritten."""
    enriched = enrich_document_data(
        {"serial_number_1": "AAA", "serial_label_combined": "PRESET VALUE"}
    )
    assert enriched["serial_label_combined"] == "PRESET VALUE"


@pytest.mark.skipif(
    shutil.which("pdftotext") is None,
    reason="pdftotext (poppler-utils) not installed; render-level check skipped",
)
@pytest.mark.parametrize(
    "packet_name",
    ["standard_closing", "used_home_closing", "full_closing_new", "full_closing_used"],
)
def test_packet_fields_render(packet_name):
    from scripts.validate_document_fills import validate_packet

    result = validate_packet(packet_name, get_field_map())

    assert not result["generation_errors"], (
        f"{packet_name}: generation errors: {result['generation_errors']}"
    )
    assert not result["required_field_failures"], (
        f"{packet_name}: required fields did not render: {result['required_field_failures']}"
    )
    assert not result["merged_blank_pages"], (
        f"{packet_name}: blank pages in merged packet: {result['merged_blank_pages']}"
    )
    assert not result["merge_dropped_values"], (
        f"{packet_name}: values rendered standalone but lost in merge: "
        f"{result['merge_dropped_values']}"
    )
    assert result["ok"], f"{packet_name}: fill completeness check failed"
