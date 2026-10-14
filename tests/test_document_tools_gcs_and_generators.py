"""Tests for document_tools GCS persistence and the HTML-document generators.

GCS functions are exercised through a fake google.cloud.storage module
(patched onto both sys.modules and the google.cloud package — the parent
attribute wins once the real submodule has been imported). The work order /
service ticket / customer email generators are pure template functions;
these tests pin their branching (warranty vs billable, default vs custom
timeline, email-type fallback) and HTML-escaping behavior.

Run: python -m pytest tests/test_document_tools_gcs_and_generators.py -v
"""

from __future__ import annotations

import sys
import types
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import tools.document_tools as dt  # noqa: E402

# ---------------------------------------------------------------------------
# Fake GCS
# ---------------------------------------------------------------------------


class FakeBlob:
    def __init__(self, name, size=100, time_created=None):
        self.name = name
        self.size = size
        self.time_created = time_created or datetime(2026, 10, 1, 12, 0, 0)
        self.uploads = []
        self.downloads = []
        self.exists_result = True

    def upload_from_filename(self, local_path, content_type=None):
        self.uploads.append((local_path, content_type))

    def exists(self):
        return self.exists_result

    def download_to_filename(self, local_path):
        self.downloads.append(local_path)


class FakeBucket:
    def __init__(self, name, blobs=None, raise_on=None):
        self.name = name
        self._blobs = blobs or []
        self.raise_on = raise_on

    def blob(self, name):
        if self.raise_on == "blob":
            raise RuntimeError("blob failed")
        for existing in self._blobs:
            if existing.name == name:
                return existing
        new = FakeBlob(name)
        self._blobs.append(new)
        return new

    def list_blobs(self, prefix=""):
        if self.raise_on == "list":
            raise RuntimeError("list failed")
        return [b for b in self._blobs if b.name.startswith(prefix)]


@pytest.fixture
def gcs(monkeypatch, tmp_path):
    """Reset the module-level GCS cache and install a fake storage module."""
    monkeypatch.setattr(dt, "_gcs_client", None)
    monkeypatch.setattr(dt, "_gcs_bucket", None)
    monkeypatch.delenv("THO_DISABLE_GCS_UPLOADS", raising=False)

    state = {"bucket": FakeBucket("tho-test-bucket")}

    import google.cloud

    storage = types.ModuleType("google.cloud.storage")
    storage.Client = lambda: SimpleNamespace(bucket=lambda name: state["bucket"])
    monkeypatch.setitem(sys.modules, "google.cloud.storage", storage)
    monkeypatch.setattr(google.cloud, "storage", storage, raising=False)
    return state


class TestGcsUpload:
    def test_upload_returns_uri(self, gcs, tmp_path):
        local = tmp_path / "doc.pdf"
        local.write_bytes(b"%PDF-fake")

        uri = dt.upload_to_gcs(str(local), "doc.pdf")

        # URI is built from the module's bucket constant, not the fake name.
        assert uri == f"gs://{dt._GCS_BUCKET_NAME}/generated_docs/doc.pdf"
        blob = gcs["bucket"].blob("generated_docs/doc.pdf")
        assert blob.uploads == [(str(local), "application/pdf")]

    def test_upload_disabled_by_env(self, gcs, monkeypatch, tmp_path):
        monkeypatch.setenv("THO_DISABLE_GCS_UPLOADS", "1")
        local = tmp_path / "doc.pdf"
        local.write_bytes(b"x")
        assert dt.upload_to_gcs(str(local), "doc.pdf") is None
        assert gcs["bucket"].list_blobs(prefix="generated_docs/") == []

    def test_upload_failure_returns_none(self, gcs, tmp_path, monkeypatch):
        import google.cloud

        class ExplodingBlob(FakeBlob):
            def upload_from_filename(self, local_path, content_type=None):
                raise RuntimeError("network down")

        bucket = FakeBucket(dt._GCS_BUCKET_NAME)
        bucket._blobs.append(ExplodingBlob("generated_docs/boom.pdf"))
        monkeypatch.setattr(
            google.cloud.storage,
            "Client",
            lambda: SimpleNamespace(bucket=lambda name: bucket),
        )

        assert dt.upload_to_gcs(str(tmp_path / "x.pdf"), "boom.pdf") is None

    def test_upload_without_bucket_returns_none(self, gcs, monkeypatch, tmp_path):
        gcs["bucket"] = None
        assert dt.upload_to_gcs(str(tmp_path / "x.pdf"), "doc.pdf") is None


