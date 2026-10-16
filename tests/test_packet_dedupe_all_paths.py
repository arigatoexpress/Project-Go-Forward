"""No packet path may print the same form twice (Mark's Oct 1 packet).

The Document Center builds packets through ``/api/documents/generate-batch``,
which skipped the duplicate check, so one packet carried both the 2013 Retail
Monitoring Checklist and the Rev. 10/2024 Compliance Review Checklist, and two
editions of the "Need to Vacate Home" notice (Form 1074 Eff. 07/03/2011 and
Rev. 11/15/2019). Every path now runs ``tools.form_set.dedupe_packet_templates``
and keeps the newer edition.
"""

from __future__ import annotations

import os
import sys

import pytest
from pypdf import PdfWriter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools.document_engine as v1  # noqa: E402
import tools.document_engine_v2 as v2  # noqa: E402
from config.field_map_loader import get_field_map, get_form_set_rules  # noqa: E402
from tools.form_set import dedupe_packet_templates  # noqa: E402

RETMONLIST_2013 = "TDHCA-retmonlist.pdf"
COMPLIANCE_REVIEW_2024 = "TDHCA_1058_Compliance_Review.pdf"
VACATE_2019 = "TDHCA_1074_Vacate_If_No_Financing.pdf"
VACATE_2011 = "TDHCA_Disclosure.pdf"

OLDER_EDITIONS = {RETMONLIST_2013, VACATE_2011}
NEWER_EDITIONS = {COMPLIANCE_REVIEW_2024, VACATE_2019}

PACKETS = sorted(get_field_map().get("packets", {}))
DOCUMENT_CENTER_SELECTION = [
    *get_field_map()["packets"]["full_closing_new"]["templates"],
    "TMHA_SalesContract.pdf",
    VACATE_2011,
]


def _assert_clean(templates: list[str]) -> None:
    dupes = sorted({t for t in templates if templates.count(t) > 1})
    assert not dupes, f"exact duplicate forms: {dupes}"
    for group in get_form_set_rules().get("duplicate_groups", []):
        present = [t for t in group["keep_first_present"] if t in templates]
        assert len(present) <= 1, f"{group['document']} printed {len(present)}x: {present}"


@pytest.fixture
def v2_calls(monkeypatch):
    calls: list[str] = []

    def fake_generate_document(template_name, data, output_filename=None, deal_id=None):
        calls.append(template_name)
        return {"success": True, "filename": None, "file_path": None}

    monkeypatch.setattr(v2._engine, "generate_document", fake_generate_document)
    monkeypatch.setattr(v2._engine, "_quality_gate", lambda data, templates: None)
    return calls


@pytest.fixture
def v1_calls(monkeypatch, tmp_path):
    calls: list[str] = []
    blank = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with open(blank, "wb") as fh:
        writer.write(fh)

    def fake_generate_document(template_name, data, output_filename=None, deal_id=None):
        calls.append(template_name)
        copy = tmp_path / f"{len(calls)}.pdf"
        copy.write_bytes(blank.read_bytes())
        return {"success": True, "file_path": str(copy), "filename": copy.name}

    monkeypatch.setattr(v1, "generate_document", fake_generate_document)
    monkeypatch.setattr(v1, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(v1, "upload_to_gcs", lambda *a, **k: None)
    return calls


def test_shared_dedupe_keeps_the_newer_edition_of_each_form():
    out = dedupe_packet_templates(
        [RETMONLIST_2013, VACATE_2011, COMPLIANCE_REVIEW_2024, VACATE_2019, VACATE_2019]
    )
    assert out == [COMPLIANCE_REVIEW_2024, VACATE_2019]


def test_duplicate_groups_reference_real_templates():
    templates = get_field_map()["templates"]
    for group in get_form_set_rules()["duplicate_groups"]:
        for tpl in group["keep_first_present"]:
            assert tpl in templates, f"{group['document']}: {tpl} is not a mapped template"


def test_document_center_batch_path_dedupes(v2_calls):
    result = v2.generate_batch(list(DOCUMENT_CENTER_SELECTION), {}, merge=False)

    _assert_clean(v2_calls)
    assert NEWER_EDITIONS <= set(v2_calls)
    assert not OLDER_EDITIONS & set(v2_calls)
    assert v2_calls.count("TMHA_SalesContract.pdf") == 1
    assert set(result["duplicates_removed"]) == OLDER_EDITIONS | {"TDHCA_ArbitrationAgreement.pdf"}


@pytest.mark.parametrize("packet", PACKETS)
@pytest.mark.parametrize("condition", ["New", "Used"])
def test_named_packet_path_dedupes(v2_calls, packet, condition):
    v2.generate_packet(packet, {"condition": condition})
    _assert_clean(v2_calls)
    assert not OLDER_EDITIONS & set(v2_calls)


@pytest.mark.parametrize("packet", PACKETS)
def test_legacy_v1_packet_path_dedupes(v1_calls, packet):
    result = v1.generate_packet(packet, {"buyer_name": "Jordan Brooks"})
    assert result["success"], result
    _assert_clean(v1_calls)
    _assert_clean(result["documents_included"])
    assert not OLDER_EDITIONS & set(v1_calls)


def test_legacy_v1_batch_path_dedupes(v1_calls):
    v1.generate_batch(list(DOCUMENT_CENTER_SELECTION), {"buyer_name": "Jordan Brooks"})
    _assert_clean(v1_calls)
    assert not OLDER_EDITIONS & set(v1_calls)


def test_http_routes_use_the_deduping_engine():
    import main

    assert main.engine_generate_batch is v2.generate_batch
    assert main.engine_generate_packet is v2.generate_packet
