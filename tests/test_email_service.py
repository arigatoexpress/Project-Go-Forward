"""Tests for email_service.send_document_email and the document-generation
endpoints' auto-delivery behaviour.

Covers:
  * `send_document_email` builds the correct subject + branded body + plain
    text fallback and routes through `send_email`.
  * Sending logs to the Firestore email_log with type=document_delivery.
  * The `RESEND_FROM` env var is read at send time (not at module import).
  * `/api/documents/generate` invokes `send_document_email` when
    `customer_email` is provided.
  * `/api/documents/generate` does NOT break the response when
    `send_document_email` raises.

Run: python -m pytest tests/test_email_service.py -v
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ─────────────────────────────────────────────────────────────────────────────
# Pure-unit tests for send_document_email
# ─────────────────────────────────────────────────────────────────────────────


def _fresh_email_service(monkeypatch):
    """Reload email_service so env-var changes are picked up cleanly."""
    sys.modules.pop("email_service", None)
    return importlib.import_module("email_service")


class TestSendDocumentEmail:
    def test_builds_subject_and_body_shape(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        monkeypatch.setenv(
            "RESEND_FROM",
            "Texas Home Outlet <noreply@texashomeoutlet.com>",
        )
        email_service = _fresh_email_service(monkeypatch)

        sent_payloads: list[dict] = []

        # Stub the resend module so no real network call is attempted.
        fake_resend = types.SimpleNamespace(
            api_key="",
            Emails=types.SimpleNamespace(
                send=lambda payload: (sent_payloads.append(payload) or {"id": "msg_123"}),
            ),
        )
        monkeypatch.setitem(sys.modules, "resend", fake_resend)
        # Skip Firestore logging in the unit test.
        monkeypatch.setattr(email_service, "_log_email_activity", lambda *a, **k: None)

        result = email_service.send_document_email(
            to="alice@example.com",
            customer_name="Alice Buyer",
            doc_filename="TMHA_SalesContract_alice.pdf",
            doc_type="sales_contract",
            download_url="/api/documents/download/TMHA_SalesContract_alice.pdf",
            deal_id="deal-42",
        )

        assert result["success"] is True
        assert result["message_id"] == "msg_123"
        assert len(sent_payloads) == 1
        payload = sent_payloads[0]

        # Sender comes from the (currently-set) env var, not a hardcoded test
        # domain.
        assert "texashomeoutlet.com" in payload["from"]
        assert "resend.dev" not in payload["from"]

        # Subject mentions the doc type and the first name of the customer.
        assert payload["subject"] == "Your Sales Contract is ready, Alice"

        # HTML body is wrapped in the THO branded template and references
        # both the document filename and the CTA download link.
        assert "Texas Home Outlet" in payload["html"]
        assert "TMHA_SalesContract_alice.pdf" in payload["html"]
        assert "/api/documents/download/TMHA_SalesContract_alice.pdf" in payload["html"]
        assert "Download Your Document" in payload["html"]

        # Plain-text alternative is supplied automatically.
        assert "text" in payload and payload["text"]
        assert "Sales Contract" in payload["text"]
        assert "TMHA_SalesContract_alice.pdf" in payload["text"]

        # Recipient is a single-element list (Resend convention).
        assert payload["to"] == ["alice@example.com"]

    def test_logs_to_email_log_with_document_delivery_type(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        email_service = _fresh_email_service(monkeypatch)

        captured: list[tuple] = []

        def _capture_log(to, subject, email_type, related_id=None):
            captured.append((to, subject, email_type, related_id))

        monkeypatch.setattr(email_service, "_log_email_activity", _capture_log)

        fake_resend = types.SimpleNamespace(
            api_key="",
            Emails=types.SimpleNamespace(send=lambda payload: {"id": "msg_log"}),
        )
        monkeypatch.setitem(sys.modules, "resend", fake_resend)

        email_service.send_document_email(
            to="bob@example.com",
            customer_name="Bob",
            doc_filename="packet.pdf",
            doc_type="standard_closing closing packet",
            download_url="https://app.example.com/api/documents/download/packet.pdf",
            deal_id="deal-7",
        )

        assert len(captured) == 1
        to, subject, email_type, related_id = captured[0]
        assert to == "bob@example.com"
        assert email_type == "document_delivery"
        assert related_id == "deal-7"
        # Subject still reflects the humanized doc type.
        assert subject.lower().startswith("your standard closing closing packet is ready")

    def test_resend_from_env_is_read_at_send_time(self, monkeypatch):
        """If a deployment swaps RESEND_FROM after import, the next send
        should pick up the new value (no module reload required)."""
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        monkeypatch.setenv(
            "RESEND_FROM",
            "Texas Home Outlet <noreply@texashomeoutlet.com>",
        )
        email_service = _fresh_email_service(monkeypatch)

        sent_payloads: list[dict] = []
        fake_resend = types.SimpleNamespace(
            api_key="",
            Emails=types.SimpleNamespace(
                send=lambda payload: (sent_payloads.append(payload) or {"id": "msg_x"}),
            ),
        )
        monkeypatch.setitem(sys.modules, "resend", fake_resend)
        monkeypatch.setattr(email_service, "_log_email_activity", lambda *a, **k: None)

        # First send uses the originally set env var.
        email_service.send_email(
            to="t@example.com",
            subject="hi",
            html="<p>hi</p>",
        )
        # Now swap the env var (e.g. operator changes alias).
        monkeypatch.setenv(
            "RESEND_FROM",
            "Texas Home Outlet <documents@texashomeoutlet.com>",
        )
        email_service.send_email(
            to="t@example.com",
            subject="hi2",
            html="<p>hi</p>",
        )

        assert "noreply@texashomeoutlet.com" in sent_payloads[0]["from"]
        assert "documents@texashomeoutlet.com" in sent_payloads[1]["from"]

    def test_staff_alerts_fan_out_to_all_recipients(self, monkeypatch):
        """notify_new_lead sends ONE email addressed to every configured staff
        recipient; replies still route to the shared mailbox, and the email_log
        records the joined recipient string."""
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        monkeypatch.setenv(
            "NOTIFICATION_EMAIL",
            "ben@texashomeoutlet.com, lee@texashomeoutlet.com ;celeste@texashomeoutlet.com",
        )
        email_service = _fresh_email_service(monkeypatch)

        # The comma/semicolon list is parsed and de-duplicated into a clean list.
        assert email_service.NOTIFICATION_EMAILS == [
            "ben@texashomeoutlet.com",
            "lee@texashomeoutlet.com",
            "celeste@texashomeoutlet.com",
        ]

        sent_payloads: list[dict] = []
        logged: list[tuple] = []
        fake_resend = types.SimpleNamespace(
            api_key="",
            Emails=types.SimpleNamespace(
                send=lambda payload: (sent_payloads.append(payload) or {"id": "msg_fanout"}),
            ),
        )
        monkeypatch.setitem(sys.modules, "resend", fake_resend)
        monkeypatch.setattr(
            email_service,
            "_log_email_activity",
            lambda to, *a, **k: logged.append((to, *a)),
        )

        result = email_service.notify_new_lead(
            customer_name="Carol Lead", phone="555-0100", email="carol@example.com"
        )

        assert result["success"] is True
        assert len(sent_payloads) == 1
        # ONE email, delivered to ALL staff recipients.
        assert sent_payloads[0]["to"] == [
            "ben@texashomeoutlet.com",
            "lee@texashomeoutlet.com",
            "celeste@texashomeoutlet.com",
        ]
        # Customer replies still route to the shared mailbox, not the staff list.
        assert sent_payloads[0]["reply_to"] == "sales@texashomeoutlet.com"
        # The activity log captures the joined recipient string.
        assert logged[0][0] == (
            "ben@texashomeoutlet.com, lee@texashomeoutlet.com, celeste@texashomeoutlet.com"
        )

    def test_skips_send_when_api_key_missing(self, monkeypatch):
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        email_service = _fresh_email_service(monkeypatch)

        # Even without an API key, the helper must not raise.
        result = email_service.send_document_email(
            to="x@example.com",
            customer_name="X",
            doc_filename="f.pdf",
            doc_type="sales_contract",
            download_url="/x",
        )
        assert result["success"] is False
        assert result["dry_run"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint integration tests — auto-delivery from /api/documents/generate
# ─────────────────────────────────────────────────────────────────────────────


def _build_main_with_doc_engine(monkeypatch, *, engine_result=None):
    """Boot a TestClient against `main` with the heavy GCP dependencies stubbed.

    Mirrors the helper used in `tests/test_deal_document_validation.py` so we
    don't drag in npm builds or live Firestore.
    """
    frontend_dist = REPO_ROOT / "frontend" / "dist"
    (frontend_dist / "assets").mkdir(parents=True, exist_ok=True)
    index_html = frontend_dist / "index.html"
    if not index_html.exists():
        index_html.write_text("<html><body>test</body></html>")

    fake_db = types.SimpleNamespace(get_deal=lambda deal_id: None)

    class _FakeFirestoreClient:
        def __init__(self, *a, **k):
            pass

        def collection(self, *a, **k):
            raise RuntimeError("Tests must not exercise live Firestore queries")

    from google.cloud import firestore as _firestore_module

    monkeypatch.setattr(_firestore_module, "Client", _FakeFirestoreClient)

    fake_firestore_client_module = types.ModuleType("database.firestore_client")
    fake_firestore_client_module.get_database = lambda: fake_db
    fake_firestore_client_module.THODatabase = type("THODatabase", (), {})
    monkeypatch.setitem(sys.modules, "database.firestore_client", fake_firestore_client_module)

    sys.modules.pop("main", None)
    main_module = importlib.import_module("main")

    monkeypatch.setattr(main_module, "_db", fake_db)
    monkeypatch.setattr(main_module, "_deal_db", fake_db)

    if engine_result is None:
        engine_result = {
            "success": True,
            "filename": "out.pdf",
            "message": "generated",
        }

    monkeypatch.setattr(
        main_module,
        "engine_generate_document",
        lambda template_name, data: dict(engine_result),
    )

    monkeypatch.setattr(main_module, "_verify_admin_token", lambda token: True)

    client = TestClient(main_module.app)
    return client, main_module


class TestGenerateDocumentAutoEmail:
    def test_calls_send_document_email_when_customer_email_present(self, monkeypatch):
        client, main_module = _build_main_with_doc_engine(monkeypatch)

        calls = []

        def _fake_send(**kwargs):
            calls.append(kwargs)
            return {"success": True, "message_id": "msg_a"}

        monkeypatch.setattr(main_module, "send_document_email", _fake_send)

        response = client.post(
            "/api/documents/generate",
            headers={"X-Admin-Token": "test"},
            json={
                "template_name": "TMHA_SalesContract.pdf",
                "data": {"buyer_name": "Alice"},
                "customer_email": "alice@example.com",
                "customer_name": "Alice Buyer",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True
        assert body["filename"] == "out.pdf"

        # Email helper called exactly once with the generated filename and
        # the resolved download URL.
        assert len(calls) == 1
        call_args = calls[0]
        assert call_args["to"] == "alice@example.com"
        assert call_args["customer_name"] == "Alice Buyer"
        assert call_args["doc_filename"] == "out.pdf"
        assert call_args["doc_type"] == "single_template"
        assert call_args["download_url"] == "/api/documents/download/out.pdf"

    def test_does_not_email_when_customer_email_missing(self, monkeypatch):
        client, main_module = _build_main_with_doc_engine(monkeypatch)

        calls = []

        def _fake_send(**kwargs):
            calls.append(kwargs)
            return {"success": True}

        monkeypatch.setattr(main_module, "send_document_email", _fake_send)

        response = client.post(
            "/api/documents/generate",
            headers={"X-Admin-Token": "test"},
            json={
                "template_name": "TMHA_SalesContract.pdf",
                "data": {"buyer_name": "Alice"},
            },
        )

        assert response.status_code == 200, response.text
        # No customer email → no delivery attempt.
        assert calls == []

    def test_email_failure_does_not_break_response(self, monkeypatch):
        """A doc generation must succeed even when send_document_email
        raises — the email is best-effort."""
        client, main_module = _build_main_with_doc_engine(monkeypatch)

        def _boom(**kwargs):
            raise RuntimeError("resend down")

        monkeypatch.setattr(main_module, "send_document_email", _boom)

        response = client.post(
            "/api/documents/generate",
            headers={"X-Admin-Token": "test"},
            json={
                "template_name": "TMHA_SalesContract.pdf",
                "data": {"buyer_name": "Alice"},
                "customer_email": "alice@example.com",
                "customer_name": "Alice Buyer",
            },
        )

        # Even though the email helper raised, the doc-gen response is OK.
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True
        assert body["filename"] == "out.pdf"
        assert body["download_url"] == "/api/documents/download/out.pdf"

    def test_email_non_success_dict_does_not_break_response(self, monkeypatch):
        """When send_document_email returns success=False (e.g. RESEND_API_KEY
        unset) the doc-gen response should still come back clean."""
        client, main_module = _build_main_with_doc_engine(monkeypatch)

        monkeypatch.setattr(
            main_module,
            "send_document_email",
            lambda **kwargs: {"success": False, "error": "no api key"},
        )

        response = client.post(
            "/api/documents/generate",
            headers={"X-Admin-Token": "test"},
            json={
                "template_name": "TMHA_SalesContract.pdf",
                "data": {"buyer_name": "Alice"},
                "customer_email": "alice@example.com",
            },
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Email hardening tests
# ─────────────────────────────────────────────────────────────────────────────


class TestEmailHardening:
    def test_rejects_invalid_recipient(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        email_service = _fresh_email_service(monkeypatch)

        result = email_service.send_email(
            to="not-an-email",
            subject="hi",
            html="<p>hi</p>",
        )
        assert result["success"] is False
        assert result["dry_run"] is True
        assert "Invalid recipient" in result["error"]

    def test_rejects_header_injection_in_subject(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        email_service = _fresh_email_service(monkeypatch)

        sent_payloads: list[dict] = []
        fake_resend = types.SimpleNamespace(
            api_key="",
            Emails=types.SimpleNamespace(
                send=lambda payload: (sent_payloads.append(payload) or {"id": "msg_clean"}),
            ),
        )
        monkeypatch.setitem(sys.modules, "resend", fake_resend)
        monkeypatch.setattr(email_service, "_log_email_activity", lambda *a, **k: None)

        result = email_service.send_email(
            to="alice@example.com",
            subject="Subject\nBcc: evil@example.com",
            html="<p>hi</p>",
        )
        assert result["success"] is True
        # Newlines are stripped from the subject before it reaches Resend.
        assert "\n" not in sent_payloads[0]["subject"]
        assert sent_payloads[0]["subject"] == "SubjectBcc: evil@example.com"

    def test_enforces_max_recipients(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        monkeypatch.setenv("EMAIL_MAX_RECIPIENTS", "2")
        email_service = _fresh_email_service(monkeypatch)

        result = email_service.send_email(
            to=["a@example.com", "b@example.com", "c@example.com"],
            subject="hi",
            html="<p>hi</p>",
        )
        assert result["success"] is False
        assert result["dry_run"] is True
        assert "Too many recipients" in result["error"]

    def test_rejects_invalid_sender(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        monkeypatch.setenv("RESEND_FROM", "Bad Sender <not-an-email>")
        email_service = _fresh_email_service(monkeypatch)

        result = email_service.send_email(
            to="alice@example.com",
            subject="hi",
            html="<p>hi</p>",
        )
        assert result["success"] is False
        assert result["dry_run"] is True
        assert "Invalid sender" in result["error"]

    def test_rejects_invalid_reply_to(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        monkeypatch.setenv("REPLY_TO", "not-an-email")
        email_service = _fresh_email_service(monkeypatch)

        result = email_service.send_email(
            to="alice@example.com",
            subject="hi",
            html="<p>hi</p>",
        )
        assert result["success"] is False
        assert result["dry_run"] is True
        assert "Invalid reply-to" in result["error"]

    def test_api_key_read_at_send_time(self, monkeypatch):
        """Like RESEND_FROM, the API key is re-read on every send so env swaps
        do not require a module reload."""
        monkeypatch.setenv("RESEND_API_KEY", "re_first")
        email_service = _fresh_email_service(monkeypatch)

        keys_seen: list[str] = []
        fake_resend = types.SimpleNamespace(
            api_key="",
            Emails=types.SimpleNamespace(
                send=lambda payload: (keys_seen.append(fake_resend.api_key) or {"id": "msg_k"}),
            ),
        )
        monkeypatch.setitem(sys.modules, "resend", fake_resend)
        monkeypatch.setattr(email_service, "_log_email_activity", lambda *a, **k: None)

        email_service.send_email(to="a@example.com", subject="1", html="<p>1</p>")
        monkeypatch.setenv("RESEND_API_KEY", "re_second")
        email_service.send_email(to="b@example.com", subject="2", html="<p>2</p>")

        assert keys_seen == ["re_first", "re_second"]
