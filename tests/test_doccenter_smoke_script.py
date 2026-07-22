import json

from scripts import doccenter_smoke


def test_default_doccenter_smoke_targets_canonical_tho_domain():
    assert doccenter_smoke.DEFAULT_BASE_URL == "https://www.texashomeoutlet.com"


def test_document_smoke_data_uses_hidden_synthetic_buyer_marker():
    data = doccenter_smoke._build_document_data({}, "20260515193000")

    assert "Smoke Buyer" in data["buyer_name"]
    assert data["serial_number_1"].startswith("SMOKE-")
    assert data["buyer_email"].endswith("@example.com")


def test_document_result_downloads_collects_batch_and_packet_urls():
    result = {
        "merged_document": {"download_url": "/api/documents/download/merged.pdf"},
        "documents": [{"download_url": "/api/documents/download/one.pdf"}],
        "download_url": "/api/documents/download/packet.pdf",
    }

    assert doccenter_smoke._document_result_downloads(result) == [
        ("batch_merged_pdf", "/api/documents/download/merged.pdf"),
        ("batch_document_1_pdf", "/api/documents/download/one.pdf"),
        ("packet_pdf", "/api/documents/download/packet.pdf"),
    ]


def test_customer_search_probe_rejects_raw_ssn_in_response(monkeypatch):
    calls = []

    class FakeClient:
        def json(self, method, path, *, payload=None):
            calls.append((method, path, payload))
            if path == "/api/admin/verify":
                return 200, {"success": True}, 1
            if path == "/api/admin/check":
                return 200, {"valid": True}, 1
            if path == "/api/documents/readiness":
                return (
                    200,
                    {
                        "status": "ready",
                        "template_count": 63,
                        "packet_count": 5,
                        "output_dir": "available",
                        "gcs_document_count": 100,
                    },
                    1,
                )
            if path == "/api/documents/templates":
                return (
                    200,
                    {
                        "templates": [{"template_name": doccenter_smoke.DEFAULT_TEMPLATE}],
                        "packets": [{"packet_name": doccenter_smoke.DEFAULT_PACKET}],
                    },
                    1,
                )
            if path.startswith("/api/inventory"):
                return 200, {"inventory": [{"id": "home-1", "manufacturer": "TRU"}]}, 1
            if path == "/api/documents/history":
                return 200, {"documents": [], "total": 0, "hidden_test_document_count": 0}, 1
            if path == "/api/customers":
                return 200, {"success": True, "id": "cust-1"}, 1
            if path.startswith("/api/customers/search"):
                return (
                    200,
                    {"customers": [{"full_name": "Document Smoke Buyer 20260515193000"}]},
                    1,
                )
            raise AssertionError(path)

    monkeypatch.setattr(doccenter_smoke, "SmokeClient", lambda *_args, **_kwargs: FakeClient())
    monkeypatch.setattr(doccenter_smoke, "_iso_stamp", lambda: "20260515193000")

    ok, summary = doccenter_smoke.run_smoke(
        base_url="https://example.test",
        pin="1234",
        timeout=1,
        write_documents=False,
        write_packet=False,
        write_customer=True,
        template=doccenter_smoke.DEFAULT_TEMPLATE,
        packet_name=doccenter_smoke.DEFAULT_PACKET,
    )

    assert ok
    assert any(probe["name"] == "customer_record_search" for probe in summary["probes"])
    assert "123-45-6789" not in json.dumps(summary)
    assert any(call[1] == "/api/customers" for call in calls)
