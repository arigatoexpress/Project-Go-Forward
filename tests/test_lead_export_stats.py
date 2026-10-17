"""Tests for admin lead export/stats and document download endpoints.

Covers endpoints that had zero test references before the Python-3.12 honest
coverage re-measurement flagged them as real gaps (see
docs/MAIN_COVERAGE_INVESTIGATION.md):

- ``GET /leads/export``  — CSV streaming export of leads
- ``GET /leads/stats``   — lead statistics aggregation
- ``GET /api/documents/download/{filename}`` — local-disk + GCS-fallback PDF
  download with path-traversal guards

Run: python -m pytest tests/test_lead_export_stats.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(Path(__file__).parent))
from test_api_v1 import FakeLead, create_client  # noqa: E402


def _admin_headers(main) -> dict:
    return {"X-Admin-Token": main._create_admin_token()}


def _seed_leads(main) -> None:
    """Give the fake lead manager a varied population for stats/export."""
    main.lead_manager.leads.extend(
        [
            FakeLead(
                lead_id="lead-2",
                user_id="user-2",
                session_id="session-2",
                name="Second Lead",
                status="contacted",
                appointment_requested=True,
                budget_max=95000.0,
                bedrooms=4,
            ),
            FakeLead(
                lead_id="lead-3",
                user_id="user-3",
                session_id="session-3",
                name="Third Lead",
                status="new",
                financing_discussed=True,
                gclid="gcl-test-123",
            ),
        ]
    )


class TestExportLeads:
    def test_export_requires_admin_auth(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)
        resp = client.get("/leads/export", headers={"X-Admin-Token": "bad-token"})
        assert resp.status_code in (401, 403)

    def test_export_streams_csv_with_all_leads(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)
        _seed_leads(main)

        resp = client.get("/leads/export", headers=_admin_headers(main))

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        assert "attachment" in resp.headers["content-disposition"]
        body = resp.text
        header = body.splitlines()[0]
        assert "Lead ID" in header and "Name" in header and "Status" in header
        # Default fake lead plus the two seeded leads.
        assert "lead-1" in body and "lead-2" in body and "lead-3" in body
        # Seeded values visible in their CSV rows.
        assert "contacted" in body
        assert "$95,000" in body
        assert "gcl-test-123" in body

    def test_export_filters_by_status(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)
        _seed_leads(main)

        resp = client.get(
            "/leads/export",
            params={"status": "contacted"},
            headers=_admin_headers(main),
        )

        assert resp.status_code == 200
        body = resp.text
        # Only the "contacted" lead may appear in the filtered export.
        assert "lead-2" in body
        assert "lead-1" not in body
        assert "lead-3" not in body

    def test_export_no_leads_returns_message(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)
        main.lead_manager.leads.clear()

        resp = client.get("/leads/export", headers=_admin_headers(main))

        assert resp.status_code == 200
        assert resp.json() == {"message": "No leads found", "count": 0}

    def test_export_failure_returns_500(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)

        async def _boom(*args, **kwargs):
            raise RuntimeError("firestore down")

        monkeypatch.setattr(main.lead_manager, "list_leads", _boom)

        resp = client.get("/leads/export", headers=_admin_headers(main))

        assert resp.status_code == 500
        assert "error" in resp.json()


class TestLeadStats:
    def test_stats_aggregates_counts(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)
        _seed_leads(main)

        resp = client.get("/leads/stats", headers=_admin_headers(main))

        assert resp.status_code == 200
        stats = resp.json()
        assert stats["total"] == 3
        assert stats["by_status"] == {"new": 2, "contacted": 1}
        # lead-1 (default fake) has email+phone; lead-2 has neither; lead-3 neither.
        assert stats["with_contact_info"] == 1
        assert stats["appointment_requested"] == 1
        assert stats["financing_discussed"] == 1

    def test_stats_failure_returns_error_body(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)

        async def _boom(*args, **kwargs):
            raise RuntimeError("firestore down")

        monkeypatch.setattr(main.lead_manager, "list_leads", _boom)

        resp = client.get("/leads/stats", headers=_admin_headers(main))

        assert resp.status_code == 200
        assert resp.json() == {"error": "Failed to load lead statistics"}


class TestDownloadDocument:
    def test_download_rejects_path_traversal(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)

        # httpx/TestClient normalizes dot segments before the request reaches
        # the app (a real browser/proxy may too), so exercise the guard by
        # calling the endpoint directly — the check itself must hold for any
        # client that forwards a raw traversal path.
        import asyncio

        resp = asyncio.run(main.download_document("../../etc/passwd"))
        assert resp.status_code == 400

        resp = asyncio.run(main.download_document("nested/../../secret.pdf"))
        assert resp.status_code == 400

    def test_download_rejects_non_pdf(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)

        resp = client.get(
            "/api/documents/download/notes.txt",
            headers=_admin_headers(main),
        )

        assert resp.status_code == 400
        assert "PDF" in resp.json()["error"]

    def test_download_serves_local_file(self, monkeypatch, tmp_path):
        client, main, *_ = create_client(monkeypatch)
        pdf = tmp_path / "packet.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        monkeypatch.setattr(main, "OUTPUT_DIR", str(tmp_path))

        resp = client.get(
            "/api/documents/download/packet.pdf",
            headers=_admin_headers(main),
        )

        assert resp.status_code == 200
        assert resp.content == b"%PDF-1.4 fake"
        assert resp.headers["content-type"] == "application/pdf"

    def test_download_falls_back_to_gcs(self, monkeypatch, tmp_path):
        client, main, *_ = create_client(monkeypatch)
        monkeypatch.setattr(main, "OUTPUT_DIR", str(tmp_path))

        def _fake_gcs_download(filename, dest):
            Path(dest).write_bytes(b"%PDF-gcs")
            return True

        monkeypatch.setattr(main, "download_from_gcs", _fake_gcs_download)

        resp = client.get(
            "/api/documents/download/cloud-only.pdf",
            headers=_admin_headers(main),
        )

        assert resp.status_code == 200
        assert resp.content == b"%PDF-gcs"

    def test_download_404_when_missing_everywhere(self, monkeypatch, tmp_path):
        client, main, *_ = create_client(monkeypatch)
        monkeypatch.setattr(main, "OUTPUT_DIR", str(tmp_path))
        monkeypatch.setattr(main, "download_from_gcs", lambda *a, **k: False)

        resp = client.get(
            "/api/documents/download/nope.pdf",
            headers=_admin_headers(main),
        )

        assert resp.status_code == 404
        assert resp.json() == {"error": "File not found"}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
