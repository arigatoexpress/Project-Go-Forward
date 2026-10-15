"""Tests for document-email helper, analytics endpoints, and RAG query.

Targets flagged by the Python-3.12 honest coverage re-measurement (see
docs/MAIN_COVERAGE_INVESTIGATION.md):

- ``main._maybe_email_document``      — best-effort document delivery email
- ``GET /api/analytics/documents``    — document generation analytics
- ``GET /api/analytics/inventory``    — inventory analytics
- ``GET /api/analytics/chat``         — chat analytics placeholder
- ``POST /api/v1/rag/query``          — partner-keyed semantic search

Run: python -m pytest tests/test_document_analytics_rag.py -v
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(Path(__file__).parent))
from test_api_v1 import create_client  # noqa: E402


def _admin_headers(main) -> dict:
    return {"X-Admin-Token": main._create_admin_token()}


def _partner_headers() -> dict:
    return {"Authorization": "Bearer tho-secret"}


class TestMaybeEmailDocument:
    def test_skips_without_customer_email(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)
        sent: list[dict] = []
        monkeypatch.setattr(
            main, "send_document_email", lambda **kwargs: sent.append(kwargs)
        )
        audits: list[tuple] = []
        monkeypatch.setattr(main, "_safe_audit", lambda a, d: audits.append((a, d)))

        main._maybe_email_document(
            customer_email=None,
            customer_name="Buyer",
            doc_filename="contract.pdf",
            doc_type="sales_contract",
            download_url="https://example.com/contract.pdf",
            deal_id="deal-1",
        )

        assert sent == []
        assert audits == []

    def test_send_failure_is_swallowed_and_warns(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)

        def _boom(**kwargs):
            raise RuntimeError("smtp down")

        monkeypatch.setattr(main, "send_document_email", _boom)
        audits: list[tuple] = []
        monkeypatch.setattr(main, "_safe_audit", lambda a, d: audits.append((a, d)))

        # Must not raise.
        main._maybe_email_document(
            customer_email="buyer@example.com",
            customer_name="Buyer",
            doc_filename="contract.pdf",
            doc_type="sales_contract",
            download_url="https://example.com/contract.pdf",
        )

        assert audits == []

    def test_success_audits_delivery_ok(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)
        monkeypatch.setattr(
            main,
            "send_document_email",
            lambda **kwargs: {"success": True, "message_id": "m-1"},
        )
        audits: list[tuple] = []
        monkeypatch.setattr(main, "_safe_audit", lambda a, d: audits.append((a, d)))

        main._maybe_email_document(
            customer_email="buyer@example.com",
            customer_name="Buyer",
            doc_filename="contract.pdf",
            doc_type="sales_contract",
            download_url="https://example.com/contract.pdf",
            deal_id="deal-1",
        )

        assert len(audits) == 1
        action, details = audits[0]
        assert action == "document.email_delivery"
        assert details["delivery"] == "ok"
        assert details["doc_filename"] == "contract.pdf"
        assert details["deal_id"] == "deal-1"

    def test_non_success_result_audits_delivery_failed(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)
        monkeypatch.setattr(
            main,
            "send_document_email",
            lambda **kwargs: {"success": False, "error": "bounce"},
        )
        audits: list[tuple] = []
        monkeypatch.setattr(main, "_safe_audit", lambda a, d: audits.append((a, d)))

        main._maybe_email_document(
            customer_email="buyer@example.com",
            customer_name="Buyer",
            doc_filename="contract.pdf",
            doc_type="sales_contract",
            download_url="https://example.com/contract.pdf",
        )

        assert len(audits) == 1
        assert audits[0][1]["delivery"] == "failed"
        assert audits[0][1]["error"] == "bounce"

    def test_broken_logger_on_skip_is_tolerated(self, monkeypatch):
        """The warn-log guards (except: pass) must hold even if logging fails."""
        client, main, *_ = create_client(monkeypatch)

        class _InfoRaises:
            def info(self, **kwargs):
                raise RuntimeError("logger down")

        monkeypatch.setattr(main, "struct_logger", _InfoRaises())

        main._maybe_email_document(
            customer_email=None,
            customer_name="Buyer",
            doc_filename="contract.pdf",
            doc_type="sales_contract",
            download_url="https://example.com/contract.pdf",
        )

    def test_broken_logger_on_send_error_is_tolerated(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)

        def _boom(**kwargs):
            raise RuntimeError("smtp down")

        class _WarningRaises:
            def warning(self, **kwargs):
                raise RuntimeError("logger down")

        monkeypatch.setattr(main, "send_document_email", _boom)
        monkeypatch.setattr(main, "struct_logger", _WarningRaises())

        main._maybe_email_document(
            customer_email="buyer@example.com",
            customer_name="Buyer",
            doc_filename="contract.pdf",
            doc_type="sales_contract",
            download_url="https://example.com/contract.pdf",
        )

    def test_broken_logger_on_non_success_is_tolerated(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)
        monkeypatch.setattr(
            main,
            "send_document_email",
            lambda **kwargs: {"success": False, "error": "bounce"},
        )

        class _WarningRaises:
            def warning(self, **kwargs):
                raise RuntimeError("logger down")

        monkeypatch.setattr(main, "struct_logger", _WarningRaises())

        main._maybe_email_document(
            customer_email="buyer@example.com",
            customer_name="Buyer",
            doc_filename="contract.pdf",
            doc_type="sales_contract",
            download_url="https://example.com/contract.pdf",
        )


class TestDocumentAnalytics:
    def _seed_pdfs(self, tmp_path) -> None:
        names = [
            "TMHA Sales Contract - Buyer.pdf",  # Sales Contracts
            "TDHCA Form 4100.pdf",  # TDHCA Forms
            "closing_packet_buyer.pdf",  # Closing Packets
            "work_order_service.pdf",  # Service Documents
            "misc_notes.pdf",  # Other
        ]
        for name in names:
            (tmp_path / name).write_bytes(b"%PDF-fake")

    def test_analytics_scan_and_categorize(self, monkeypatch, tmp_path):
        client, main, *_ = create_client(monkeypatch)
        self._seed_pdfs(tmp_path)
        monkeypatch.setattr(main, "OUTPUT_DIR", str(tmp_path))

        resp = client.get("/api/analytics/documents", headers=_admin_headers(main))

        assert resp.status_code == 200
        body = resp.json()
        assert body["total_generated"] == 5
        assert body["by_type"] == {
            "Sales Contracts": 1,
            "TDHCA Forms": 1,
            "Closing Packets": 1,
            "Service Documents": 1,
            "Other": 1,
        }
        # Every seeded file was just created → all count as this month.
        assert body["this_month"] == 5
        # Ties broken by max() first-seen order — all counts equal here, so
        # the most-popular bucket still resolves to a real category.
        assert body["most_popular"]["count"] == 1
        assert len(body["recent"]) == 5
        assert {"template_name", "buyer_name", "created_at"} == set(
            body["recent"][0].keys()
        )

    def test_analytics_missing_dir_returns_zeros(self, monkeypatch, tmp_path):
        client, main, *_ = create_client(monkeypatch)
        monkeypatch.setattr(main, "OUTPUT_DIR", str(tmp_path / "does-not-exist"))

        resp = client.get("/api/analytics/documents", headers=_admin_headers(main))

        assert resp.status_code == 200
        body = resp.json()
        assert body["total_generated"] == 0
        assert body["this_month"] == 0
        assert body["by_type"] == {}
        assert body["recent"] == []

    def test_analytics_failure_returns_error(self, monkeypatch, tmp_path):
        client, main, *_ = create_client(monkeypatch)
        self._seed_pdfs(tmp_path)
        monkeypatch.setattr(main, "OUTPUT_DIR", str(tmp_path))

        def _boom(*args, **kwargs):
            raise OSError("disk gone")

        monkeypatch.setattr(os, "walk", _boom)

        resp = client.get("/api/analytics/documents", headers=_admin_headers(main))

        assert resp.status_code == 200
        assert resp.json() == {"error": "Failed to load document analytics"}


class TestInventoryAnalytics:
    def test_inventory_analytics_aggregates(self, monkeypatch):
        client, main, fake_db, _logger = create_client(monkeypatch)
        fake_db.collections["inventory"]["inv-1"].update(
            {"is_new": True, "image_url": "https://img/1.jpg", "view_count": 7}
        )
        fake_db.collections["inventory"]["inv-2"].update(
            {
                "is_new": False,
                "photos": ["https://img/2a.jpg"],
                "matterport_id": "mp-2",
                "view_count": 42,
            }
        )

        resp = client.get("/api/analytics/inventory", headers=_admin_headers(main))

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert body["new_count"] == 1
        assert body["used_count"] == 1
        assert body["with_photos"] == 2
        assert body["new_with_photos"] == 1
        assert body["used_with_photos"] == 1
        assert body["with_tours"] == 1
        # Top-viewed ordering.
        assert body["top_viewed"][0]["id"] == "inv-2"

    def test_inventory_analytics_failure_returns_error(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)

        def _boom(**kwargs):
            raise RuntimeError("firestore down")

        monkeypatch.setattr(main._deal_db, "search_inventory", _boom)

        resp = client.get("/api/analytics/inventory", headers=_admin_headers(main))

        assert resp.status_code == 200
        assert resp.json() == {"error": "Failed to load inventory analytics"}


class TestChatAnalytics:
    def test_chat_analytics_placeholder_shape(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)

        resp = client.get("/api/analytics/chat", headers=_admin_headers(main))

        assert resp.status_code == 200
        assert resp.json() == {
            "total_conversations": 0,
            "avg_per_day": 0,
            "unique_users": 0,
            "top_questions": [],
            "conversion_to_lead": 0,
        }


class TestV1RagQuery:
    def _stub_rag(self, monkeypatch, main, results=None, error=None):
        calls: list[dict] = []

        class _StubRag:
            def query(self, query, k=5):
                calls.append({"query": query, "k": k})
                if error is not None:
                    raise error
                return results or []

        monkeypatch.setattr(main, "_get_document_rag", lambda: _StubRag())
        return calls

    def test_requires_partner_key(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)

        resp = client.post("/api/v1/rag/query", json={"query": "x"})

        assert resp.status_code in (401, 403)

    def test_invalid_json_rejected(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)

        resp = client.post(
            "/api/v1/rag/query",
            content=b"not-json",
            headers={**_partner_headers(), "Content-Type": "application/json"},
        )

        assert resp.status_code == 400

    def test_missing_query_rejected(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)

        resp = client.post("/api/v1/rag/query", json={}, headers=_partner_headers())

        assert resp.status_code == 400
        assert resp.json()["detail"] == "query is required"

    def test_query_returns_serialized_results(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)
        results = [
            types.SimpleNamespace(
                chunk_id="TMHA_SalesContract.pdf#p1#c0",
                template="TMHA_SalesContract.pdf",
                page=1,
                score=0.823456,
                text="Buyer signature..." * 100,  # >500 chars → truncated
            )
        ]
        calls = self._stub_rag(monkeypatch, main, results=results)

        resp = client.post(
            "/api/v1/rag/query",
            json={"query": "  Who signs?  ", "k": 3},
            headers=_partner_headers(),
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["query"] == "Who signs?"  # sanitized + stripped
        assert body["count"] == 1
        hit = body["results"][0]
        assert hit["chunk_id"] == "TMHA_SalesContract.pdf#p1#c0"
        assert hit["template"] == "TMHA_SalesContract.pdf"
        assert hit["page"] == 1
        assert hit["score"] == 0.8235  # rounded to 4 places
        assert len(hit["text"]) == 500  # truncated
        assert calls == [{"query": "Who signs?", "k": 3}]

    def test_k_coerced_and_clamped(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)
        calls = self._stub_rag(monkeypatch, main)

        resp = client.post(
            "/api/v1/rag/query",
            json={"query": "x", "k": "not-a-number"},
            headers=_partner_headers(),
        )
        assert resp.status_code == 200
        assert calls[-1]["k"] == 5  # bad value → default

        resp = client.post(
            "/api/v1/rag/query",
            json={"query": "x", "k": 500},
            headers=_partner_headers(),
        )
        assert resp.status_code == 200
        assert calls[-1]["k"] == 20  # clamped to max

        resp = client.post(
            "/api/v1/rag/query",
            json={"query": "x", "k": 0},
            headers=_partner_headers(),
        )
        assert resp.status_code == 200
        assert calls[-1]["k"] == 1  # clamped to min

    def test_rag_failure_returns_500(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)
        self._stub_rag(monkeypatch, main, error=RuntimeError("index corrupt"))

        resp = client.post(
            "/api/v1/rag/query",
            json={"query": "x"},
            headers=_partner_headers(),
        )

        assert resp.status_code == 500


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
