"""Nothing reaches a buyer for signature without staff review.

Covers the main.py wiring:
  * deal stage changes (Contract / Pending) queue a CRM task, never send,
    unless ESIGN_AUTO_SEND is explicitly on AND the deal passes the gate
  * a new website lead never gets a Credit Authorization; staff get a task
  * closing-packet generation does not e-sign dispatch by default
  * Send for Signature: preview -> confirm -> send, refusing blank money,
    unconfirmed sends, and stale previews
  * cancel a pending signing request via the DocuSeal archive API

Run: python -m pytest tests/test_esign_staff_review_routes.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.test_api_v1 import load_app  # noqa: E402
from tests.test_esign_review import _consistent_printed_values, _ready_doc_data  # noqa: E402

SALES_CONTRACT = "TMHA_SalesContract.pdf"
DEPOSIT = "TMHA-SalesContractDepositAgreement.pdf"


class FakeDealDB:
    def __init__(self, deals: dict[str, dict]):
        self.deals = deals

    def get_deal(self, deal_id):
        deal = self.deals.get(deal_id)
        return dict(deal) if deal else None

    def update_deal(self, deal_id, data):
        self.deals.setdefault(deal_id, {"id": deal_id}).update(data)
        return True


class Harness:
    def __init__(self, monkeypatch, *, configured=True, doc_data=None):
        monkeypatch.delenv("FF_ESIGN_AUTO_SEND", raising=False)
        main, _fake_db, _logger = load_app(monkeypatch, tho_api_key="tho-secret")
        main._crm_tasks.clear()
        main._esign_requests.clear()
        self.main = main
        self.client = TestClient(main.app)
        self.headers = {"Authorization": f"Bearer {main._create_admin_token()}"}
        self.doc_data = doc_data if doc_data is not None else _ready_doc_data()
        self.deal_db = FakeDealDB({"deal-1": {"id": "deal-1", "status": "approved"}})
        self.sent: list[dict] = []
        self.file_sent: list[dict] = []
        self.archived: list[str] = []
        self.generated: list[dict] = []

        monkeypatch.setattr(main, "_deal_db", self.deal_db)
        monkeypatch.setattr(main, "docuseal_is_configured", lambda: configured)
        monkeypatch.setattr(main, "_deal_document_data", lambda _deal: dict(self.doc_data))

        def generate(**kwargs):
            self.generated.append(kwargs)
            return {"success": True, "file_path": "/tmp/esign.pdf", "filename": "esign.pdf"}

        monkeypatch.setattr(main, "engine_generate_document", generate)
        monkeypatch.setattr(
            main.esign_review,
            "read_filled_values",
            lambda _path: _consistent_printed_values(self.doc_data),
        )

        async def send_for_signature(**kwargs):
            self.sent.append(kwargs)
            return {"success": True, "submission": [{"id": 5, "submission_id": 42}]}

        async def send_file_for_signature(**kwargs):
            self.file_sent.append(kwargs)
            return {"success": True}

        async def archive(submission_id):
            self.archived.append(submission_id)
            return {"success": True}

        monkeypatch.setattr(main, "docuseal_send_for_signature", send_for_signature)
        monkeypatch.setattr(main, "docuseal_send_file_for_signature", send_file_for_signature)
        monkeypatch.setattr(main, "docuseal_archive_submission", archive)
        monkeypatch.setattr(main, "send_deal_status_update", lambda **_kw: {"success": True})
        self.audit: list[dict] = []
        monkeypatch.setattr(main, "log_admin_action", lambda **kw: self.audit.append(kw))

    def audit_actions(self):
        return [entry["action"] for entry in self.audit]

    def tasks(self):
        return list(self.main._crm_tasks.values())

    def move(self, status):
        return self.client.put(
            "/api/deals/deal-1/status", headers=self.headers, json={"status": status}
        )

    def preview(self, template=SALES_CONTRACT):
        return self.client.post(
            "/api/deals/deal-1/esign/preview",
            headers=self.headers,
            json={"template_name": template},
        )

    def send(self, **body):
        body.setdefault("template_name", SALES_CONTRACT)
        return self.client.post("/api/deals/deal-1/esign/send", headers=self.headers, json=body)


# ─── 1. Stage changes never auto-send by default ────────────────────────────


@pytest.mark.parametrize(
    ("status", "template"), [("contract", SALES_CONTRACT), ("pending", DEPOSIT)]
)
def test_stage_change_queues_review_task_and_sends_nothing(monkeypatch, status, template):
    h = Harness(monkeypatch)
    response = h.move(status)
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["esign"] == "queued_for_review"
    assert h.sent == [] and h.file_sent == []

    tasks = h.tasks()
    assert len(tasks) == 1
    assert tasks[0]["related_deal"] == "deal-1"
    assert tasks[0]["status"] == "pending"
    assert "Nothing was sent to the buyer" in tasks[0]["description"]
    assert template.removesuffix(".pdf") in tasks[0]["task_id"]

    # Moving to the same stage again refreshes the one task instead of piling up.
    h.move(status)
    assert len(h.tasks()) == 1


def test_stage_change_without_docuseal_does_nothing(monkeypatch):
    h = Harness(monkeypatch, configured=False)
    body = h.move("contract").json()
    assert body["success"] is True
    assert "esign" not in body
    assert h.tasks() == [] and h.sent == []


def test_stage_without_signing_document_does_nothing(monkeypatch):
    h = Harness(monkeypatch)
    body = h.move("funded").json()
    assert "esign" not in body
    assert h.tasks() == [] and h.sent == []


def test_auto_send_opt_in_still_refuses_blank_money(monkeypatch):
    h = Harness(monkeypatch, doc_data=_ready_doc_data(down_payment=0, tax_rate=None))
    monkeypatch.setenv("FF_ESIGN_AUTO_SEND", "1")
    body = h.move("contract").json()
    assert body["esign"] == "queued_for_review"
    assert h.sent == []
    description = h.tasks()[0]["description"]
    assert "Down payment is blank or $0" in description
    assert "Property tax rate is blank or 0%" in description


def test_auto_send_opt_in_sends_complete_deal_with_values(monkeypatch):
    h = Harness(monkeypatch)
    monkeypatch.setenv("FF_ESIGN_AUTO_SEND", "1")
    body = h.move("contract").json()
    assert body["esign"] == "auto_sent"
    assert len(h.sent) == 1
    assert h.sent[0]["values"]["topmostSubform[0].Page1[0].SalePrice[0]"] == "80,000.00"
    assert h.tasks() == []
    assert h.main._esign_requests["deal-1_42"]["trigger"] == "deal.status_change.contract"


# ─── 2. Send for Signature: preview, confirm, refuse ────────────────────────


def test_preview_reports_blank_money_and_never_sends(monkeypatch):
    h = Harness(
        monkeypatch,
        doc_data=_ready_doc_data(sales_price=None, annual_insurance=0, apr=None),
    )
    response = h.preview()
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is False
    messages = " ".join(p["message"] for p in body["problems"])
    assert "Sales price is blank or $0" in messages
    assert "Insurance is blank or $0" in messages
    assert "APR or loan term is blank" in messages
    assert h.generated == [] and h.sent == []


def test_preview_of_ready_deal_shows_summary_without_raw_values(monkeypatch):
    h = Harness(monkeypatch)
    body = h.preview().json()
    assert body["ready"] is True
    assert body["esign_configured"] is True
    assert body["review_token"]
    assert body["download_url"] == "/api/documents/download/esign.pdf"
    assert "values" not in body
    labels = {row["label"]: row["value"] for row in body["money_summary"]}
    assert labels["Sales price"] == "$80,000.00"
    assert labels["Total monthly payment"] == "$884.20"
    assert h.sent == []


def test_send_requires_confirmation(monkeypatch):
    h = Harness(monkeypatch)
    token = h.preview().json()["review_token"]
    response = h.send(review_token=token)
    assert response.status_code == 400
    assert response.json()["message"].startswith("Not sent.")
    assert h.sent == []


def test_send_refuses_when_money_fields_blank(monkeypatch):
    h = Harness(monkeypatch, doc_data=_ready_doc_data(down_payment=None))
    response = h.send(confirm=True, review_token="anything")
    assert response.status_code == 422
    body = response.json()
    assert body["message"] == (
        "Not sent. Down payment is blank or $0. Enter the down payment on the deal."
    )
    assert "values" not in body
    assert h.sent == []


def test_send_refuses_stale_preview(monkeypatch):
    h = Harness(monkeypatch)
    token = h.preview().json()["review_token"]
    h.doc_data["sales_price"] = 81000
    response = h.send(confirm=True, review_token=token)
    assert response.status_code == 409
    assert "changed after you reviewed it" in response.json()["message"]
    assert h.sent == []


@pytest.mark.parametrize(
    ("field", "value"),
    [("buyer_email", "someone-else@example.com"), ("buyer_first_name", "Mallory")],
)
def test_send_refuses_when_signer_changed_after_preview(monkeypatch, field, value):
    h = Harness(monkeypatch)
    token = h.preview().json()["review_token"]
    h.doc_data[field] = value
    response = h.send(confirm=True, review_token=token)
    assert response.status_code == 409
    assert response.json()["error"] == "review_stale"
    assert h.sent == []


def test_confirmed_send_uses_deal_values_and_records_request(monkeypatch):
    h = Harness(monkeypatch)
    token = h.preview().json()["review_token"]
    response = h.send(confirm=True, review_token=token)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["submission_ids"] == ["42"]
    assert body["message"] == "Sales Contract sent to alice@example.com for signature."

    assert len(h.sent) == 1
    sent = h.sent[0]
    assert sent["email"] == "alice@example.com"
    assert sent["name"] == "Alice Buyer"
    assert sent["template_name"] == SALES_CONTRACT
    assert sent["values"]["topmostSubform[0].Page1[0].SalePrice[0]"] == "80,000.00"
    assert sent["values"]["topmostSubform[0].Page1[0].DownPmt[0]"] == "5,000.00"

    listed = h.client.get("/api/deals/deal-1/esign", headers=h.headers).json()["requests"]
    assert [(r["submission_id"], r["status"]) for r in listed] == [("42", "pending")]
    assert h.audit_actions() == ["document.esign_send"]
    assert "values" not in h.audit[0]["details"]


def test_send_not_configured_returns_501(monkeypatch):
    h = Harness(monkeypatch, configured=False)
    response = h.send(confirm=True, review_token="x")
    assert response.status_code == 501
    assert h.sent == []


def test_legacy_docuseal_send_requires_a_deal(monkeypatch):
    h = Harness(monkeypatch)
    response = h.client.post(
        "/api/docuseal/send",
        headers=h.headers,
        json={
            "template_name": SALES_CONTRACT,
            "signer_email": "buyer@example.com",
            "signer_name": "Buyer",
        },
    )
    assert response.status_code == 400
    assert "filled in with the deal's numbers" in response.json()["message"]

    unconfirmed = h.client.post(
        "/api/docuseal/send",
        headers=h.headers,
        json={"deal_id": "deal-1", "template_name": SALES_CONTRACT},
    )
    assert unconfirmed.status_code == 400
    assert h.sent == []


def test_esign_routes_require_admin(monkeypatch):
    h = Harness(monkeypatch)
    assert h.client.post("/api/deals/deal-1/esign/preview", json={}).status_code == 401
    assert h.client.post("/api/deals/deal-1/esign/send", json={"confirm": True}).status_code == 401
    assert h.client.post("/api/deals/deal-1/esign/42/cancel").status_code == 401


# ─── 3. New lead: never a Credit Authorization ──────────────────────────────


def test_new_lead_creates_task_instead_of_credit_auth(monkeypatch):
    h = Harness(monkeypatch)
    monkeypatch.setenv("NOTION_LEAD_SYNC", "off")
    response = h.client.post(
        "/api/contact",
        json={"name": "Synthetic Lead", "phone": "5125550123", "email": "lead@example.com"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    assert "docuseal_failed" not in response.json().get("warnings", [])
    assert h.sent == [] and h.file_sent == []

    tasks = h.tasks()
    assert len(tasks) == 1
    assert "Credit Authorization" in tasks[0]["title"]
    assert tasks[0]["related_lead"] == response.json()["lead_id"]
    assert "Nothing was sent" in tasks[0]["description"]


# ─── Closing packet: no automatic e-sign by default ─────────────────────────


def test_packet_generation_does_not_esign_by_default(monkeypatch):
    h = Harness(monkeypatch)
    monkeypatch.setattr(h.main, "Deal", _DocDeal(h))
    monkeypatch.setattr(h.main, "validate_for_documents", lambda _data: {})
    monkeypatch.setattr(
        h.main,
        "engine_generate_packet",
        lambda **_kw: {
            "success": True,
            "file_path": "/tmp/packet.pdf",
            "filename": "packet.pdf",
            "download_url": "/api/documents/download/packet.pdf",
            "message": "ok",
        },
    )
    monkeypatch.setattr(h.main, "_maybe_email_document", lambda **_kw: None)
    response = h.client.post(
        "/api/deals/deal-1/generate-packet",
        headers=h.headers,
        json={"packet_name": "standard_closing"},
    )
    assert response.status_code == 200
    assert response.json()["success"] is True, response.text
    assert h.file_sent == []


class _DocDeal:
    """Deal stand-in whose to_document_data returns the harness doc data."""

    def __init__(self, harness):
        self._harness = harness

    def __call__(self, **_data):
        harness = self._harness

        class _Deal:
            def model_dump(self):
                return dict(harness.doc_data)

            def to_document_data(self):
                return dict(harness.doc_data)

        return _Deal()


# ─── 4. Cancel a pending signing request ────────────────────────────────────


def _send_one(h):
    token = h.preview().json()["review_token"]
    assert h.send(confirm=True, review_token=token).status_code == 200


def test_cancel_pending_request_archives_in_docuseal(monkeypatch):
    h = Harness(monkeypatch)
    _send_one(h)
    response = h.client.post("/api/deals/deal-1/esign/42/cancel", headers=h.headers)
    assert response.status_code == 200, response.text
    assert response.json()["message"] == (
        "Signing request cancelled. The buyer can no longer sign it."
    )
    assert h.archived == ["42"]
    assert h.main._esign_requests["deal-1_42"]["status"] == "cancelled"
    assert h.audit_actions()[-1] == "document.esign_cancel"

    again = h.client.post("/api/deals/deal-1/esign/42/cancel", headers=h.headers)
    assert again.status_code == 409
    assert h.archived == ["42"]


def test_cancel_unknown_or_other_deal_request_is_refused(monkeypatch):
    h = Harness(monkeypatch)
    _send_one(h)
    missing = h.client.post("/api/deals/deal-1/esign/999/cancel", headers=h.headers)
    other_deal = h.client.post("/api/deals/deal-2/esign/42/cancel", headers=h.headers)
    assert missing.status_code == 404
    assert other_deal.status_code == 404
    assert h.archived == []


def test_cancel_failure_keeps_request_pending(monkeypatch):
    h = Harness(monkeypatch)
    _send_one(h)

    async def failing_archive(_submission_id):
        return {"success": False, "error": "DocuSeal API returned 500"}

    monkeypatch.setattr(h.main, "docuseal_archive_submission", failing_archive)
    response = h.client.post("/api/deals/deal-1/esign/42/cancel", headers=h.headers)
    assert response.status_code == 502
    assert h.main._esign_requests["deal-1_42"]["status"] == "pending"
