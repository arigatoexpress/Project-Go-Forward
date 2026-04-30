"""Unit tests for tools/gmail_sync.py and the inbound-status admin endpoint.

These tests use mocks throughout — no live Gmail calls. Run with:

    python -m pytest tests/test_gmail_sync.py -v
"""

from __future__ import annotations

import base64
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import tools.gmail_sync as gmail_sync
from tools.gmail_sync import (
    PROCESSED_LABEL_NAME,
    GmailInboxSync,
    Message,
    _decode_b64url,
    _extract_bodies,
    _headers_to_dict,
    _parse_timestamp,
    parse_message,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _b64url(s: str) -> str:
    """Encode a string the way Gmail does (URL-safe base64, no padding)."""
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii").rstrip("=")


def _raw_message(
    *,
    msg_id: str = "msg-1",
    from_addr: str = "customer@example.com",
    to_addr: str = "ops@texashomeoutlet.com",
    subject: str = "Re: Your appointment",
    text: str | None = "Hi, can we reschedule?",
    html: str | None = "<p>Hi, can we reschedule?</p>",
    date_hdr: str = "Tue, 30 Apr 2026 09:00:00 +0000",
    internal_date_ms: str = "1777845600000",
) -> dict:
    """Build a Gmail-shaped raw message dict for parse tests."""
    parts = []
    if text is not None:
        parts.append({"mimeType": "text/plain", "body": {"data": _b64url(text)}})
    if html is not None:
        parts.append({"mimeType": "text/html", "body": {"data": _b64url(html)}})
    return {
        "id": msg_id,
        "internalDate": internal_date_ms,
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "From", "value": from_addr},
                {"name": "To", "value": to_addr},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": date_hdr},
            ],
            "parts": parts,
        },
    }


# ---------------------------------------------------------------------------
# Pure-function parser tests
# ---------------------------------------------------------------------------


class TestHeaderParsing:
    def test_headers_lowercase_keys(self):
        payload = {
            "headers": [{"name": "Subject", "value": "Hi"}, {"name": "From", "value": "a@b"}]
        }
        out = _headers_to_dict(payload)
        assert out == {"subject": "Hi", "from": "a@b"}

    def test_headers_empty_payload(self):
        assert _headers_to_dict({}) == {}

    def test_headers_skip_blank_names(self):
        payload = {"headers": [{"name": "", "value": "x"}, {"name": "Subject", "value": "Y"}]}
        assert _headers_to_dict(payload) == {"subject": "Y"}


class TestBodyExtraction:
    def test_decode_b64url_handles_padding(self):
        assert _decode_b64url(_b64url("hello world")) == "hello world"

    def test_decode_b64url_empty(self):
        assert _decode_b64url("") == ""

    def test_extract_bodies_multipart(self):
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64url("plain body")}},
                {"mimeType": "text/html", "body": {"data": _b64url("<b>html body</b>")}},
            ],
        }
        text, html = _extract_bodies(payload)
        assert text == "plain body"
        assert html == "<b>html body</b>"

    def test_extract_bodies_text_only(self):
        payload = {"mimeType": "text/plain", "body": {"data": _b64url("just text")}}
        text, html = _extract_bodies(payload)
        assert text == "just text"
        assert html == ""

    def test_extract_bodies_nested(self):
        # multipart/mixed -> multipart/alternative -> text+html
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": _b64url("nested")}},
                    ],
                }
            ],
        }
        text, html = _extract_bodies(payload)
        assert text == "nested"


class TestTimestampParsing:
    def test_uses_date_header(self):
        ts = _parse_timestamp({"date": "Tue, 30 Apr 2026 09:00:00 +0000"}, None)
        assert ts.tzinfo is not None
        assert ts == datetime(2026, 4, 30, 9, 0, tzinfo=UTC)

    def test_falls_back_to_internal_date(self):
        ts = _parse_timestamp({}, "1777845600000")
        assert ts == datetime.fromtimestamp(1777845600, tz=UTC)

    def test_falls_back_to_now_when_both_missing(self):
        ts = _parse_timestamp({}, None)
        assert ts.tzinfo is not None  # always tz-aware


