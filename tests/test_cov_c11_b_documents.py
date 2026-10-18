"""Coverage campaign c11-b — document-generation pipeline branch closure.

Hermetic tests (no network, no Firestore emulator, no GCS) that exercise the
previously-missing branches of:

  * tools/form_extraction.py    — template-not-found / all-PII / no-conversation
                                  early returns, markdown-fence stripping,
                                  ImportError / generic-exception LLM paths, and
                                  every branch of _get_conversation_text (ADK
                                  runner session + conversation-memory fallback).
  * tools/document_engine_v2.py — _coerce_flag parsing, ProductModel/FinancialModel
                                  validator branches, __init__ schema-missing and
                                  legacy-load-failure, generate_document validation
                                  failure / unknown template / missing PDF /
                                  legacy money-format failure / str checkbox /
                                  Drive mirror / fill exception, generate_batch
                                  merge + Drive mirror + merge exception,
                                  generate_packet v2-overrides-legacy, and
                                  get_template_fields (+ its proxy).
  * tools/document_engine.py    — _has_value/_full_address/_set_status_flags unit
                                  branches, own/rent normalization, checkbox
                                  "Yes", template-file-not-found, Drive mirror,
                                  fill exception, packet cover TypeError fallback,
                                  cover-skip, no-documents, packet Drive mirror,
                                  cleanup OSError, packet merge exception, batch
                                  Drive mirror, batch merge exception,
                                  get_all_field_definitions, _to_float failure.
  * tools/document_tools.py     — ToolContext/OUTPUT_DIR import-time fallbacks
                                  (module reload), GCS-unavailable download/list,
                                  summary-PDF page break, no-annotation widget
                                  helpers, checkbox-state introspection failure,
                                  no-on-state / falsy checkbox resolution,
                                  _draw_fit_text empty/overflow branches, overlay
                                  page-out-of-range, fill failure -> summary
                                  fallback -> summary failure, and both _fill_xfa
                                  early-return paths.

Run: python -m pytest tests/test_cov_c11_b_documents.py -q
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import types
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import google.genai  # noqa: F401,E402  (real submodule must pre-exist for patching)

from tools import document_engine as de  # noqa: E402
from tools import document_engine_v2 as ev2  # noqa: E402
from tools import document_tools as dt  # noqa: E402
from tools import form_extraction as fe  # noqa: E402

V2_SAMPLE_DATA = {
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
    "no_of_sections": "Double Section",
    "sales_price": "95000",
    "down_payment": "5000",
    "loan_term": "240",
    "apr": "8.5",
}

V1_SAMPLE_DATA = {
    "buyer_name": "John Test Buyer",
    "buyer_address": "123 Main Street",
    "buyer_city": "Houston",
    "buyer_county": "Harris",
    "buyer_state": "TX",
    "buyer_zip": "77001",
    "buyer_phone": "(281) 555-0100",
    "manufacturer": "Clayton Homes",
    "model": "The Freedom 3256",
    "year": "2026",
    "serial_number_1": "CLH123456TX",
    "label_number_1": "TEX0012345",
    "no_of_sections": "2",
    "sales_price": "125000",
    "down_payment": "25000",
    "seller_name": "Texas Home Outlet",
    "seller_rbi": "RBI-12345",
    "date_of_sale": "02/12/2026",
}


def _run(coro):
    return asyncio.run(coro)


def _blank_pdf(path) -> None:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with open(path, "wb") as fh:
        writer.write(fh)


def _capture_fill_factory(monkeypatch, module, tmp_path, captured):
    """fill_pdf_form stand-in: records pdf_data and writes a tiny valid PDF."""

    def _capture(template_path, pdf_data, output_filename):
        captured[os.path.basename(template_path)] = dict(pdf_data)
        out_path = tmp_path / output_filename
        _blank_pdf(out_path)
        return str(out_path)

    monkeypatch.setattr(module, "fill_pdf_form", _capture)
    return _capture


# ---------------------------------------------------------------------------
# tools/form_extraction.py — extract_form_data_from_session early returns
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, text):
        self.text = text


class _FakeModels:
    def __init__(self, captured, response_json):
        self._captured = captured
        self._response_json = response_json

    def generate_content(self, model=None, contents=None):
        self._captured["model"] = model
        self._captured["contents"] = contents
        return _FakeResponse(self._response_json)


class _FakeClient:
    def __init__(self, captured, response_json):
        self.models = _FakeModels(captured, response_json)


def _fake_convo(text):
    async def _get(session_id, runner=None):
        return text

    return _get


class TestFormExtractionBranches:
    def test_template_not_found_returns_early(self):
        result = _run(fe.extract_form_data_from_session("sess-x", "zzz-no-such-template.pdf"))
        assert result == {"extracted_data": {}, "message": "Template not found"}

    def test_all_pii_template_returns_no_extractable_fields(self, monkeypatch):
        monkeypatch.setattr(
            fe,
            "get_fields_for_template",
            lambda _name: {"buyer_ssn": {"pii": True, "label": "SSN"}},
        )
        with patch.object(fe, "_get_conversation_text", side_effect=_fake_convo("hello")):
            result = _run(fe.extract_form_data_from_session("sess-x", "creditapp.pdf"))
        assert result == {"extracted_data": {}, "message": "No extractable fields"}

    def test_no_conversation_history_returns_early(self, monkeypatch):
        with patch.object(fe, "_get_conversation_text", side_effect=_fake_convo(None)):
            result = _run(fe.extract_form_data_from_session("sess-x", "creditapp.pdf"))
        assert result == {"extracted_data": {}, "message": "No conversation history found"}

    def test_markdown_fences_are_stripped_before_json_parse(self):
        captured: dict = {}
        fenced = '```json\n{"buyer_name": "Jane Doe"}\n```'
        with (
            patch("google.genai.Client", lambda *a, **k: _FakeClient(captured, fenced)),
            patch.object(fe, "_get_conversation_text", side_effect=_fake_convo("user: Jane")),
        ):
            result = _run(fe.extract_form_data_from_session("sess-x", "creditapp.pdf"))
        assert result["extracted_data"] == {"buyer_name": "Jane Doe"}

    def test_genai_import_error_fails_softly(self, monkeypatch):
        import google

        # `from google import genai` consults the parent-package attribute before
        # the import system, so both the sys.modules entry and the attribute
        # must be removed for the ImportError branch to trigger.
        monkeypatch.setitem(sys.modules, "google.genai", None)
        monkeypatch.delattr(google, "genai", raising=False)
        with patch.object(fe, "_get_conversation_text", side_effect=_fake_convo("user: hi")):
            result = _run(fe.extract_form_data_from_session("sess-x", "creditapp.pdf"))
        assert result["message"] == "AI extraction not available (genai not installed)"
        assert result["extracted_data"] == {}

    def test_llm_exception_fails_softly(self):
        with (
            patch("google.genai.Client", side_effect=RuntimeError("api down")),
            patch.object(fe, "_get_conversation_text", side_effect=_fake_convo("user: hi")),
        ):
            result = _run(fe.extract_form_data_from_session("sess-x", "creditapp.pdf"))
        assert result["message"].startswith("Extraction failed:")
        assert result["extracted_data"] == {}


# ---------------------------------------------------------------------------
# tools/form_extraction.py — _get_conversation_text runner + memory paths
# ---------------------------------------------------------------------------


class _Part:
    def __init__(self, text=None):
        if text is not None:
            self.text = text


class _Event:
    def __init__(self, author=None, content=None):
        if author is not None:
            self.author = author
        self.content = content


class _Content:
    def __init__(self, parts):
        self.parts = parts


class _SessionService:
    def __init__(self, session="unset", error=None):
        self._session = session
        self._error = error

    async def get_session(self, **kwargs):
        assert kwargs["app_name"] == "root_agent"
        assert kwargs["session_id"] == "sess-1"
        if self._error is not None:
            raise self._error
        return self._session


def _memory_module(context=None, error=None):
    mod = types.ModuleType("conversation_memory")

    class FakeMemory:
        def get_context(self, session_id):
            if error is not None:
                raise error
            return context

    mod.ConversationMemory = FakeMemory
    return mod


class TestConversationText:
    def test_runner_session_last_20_events_formatted(self):
        events = []
        for i in range(25):
            events.append(_Event(author="user", content=_Content([_Part(f"message {i}")])))
        # Noise events that must be skipped: no content, empty text, a part
        # without a text attribute, and content without parts.
        events.append(_Event(author="user", content=None))
        events.append(_Event(author="user", content=_Content([_Part("   ")])))
        events.append(_Event(author="user", content=_Content([_Part(), object()])))
        events.append(_Event(author="user", content=SimpleNamespace()))
        events.append(_Event(content=_Content([_Part("orphan text")])))  # no author
        session = SimpleNamespace(events=events)
        runner = SimpleNamespace(session_service=_SessionService(session=session))

        text = _run(fe._get_conversation_text("sess-1", runner))

        # 30 events total -> only the last 20 survive the events[-20:] slice.
        assert "message 10" in text
        assert "message 9" not in text
        assert "user: message 24" in text
        assert "unknown: orphan text" in text  # author defaults via getattr

    def test_runner_session_none_falls_back_to_memory(self, monkeypatch):
        runner = SimpleNamespace(session_service=_SessionService(session=None))
        monkeypatch.setitem(
            sys.modules,
            "conversation_memory",
            _memory_module(context=SimpleNamespace(recent_messages=["m1", "m2"])),
        )
        text = _run(fe._get_conversation_text("sess-1", runner))
        assert text == "m1\nm2"

    def test_runner_error_falls_back_to_memory(self, monkeypatch):
        runner = SimpleNamespace(session_service=_SessionService(error=RuntimeError("adk down")))
        monkeypatch.setitem(
            sys.modules,
            "conversation_memory",
            _memory_module(context=SimpleNamespace(recent_messages=["only"])),
        )
        text = _run(fe._get_conversation_text("sess-1", runner))
        assert text == "only"

    def test_no_runner_uses_memory(self, monkeypatch):
        monkeypatch.setitem(
            sys.modules,
            "conversation_memory",
            _memory_module(context=SimpleNamespace(recent_messages=["a", "b", "c"])),
        )
        text = _run(fe._get_conversation_text("sess-1", runner=None))
        assert text == "a\nb\nc"

    def test_memory_error_returns_none(self, monkeypatch):
        runner = SimpleNamespace(session_service=_SessionService(session=None))
        monkeypatch.setitem(
            sys.modules,
            "conversation_memory",
            _memory_module(error=RuntimeError("firestore down")),
        )
        assert _run(fe._get_conversation_text("sess-1", runner)) is None

    def test_memory_context_without_recent_messages_returns_none(self, monkeypatch):
        monkeypatch.setitem(
            sys.modules,
            "conversation_memory",
            _memory_module(context=SimpleNamespace()),
        )
        assert _run(fe._get_conversation_text("sess-1", runner=None)) is None


# ---------------------------------------------------------------------------
# tools/document_engine_v2.py — _coerce_flag / pydantic models
# ---------------------------------------------------------------------------


class TestV2FlagAndModels:
    def test_coerce_flag_branches(self):
        assert ev2._coerce_flag(True) is True
        assert ev2._coerce_flag(None) is None
        assert ev2._coerce_flag(" YES ") is True
        assert ev2._coerce_flag("On") is True
        assert ev2._coerce_flag("1") is True
        assert ev2._coerce_flag("Off") is False
        assert ev2._coerce_flag("0") is False
        assert ev2._coerce_flag("n") is False
        assert ev2._coerce_flag("maybe") is None

    def test_resolve_form_set_without_rules_dedupes(self, monkeypatch):
        monkeypatch.setattr(ev2, "get_form_set_rules", lambda: {})
        assert ev2.resolve_form_set(["a.pdf", "a.pdf", "b.pdf"], {}) == ["a.pdf", "b.pdf"]

    def test_product_model_both_new_and_used_defaults_to_new(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="tools.document_engine_v2"):
            model = ev2.ProductModel(
                manufacturer="M", model="M2", serial_number_1="S", is_new=True, is_used=True
            )
        assert model.is_new is True and model.is_used is False
        assert any("Both is_new and is_used" in r.message for r in caplog.records)

    def test_product_model_neither_new_nor_used_defaults_to_new(self):
        model = ev2.ProductModel(
            manufacturer="M", model="M2", serial_number_1="S", is_new=False, is_used=False
        )
        assert model.is_new is True and model.is_used is False

    def test_financial_model_none_and_blank_defaults(self):
        f = ev2.FinancialModel(sales_price=None, down_payment=None)
        assert f.sales_price == Decimal("0") and f.down_payment == Decimal("0")

        f = ev2.FinancialModel(loan_term=None)
        assert f.loan_term == 240

        f = ev2.FinancialModel(apr=None)
        assert f.apr == Decimal("7.5")

        f = ev2.FinancialModel(apr=8.5)  # non-str, non-None passthrough
        assert f.apr == Decimal("8.5")

    def test_financial_model_blank_optional_money_becomes_none(self):
        f = ev2.FinancialModel(doc_fee="", amt_points="  ")
        assert f.doc_fee is None and f.amt_points is None

    def test_financial_model_non_string_optional_money_passthrough(self):
        f = ev2.FinancialModel(doc_fee=150, amt_points=25)
        assert f.doc_fee == Decimal("150") and f.amt_points == Decimal("25")

    def test_financial_model_none_escrow_defaults_to_zero(self):
        f = ev2.FinancialModel(monthly_escrow=None)
        assert f.monthly_escrow == Decimal("0")

    def test_financial_model_zero_apr_uses_simple_division(self):
        f = ev2.FinancialModel(sales_price="24000", down_payment="0", loan_term=12, apr=0)
        assert f.monthly_payment == Decimal("2000.00")

    def test_flat_payload_carries_meta_keys(self):
        payload = ev2.UnifiedPayload.from_flat_payload(
            {
                "buyer_name": "Meta Buyer",
                "buyer_address": "1 Meta Way",
                "manufacturer": "M",
                "model": "M2",
                "serial_number_1": "S1",
                "sales_price": "1000",
                "generation_id": "GEN99",
                "generated_by": "c11-b test",
            }
        )
        assert payload.meta.generation_id == "GEN99"
        assert payload.meta.generated_by == "c11-b test"


# ---------------------------------------------------------------------------
# tools/document_engine_v2.py — engine init / config helpers
# ---------------------------------------------------------------------------


class TestV2InitAndConfig:
    def test_init_missing_schema_file_uses_empty_schema(self, tmp_path, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="tools.document_engine_v2"):
            engine = ev2.DocumentEngineV2(schema_path=str(tmp_path / "missing.json"))
        assert engine.schema == {"domains": {}, "templates": {}, "packets": {}}
        assert any("Schema not found" in r.message for r in caplog.records)
        # Legacy field_map.json still loads on the default fallback path.
        assert engine.legacy_schema.get("templates")

    def test_init_legacy_schema_failure_uses_empty_legacy(self, monkeypatch, tmp_path, caplog):
        import logging

        def _boom():
            raise RuntimeError("field map unreadable")

        monkeypatch.setattr(ev2, "get_field_map", _boom)
        with caplog.at_level(logging.ERROR, logger="tools.document_engine_v2"):
            engine = ev2.DocumentEngineV2(schema_path=str(tmp_path / "missing.json"))
        assert engine.legacy_schema == {"templates": {}, "packets": {}}
        assert any("Failed to load legacy schema" in r.message for r in caplog.records)

    def test_template_config_legacy_only_returns_legacy(self):
        cfg = ev2._engine._template_config("TDHCA_1054_Habitability_Warranty.pdf")
        assert cfg is not None
        assert cfg.get("display_name")

    def test_template_config_merged_fallbacks(self):
        engine = ev2.DocumentEngineV2(schema_path="config/unified_schema.json")
        engine.schema = {"templates": {"T.pdf": {"display_name": "V2 Name", "field_map": {}}}}
        engine.legacy_schema = {
            "templates": {
                "T.pdf": {
                    "display_name": "Legacy Name",
                    "required_fields": ["buyer_name"],
                    "description": "legacy desc",
                }
            },
            "packets": {},
        }
        cfg = engine._template_config("T.pdf")
        assert cfg["display_name"] == "V2 Name"  # v2 wins the merge
        assert cfg["required_fields"] == ["buyer_name"]  # legacy fills the gap
        assert cfg["description"] == "legacy desc"


# ---------------------------------------------------------------------------
# tools/document_engine_v2.py — generate_document branches
# ---------------------------------------------------------------------------


class TestV2GenerateDocumentBranches:
    def test_payload_validation_failure(self):
        result = ev2.generate_document(
            "TMHA_SalesContract.pdf", {**V2_SAMPLE_DATA, "loan_term": "not-a-number"}
        )
        assert result["success"] is False
        assert result["message"].startswith("Validation failed:")

    def test_unknown_template_returns_not_found(self):
        result = ev2.generate_document("zzz-no-such-template.pdf", V2_SAMPLE_DATA)
        assert result["success"] is False
        assert "not found in unified or legacy schemas" in result["message"]

    def test_template_pdf_missing_on_disk(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ev2, "DOCUMENTS_DIR", str(tmp_path))  # empty dir
        result = ev2.generate_document("TDHCA_1054_Habitability_Warranty.pdf", V2_SAMPLE_DATA)
        assert result["success"] is False
        assert "Template file not found" in result["message"]

    def test_legacy_money_format_failure_falls_back_to_raw_string(self, monkeypatch, tmp_path):
        captured: dict = {}
        _capture_fill_factory(monkeypatch, ev2, tmp_path, captured)
        # tax_escrow_payment has no LEGACY_FIELD_SOURCES entry, so the raw data
        # value is used; a non-numeric string must hit the Decimal() except path.
        data = {**V2_SAMPLE_DATA, "buyer_county": "Harris", "tax_escrow_payment": "12.34.56"}
        result = ev2.generate_document("Internal_ImportantNoticeTax.pdf", data)
        assert result["success"] is True
        assert (
            captured["Internal_ImportantNoticeTax.pdf"][
                "topmostSubform[0].Page1[0].Tax_Escrow_Pmt[0]"
            ]
            == "12.34.56"
        )

    def test_legacy_checkbox_string_truthy_maps_to_on(self, monkeypatch, tmp_path):
        captured: dict = {}
        _capture_fill_factory(monkeypatch, ev2, tmp_path, captured)
        result = ev2.generate_document(
            "TMHA_SalesContract.pdf", {**V2_SAMPLE_DATA, "wheels_included_yes": "yes"}
        )
        assert result["success"] is True
        assert (
            captured["TMHA_SalesContract.pdf"]["topmostSubform[0].Page1[0].Wheels_Yes[0]"] == "On"
        )

    def test_domain_payload_with_person_key(self, monkeypatch, tmp_path):
        captured: dict = {}
        _capture_fill_factory(monkeypatch, ev2, tmp_path, captured)
        domain_payload = {
            "person": {
                "buyer_name": "Domain Buyer",
                "buyer_address": "456 Domain Ave",
                "buyer_city": "Austin",
                "buyer_state": "TX",
                "buyer_zip": "78701",
            },
            "product": {
                "manufacturer": "TRU Homes",
                "model": "The Marvel",
                "serial_number_1": "TRU-987654",
            },
            "financial": {"sales_price": "95000", "down_payment": "5000"},
            # Flat copies keep the quality gate (which reads flat keys) happy.
            **V2_SAMPLE_DATA,
        }
        result = ev2.generate_document("TDHCA_1054_Habitability_Warranty.pdf", domain_payload)
        assert result["success"] is True
        assert result["intelligence"]["mapping"]["source"] == "legacy_fallback"
        assert captured["TDHCA_1054_Habitability_Warranty.pdf"]

    def test_drive_mirror_on_single_document(self, monkeypatch, tmp_path):
        captured: dict = {}
        _capture_fill_factory(monkeypatch, ev2, tmp_path, captured)
        uploads = []
        monkeypatch.setattr(ev2, "ensure_deal_folder", lambda deal_id: f"folder-{deal_id}")
        monkeypatch.setattr(
            ev2,
            "upload_to_drive",
            lambda path, name, folder: uploads.append((path, name, folder)) or "drive://doc",
        )
        result = ev2.generate_document(
            "TDHCA_1054_Habitability_Warranty.pdf", V2_SAMPLE_DATA, deal_id="D-55"
        )
        assert result["success"] is True
        assert result["drive_url"] == "drive://doc"
        assert uploads and uploads[0][2] == "folder-D-55"

    def test_fill_failure_returns_failure(self, monkeypatch):
        def _boom(*_a, **_k):
            raise RuntimeError("fill exploded")

        monkeypatch.setattr(ev2, "fill_pdf_form", _boom)
        result = ev2.generate_document("TDHCA_1054_Habitability_Warranty.pdf", V2_SAMPLE_DATA)
        assert result["success"] is False
        assert result["message"] == "fill exploded"


# ---------------------------------------------------------------------------
# tools/document_engine_v2.py — batch / packet / template fields
# ---------------------------------------------------------------------------


class TestV2BatchAndPacket:
    def _prep_batch(self, monkeypatch, tmp_path, captured):
        _capture_fill_factory(monkeypatch, ev2, tmp_path, captured)
        monkeypatch.setattr(ev2, "OUTPUT_DIR", str(tmp_path))
        monkeypatch.setattr(ev2, "upload_to_gcs", lambda *_a, **_k: None)

    def test_batch_merges_and_mirrors_to_drive(self, monkeypatch, tmp_path):
        captured: dict = {}
        self._prep_batch(monkeypatch, tmp_path, captured)
        uploads = []
        monkeypatch.setattr(ev2, "ensure_deal_folder", lambda deal_id: f"folder-{deal_id}")
        monkeypatch.setattr(
            ev2,
            "upload_to_drive",
            lambda path, name, folder: uploads.append((name, folder)) or "drive://merged",
        )
        result = ev2.generate_batch(
            ["TMHA_LimitedWarranty.pdf", "TDHCA_1054_Habitability_Warranty.pdf"],
            V2_SAMPLE_DATA,
            merge=True,
            deal_id="D-77",
        )
        assert result["success"] is True
        assert result["merged"] is not None
        assert result["merged"]["drive_url"] == "drive://merged"
        assert (result["merged"]["filename"], "folder-D-77") in uploads

    def test_batch_merge_exception_swallowed(self, monkeypatch, tmp_path):
        captured: dict = {}
        self._prep_batch(monkeypatch, tmp_path, captured)

        def _boom(*_a, **_k):
            raise RuntimeError("gcs down")

        monkeypatch.setattr(ev2, "upload_to_gcs", _boom)
        result = ev2.generate_batch(
            ["TMHA_LimitedWarranty.pdf", "TDHCA_1054_Habitability_Warranty.pdf"],
            V2_SAMPLE_DATA,
            merge=True,
        )
        assert result["success"] is True
        assert result["merged"] is None  # merge failed but docs still generated

    def test_packet_not_found(self):
        result = ev2.generate_packet("zzz-no-such-packet", {})
        assert result["success"] is False
        assert "Packet zzz-no-such-packet not found" in result["message"]

    def test_v2_packet_definition_overrides_legacy(self, monkeypatch):
        # The real unified_schema.json has no "packets" key, so exercise the
        # v2-overrides-legacy branch on a dedicated engine instance.
        engine = ev2.DocumentEngineV2(schema_path="config/unified_schema.json")
        engine.schema["packets"] = {"c11_fake_packet": {"templates": ["A.pdf"]}}
        engine.legacy_schema = {
            "templates": {},
            "packets": {"c11_fake_packet": {"templates": ["B.pdf"], "display_name": "legacy"}},
        }
        calls = {}
        monkeypatch.setattr(
            engine,
            "generate_batch",
            lambda templates, data, merge=True, deal_id=None: calls.update(
                templates=list(templates), merge=merge, deal_id=deal_id
            )
            or {"success": True},
        )
        result = engine.generate_packet("c11_fake_packet", V2_SAMPLE_DATA, deal_id="D-1")
        assert result["success"] is True
        assert calls["templates"] == ["A.pdf"]  # v2 definition wins
        assert calls["merge"] is True and calls["deal_id"] == "D-1"


class TestV2TemplateFields:
    def test_get_template_fields_groups_by_domain(self):
        fields = ev2.get_template_fields("TMHA_SalesContract.pdf")
        assert fields is not None
        assert fields["template_name"] == "TMHA_SalesContract.pdf"
        for domain in ("person", "product", "financial"):
            assert domain in fields["sections"]
            assert fields["sections"][domain]
            first = fields["sections"][domain][0]
            assert {"field_name", "label", "type", "required"} <= set(first)

    def test_get_template_fields_unknown_returns_none(self):
        assert ev2.get_template_fields("zzz-no-such-template.pdf") is None

    def test_list_available_packets_includes_v2_definitions(self, monkeypatch):
        # unified_schema.json ships no "packets" key; inject one to prove the
        # v2 packet listing branch overrides the legacy listing.
        schema = {**ev2._engine.schema, "packets": {"c11_v2_packet": {"templates": ["A.pdf"]}}}
        monkeypatch.setattr(ev2._engine, "schema", schema)
        packets = {p["packet_name"]: p for p in ev2.list_available_packets()}
        entry = packets["c11_v2_packet"]
        assert entry["templates"] == ["A.pdf"]
        assert entry["template_count"] == 1
        assert entry["production_ready"] is True


# ---------------------------------------------------------------------------
# tools/document_engine.py (v1) — unit helpers + generation branches
# ---------------------------------------------------------------------------


class TestV1UnitHelpers:
    def test_has_value_blank_string(self):
        assert de._has_value("   ") is False
        assert de._has_value("x") is True

    def test_full_address_without_city_state_zip(self):
        assert de._full_address("123 Main St", None) == "123 Main St"

    def test_status_flags_all_branches(self):
        married = {"buyer_marital_status": "Married"}
        de._set_status_flags(married, "buyer_marital_status", "m", "u", "s")
        assert married["m"] is True and "u" not in married and "s" not in married

        single = {"buyer_marital_status": "single"}
        de._set_status_flags(single, "buyer_marital_status", "m", "u", "s")
        assert single["u"] is True and "m" not in single

        separated = {"buyer_marital_status": "SEPARATED"}
        de._set_status_flags(separated, "buyer_marital_status", "m", "u", "s")
        assert separated["s"] is True

    def test_normalize_own_rent_branches(self):
        own = de._normalize_document_data({"mailing_own_rent": "owns"})
        assert own["buyer_owns_residence"] is True
        rent = de._normalize_document_data({"mailing_own_rent": "rents"})
        assert rent["buyer_rents_residence"] is True

    def test_to_float_invalid_returns_none(self):
        assert de._to_float("not-a-number") is None
        assert de._to_float("1,234.5") == 1234.5


class TestV1GenerateBranches:
    def test_checkbox_value_marks_yes(self, monkeypatch, tmp_path):
        captured: dict = {}
        _capture_fill_factory(monkeypatch, de, tmp_path, captured)
        result = de.generate_document("TMHA_SalesContract.pdf", {**V1_SAMPLE_DATA, "is_new": True})
        assert result["success"] is True
        assert captured["TMHA_SalesContract.pdf"]["topmostSubform[0].Page1[0].New_Yes[0]"] == "Yes"

    def test_template_file_not_found(self, monkeypatch, tmp_path):
        monkeypatch.setattr(de, "DOCUMENTS_DIR", str(tmp_path))  # empty dir
        result = de.generate_document("TMHA_SalesContract.pdf", V1_SAMPLE_DATA)
        assert result["success"] is False
        assert "Template file not found" in result["message"]

    def test_drive_mirror_on_document(self, monkeypatch, tmp_path):
        captured: dict = {}
        _capture_fill_factory(monkeypatch, de, tmp_path, captured)
        uploads = []
        monkeypatch.setattr(de, "ensure_deal_folder", lambda deal_id: f"folder-{deal_id}")
        monkeypatch.setattr(
            de,
            "upload_to_drive",
            lambda path, name, folder: uploads.append((name, folder)) or "drive://v1doc",
        )
        result = de.generate_document(
            "TDHCA_1038_Consumer_Disclosure.pdf", V1_SAMPLE_DATA, deal_id="D-9"
        )
        assert result["success"] is True
        assert result["drive_url"] == "drive://v1doc"
        assert uploads[0][1] == "folder-D-9"

    def test_fill_exception_returns_failure(self, monkeypatch):
        def _boom(*_a, **_k):
            raise RuntimeError("v1 fill exploded")

        monkeypatch.setattr(de, "fill_pdf_form", _boom)
        result = de.generate_document("TDHCA_1038_Consumer_Disclosure.pdf", V1_SAMPLE_DATA)
        assert result["success"] is False
        assert result["message"] == "v1 fill exploded"

    def test_get_all_field_definitions(self):
        defs = de.get_all_field_definitions()
        assert isinstance(defs, dict) and defs


# ---------------------------------------------------------------------------
# tools/document_engine.py (v1) — packet branches
# ---------------------------------------------------------------------------


def _fake_packet_gen(tmp_path, fail_templates=()):
    def _fake(template_name, data, output_filename=None):
        if template_name in fail_templates:
            return {"success": False, "message": f"{template_name} broke"}
        filename = output_filename or f"{template_name}.pdf"
        path = tmp_path / filename
        _blank_pdf(path)
        return {"success": True, "file_path": str(path), "filename": filename}

    return _fake


class TestV1PacketBranches:
    def _prep(self, monkeypatch, tmp_path, fake_gen, packet_config, template_config=None):
        monkeypatch.setattr(de, "OUTPUT_DIR", str(tmp_path))
        monkeypatch.setattr(de, "upload_to_gcs", lambda *_a, **_k: None)
        monkeypatch.setattr(de, "get_packet_config", lambda _name: packet_config)
        monkeypatch.setattr(
            de,
            "get_template_config",
            template_config if template_config is not None else (lambda _name: None),
        )
        monkeypatch.setattr(de, "generate_document", fake_gen)

    def test_packet_with_no_templates(self, monkeypatch, tmp_path):
        self._prep(
            monkeypatch,
            tmp_path,
            _fake_packet_gen(tmp_path),
            {"display_name": "Empty", "templates": []},
        )
        result = de.generate_packet("empty_packet", {"buyer_name": "B"})
        assert result["success"] is False
        assert result["message"] == "Packet has no templates defined"

    def test_cover_type_error_fallback_for_mocked_generator(self, monkeypatch, tmp_path):
        # Mock generate_document with the legacy 3-arg signature (no deal_id):
        # the packet's primary call raises TypeError and the fallback retry
        # (without deal_id) must succeed.
        fake = _fake_packet_gen(tmp_path)
        packet_config = {
            "display_name": "P",
            "templates": ["B.pdf"],
            "include_cover_page": True,
            "cover_page_template": "Cover.pdf",
        }
        self._prep(monkeypatch, tmp_path, fake, packet_config, lambda name: {"display_name": name})
        result = de.generate_packet("cover_packet", {"buyer_name": "Cover Buyer"})
        assert result["success"] is True
        assert result["documents_included"] == ["Cover.pdf", "B.pdf"]

    def test_cover_failure_is_recorded_and_packet_continues(self, monkeypatch, tmp_path):
        fake = _fake_packet_gen(tmp_path, fail_templates={"Cover.pdf"})
        packet_config = {
            "display_name": "P",
            "templates": ["B.pdf"],
            "include_cover_page": True,
            "cover_page_template": "Cover.pdf",
        }
        self._prep(monkeypatch, tmp_path, fake, packet_config, lambda name: {"display_name": name})
        result = de.generate_packet("cover_packet", {"buyer_name": "Skip Buyer"})
        assert result["success"] is True
        assert result["documents_included"] == ["B.pdf"]
        assert result["documents_skipped"] == [
            {"template": "Cover.pdf", "reason": "Cover.pdf broke"}
        ]

    def test_packet_all_documents_fail(self, monkeypatch, tmp_path):
        fake = _fake_packet_gen(tmp_path, fail_templates={"Cover.pdf", "B.pdf"})
        packet_config = {
            "display_name": "P",
            "templates": ["B.pdf"],
            "include_cover_page": True,
            "cover_page_template": "Cover.pdf",
        }
        self._prep(monkeypatch, tmp_path, fake, packet_config, lambda name: {"display_name": name})
        result = de.generate_packet("cover_packet", {"buyer_name": "Fail Buyer"})
        assert result["success"] is False
        assert result["message"] == "No documents were generated successfully"
        assert len(result["documents_skipped"]) == 2

    def test_packet_mirrors_merged_pdf_to_drive(self, monkeypatch, tmp_path):
        fake = _fake_packet_gen(tmp_path)
        uploads = []
        monkeypatch.setattr(de, "ensure_deal_folder", lambda deal_id: f"folder-{deal_id}")
        monkeypatch.setattr(
            de,
            "upload_to_drive",
            lambda path, name, folder: uploads.append((name, folder)) or "drive://packet",
        )
        self._prep(monkeypatch, tmp_path, fake, {"display_name": "P", "templates": ["B.pdf"]})
        result = de.generate_packet("drive_packet", {"buyer_name": "Drive Buyer"}, deal_id="D-1")
        assert result["success"] is True
        assert result["drive_url"] == "drive://packet"
        assert uploads == [(result["filename"], "folder-D-1")]

    def test_packet_cleanup_oserror_is_swallowed(self, monkeypatch, tmp_path):
        fake = _fake_packet_gen(tmp_path)
        self._prep(monkeypatch, tmp_path, fake, {"display_name": "P", "templates": ["B.pdf"]})
        # Scoped os shim: only tools.document_engine sees the failing remove().
        fake_os = types.ModuleType("os")
        fake_os.path = os.path
        fake_os.makedirs = os.makedirs
        fake_os.remove = lambda _p: (_ for _ in ()).throw(OSError("file locked"))
        monkeypatch.setattr(de, "os", fake_os)

        result = de.generate_packet("locked_packet", {"buyer_name": "Lock Buyer"})
        assert result["success"] is True
        assert os.path.exists(result["file_path"])  # merge output survives

    def test_packet_merge_failure_returns_failure(self, monkeypatch, tmp_path):
        fake = _fake_packet_gen(tmp_path)

        def _boom(*_a, **_k):
            raise RuntimeError("gcs down")

        self._prep(monkeypatch, tmp_path, fake, {"display_name": "P", "templates": ["B.pdf"]})
        monkeypatch.setattr(de, "upload_to_gcs", _boom)
        result = de.generate_packet("merge_fail_packet", {"buyer_name": "Merge Buyer"})
        assert result["success"] is False
        assert result["message"] == "gcs down"


# ---------------------------------------------------------------------------
# tools/document_engine.py (v1) — batch branches
# ---------------------------------------------------------------------------


def _fake_batch_gen(tmp_path):
    def _fake(template_name, data, output_filename=None, deal_id=None):
        filename = output_filename or f"{template_name}.pdf"
        path = tmp_path / filename
        _blank_pdf(path)
        return {"success": True, "file_path": str(path), "filename": filename}

    return _fake


class TestV1BatchBranches:
    def _prep(self, monkeypatch, tmp_path):
        monkeypatch.setattr(de, "OUTPUT_DIR", str(tmp_path))
        monkeypatch.setattr(de, "upload_to_gcs", lambda *_a, **_k: None)
        monkeypatch.setattr(de, "get_template_config", lambda name: {"display_name": name})
        monkeypatch.setattr(de, "generate_document", _fake_batch_gen(tmp_path))

    def test_batch_mirrors_merged_pdf_to_drive(self, monkeypatch, tmp_path):
        self._prep(monkeypatch, tmp_path)
        uploads = []
        monkeypatch.setattr(de, "ensure_deal_folder", lambda deal_id: f"folder-{deal_id}")
        monkeypatch.setattr(
            de,
            "upload_to_drive",
            lambda path, name, folder: uploads.append((name, folder)) or "drive://batch",
        )
        result = de.generate_batch(
            ["one.pdf", "two.pdf"], {"buyer_name": "Batch Buyer"}, merge=True, deal_id="D-2"
        )
        assert result["success"] is True
        assert result["merged"]["drive_url"] == "drive://batch"
        assert uploads == [(result["merged"]["filename"], "folder-D-2")]

    def test_batch_merge_failure_swallowed(self, monkeypatch, tmp_path):
        self._prep(monkeypatch, tmp_path)

        def _boom(*_a, **_k):
            raise RuntimeError("gcs down")

        monkeypatch.setattr(de, "upload_to_gcs", _boom)
        result = de.generate_batch(
            ["one.pdf", "two.pdf"], {"buyer_name": "Batch Buyer"}, merge=True
        )
        assert result["success"] is True
        assert result["merged"] is None


# ---------------------------------------------------------------------------
# tools/document_tools.py — import-time fallbacks + GCS-unavailable paths
# ---------------------------------------------------------------------------


class TestDocumentToolsImportFallbacks:
    def test_toolcontext_import_error_falls_back_to_none(self):
        import google.adk.tools as real_adk_tools

        fake_tools = types.ModuleType("google.adk.tools")  # no ToolContext attribute
        sys.modules["google.adk.tools"] = fake_tools
        try:
            importlib.reload(dt)
            assert dt.ToolContext is None
        finally:
            sys.modules["google.adk.tools"] = real_adk_tools
            importlib.reload(dt)
        assert dt.ToolContext is not None

    def test_output_dir_tmp_fallback_when_default_not_writable(self, monkeypatch):
        # Reloading the module rebinds _DEFAULT_OUTPUT_DIR from __file__, so
        # block os.makedirs for the default dir to simulate a read-only FS.
        default_dir = dt._DEFAULT_OUTPUT_DIR
        real_makedirs = os.makedirs

        def _guarded(name, *args, **kwargs):
            if os.path.abspath(str(name)) == os.path.abspath(default_dir):
                raise OSError("read-only filesystem")
            return real_makedirs(name, *args, **kwargs)

        monkeypatch.setattr(os, "makedirs", _guarded)
        try:
            importlib.reload(dt)
            assert dt.OUTPUT_DIR == dt._TMP_OUTPUT_DIR
        finally:
            monkeypatch.undo()
            importlib.reload(dt)
        assert dt.OUTPUT_DIR == default_dir


class _FakeBlob:
    def __init__(self, name):
        self.name = name
        self.size = 10
        self.time_created = datetime(2026, 10, 1, 12, 0, 0)

    def upload_from_filename(self, *_a, **_k):
        return None

    def exists(self):
        return True

    def download_to_filename(self, *_a, **_k):
        return None


class _FakeBucket:
    def blob(self, _name):
        return _FakeBlob(_name)

    def list_blobs(self, prefix=""):
        return [_FakeBlob(f"{prefix}doc.pdf")]


@pytest.fixture
def gcs_unavailable(monkeypatch):
    """Fake google.cloud.storage whose Client() raises (no credentials)."""
    monkeypatch.setattr(dt, "_gcs_client", None)
    monkeypatch.setattr(dt, "_gcs_bucket", None)
    monkeypatch.delenv("THO_DISABLE_GCS_UPLOADS", raising=False)

    import google.cloud

    storage = types.ModuleType("google.cloud.storage")

    def _client():
        raise RuntimeError("no application credentials")

    storage.Client = _client
    monkeypatch.setitem(sys.modules, "google.cloud.storage", storage)
    monkeypatch.setattr(google.cloud, "storage", storage, raising=False)
    return storage


class TestDocumentToolsGcsUnavailable:
    def test_upload_returns_none(self, gcs_unavailable, tmp_path):
        assert dt.upload_to_gcs(str(tmp_path / "x.pdf"), "x.pdf") is None

    def test_download_returns_false(self, gcs_unavailable, tmp_path):
        assert dt.download_from_gcs("x.pdf", str(tmp_path / "out.pdf")) is False

    def test_list_returns_empty(self, gcs_unavailable):
        assert dt.list_gcs_documents() == []


# ---------------------------------------------------------------------------
# tools/document_tools.py — pure helpers
# ---------------------------------------------------------------------------


class TestDocumentToolsHelpers:
    def test_summary_pdf_page_break(self, tmp_path):
        out = tmp_path / "summary.pdf"
        dt.create_summary_pdf({f"field_{i}": f"value {i}" for i in range(80)}, str(out))
        assert out.exists() and out.stat().st_size > 0

    def test_helvetica_skip_when_no_annotations(self):
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        dt._force_helvetica_field_appearance(writer)  # no /Annots -> continue

    def test_rect_overrides_skip_when_no_annotations(self):
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        dt._apply_pdf_widget_rect_overrides("TMHA_SalesContract.pdf", writer)

    def test_checkbox_states_introspection_failure(self, caplog):
        import logging

        class _BoomReader:
            def get_fields(self):
                raise RuntimeError("corrupt xref")

        with caplog.at_level(logging.WARNING, logger="tools.document_tools"):
            states = dt._checkbox_field_states(_BoomReader())
        assert states == {}
        assert any("checkbox state introspection failed" in r.message for r in caplog.records)

    def test_resolve_checkbox_no_on_states_is_skipped(self):
        data = {"cb": "on"}
        states = {"cb": ["/Off"]}
        assert dt._resolve_checkbox_values(data, states) == {"cb": "on"}

    def test_resolve_checkbox_falsy_value_maps_to_off(self):
        data = {"warranty": "no"}
        states = {"warranty": ["/Off", "/Yes"]}
        assert dt._resolve_checkbox_values(data, states)["warranty"] == "/Off"

    def test_draw_fit_text_empty_value_returns_early(self):
        from io import BytesIO

        from reportlab.pdfgen import canvas as rl_canvas

        buf = BytesIO()
        c = rl_canvas.Canvas(buf)
        dt._draw_fit_text(c, "   ", x=10, y=700, max_width=100, font_size=10)
        c.save()
        assert buf.getvalue()

    def test_draw_fit_text_shrinks_until_it_fits(self):
        from io import BytesIO

        from reportlab.pdfgen import canvas as rl_canvas

        buf = BytesIO()
        c = rl_canvas.Canvas(buf)
        dt._draw_fit_text(
            c, "A" * 300, x=10, y=700, max_width=50, font_size=12, clear_background=True
        )
        c.save()
        assert buf.getvalue()

    def test_overlay_page_out_of_range_is_skipped(self, monkeypatch):
        from pypdf import PdfWriter

        monkeypatch.setitem(
            dt.PDF_TEXT_OVERLAYS,
            "C11Fake.pdf",
            {5: ({"field": "f", "x": 0, "y": 0, "max_width": 10, "font_size": 10},)},
        )
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        dt._apply_pdf_text_overlays("C11Fake.pdf", writer, {"f": "v"})  # page 5 > 1 page

    def test_fill_xfa_without_datasets_returns_false(self):
        from pypdf import PdfWriter
        from pypdf.generic import ArrayObject, DecodedStreamObject, TextStringObject

        writer = PdfWriter()
        xfa = ArrayObject([TextStringObject("form"), DecodedStreamObject()])
        assert dt._fill_xfa(writer, xfa, {"field": "value"}) is False

    def test_fill_xfa_writer_without_acroform_returns_false(self):
        from pypdf import PdfWriter
        from pypdf.generic import ArrayObject, DecodedStreamObject, TextStringObject

        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        xfa = ArrayObject([TextStringObject("datasets"), DecodedStreamObject()])
        assert dt._fill_xfa(writer, xfa, {"field": "value"}) is False

    def test_fill_xfa_internal_error_is_swallowed(self, caplog):
        import logging

        from pypdf import PdfWriter
        from pypdf.generic import ArrayObject, DecodedStreamObject, TextStringObject

        writer = PdfWriter()
        xfa = ArrayObject([TextStringObject("datasets"), DecodedStreamObject()])
        with caplog.at_level(logging.WARNING, logger="tools.document_tools"):
            # A non-mapping data_dict makes data_dict.items() raise inside the try.
            assert dt._fill_xfa(writer, xfa, None) is False
        assert any("XFA fill failed" in r.message for r in caplog.records)


class TestFillPdfFormFailurePaths:
    def _acroform_template(self, tmp_path):
        from pypdf import PdfWriter
        from pypdf.generic import ArrayObject, DictionaryObject, NameObject

        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer._root_object[NameObject("/AcroForm")] = DictionaryObject(
            {NameObject("/Fields"): ArrayObject()}
        )
        tpl = tmp_path / "fake.pdf"
        with open(tpl, "wb") as fh:
            writer.write(fh)
        return tpl

    def test_fill_failure_falls_back_to_summary_pdf(self, monkeypatch, tmp_path):
        monkeypatch.setattr(dt, "OUTPUT_DIR", str(tmp_path))

        def _boom(*_a, **_k):
            raise RuntimeError("appearance boom")

        monkeypatch.setattr(dt, "_prepare_acroform_appearance_data", _boom)
        out = dt.fill_pdf_form(str(self._acroform_template(tmp_path)), {"a": "b"}, "c11_out.pdf")
        assert out == str(tmp_path / "c11_out.pdf")
        assert (tmp_path / "c11_out.pdf").exists()  # summary PDF was generated

    def test_summary_generation_failure_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr(dt, "OUTPUT_DIR", str(tmp_path))

        def _boom(*_a, **_k):
            raise RuntimeError("appearance boom")

        def _summary_boom(*_a, **_k):
            raise RuntimeError("summary boom")

        monkeypatch.setattr(dt, "_prepare_acroform_appearance_data", _boom)
        monkeypatch.setattr(dt, "create_summary_pdf", _summary_boom)
        with pytest.raises(RuntimeError, match="summary boom"):
            dt.fill_pdf_form(str(self._acroform_template(tmp_path)), {"a": "b"}, "c11_out.pdf")