class TestGcsDownload:
    def test_download_hit(self, gcs, tmp_path):
        target = tmp_path / "out.pdf"
        assert dt.download_from_gcs("doc.pdf", str(target)) is True
        blob = gcs["bucket"].blob("generated_docs/doc.pdf")
        assert blob.downloads == [str(target)]

    def test_download_missing_blob(self, gcs, tmp_path):
        blob = gcs["bucket"].blob("generated_docs/ghost.pdf")
        blob.exists_result = False
        assert dt.download_from_gcs("ghost.pdf", str(tmp_path / "out.pdf")) is False

    def test_download_failure_returns_false(self, gcs, tmp_path):
        gcs["bucket"].raise_on = "blob"
        assert dt.download_from_gcs("doc.pdf", str(tmp_path / "out.pdf")) is False


class TestGcsList:
    def test_lists_only_pdfs_sorted_newest_first(self, gcs):
        gcs["bucket"]._blobs = [
            FakeBlob("generated_docs/old.pdf", size=10, time_created=datetime(2026, 1, 1)),
            FakeBlob("generated_docs/new.pdf", size=20, time_created=datetime(2026, 10, 1)),
            FakeBlob("generated_docs/notes.txt", size=5),
            FakeBlob("other/ignore.pdf", size=7),
        ]
        docs = dt.list_gcs_documents()
        assert [d["filename"] for d in docs] == ["new.pdf", "old.pdf"]
        assert docs[0]["size_bytes"] == 20
        assert docs[0]["download_url"] == "/api/documents/download/new.pdf"
        assert docs[0]["created_at"].startswith("2026-10-01")

    def test_list_failure_returns_empty(self, gcs):
        gcs["bucket"].raise_on = "list"
        assert dt.list_gcs_documents() == []


# ---------------------------------------------------------------------------
# HTML document generators
# ---------------------------------------------------------------------------


class TestGenerateWorkOrderPdf:
    def test_warranty_claim_has_no_cost_section(self):
        result = dt.generate_work_order_pdf(
            customer_name="Jane <Doe>",
            customer_address="1 Main St",
            customer_phone="281-555-0100",
            work_description="Fix <leaky> roof",
            is_warranty=True,
        )
        assert result["success"] is True
        assert result["document_id"].startswith("WO-")
        assert "WARRANTY CLAIM" in result["html_content"]
        assert "Estimated Cost" not in result["html_content"]
        # Arguments are HTML-escaped.
        assert "Jane &lt;Doe&gt;" in result["html_content"]
        assert "Fix &lt;leaky&gt; roof" in result["html_content"]

    def test_billable_includes_cost(self):
        result = dt.generate_work_order_pdf(
            customer_name="Jane",
            customer_address="1 Main St",
            customer_phone="281",
            work_description="Repair",
            is_warranty=False,
            estimated_cost=1250.5,
        )
        assert "CUSTOMER BILLABLE" in result["html_content"]
        assert "$1,250.50" in result["html_content"]

    def test_billable_without_cost_omits_section(self):
        result = dt.generate_work_order_pdf(
            customer_name="Jane",
            customer_address="1 Main St",
            customer_phone="281",
            work_description="Repair",
            is_warranty=False,
        )
        assert "Estimated Cost" not in result["html_content"]