class TestParseMessage:
    def test_parses_full_message(self):
        msg = parse_message(_raw_message())
        assert isinstance(msg, Message)
        assert msg.gmail_message_id == "msg-1"
        assert msg.from_addr == "customer@example.com"
        assert msg.to_addr == "ops@texashomeoutlet.com"
        assert msg.subject == "Re: Your appointment"
        assert msg.body_text == "Hi, can we reschedule?"
        assert msg.body_html == "<p>Hi, can we reschedule?</p>"
        assert msg.timestamp.year == 2026
        assert "from" in msg.raw_headers


# ---------------------------------------------------------------------------
# GmailInboxSync — mocked googleapiclient
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_service():
    """A MagicMock pretending to be a googleapiclient ``service`` resource."""
    return MagicMock(name="gmail_service")


@pytest.fixture
def sync_with_mock(mock_service):
    """A GmailInboxSync where ``_build_service`` returns the mock service."""
    sync = GmailInboxSync(
        service_account_json_path="/fake/sa.json",
        delegated_user="ops@texashomeoutlet.com",
        label="inbox",
    )
    # Pre-populate the cached service so _build_service() returns the mock.
    sync._service = mock_service
    return sync, mock_service


class TestConstruction:
    def test_requires_service_account_path(self):
        with pytest.raises(ValueError):
            GmailInboxSync(service_account_json_path="", delegated_user="a@b.com")

    def test_requires_delegated_user(self):
        with pytest.raises(ValueError):
            GmailInboxSync(service_account_json_path="/x.json", delegated_user="")

    def test_construction_does_no_network_io(self):
        # Even with bogus creds path, construction must not touch the network.
        sync = GmailInboxSync(
            service_account_json_path="/nonexistent.json",
            delegated_user="ops@texashomeoutlet.com",
        )
        assert sync._service is None
        assert sync.label == "inbox"


class TestQueryBuilding:
    def test_query_default_excludes_processed(self, sync_with_mock):
        sync, _ = sync_with_mock
        q = sync._build_query(None)
        assert "label:inbox" in q
        assert f"-label:{PROCESSED_LABEL_NAME}" in q
        assert "after:" not in q

    def test_query_with_since(self, sync_with_mock):
        sync, _ = sync_with_mock
        q = sync._build_query("2026-04-30T00:00:00Z")
        assert "after:2026/04/30" in q

    def test_query_ignores_bad_since(self, sync_with_mock):
        sync, _ = sync_with_mock
        q = sync._build_query("not-a-date")
        assert "after:" not in q


class TestPoll:
    def test_poll_returns_parsed_messages(self, sync_with_mock):
        sync, svc = sync_with_mock
        # list() returns 2 IDs
        list_call = svc.users.return_value.messages.return_value.list.return_value
        list_call.execute.return_value = {"messages": [{"id": "msg-1"}, {"id": "msg-2"}]}
        # get() returns full payloads
        get_call = svc.users.return_value.messages.return_value.get.return_value
        get_call.execute.side_effect = [
            _raw_message(msg_id="msg-1", subject="First"),
            _raw_message(msg_id="msg-2", subject="Second"),
        ]

        msgs = sync.poll(max_messages=10)

        assert len(msgs) == 2
        assert all(isinstance(m, Message) for m in msgs)
        assert [m.gmail_message_id for m in msgs] == ["msg-1", "msg-2"]
        assert [m.subject for m in msgs] == ["First", "Second"]
        # Verify list call was made with our query
        svc.users.return_value.messages.return_value.list.assert_called_once()
        list_kwargs = svc.users.return_value.messages.return_value.list.call_args.kwargs
        assert list_kwargs["userId"] == "me"
        assert "label:inbox" in list_kwargs["q"]
        assert list_kwargs["maxResults"] == 10

    def test_poll_empty_inbox(self, sync_with_mock):
        sync, svc = sync_with_mock
        svc.users.return_value.messages.return_value.list.return_value.execute.return_value = {}
        assert sync.poll() == []

    def test_poll_zero_max_short_circuits(self, sync_with_mock):
        sync, svc = sync_with_mock
        # Even with mocks set up, max_messages=0 must not call the API
        assert sync.poll(max_messages=0) == []
        svc.users.assert_not_called()


