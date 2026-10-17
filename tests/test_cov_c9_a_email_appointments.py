"""Coverage campaign C9-A: request-path gaps in email_service + appointment_manager.

Targets (Python 3.12 ranking, verified against 3.11 term-missing):

email_service:
  * _validate_email_address / _sanitize_header_value / _extract_address edge lines
  * email_delivery_configured, _resend_error_type, _probe_resend_key,
    check_email_liveness (all branches)
  * _parse_recipients empty-raw branch, _get_db lazy init + failure swallow,
    _log_email_activity (no-db / success / failure), get_email_log (all branches)
  * send_email empty-subject and send-exception branches
  * send_admin_login_code no-link branch
  * send_appointment_confirmation / send_lead_welcome / send_deal_status_update
    (full bodies incl. HTML-escape of first name)
  * _humanize_doc_type empty branch, send_document_email PUBLIC_SITE_URL prefix
  * format_lead_home_interest / _lead_home_interest_html with-home branches
  * notify_new_lead / notify_new_appointment incl. no-recipients and home-note paths

appointment_manager:
  * db setter injection, create_appointment_sync transaction closure
    (happy path, oversold slot, today-slot-past), cancel/get/by_date/by_phone
    closures, get_available_slots closed-day + today-filter branches,
    list_appointments closure.

Run: python -m pytest tests/test_cov_c9_a_email_appointments.py -q
"""

from __future__ import annotations

