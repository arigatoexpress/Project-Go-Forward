"""Signing gate: money checks, real-PDF value read-back, DocuSeal safety.

Run: python -m pytest tests/test_esign_review.py -v
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import docuseal_service  # noqa: E402
import esign_review  # noqa: E402
import tools.document_tools as document_tools  # noqa: E402
from tools.document_engine_v2 import generate_document  # noqa: E402

SALES_CONTRACT = "TMHA_SalesContract.pdf"


def _ready_doc_data(**overrides) -> dict:
    data = {
        "buyer_first_name": "Alice",
        "buyer_last_name": "Buyer",
        "buyer_email": "alice@example.com",
        "buyer_address": "123 Main St",
        "buyer_city": "Houston",
        "buyer_county": "Harris",
        "buyer_state": "TX",
        "buyer_zip": "77001",
        "manufacturer": "Clayton",
        "manufacturer_address": "500 Factory Road",
        "manufacturer_city": "Fort Worth",
        "manufacturer_state": "TX",
        "manufacturer_zip": "76101",
        "model": "TruMH 1680",
        "serial_number_1": "CLW123987TX",
        "label_number_1": "NTA1876543",
        "no_of_sections": "2",
        "sales_price": 80000,
        "down_payment": 5000,
        "creditor_name": "21st Mortgage",
        "loan_term": "240",
        "apr": "8.5",
        "tax_rate": 2.0,
        "annual_insurance": 1200,
    }
    data.update(overrides)
    return data


def _consistent_printed_values(doc_data: dict) -> dict:
    """PDF values matching what the deal says each money line should be."""
    from config.field_map_loader import get_template_field_map
    from tools.document_quality import enrich_document_data

    enriched = enrich_document_data(doc_data)
    values = {}
    for pdf_field, key in get_template_field_map(SALES_CONTRACT).items():
        value = enriched.get(key)
        if value is None:
            continue
        amount = esign_review._amount(value) if key in esign_review.PRINTED_MONEY_LABELS else None
        values[pdf_field] = f"{amount:,.2f}" if amount is not None else str(value)
    return values


def _fake_generate(calls: list | None = None, result: dict | None = None):
    def generate(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        return result or {"success": True, "file_path": "/tmp/fake.pdf", "filename": "fake.pdf"}

    return generate


# ─── Auto-send flag ──────────────────────────────────────────────────────────


class TestAutoSendFlag:
    def test_defaults_off(self, monkeypatch):
        monkeypatch.delenv("FF_ESIGN_AUTO_SEND", raising=False)
        monkeypatch.delenv("FEATURE_FLAG_ESIGN_AUTO_SEND", raising=False)
        assert esign_review.auto_send_enabled() is False

    def test_config_yaml_ships_off(self):
        from config_loader import get_config

        assert get_config()["feature_flags"]["ESIGN_AUTO_SEND"] is False

    def test_env_opt_in(self, monkeypatch):
        monkeypatch.setenv("FF_ESIGN_AUTO_SEND", "1")
        assert esign_review.auto_send_enabled() is True


# ─── Money checks ────────────────────────────────────────────────────────────


class TestMoneyProblems:
    def test_complete_deal_has_no_problems(self):
        assert esign_review.money_problems(_ready_doc_data()) == []

    @pytest.mark.parametrize(
        ("field", "value", "flagged", "phrase"),
        [
            ("sales_price", None, "sales_price", "Sales price is blank or $0"),
            ("sales_price", 0, "sales_price", "Sales price is blank or $0"),
            ("down_payment", None, "down_payment", "Down payment is blank or $0"),
            ("down_payment", 0, "down_payment", "Down payment is blank or $0"),
            ("apr", None, "monthly_payment", "APR or loan term is blank"),
            ("loan_term", "", "monthly_payment", "APR or loan term is blank"),
            ("tax_rate", None, "tax_rate", "Property tax rate is blank or 0%"),
            ("tax_rate", 0, "tax_rate", "Property tax rate is blank or 0%"),
            ("annual_insurance", None, "annual_insurance", "Insurance is blank or $0"),
            ("annual_insurance", 0, "annual_insurance", "Insurance is blank or $0"),
        ],
    )
    def test_blank_or_zero_money_field_is_named(self, field, value, flagged, phrase):
        problems = esign_review.money_problems(_ready_doc_data(**{field: value}))
        assert [p["field"] for p in problems] == [flagged]
        assert phrase in problems[0]["message"]

    def test_zero_escrow_is_flagged(self):
        problems = esign_review.money_problems(_ready_doc_data(taxable_value=0))
        assert [p["field"] for p in problems] == ["escrow"]
        assert "tax escrow works out to $0" in problems[0]["message"]

    def test_money_summary_lists_payment_lines(self):
        summary = {
            row["label"]: row["value"] for row in esign_review.money_summary(_ready_doc_data())
        }
        assert summary["Sales price"] == "$80,000.00"
        assert summary["Down payment"] == "$5,000.00"
        assert summary["Monthly tax escrow"] == "$133.33"
        assert summary["Monthly insurance escrow"] == "$100.00"
        assert summary["Principal & interest"] != "—"


# ─── prepare_signing_packet ─────────────────────────────────────────────────


class TestPrepareSigningPacket:
    def test_blank_money_refuses_without_generating(self):
        calls: list = []
        prepared = esign_review.prepare_signing_packet(
            doc_data=_ready_doc_data(sales_price=None, annual_insurance=0),
            template_name=SALES_CONTRACT,
            generate=_fake_generate(calls),
        )
        assert prepared["ready"] is False
        assert calls == []
        assert {p["field"] for p in prepared["problems"]} == {"sales_price", "annual_insurance"}
        assert "review_token" not in prepared

    def test_missing_buyer_email_refuses(self):
        prepared = esign_review.prepare_signing_packet(
            doc_data=_ready_doc_data(buyer_email=""),
            template_name=SALES_CONTRACT,
            generate=_fake_generate(),
        )
        assert prepared["ready"] is False
        assert prepared["problems"][0]["message"] == "There's no buyer email on this deal."

    def test_document_quality_failure_refuses(self):
        prepared = esign_review.prepare_signing_packet(
            doc_data=_ready_doc_data(),
            template_name=SALES_CONTRACT,
            generate=_fake_generate(
                result={
                    "success": False,
                    "quality_issues": [
                        {
                            "code": "placeholder_identifier",
                            "field": "label_number_1",
                            "message": "HUD label # 1 looks like a fake placeholder.",
                        }
                    ],
                }
            ),
        )
        assert prepared["ready"] is False
        assert prepared["problems"][0]["code"] == "placeholder_identifier"

    def test_real_quality_gate_blocks_placeholder_identifier(self):
        prepared = esign_review.prepare_signing_packet(
            doc_data=_ready_doc_data(label_number_1="1234567"),
            template_name=SALES_CONTRACT,
            generate=generate_document,
        )
        assert prepared["ready"] is False
        assert any(p["code"] == "placeholder_identifier" for p in prepared["problems"])

    def test_unsignable_template_refuses(self):
        prepared = esign_review.prepare_signing_packet(
            doc_data=_ready_doc_data(),
            template_name="State_CreditAuth.pdf",
            generate=_fake_generate(),
        )
        assert prepared["ready"] is False
        assert prepared["problems"][0]["code"] == "template_not_signable"

    def test_blank_printed_money_line_refuses(self):
        doc = _ready_doc_data()
        values = _consistent_printed_values(doc)
        values["topmostSubform[0].Page1[0].DownPmt[0]"] = ""
        prepared = esign_review.prepare_signing_packet(
            doc_data=doc,
            template_name=SALES_CONTRACT,
            generate=_fake_generate(),
            read_values=lambda _path: values,
        )
        assert prepared["ready"] is False
        assert prepared["problems"][0]["code"] == "printed_money_blank"
        assert prepared["problems"][0]["field"] == "down_payment"

    def test_printed_payment_mismatch_refuses(self):
        doc = _ready_doc_data()
        values = _consistent_printed_values(doc)
        values["topmostSubform[0].Page2[0].Total_Payment[0]"] = "650.87"
        prepared = esign_review.prepare_signing_packet(
            doc_data=doc,
            template_name=SALES_CONTRACT,
            generate=_fake_generate(),
            read_values=lambda _path: values,
        )
        assert prepared["ready"] is False
        assert prepared["problems"][0]["code"] == "printed_money_mismatch"
        assert "($650.87) doesn't match the deal ($884.20)" in prepared["problems"][0]["message"]

    def test_ready_packet_carries_values_and_stable_token(self):
        doc = _ready_doc_data()
        values = _consistent_printed_values(doc)
        kwargs = dict(
            doc_data=doc,
            template_name=SALES_CONTRACT,
            generate=_fake_generate(),
            read_values=lambda _path: dict(values),
        )
        first = esign_review.prepare_signing_packet(**kwargs)
        second = esign_review.prepare_signing_packet(**kwargs)
        assert first["ready"] is True, first["problems"]
        assert first["values"] == values
        assert first["review_token"] == second["review_token"]
        assert first["download_url"] == "/api/documents/download/fake.pdf"
        assert "values" not in esign_review.public_view(first)

        changed = dict(values, **{"topmostSubform[0].Page1[0].SalePrice[0]": "81,000.00"})
        assert esign_review.review_token(SALES_CONTRACT, changed) != first["review_token"]


# ─── Real PDF read-back (TMHA_SalesContract.pdf baseline) ───────────────────


class TestRealSalesContractValues:
    @pytest.fixture
    def filled(self, tmp_path, monkeypatch):
        monkeypatch.setattr(document_tools, "OUTPUT_DIR", str(tmp_path))
        monkeypatch.setattr(document_tools, "upload_to_gcs", lambda *a, **k: None)
        doc = _ready_doc_data()
        result = generate_document(SALES_CONTRACT, dict(doc))
        assert result["success"], result
        return doc, esign_review.read_filled_values(result["file_path"])

    def test_money_lines_are_read_back_from_the_filled_pdf(self, filled):
        _doc, values = filled
        assert values["topmostSubform[0].Page1[0].SalePrice[0]"] == "80,000.00"
        assert values["topmostSubform[0].Page1[0].DownPmt[0]"] == "5,000.00"
        assert values["topmostSubform[0].Page1[0].Unpaid_Balance[0]"] == "75,000.00"
        assert values["topmostSubform[0].Page2[0].Max_Financed[0]"] == "75,000.00"
        assert any(v == "Clayton" for v in values.values())

    def test_printed_money_lines_match_the_deal(self, filled):
        doc, values = filled
        assert values["topmostSubform[0].Page2[0].Total_Payment[0]"] == "884.20"
        assert esign_review.printed_money_problems(SALES_CONTRACT, values, doc) == []

    def test_complete_deal_is_ready_end_to_end(self, tmp_path, monkeypatch):
        monkeypatch.setattr(document_tools, "OUTPUT_DIR", str(tmp_path))
        monkeypatch.setattr(document_tools, "upload_to_gcs", lambda *a, **k: None)
        prepared = esign_review.prepare_signing_packet(
            doc_data=_ready_doc_data(),
            template_name=SALES_CONTRACT,
            generate=generate_document,
        )
        assert prepared["ready"] is True, prepared["problems"]
        assert prepared["values"]["topmostSubform[0].Page1[0].SalePrice[0]"] == "80,000.00"


# ─── DocuSeal service safety ────────────────────────────────────────────────


def _mock_http(response):
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=response)
    client.delete = AsyncMock(return_value=response)
    return client


@pytest.fixture
def docuseal_configured(monkeypatch):
    monkeypatch.setattr(docuseal_service, "API_URL", "http://docuseal.internal")
    monkeypatch.setattr(docuseal_service, "API_TOKEN", "test-token")


class TestDocuSealService:
    def test_template_send_without_values_is_refused(self, docuseal_configured):
        http = _mock_http(MagicMock(status_code=200))
        with patch("httpx.AsyncClient", return_value=http):
            result = asyncio.run(
                docuseal_service.send_for_signature(
                    email="buyer@example.com",
                    name="Buyer",
                    template_name=SALES_CONTRACT,
                    deal_id="deal-1",
                )
            )
        assert result["success"] is False
        assert result["status"] == "missing_values"
        http.post.assert_not_called()

    def test_template_send_passes_deal_values(self, docuseal_configured, monkeypatch):
        monkeypatch.setattr(docuseal_service, "get_template_id", lambda _name: 7)
        response = MagicMock(status_code=200)
        response.json.return_value = [{"id": 1, "submission_id": 42}]
        http = _mock_http(response)
        with patch("httpx.AsyncClient", return_value=http):
            result = asyncio.run(
                docuseal_service.send_for_signature(
                    email="buyer@example.com",
                    name="Buyer",
                    template_name=SALES_CONTRACT,
                    deal_id="deal-1",
                    values={"SalePrice": "80,000.00"},
                )
            )
        assert result["success"] is True
        payload = http.post.call_args.kwargs["json"]
        assert payload["submitters"][0]["values"] == {"SalePrice": "80,000.00"}

    def test_archive_calls_delete_submission(self, docuseal_configured):
        http = _mock_http(MagicMock(status_code=200))
        with patch("httpx.AsyncClient", return_value=http):
            result = asyncio.run(docuseal_service.archive_submission("42"))
        assert result == {"success": True}
        url = http.delete.call_args.args[0]
        assert url == "http://docuseal.internal/api/submissions/42"
        assert http.delete.call_args.kwargs["headers"]["X-Auth-Token"] == "test-token"

    def test_archive_rejects_non_numeric_id(self, docuseal_configured):
        http = _mock_http(MagicMock(status_code=200))
        with patch("httpx.AsyncClient", return_value=http):
            result = asyncio.run(docuseal_service.archive_submission("../templates/1"))
        assert result["success"] is False
        http.delete.assert_not_called()

    def test_archive_reports_api_error(self, docuseal_configured):
        http = _mock_http(MagicMock(status_code=404))
        with patch("httpx.AsyncClient", return_value=http):
            result = asyncio.run(docuseal_service.archive_submission("42"))
        assert result["success"] is False

    def test_archive_not_configured(self, monkeypatch):
        monkeypatch.setattr(docuseal_service, "API_URL", "")
        result = asyncio.run(docuseal_service.archive_submission("42"))
        assert result["status"] == "not_configured"

    def test_submission_ids_from_response(self):
        assert docuseal_service.submission_ids_from_response(
            [{"id": 1, "submission_id": 42}, {"id": 2, "submission_id": 42}]
        ) == ["42"]
        assert docuseal_service.submission_ids_from_response({"id": 9, "submitters": []}) == ["9"]
        assert docuseal_service.submission_ids_from_response(None) == []

    def test_no_automatic_lead_or_stage_trigger_remains(self):
        assert not hasattr(docuseal_service, "maybe_trigger_automated_signing")