class TestMarkProcessed:
    def test_mark_processed_creates_label_when_missing(self, sync_with_mock):
        sync, svc = sync_with_mock
        # labels.list returns no labels matching PROCESSED_LABEL_NAME
        labels_list = svc.users.return_value.labels.return_value.list.return_value
        labels_list.execute.return_value = {"labels": [{"id": "Label_other", "name": "Other"}]}
        # labels.create returns the new label
        labels_create = svc.users.return_value.labels.return_value.create.return_value
        labels_create.execute.return_value = {"id": "Label_tho", "name": PROCESSED_LABEL_NAME}
        # messages.modify is called and succeeds
        modify = svc.users.return_value.messages.return_value.modify.return_value
        modify.execute.return_value = {"id": "msg-1", "labelIds": ["Label_tho"]}

        sync.mark_processed("msg-1")

        # Verify create was called with the right body
        create_kwargs = svc.users.return_value.labels.return_value.create.call_args.kwargs
        assert create_kwargs["userId"] == "me"
        assert create_kwargs["body"]["name"] == PROCESSED_LABEL_NAME
        # Verify modify applied the new label
        modify_kwargs = svc.users.return_value.messages.return_value.modify.call_args.kwargs
        assert modify_kwargs["id"] == "msg-1"
        assert modify_kwargs["body"]["addLabelIds"] == ["Label_tho"]

    def test_mark_processed_reuses_existing_label(self, sync_with_mock):
        sync, svc = sync_with_mock
        labels_list = svc.users.return_value.labels.return_value.list.return_value
        labels_list.execute.return_value = {
            "labels": [{"id": "Label_existing", "name": PROCESSED_LABEL_NAME}]
        }
        modify = svc.users.return_value.messages.return_value.modify.return_value
        modify.execute.return_value = {"id": "msg-7"}

        sync.mark_processed("msg-7")

        # create() must NOT be called when the label already exists
        svc.users.return_value.labels.return_value.create.assert_not_called()
        modify_kwargs = svc.users.return_value.messages.return_value.modify.call_args.kwargs
        assert modify_kwargs["body"]["addLabelIds"] == ["Label_existing"]

    def test_mark_processed_caches_label_id_across_calls(self, sync_with_mock):
        sync, svc = sync_with_mock
        labels_list = svc.users.return_value.labels.return_value.list.return_value
        labels_list.execute.return_value = {
            "labels": [{"id": "Label_existing", "name": PROCESSED_LABEL_NAME}]
        }

        sync.mark_processed("msg-a")
        sync.mark_processed("msg-b")

        # labels.list called only once thanks to caching
        assert svc.users.return_value.labels.return_value.list.call_count == 1

    def test_mark_processed_rejects_blank_id(self, sync_with_mock):
        sync, _ = sync_with_mock
        with pytest.raises(ValueError):
            sync.mark_processed("")