import asyncio
import importlib
import io
import sys
import types
import urllib.error
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Patch firestore Client before importing appointment_manager (mirrors the
# existing test_appointment_manager.py import ritual).
with patch("google.cloud.firestore.Client"):
    from google.cloud import firestore

    from appointment_manager import Appointment, AppointmentManager


def run(coro):
    return asyncio.run(coro)


def _fresh_email_service():
    """Reload email_service so env-var changes are picked up cleanly."""
    sys.modules.pop("email_service", None)
    return importlib.import_module("email_service")


def _capture_send(monkeypatch, email_service):
    captured: dict = {}

    def _fake(**kwargs):
        captured.update(kwargs)
        return {"success": True}

    monkeypatch.setattr(email_service, "send_email", _fake)
    return captured


# ─────────────────────────────────────────────────────────────────────────────
# email_service — small helpers
# ─────────────────────────────────────────────────────────────────────────────


class TestAddressHelpers:
    def test_validate_rejects_empty_and_none(self):
        es = _fresh_email_service()
        assert es._validate_email_address("") is False
        assert es._validate_email_address(None) is False

    def test_validate_rejects_newline_injection(self):
        es = _fresh_email_service()
        assert es._validate_email_address("a@example.com\r\nBcc: x") is False
        assert es._validate_email_address("a@exa\nmple.com") is False

    def test_validate_rejects_missing_local_or_domain(self):
        es = _fresh_email_service()
        assert es._validate_email_address("@example.com") is False
        assert es._validate_email_address("a@") is False

    def test_validate_rejects_dotless_domain(self):
        es = _fresh_email_service()
        assert es._validate_email_address("a@localhost") is False

    def test_validate_rejects_bad_domain_dots(self):
        es = _fresh_email_service()
        assert es._validate_email_address("a@ex..ample.com") is False
        assert es._validate_email_address("a@example.com.") is False

    def test_validate_accepts_practical_addresses(self):
        es = _fresh_email_service()
        assert es._validate_email_address("jane.doe+tag@example.com") is True

    def test_sanitize_none_returns_empty(self):
        es = _fresh_email_service()
        assert es._sanitize_header_value(None) == ""

    def test_extract_address_empty_returns_empty(self):
        es = _fresh_email_service()
        assert es._extract_address("") == ""

    def test_extract_address_plain_passthrough(self):
        es = _fresh_email_service()
        assert es._extract_address("noreply@texashomeoutlet.com") == "noreply@texashomeoutlet.com"

    def test_extract_address_display_name_form(self):
        es = _fresh_email_service()
        assert (
            es._extract_address("Texas Home Outlet <noreply@texashomeoutlet.com>")
            == "noreply@texashomeoutlet.com"
        )

    def test_delivery_configured_reflects_env(self, monkeypatch):
        es = _fresh_email_service()
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        assert es.email_delivery_configured() is False
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        assert es.email_delivery_configured() is True


# ─────────────────────────────────────────────────────────────────────────────
# email_service — Resend liveness probe
# ─────────────────────────────────────────────────────────────────────────────


def _http_error(code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://api.resend.com/domains", code, "error", {}, io.BytesIO(body)
    )


class _ProbeResponse:
    def __init__(self, status: int):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestResendErrorType:
    def test_reads_name_field(self):
        es = _fresh_email_service()
        exc = _http_error(401, b'{"name": "restricted_api_key"}')
        assert es._resend_error_type(exc) == "restricted_api_key"

    def test_falls_back_to_type_field(self):
        es = _fresh_email_service()
        exc = _http_error(403, b'{"type": "suspended_api_key"}')
        assert es._resend_error_type(exc) == "suspended_api_key"

    def test_invalid_json_returns_empty(self):
        es = _fresh_email_service()
        exc = _http_error(500, b"not-json{")
        assert es._resend_error_type(exc) == ""

    def test_non_dict_payload_returns_empty(self):
        es = _fresh_email_service()
        exc = _http_error(500, b"[1, 2, 3]")
        assert es._resend_error_type(exc) == ""

    def test_non_string_name_returns_empty(self):
        es = _fresh_email_service()
        exc = _http_error(500, b'{"name": 123}')
        assert es._resend_error_type(exc) == ""

    def test_unreadable_body_returns_empty(self):
        es = _fresh_email_service()

        class _Unreadable:
            def read(self, n=-1):
                raise OSError("stream broken")

            def close(self):
                pass

        exc = urllib.error.HTTPError(
            "https://api.resend.com/domains", 500, "error", {}, _Unreadable()
        )
        assert es._resend_error_type(exc) == ""


class TestProbeResendKey:
    def test_success_status_is_ok(self):
        es = _fresh_email_service()
        assert es._probe_resend_key("re_k", opener=lambda req, timeout: _ProbeResponse(200)) == "ok"

    def test_non_2xx_status_is_unreachable(self):
        es = _fresh_email_service()
        assert es._probe_resend_key("re_k", opener=lambda req, timeout: _ProbeResponse(500)) == (
            "unreachable"
        )

    def test_401_restricted_key_is_ok(self):
        es = _fresh_email_service()
        exc = _http_error(401, b'{"name": "restricted_api_key"}')

        def _opener(req, timeout):
            raise exc

        assert es._probe_resend_key("re_k", opener=_opener) == "ok"

    def test_401_other_type_is_invalid_key(self):
        es = _fresh_email_service()
        exc = _http_error(401, b'{"name": "something_else"}')

        def _opener(req, timeout):
            raise exc

        assert es._probe_resend_key("re_k", opener=_opener) == "invalid_key"

    def test_403_suspended_key_is_invalid_key(self):
        es = _fresh_email_service()
        exc = _http_error(403, b'{"name": "suspended_api_key"}')

        def _opener(req, timeout):
            raise exc

        assert es._probe_resend_key("re_k", opener=_opener) == "invalid_key"

    def test_403_other_type_is_unreachable(self):
        es = _fresh_email_service()
        exc = _http_error(403, b'{"name": "other"}')

        def _opener(req, timeout):
            raise exc

        assert es._probe_resend_key("re_k", opener=_opener) == "unreachable"

    def test_500_is_unreachable(self):
        es = _fresh_email_service()
        exc = _http_error(500, b'{"name": "server_error"}')

        def _opener(req, timeout):
            raise exc

        assert es._probe_resend_key("re_k", opener=_opener) == "unreachable"

    def test_url_error_is_unreachable(self):
        es = _fresh_email_service()

        def _opener(req, timeout):
            raise urllib.error.URLError("dns down")

        assert es._probe_resend_key("re_k", opener=_opener) == "unreachable"

    def test_timeout_is_unreachable(self):
        es = _fresh_email_service()

        def _opener(req, timeout):
            raise TimeoutError("slow")

        assert es._probe_resend_key("re_k", opener=_opener) == "unreachable"


class TestCheckEmailLiveness:
    def test_no_key_is_not_configured(self, monkeypatch):
        es = _fresh_email_service()
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        assert es.check_email_liveness() == {"ok": False, "state": "not_configured"}

    def test_empty_explicit_key_is_not_configured(self):
        es = _fresh_email_service()
        assert es.check_email_liveness("") == {"ok": False, "state": "not_configured"}

    def test_verifier_exception_is_unreachable(self, monkeypatch):
        es = _fresh_email_service()
        monkeypatch.setenv("RESEND_API_KEY", "re_k")

        def _boom(key):
            raise ValueError("provider exploded")

        assert es.check_email_liveness(verify=_boom) == {"ok": False, "state": "unreachable"}

    def test_unknown_state_is_unreachable(self, monkeypatch):
        es = _fresh_email_service()
        monkeypatch.setenv("RESEND_API_KEY", "re_k")
        assert es.check_email_liveness(verify=lambda key: "garbage") == {
            "ok": False,
            "state": "unreachable",
        }

    def test_not_configured_state_is_unreachable(self, monkeypatch):
        es = _fresh_email_service()
        monkeypatch.setenv("RESEND_API_KEY", "re_k")
        assert es.check_email_liveness(verify=lambda key: "not_configured") == {
            "ok": False,
            "state": "unreachable",
        }

    def test_ok_and_invalid_states(self, monkeypatch):
        es = _fresh_email_service()
        monkeypatch.setenv("RESEND_API_KEY", "re_k")
        assert es.check_email_liveness(verify=lambda key: "ok") == {"ok": True, "state": "ok"}
        assert es.check_email_liveness(verify=lambda key: "invalid_key") == {
            "ok": False,
            "state": "invalid_key",
        }


# ─────────────────────────────────────────────────────────────────────────────
# email_service — Firestore activity log
# ─────────────────────────────────────────────────────────────────────────────


class _FakeSnapshot:
    def __init__(self, data):
        self._data = data

    def to_dict(self):
        return dict(self._data)


class _FakeEmailLogCollection:
    def __init__(self, entries=None, add_exc=None):
        self.entries = list(entries or [])
        self.add_exc = add_exc
        self.added: list[dict] = []

    def add(self, payload, timeout=None):
        if self.add_exc:
            raise self.add_exc
        self.added.append(payload)

    def where(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, n):
        return self

    def stream(self, timeout=None):
        return iter(self.entries)


def _db_with_email_log(coll):
    return types.SimpleNamespace(collection=lambda name: coll)


class TestGetDb:
    def test_lazy_init_caches_client(self, monkeypatch):
        es = _fresh_email_service()
        fake_client = object()
        monkeypatch.setattr(firestore, "Client", lambda project=None: fake_client)
        assert es._get_db() is fake_client
        assert es._firestore_client is fake_client
        # Second call returns the cache without constructing again.
        assert es._get_db() is fake_client

    def test_client_error_is_swallowed(self, monkeypatch):
        es = _fresh_email_service()

        def _boom(project=None):
            raise RuntimeError("no credentials")

        monkeypatch.setattr(firestore, "Client", _boom)
        assert es._get_db() is None


class TestLogEmailActivity:
    def test_no_db_returns_silently(self, monkeypatch):
        es = _fresh_email_service()
        monkeypatch.setattr(es, "_get_db", lambda: None)
        es._log_email_activity("a@b.com", "Hi", "general")  # must not raise

    def test_writes_log_entry(self, monkeypatch):
        es = _fresh_email_service()
        coll = _FakeEmailLogCollection()
        monkeypatch.setattr(es, "_get_db", lambda: _db_with_email_log(coll))

        es._log_email_activity("a@b.com", "Hello there", "lead_welcome", "lead-9")

        assert len(coll.added) == 1
        entry = coll.added[0]
        assert entry["to"] == "a@b.com"
        assert entry["subject"] == "Hello there"
        assert entry["email_type"] == "lead_welcome"
        assert entry["related_id"] == "lead-9"
        assert entry["sent_at"]  # ISO timestamp present
        assert "from" in entry

    def test_write_error_is_swallowed(self, monkeypatch):
        es = _fresh_email_service()
        coll = _FakeEmailLogCollection(add_exc=RuntimeError("firestore down"))
        monkeypatch.setattr(es, "_get_db", lambda: _db_with_email_log(coll))
        es._log_email_activity("a@b.com", "Hi", "general")  # must not raise
        assert coll.added == []


class TestGetEmailLog:
    def test_returns_streamed_entries(self, monkeypatch):
        es = _fresh_email_service()
        coll = _FakeEmailLogCollection(entries=[_FakeSnapshot({"to": "a@b.com"})])
        monkeypatch.setattr(es, "_get_db", lambda: _db_with_email_log(coll))

        result = es.get_email_log(limit=10, email_type="lead_welcome")

        assert result == [{"to": "a@b.com"}]

    def test_without_type_filter(self, monkeypatch):
        es = _fresh_email_service()
        entries = [_FakeSnapshot({"to": f"u{i}@b.com"}) for i in range(3)]
        coll = _FakeEmailLogCollection(entries=entries)
        monkeypatch.setattr(es, "_get_db", lambda: _db_with_email_log(coll))

        result = es.get_email_log(limit=50)
        assert len(result) == 3

    def test_no_db_returns_empty(self, monkeypatch):
        es = _fresh_email_service()
        monkeypatch.setattr(es, "_get_db", lambda: None)
        assert es.get_email_log() == []

    def test_query_error_returns_empty(self, monkeypatch):
        es = _fresh_email_service()

        class _BrokenCollection(_FakeEmailLogCollection):
            def stream(self, timeout=None):
                raise RuntimeError("query failed")

        coll = _BrokenCollection(entries=[_FakeSnapshot({"to": "a@b.com"})])
        monkeypatch.setattr(es, "_get_db", lambda: _db_with_email_log(coll))
        assert es.get_email_log() == []


# ─────────────────────────────────────────────────────────────────────────────
# email_service — recipient parsing + send_email branches
# ─────────────────────────────────────────────────────────────────────────────


class TestRecipientParsing:
    def test_empty_raw_yields_empty_list(self, monkeypatch):
        monkeypatch.setenv("NOTIFICATION_EMAIL", "")
        monkeypatch.setenv("REQUIRED_NOTIFICATION_EMAILS", "")
        es = _fresh_email_service()
        assert es.NOTIFICATION_EMAILS == []


class TestSendEmailBranches:
    def test_blank_subject_rejected(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        es = _fresh_email_service()

        result = es.send_email(to="a@example.com", subject="   ", html="<p>x</p>")

        assert result["success"] is False
        assert result["dry_run"] is True
        assert result["error"] == "Subject is required"

    def test_send_exception_captured(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        es = _fresh_email_service()

        def _raise(payload):
            raise RuntimeError("resend down")

        fake_resend = types.SimpleNamespace(api_key="", Emails=types.SimpleNamespace(send=_raise))
        monkeypatch.setitem(sys.modules, "resend", fake_resend)
        monkeypatch.setattr(es, "_log_email_activity", lambda *a, **k: None)

        result = es.send_email(to="a@example.com", subject="hi", html="<p>hi</p>")

        assert result["success"] is False
        assert result["error"] == "resend down"

    def test_plain_from_address_accepted(self, monkeypatch):
        """A RESEND_FROM without a display-name covers _extract_address passthrough."""
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        monkeypatch.setenv("RESEND_FROM", "noreply@texashomeoutlet.com")
        es = _fresh_email_service()

        sent: list[dict] = []

        def _send(payload):
            sent.append(payload)
            return {"id": "msg_plain"}

        fake_resend = types.SimpleNamespace(api_key="", Emails=types.SimpleNamespace(send=_send))
        monkeypatch.setitem(sys.modules, "resend", fake_resend)
        monkeypatch.setattr(es, "_log_email_activity", lambda *a, **k: None)

        result = es.send_email(to="a@example.com", subject="hi", html="<p>hi</p>")

        assert result["success"] is True
        assert sent[0]["from"] == "noreply@texashomeoutlet.com"

    def test_empty_from_skips_sender_validation(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        monkeypatch.setenv("RESEND_FROM", "")
        es = _fresh_email_service()

        result = es.send_email(to="a@example.com", subject="hi", html="<p>hi</p>")

        # No sender configured -> the dry-run branch for missing API key shape,
        # but crucially NOT the invalid-sender branch.
        assert result["success"] is False
        assert "Invalid sender" not in result.get("error", "")


# ─────────────────────────────────────────────────────────────────────────────
# email_service — templates
# ─────────────────────────────────────────────────────────────────────────────


class TestAdminLoginCode:
    def test_no_link_uses_code_only_copy(self, monkeypatch):
        es = _fresh_email_service()
        captured = _capture_send(monkeypatch, es)

        result = es.send_admin_login_code("staff@texashomeoutlet.com", "424242")

        assert result["success"] is True
        assert "Sign me in" not in captured["html"]
        assert "Type this code on the staff sign-in page" in captured["html"]
        assert "424242" in captured["html"]


class TestAppointmentConfirmation:
    def test_escapes_first_name_and_includes_details(self, monkeypatch):
        es = _fresh_email_service()
        captured = _capture_send(monkeypatch, es)

        result = es.send_appointment_confirmation(
            to="jane@example.com",
            customer_name="Jane<script>alert(1)</script> Doe",
            date="2026-06-20",
            time_slot="10:00 AM",
            appointment_id="appt-5",
            notes="Bring ID",
        )

        assert result["success"] is True
        html = captured["html"]
        assert "Jane&lt;script&gt;" in html
        assert "<script>alert(1)</script>" not in html
        assert "2026-06-20" in html
        assert "10:00 AM" in html
        assert "Bring ID" in html
        assert captured["email_type"] == "appointment_confirmation"
        assert captured["related_id"] == "appt-5"

    def test_missing_name_uses_friend_greeting(self, monkeypatch):
        es = _fresh_email_service()
        captured = _capture_send(monkeypatch, es)

        es.send_appointment_confirmation(
            to="jane@example.com",
            customer_name=None,
            date="2026-06-20",
            time_slot="10:00 AM",
        )

        assert "Hi Friend," in captured["html"]

    def test_no_notes_omits_note_line(self, monkeypatch):
        es = _fresh_email_service()
        captured = _capture_send(monkeypatch, es)

        es.send_appointment_confirmation(
            to="jane@example.com",
            customer_name="Jane Doe",
            date="2026-06-20",
            time_slot="10:00 AM",
        )

        assert "Note:" not in captured["html"]


class TestLeadWelcome:
    def test_escapes_first_name(self, monkeypatch):
        es = _fresh_email_service()
        captured = _capture_send(monkeypatch, es)

        result = es.send_lead_welcome(
            to="bob@example.com",
            customer_name="Bob<b>Bold</b>",
            lead_id="lead-3",
        )

        assert result["success"] is True
        assert "Bob&lt;b&gt;Bold&lt;/b&gt;" in captured["html"]
        assert "Thanks for contacting Texas Home Outlet, Bob" in captured["subject"]
        assert captured["email_type"] == "lead_welcome"
        assert captured["related_id"] == "lead-3"

    def test_missing_name_uses_friend(self, monkeypatch):
        es = _fresh_email_service()
        captured = _capture_send(monkeypatch, es)

        es.send_lead_welcome(to="bob@example.com", customer_name=None)

        assert "Hi Friend," in captured["html"]


class TestDealStatusUpdate:
    def test_known_status_uses_template(self, monkeypatch):
        es = _fresh_email_service()
        captured = _capture_send(monkeypatch, es)

        result = es.send_deal_status_update(
            to="carol@example.com",
            customer_name="Carol Buyer",
            deal_id="deal-8",
            new_status="funded",
            home_name="Model 1672",
        )

        assert result["success"] is True
        assert "Funding Complete!" in captured["html"]
        assert "Model 1672" in captured["html"]
        assert "Home:</strong> Model 1672" in captured["html"]
        assert captured["email_type"] == "deal_status_update"
        assert captured["related_id"] == "deal-8"

    def test_unknown_status_uses_fallback_copy(self, monkeypatch):
        es = _fresh_email_service()
        captured = _capture_send(monkeypatch, es)

        es.send_deal_status_update(
            to="carol@example.com",
            customer_name="Carol",
            deal_id="deal-8",
            new_status="pending_review",
        )

        assert "Pending_Review" in captured["html"]
        assert "updated to: pending_review" in captured["html"]
        assert "Home:</strong>" not in captured["html"]  # no home_name -> no home line

    def test_each_known_status_renders(self, monkeypatch):
        es = _fresh_email_service()
        for status, title in (
            ("approved", "Your Application is Approved!"),
            ("contract", "Contract Ready for Review"),
            ("complete", "Welcome Home!"),
        ):
            captured = _capture_send(monkeypatch, es)
            es.send_deal_status_update(
                to="c@example.com", customer_name="C", deal_id="d", new_status=status
            )
            assert title in captured["html"]

    def test_escapes_first_name(self, monkeypatch):
        es = _fresh_email_service()
        captured = _capture_send(monkeypatch, es)

        es.send_deal_status_update(
            to="c@example.com", customer_name="<i>Eve</i> Evil", deal_id="d", new_status="approved"
        )

        assert "&lt;i&gt;Eve&lt;/i&gt;" in captured["html"]


class TestHumanizeDocType:
    def test_empty_doc_type(self, monkeypatch):
        es = _fresh_email_service()
        captured = _capture_send(monkeypatch, es)

        es.send_document_email(
            to="d@example.com",
            customer_name="Dee",
            doc_filename="doc.pdf",
            doc_type="",
            download_url="",
        )

        assert "Your document is ready" in captured["subject"]


class TestDocumentEmailPublicUrl:
    def test_relative_url_prefixed_with_public_site(self, monkeypatch):
        monkeypatch.setenv("PUBLIC_SITE_URL", "https://www.texashomeoutlet.com")
        es = _fresh_email_service()
        captured = _capture_send(monkeypatch, es)

        es.send_document_email(
            to="d@example.com",
            customer_name="Dee",
            doc_filename="doc.pdf",
            doc_type="sales_contract",
            download_url="/api/documents/download/doc.pdf",
        )

        assert "https://www.texashomeoutlet.com/api/documents/download/doc.pdf" in captured["html"]
        assert (
            "https://www.texashomeoutlet.com/api/documents/download/doc.pdf" in captured["text"]
        )


# ─────────────────────────────────────────────────────────────────────────────
# email_service — staff notifications
# ─────────────────────────────────────────────────────────────────────────────


class TestLeadHomeInterest:
    def test_full_home_line(self):
        es = _fresh_email_service()
        home = {"label": "Model 1672", "stock": "STK-9", "url": "https://tho.example/h/9"}
        assert es.format_lead_home_interest(home) == (
            "Interested in: Model 1672 — STK-9 — https://tho.example/h/9"
        )

    def test_label_only(self):
        es = _fresh_email_service()
        assert (
            es.format_lead_home_interest({"label": "Model X"})
            == "Interested in: Model X"
        )

    def test_stock_only_uses_fallback_label(self):
        es = _fresh_email_service()
        assert (
            es.format_lead_home_interest({"stock": "STK-1"})
            == "Interested in: Home (model not given) — STK-1"
        )

    def test_html_with_note_and_url(self):
        es = _fresh_email_service()
        home = {
            "label": "Model 1672",
            "stock": "STK-9",
            "url": "https://tho.example/h/9",
            "note": "Ask for Ben",
        }
        html = es._lead_home_interest_html(home)
        assert "Interested in:</strong> Model 1672" in html
        assert "STK-9" in html
        assert 'href="https://tho.example/h/9"' in html
        assert "Ask for Ben" in html

    def test_html_stock_only(self):
        es = _fresh_email_service()
        html = es._lead_home_interest_html({"stock": "STK-1"})
        assert "Home (model not given)" in html
        assert "STK-1" in html
        assert "View listing" not in html


class TestNotifyNewLead:
    def _fresh_with_notifications(self, monkeypatch):
        monkeypatch.delenv("NOTIFICATION_EMAIL", raising=False)
        monkeypatch.delenv("REQUIRED_NOTIFICATION_EMAILS", raising=False)
        return _fresh_email_service()

    def test_no_recipients_returns_error(self, monkeypatch):
        monkeypatch.setenv("NOTIFICATION_EMAIL", "")
        monkeypatch.setenv("REQUIRED_NOTIFICATION_EMAILS", "")
        es = _fresh_email_service()

        result = es.notify_new_lead("Someone", "555-0000")

        assert result == {"success": False, "error": "No notification email configured"}

    def test_home_note_appears_in_text_and_html(self, monkeypatch):
        es = self._fresh_with_notifications(monkeypatch)
        captured = _capture_send(monkeypatch, es)

        es.notify_new_lead(
            customer_name="Dana Lead",
            phone="555-0100",
            email="dana@example.com",
            source="website",
            home={"label": "Model 1672", "stock": "STK-9", "note": "Ask for Ben"},
        )

        assert "Ask for Ben" in captured["text"]
        assert "Ask for Ben" in captured["html"]
        assert "Interested in: Model 1672 — STK-9" in captured["text"]
        assert "— STK-9" in captured["subject"]


class TestNotifyNewAppointment:
    def test_sends_staff_alert(self, monkeypatch):
        monkeypatch.delenv("NOTIFICATION_EMAIL", raising=False)
        monkeypatch.delenv("REQUIRED_NOTIFICATION_EMAILS", raising=False)
        es = _fresh_email_service()
        captured = _capture_send(monkeypatch, es)

        result = es.notify_new_appointment(
            customer_name="Amy <b>Buyer</b>",
            phone="555-0200",
            date="2026-06-20",
            time_slot="10:00 AM",
            email="amy@example.com",
            notes="First visit",
        )

        assert result["success"] is True
        assert captured["subject"] == "Appointment: Amy <b>Buyer</b> — 2026-06-20 10:00 AM"
        assert "Amy &lt;b&gt;Buyer&lt;/b&gt;" in captured["html"]
        assert "First visit" in captured["html"]
        assert captured["email_type"] == "admin_appointment_notification"
        assert es.NOTIFICATION_EMAILS  # fan-out list present

    def test_no_recipients_returns_error(self, monkeypatch):
        monkeypatch.setenv("NOTIFICATION_EMAIL", "")
        monkeypatch.setenv("REQUIRED_NOTIFICATION_EMAILS", "")
        es = _fresh_email_service()

        result = es.notify_new_appointment("Someone", "555", "2026-06-20", "10:00 AM")

        assert result == {"success": False, "error": "No notification email configured"}

    def test_minimal_fields_omit_optional_lines(self, monkeypatch):
        monkeypatch.delenv("NOTIFICATION_EMAIL", raising=False)
        monkeypatch.delenv("REQUIRED_NOTIFICATION_EMAILS", raising=False)
        es = _fresh_email_service()
        captured = _capture_send(monkeypatch, es)

        es.notify_new_appointment("Someone", "555", "2026-06-20", "10:00 AM")

        assert "<strong>Email:</strong>" not in captured["html"]
        assert "<strong>Notes:</strong>" not in captured["html"]


# ─────────────────────────────────────────────────────────────────────────────
# appointment_manager — fake Firestore
# ─────────────────────────────────────────────────────────────────────────────


class _Snapshot:
    def __init__(self, data):
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return dict(self._data)


class _DocRef:
    def __init__(self, store, doc_id):
        self._store = store
        self.id = doc_id

    def get(self, timeout=None):
        return _Snapshot(self._store.get(self.id))

    def set(self, data, **kwargs):
        self._store[self.id] = dict(data)

    def update(self, fields, timeout=None):
        self._store.setdefault(self.id, {}).update(fields)


class _Query:
    def __init__(self, store, filters=(), order=None, limit_n=None):
        self._store = store
        self._filters = filters
        self._order = order
        self._limit = limit_n

    def where(self, field, op, value):
        return _Query(self._store, self._filters + ((field, op, value),), self._order, self._limit)

    def order_by(self, field, direction=None):
        return _Query(self._store, self._filters, (field, direction), self._limit)

    def limit(self, n):
        return _Query(self._store, self._filters, self._order, n)

    def _results(self):
        docs = [dict(d) for d in self._store.values()]
        for field, _op, value in self._filters:
            docs = [d for d in docs if d.get(field) == value]
        if self._order:
            field, direction = self._order
            docs.sort(key=lambda d: str(d.get(field, "")), reverse=direction == firestore.Query.DESCENDING)
        if self._limit is not None:
            docs = docs[: self._limit]
        return docs

    def stream(self, timeout=None, transaction=None):
        for doc in self._results():
            yield _Snapshot(doc)


class _Collection:
    def __init__(self, store):
        self._store = store

    def document(self, doc_id):
        return _DocRef(self._store, doc_id)

    def where(self, field, op, value):
        return _Query(self._store).where(field, op, value)

    def order_by(self, field, direction=None):
        return _Query(self._store).order_by(field, direction)

    def limit(self, n):
        return _Query(self._store).limit(n)


class _Transaction:
    def get(self, ref, **kwargs):
        return ref.get(**kwargs)

    def set(self, ref, data):
        ref.set(data)

    def update(self, ref, fields):
        ref.update(fields)


class _FakeFirestore:
    def __init__(self, docs=None):
        self.docs = dict(docs or {})

    def collection(self, name):
        assert name == "appointments"
        return _Collection(self.docs)

    def transaction(self):
        return _Transaction()


def _appt_doc(appointment_id, date_str, time_slot, status="confirmed", phone="555-0000"):
    return {
        "appointment_id": appointment_id,
        "name": "Test Person",
        "phone": phone,
        "email": None,
        "date": date_str,
        "time_slot": time_slot,
        "duration_minutes": 60,
        "notes": None,
        "source": "website",
        "status": status,
        "created_at": "2026-06-15T09:00:00-05:00",
        "updated_at": "2026-06-15T09:00:00-05:00",
    }


def _manager(docs=None):
    mgr = AppointmentManager()
    mgr.db = _FakeFirestore(docs)
    return mgr


class _FrozenDateTime(datetime):
    """Fixed 'now': Monday 2026-06-15 at 3:00 PM CDT."""

    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 6, 15, 15, 0, tzinfo=tz)


@pytest.fixture
def frozen_now(monkeypatch):
    monkeypatch.setattr("appointment_manager.datetime", _FrozenDateTime)


@pytest.fixture
def passthrough_transactional(monkeypatch):
    """Use a passthrough decorator so txn_create's body runs against the fake
    transaction (the real google retry wrapper needs a real Transaction)."""
    monkeypatch.setattr("appointment_manager.firestore.transactional", lambda fn: fn)


# ─────────────────────────────────────────────────────────────────────────────
# appointment_manager — db property + transaction paths
# ─────────────────────────────────────────────────────────────────────────────


class TestDbProperty:
    def test_setter_injects_fake_client(self):
        mgr = AppointmentManager()
        fake = _FakeFirestore()
        mgr.db = fake
        assert mgr._db is fake
        assert mgr.db is fake


class TestCreateAppointmentTransaction:
    def test_happy_path_persists_document(self, frozen_now, passthrough_transactional):
        mgr = _manager()
        appt = Appointment(
            appointment_id="appt-new",
            name="Jane Doe",
            phone="555-1234",
            date="2026-06-16",  # Tuesday
            time_slot="10:00 AM",
        )

        result = mgr.create_appointment_sync(appt)

        assert result is appt
        stored = mgr.db.docs["appt-new"]
        assert stored["status"] == "confirmed"
        assert stored["date"] == "2026-06-16"
        assert stored["time_slot"] == "10:00 AM"

    def test_happy_path_via_async_wrapper(self, frozen_now, passthrough_transactional):
        mgr = _manager()
        appt = Appointment(
            appointment_id="appt-async",
            name="Jane Doe",
            phone="555-1234",
            date="2026-06-16",
            time_slot="11:00 AM",
        )
        result = run(mgr.create_appointment(appt))
        assert result is appt
        assert "appt-async" in mgr.db.docs

    def test_oversold_slot_rejected(self, frozen_now, passthrough_transactional):
        docs = {
            "a1": _appt_doc("a1", "2026-06-16", "10:00 AM"),
            "a2": _appt_doc("a2", "2026-06-16", "10:00 AM"),
        }
        mgr = _manager(docs)
        appt = Appointment(
            appointment_id="appt-third",
            name="Jane Doe",
            phone="555-1234",
            date="2026-06-16",
            time_slot="10:00 AM",
        )

        with pytest.raises(ValueError, match="fully booked"):
            mgr.create_appointment_sync(appt)
        assert "appt-third" not in mgr.db.docs

    def test_cancelled_bookings_do_not_block_reuse(self, frozen_now, passthrough_transactional):
        docs = {
            "a1": _appt_doc("a1", "2026-06-16", "10:00 AM", status="cancelled"),
            "a2": _appt_doc("a2", "2026-06-16", "10:00 AM"),
        }
        mgr = _manager(docs)
        appt = Appointment(
            appointment_id="appt-reuse",
            name="Jane Doe",
            phone="555-1234",
            date="2026-06-16",
            time_slot="10:00 AM",
        )
        result = mgr.create_appointment_sync(appt)
        assert result is appt

    def test_past_slot_today_rejected(self, frozen_now):
        mgr = _manager()
        appt = Appointment(
            appointment_id="appt-late",
            name="Jane Doe",
            phone="555-1234",
            date="2026-06-15",  # today (frozen Monday)
            time_slot="2:00 PM",  # 14:00 <= 15:00 frozen now
        )

        with pytest.raises(ValueError, match="already passed"):
            mgr.create_appointment_sync(appt)

    def test_future_slot_today_allowed(self, frozen_now, passthrough_transactional):
        mgr = _manager()
        appt = Appointment(
            appointment_id="appt-later",
            name="Jane Doe",
            phone="555-1234",
            date="2026-06-15",
            time_slot="4:00 PM",  # 16:00 > 15:00 frozen now
        )
        result = mgr.create_appointment_sync(appt)
        assert result is appt


# ─────────────────────────────────────────────────────────────────────────────
# appointment_manager — read/cancel closures
# ─────────────────────────────────────────────────────────────────────────────


class TestCancelAppointment:
    def test_existing_appointment_cancelled(self):
        docs = {"appt-1": _appt_doc("appt-1", "2026-06-16", "10:00 AM")}
        mgr = _manager(docs)

        appt = run(mgr.cancel_appointment("appt-1"))

        assert appt is not None
        assert appt.status == "cancelled"
        assert mgr.db.docs["appt-1"]["status"] == "cancelled"
        assert mgr.db.docs["appt-1"]["updated_at"] != "2026-06-15T09:00:00-05:00"

    def test_missing_appointment_returns_none(self):
        mgr = _manager()
        assert run(mgr.cancel_appointment("nope")) is None


class TestGetAppointment:
    def test_found_returns_appointment(self):
        docs = {"appt-1": _appt_doc("appt-1", "2026-06-16", "10:00 AM", phone="555-7777")}
        mgr = _manager(docs)

        appt = run(mgr.get_appointment("appt-1"))

        assert appt is not None
        assert appt.appointment_id == "appt-1"
        assert appt.phone == "555-7777"

    def test_missing_returns_none(self):
        mgr = _manager()
        assert run(mgr.get_appointment("nope")) is None


class TestGetAppointmentsByDate:
    def test_filters_confirmed_only(self):
        docs = {
            "a1": _appt_doc("a1", "2026-06-16", "10:00 AM"),
            "a2": _appt_doc("a2", "2026-06-16", "11:00 AM", status="cancelled"),
            "a3": _appt_doc("a3", "2026-06-17", "10:00 AM"),
        }
        mgr = _manager(docs)

        result = run(mgr.get_appointments_by_date("2026-06-16"))

        assert [a.appointment_id for a in result] == ["a1"]

    def test_empty_store_returns_empty(self):
        mgr = _manager()
        assert run(mgr.get_appointments_by_date("2026-06-16")) == []


class TestGetAppointmentsByPhone:
    def test_filters_by_phone_and_status(self):
        docs = {
            "a1": _appt_doc("a1", "2026-06-16", "10:00 AM", phone="555-1111"),
            "a2": _appt_doc("a2", "2026-06-17", "11:00 AM", phone="555-1111"),
            "a3": _appt_doc("a3", "2026-06-18", "10:00 AM", phone="555-2222"),
            "a4": _appt_doc("a4", "2026-06-19", "10:00 AM", phone="555-1111", status="no_show"),
        }
        mgr = _manager(docs)

        result = run(mgr.get_appointments_by_phone("555-1111"))

        assert [a.appointment_id for a in result] == ["a1", "a2"]


# ─────────────────────────────────────────────────────────────────────────────
# appointment_manager — get_available_slots remaining branches
# ─────────────────────────────────────────────────────────────────────────────


class TestGetAvailableSlotsRemaining:
    def test_closed_day_returns_message(self, frozen_now):
        mgr = _manager()
        result = run(mgr.get_available_slots("2026-06-21"))  # Sunday
        assert result["available_slots"] == []
        assert result["message"] == "We are closed on this day."
        assert result["day_name"] == "Sunday"

    def test_today_filters_past_slots(self, frozen_now):
        docs = {
            "a1": _appt_doc("a1", "2026-06-15", "5:00 PM"),
            "a2": _appt_doc("a2", "2026-06-15", "5:00 PM"),
        }
        mgr = _manager(docs)

        result = run(mgr.get_available_slots("2026-06-15"))

        # Frozen now is 3 PM Monday: only 4 PM and 5 PM survive the past-slot
        # filter, and 5 PM is fully booked by a1 + a2 (MAX_PER_SLOT = 2).
        assert result["available_slots"] == ["4:00 PM"]
        assert result["total_slots"] == 2
        assert result["booked_slots"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# appointment_manager — list_appointments
# ─────────────────────────────────────────────────────────────────────────────


class TestListAppointments:
    def test_lists_all_descending_by_date(self):
        docs = {
            "a1": _appt_doc("a1", "2026-06-16", "10:00 AM"),
            "a2": _appt_doc("a2", "2026-06-20", "10:00 AM", status="cancelled"),
            "a3": _appt_doc("a3", "2026-06-18", "11:00 AM"),
        }
        mgr = _manager(docs)

        result = run(mgr.list_appointments())

        assert [a.appointment_id for a in result] == ["a2", "a3", "a1"]

    def test_status_filter_applies(self):
        docs = {
            "a1": _appt_doc("a1", "2026-06-16", "10:00 AM"),
            "a2": _appt_doc("a2", "2026-06-20", "10:00 AM", status="cancelled"),
        }
        mgr = _manager(docs)

        result = run(mgr.list_appointments(status="cancelled"))

        assert [a.appointment_id for a in result] == ["a2"]

    def test_limit_applies(self):
        docs = {f"a{i}": _appt_doc(f"a{i}", f"2026-06-{10 + i:02d}", "10:00 AM") for i in range(5)}
        mgr = _manager(docs)

        result = run(mgr.list_appointments(limit=2))

        assert len(result) == 2