class TestGenerateServiceTicket:
    def test_default_timeline_when_empty(self):
        result = dt.generate_service_ticket(
            customer_name="Jane",
            customer_address="1 Main St",
            customer_phone="281",
            issue_description="AC not cooling",
            issue_type="HVAC",
            warranty_status="Covered",
            timeline=None,
        )
        assert result["ticket_id"].startswith("ST-")
        assert "Ticket created via AI Service Agent" in result["html_content"]
        assert "AC not cooling" in result["html_content"]

    def test_custom_timeline_rendered_and_escaped(self):
        result = dt.generate_service_ticket(
            customer_name="Jane",
            customer_address="1 Main St",
            customer_phone="281",
            issue_description="Leak",
            issue_type="Plumbing",
            warranty_status="Expired",
            status="In Progress",
            timeline=[
                {"date": "2026-10-01", "event": "Contractor <called> customer"},
                {"date": "2026-10-02", "event": "Parts ordered"},
            ],
        )
        assert "Contractor &lt;called&gt; customer" in result["html_content"]
        assert "Parts ordered" in result["html_content"]
        assert result["status"] == "In Progress"


class TestGenerateCustomerEmail:
    def test_appointment_confirmation_uses_details(self):
        result = dt.generate_customer_email(
            "Jane Doe",
            "appointment_confirmation",
            appointment_details={"date": "2026-10-20", "time": "10:00 AM"},
        )
        assert result["success"] is True
        assert "Confirmed" in result["subject"]
        assert "2026-10-20" in result["body"]
        assert "Jane" in result["body"]

    def test_service_update_mentions_ticket_and_custom_content(self):
        result = dt.generate_customer_email(
            "John Smith",
            "service_update",
            ticket_id="ST-123",
            custom_content="The part arrived early.",
        )
        assert "ST-123" in result["subject"]
        assert "(ST-123)" in result["body"]
        assert "The part arrived early." in result["body"]

    def test_welcome_and_follow_up(self):
        welcome = dt.generate_customer_email("Alice", "welcome")
        assert "Warranty" in welcome["body"] or "WARRANTY" in welcome["body"]
        follow_up = dt.generate_customer_email("Alice", "follow_up", ticket_id="ST-9")
        assert "service visit" in follow_up["body"]

    def test_unknown_type_falls_back_to_follow_up(self):
        result = dt.generate_customer_email("Alice", "bogus_type")
        assert result["email_type"] == "bogus_type"
        assert "check in" in result["body"]  # follow_up body

    def test_empty_name_uses_default_greeting(self):
        result = dt.generate_customer_email("", "welcome")
        assert "Valued Customer" in result["body"]


# ---------------------------------------------------------------------------
# Pure fill helpers
# ---------------------------------------------------------------------------


class TestPhoneDigits:
    def test_strips_and_truncates(self):
        assert dt._phone_digits("(281) 324-3020") == "2813243020"
        assert dt._phone_digits("+1 281 324 3020") == "2813243020"
        assert dt._phone_digits(None) == ""
        assert dt._phone_digits("123456789012345") == "1234567890"


class TestResolveCheckboxValues:
    def test_exact_state_match_wins(self):
        data = {"new_used": "/2"}
        states = {"new_used": ["/Off", "/1", "/2"]}
        assert dt._resolve_checkbox_values(data, states)["new_used"] == "/2"

    def test_truthy_value_on_simple_checkbox_maps_to_single_on_state(self):
        data = {"warranty": "Yes"}
        states = {"warranty": ["/Off", "/Yes"]}
        assert dt._resolve_checkbox_values(data, states)["warranty"] == "/Yes"

    def test_falsy_value_maps_to_off(self):
        data = {"warranty": "off"}
        states = {"warranty": ["/Off", "/Yes"]}
        assert dt._resolve_checkbox_values(data, states)["warranty"] == "/Off"

    def test_ambiguous_truthy_on_radio_left_untouched(self):
        data = {"choice": "on"}
        states = {"choice": ["/Off", "/A", "/B"]}
        assert dt._resolve_checkbox_values(data, states)["choice"] == "on"

    def test_unknown_field_and_no_on_states_are_skipped(self):
        data = {"other": "on"}
        states = {"cb": ["/Off"]}
        assert dt._resolve_checkbox_values(data, states) == {"other": "on"}