class TestBuildServiceCreatesDelegatedClient:
    def test_build_service_uses_subject_delegation(self, monkeypatch):
        """Verify _build_service wires the service-account credentials with
        domain-wide delegation onto the configured delegated user.

        The test injects fake ``google.oauth2.service_account`` and
        ``googleapiclient.discovery`` modules into ``sys.modules`` so the
        ``import`` statements inside ``_build_service`` resolve to mocks
        instead of the real google libs.
        """
        import types

        sync = GmailInboxSync(
            service_account_json_path="/fake/sa.json",
            delegated_user="ops@texashomeoutlet.com",
        )

        fake_creds = MagicMock(name="creds")
        delegated = MagicMock(name="delegated_creds")
        fake_creds.with_subject.return_value = delegated

        fake_sa = types.ModuleType("google.oauth2.service_account")
        fake_sa.Credentials = MagicMock()
        fake_sa.Credentials.from_service_account_file.return_value = fake_creds

        fake_build = MagicMock(return_value="<service>")
        fake_discovery = types.ModuleType("googleapiclient.discovery")
        fake_discovery.build = fake_build

        # Inject parent packages too so ``from X import Y`` works
        fake_google = types.ModuleType("google")
        fake_google_oauth2 = types.ModuleType("google.oauth2")
        fake_googleapiclient = types.ModuleType("googleapiclient")

        monkeypatch.setitem(sys.modules, "google", fake_google)
        monkeypatch.setitem(sys.modules, "google.oauth2", fake_google_oauth2)
        monkeypatch.setitem(sys.modules, "google.oauth2.service_account", fake_sa)
        monkeypatch.setitem(sys.modules, "googleapiclient", fake_googleapiclient)
        monkeypatch.setitem(sys.modules, "googleapiclient.discovery", fake_discovery)

        svc = sync._build_service()

        assert svc == "<service>"
        fake_sa.Credentials.from_service_account_file.assert_called_once_with(
            "/fake/sa.json", scopes=list(gmail_sync.GMAIL_SCOPES)
        )
        fake_creds.with_subject.assert_called_once_with("ops@texashomeoutlet.com")
        fake_build.assert_called_once()
        build_args = fake_build.call_args
        assert build_args.args[0] == "gmail"
        assert build_args.args[1] == "v1"
        assert build_args.kwargs["credentials"] is delegated


# ---------------------------------------------------------------------------
# /api/admin/email/inbound-status endpoint
# ---------------------------------------------------------------------------


class TestInboundStatusEndpoint:
    def _admin_token(self, monkeypatch, main):
        """Return headers carrying a valid admin token for the test app."""
        # Force a known PIN hash so we can mint a token deterministically.
        token = main._create_admin_token()
        return {"X-Admin-Token": token}

    def test_returns_disabled_when_env_unset(self, monkeypatch):
        from test_api_v1 import create_client  # type: ignore

        # Ensure no Gmail env vars leak in from the dev environment
        monkeypatch.delenv("GMAIL_INBOUND_ENABLED", raising=False)
        monkeypatch.delenv("GMAIL_SERVICE_ACCOUNT_JSON", raising=False)
        monkeypatch.delenv("GMAIL_DELEGATED_USER", raising=False)
        monkeypatch.delenv("GMAIL_INBOUND_LABEL", raising=False)

        client, main, _db, _logger = create_client(monkeypatch)
        headers = self._admin_token(monkeypatch, main)

        response = client.get("/api/admin/email/inbound-status", headers=headers)

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["enabled"] is False
        assert body["configured"] is False
        assert body["delegated_user"] == ""
        assert body["label"] == "inbox"
        assert body["last_poll_at"] is None
        assert body["last_error"] is None

    def test_reports_configured_when_env_present_but_disabled(self, monkeypatch):
        from test_api_v1 import create_client  # type: ignore

        monkeypatch.setenv("GMAIL_INBOUND_ENABLED", "false")
        monkeypatch.setenv("GMAIL_SERVICE_ACCOUNT_JSON", "/secrets/gmail/sa.json")
        monkeypatch.setenv("GMAIL_DELEGATED_USER", "ops@texashomeoutlet.com")
        monkeypatch.setenv("GMAIL_INBOUND_LABEL", "inbox")

        client, main, _db, _logger = create_client(monkeypatch)
        headers = self._admin_token(monkeypatch, main)

        response = client.get("/api/admin/email/inbound-status", headers=headers)

        assert response.status_code == 200
        body = response.json()
        assert body["enabled"] is False  # flag still off
        assert body["configured"] is True
        assert body["delegated_user"] == "ops@texashomeoutlet.com"

    def test_requires_admin_auth(self, monkeypatch):
        from test_api_v1 import create_client  # type: ignore

        client, _main, _db, _logger = create_client(monkeypatch)

        response = client.get("/api/admin/email/inbound-status")

        assert response.status_code == 401
