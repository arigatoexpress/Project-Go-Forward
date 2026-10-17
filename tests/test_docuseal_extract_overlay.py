"""Tests for three coverage-gap targets (Python-3.12 re-measurement backlog).

Targets (see docs/MAIN_COVERAGE_INVESTIGATION.md):

- ``POST /api/docuseal/webhook``   — HMAC-verified webhook, replay guard,
                                     GCS/Drive mirroring, deal-note write
- ``POST /api/documents/extract-fields`` — AI form-field extraction from chat
- ``_overlay_staff_photos``        — staff-uploaded photo merge onto inventory

DocuSeal notes: the webhook was previously listed as "blocked on operator
deploy", but only its LIVE end-to-end path needs the server; the handler
itself is fully unit-testable. ``httpx``, ``google.cloud.storage`` and
``tools.drive_service`` are stubbed in both ``sys.modules`` and on the
already-imported parent packages (from-imports resolve package attributes
first — the recurring ordering trap). ``google.auth`` is NOT involved here.

Run: python -m pytest tests/test_docuseal_extract_overlay.py -v
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(Path(__file__).parent))
from test_api_v1 import create_client  # noqa: E402


def _admin_headers(main) -> dict:
    return {"X-Admin-Token": main._create_admin_token()}


# ─── DocuSeal webhook ─────────────────────────────────────────────────────


_WEBHOOK_ROUTE = "/api/docuseal/webhook"
_TEST_SECRET = "test-webhook-secret"


def _sign(body: bytes, secret: str = _TEST_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _post_webhook(client, event, secret=_TEST_SECRET, sign_body=None, raw_headers=None):
    body = json.dumps(event).encode() if not isinstance(event, bytes) else event
    headers = {"X-Docuseal-Signature": _sign(sign_body if sign_body is not None else body, secret)}
    if raw_headers:
        headers.update(raw_headers)
    return client.post(_WEBHOOK_ROUTE, content=body, headers=headers)


def _base_setup(monkeypatch, fake_db=None):
    """Secret + deterministic replay key + URL safety + signed-PDF plumbing."""
    client, main, db, _logger = create_client(monkeypatch)
    monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
    monkeypatch.setattr(main, "log_user_action", lambda **kwargs: None)
    monkeypatch.setattr(main, "_DOCUSEAL_WEBHOOK_SECRET", _TEST_SECRET)
    monkeypatch.setattr(
        main, "docuseal_event_key", lambda event, body, max_age_seconds=None: ("rk-1", None)
    )
    monkeypatch.setattr(
        main,
        "is_safe_docuseal_document_url",
        lambda url, api_url=None, extra_hosts="": True,
    )
    marked: list = []
    monkeypatch.setattr(
        main, "_mark_esign_request", lambda deal_id, submission_id, status: marked.append(
            (deal_id, submission_id, status)
        )
    )
    if fake_db is not None:
        monkeypatch.setattr(
            main,
            "get_database",
            lambda: types.SimpleNamespace(collection=db.db.collection),
        )
    return client, main, db, marked


def _completed_event(event_type="form.completed", **data_overrides):
    data = {
        "id": 7,
        "submission_id": "sub-9",
        "metadata": {"deal_id": "deal-1", "template_name": "sales-contract"},
        "documents": [{"url": "https://docuseal.example.com/signed/7.pdf"}],
    }
    data.update(data_overrides)
    return {"event_type": event_type, "data": data}


def _stub_httpx(monkeypatch, calls: dict, content=b"%PDF-fake-bytes", get_error=None):
    httpx_module = types.ModuleType("httpx")

    class _Response:
        def __init__(self, body):
            self.content = body

        def raise_for_status(self):
            return None

    class _AsyncClient:
        def __init__(self, timeout=None, follow_redirects=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            calls.setdefault("urls", []).append(url)
            if get_error is not None:
                raise get_error
            return _Response(content)

    httpx_module.AsyncClient = _AsyncClient
    monkeypatch.setitem(sys.modules, "httpx", httpx_module)


def _stub_gcs_upload(monkeypatch, calls: dict, fail=False):
    storage_module = types.ModuleType("google.cloud.storage")

    class _Blob:
        def __init__(self, name):
            self.name = name

        def upload_from_filename(self, path, content_type=None):
            if fail:
                raise RuntimeError("gcs down")
            calls.setdefault("uploads", []).append((self.name, path, content_type))

    class _Bucket:
        def __init__(self, name):
            self.name = name

        def blob(self, name):
            return _Blob(name)

    class _Client:
        def bucket(self, name):
            return _Bucket(name)

    storage_module.Client = _Client
    monkeypatch.setitem(sys.modules, "google.cloud.storage", storage_module)
    import google.cloud

    monkeypatch.setattr(google.cloud, "storage", storage_module, raising=False)


def _stub_drive(monkeypatch, calls: dict, folder="folder-1", link="drive-link"):
    module = types.ModuleType("tools.drive_service")
    module.ensure_deal_folder = lambda deal_id: calls.setdefault("folders", []).append(deal_id) or folder
    module.upload_to_drive = lambda path, name, folder_id: calls.setdefault("drive", []).append(
        (path, name, folder_id)
    ) or link
    monkeypatch.setitem(sys.modules, "tools.drive_service", module)
    import tools

    monkeypatch.setattr(tools, "drive_service", module, raising=False)


class TestDocusealWebhook:
    def test_ignored_when_secret_not_configured(self, monkeypatch):
        client, main, _db, _marked = _base_setup(monkeypatch)
        monkeypatch.setattr(main, "_DOCUSEAL_WEBHOOK_SECRET", "")

        resp = _post_webhook(client, _completed_event())

        assert resp.status_code == 200
        assert resp.json() == {"status": "ignored", "reason": "webhook_secret_not_configured"}

    def test_invalid_signature_rejected(self, monkeypatch):
        client, _main, _db, _marked = _base_setup(monkeypatch)

        resp = client.post(
            _WEBHOOK_ROUTE,
            content=json.dumps(_completed_event()).encode(),
            headers={"X-Docuseal-Signature": "deadbeef" * 8},
        )

        assert resp.status_code == 401
        assert resp.json()["message"] == "Invalid webhook signature"

    def test_non_dict_payload_ignored(self, monkeypatch):
        client, _main, _db, _marked = _base_setup(monkeypatch)

        resp = _post_webhook(client, b"[1, 2, 3]")

        assert resp.status_code == 200
        assert resp.json() == {"status": "ignored", "reason": "unexpected_payload"}

    def test_replay_window_rejection_ignored(self, monkeypatch):
        client, main, _db, _marked = _base_setup(monkeypatch)
        monkeypatch.setattr(
            main,
            "docuseal_event_key",
            lambda event, body, max_age_seconds=None: ("rk-1", "event_too_old"),
        )

        resp = _post_webhook(client, _completed_event())

        assert resp.json() == {"status": "ignored", "reason": "event_too_old"}

    def test_duplicate_event_short_circuits(self, monkeypatch):
        client, main, _db, _marked = _base_setup(monkeypatch)
        monkeypatch.setattr(main._DOCUSEAL_REPLAY_GUARD, "claim", lambda key: False)

        resp = _post_webhook(client, _completed_event())

        assert resp.json() == {"status": "duplicate"}

    def test_unsupported_event_type_ignored(self, monkeypatch):
        client, _main, _db, _marked = _base_setup(monkeypatch)

        resp = _post_webhook(client, _completed_event("form.opened"))

        assert resp.json() == {"status": "ignored", "event_type": "form.opened"}

    def test_missing_document_url_or_deal_id_ignored(self, monkeypatch):
        client, _main, _db, _marked = _base_setup(monkeypatch)
        event = _completed_event()
        event["data"] = {"id": 7}  # no metadata, no documents

        resp = _post_webhook(client, event)

        assert resp.json() == {"status": "ignored", "reason": "missing_document_url_or_deal_id"}

    def test_unsafe_document_url_rejected(self, monkeypatch):
        client, main, _db, _marked = _base_setup(monkeypatch)
        monkeypatch.setattr(
            main, "is_safe_docuseal_document_url", lambda url, api_url=None, extra_hosts="": False
        )

        resp = _post_webhook(client, _completed_event())

        assert resp.json() == {"status": "ignored", "reason": "document_url_not_allowed"}

    def test_completed_event_mirrors_to_gcs_drive_and_note(self, monkeypatch):
        client, main, db, marked = _base_setup(monkeypatch, fake_db=True)
        calls: dict = {}
        _stub_httpx(monkeypatch, calls)
        _stub_gcs_upload(monkeypatch, calls)
        _stub_drive(monkeypatch, calls)
        audited: list = []
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: audited.append(kwargs))

        try:
            resp = _post_webhook(client, _completed_event())
        finally:
            os.remove("/tmp/signed_7.pdf")

        assert resp.status_code == 200
        assert resp.json() == {
            "status": "ok",
            "gcs_path": "gs://tho-secure-documents/signed_documents/deal-1/signed_7.pdf",
        }
        assert calls["urls"] == ["https://docuseal.example.com/signed/7.pdf"]
        assert calls["uploads"] == [
            (
                "signed_documents/deal-1/signed_7.pdf",
                "/tmp/signed_7.pdf",
                "application/pdf",
            )
        ]
        assert calls["folders"] == ["deal-1"]
        assert marked == [("deal-1", "sub-9", "completed")]
        # Deal note recorded in Firestore with the GCS uri.
        note = db.collections["deal_notes"]["deal-1_esign_7"]
        assert note["type"] == "esign_completed"
        assert note["deal_id"] == "deal-1"
        assert note["gcs_path"].startswith("gs://tho-secure-documents/")
        assert audited and audited[0]["action"] == "document.esign_complete"

    def test_gcs_mirror_failure_falls_back_to_local_path(self, monkeypatch):
        client, _main, db, _marked = _base_setup(monkeypatch, fake_db=True)
        calls: dict = {}
        _stub_httpx(monkeypatch, calls)
        _stub_gcs_upload(monkeypatch, calls, fail=True)
        _stub_drive(monkeypatch, calls)

        try:
            resp = _post_webhook(client, _completed_event())
        finally:
            os.remove("/tmp/signed_7.pdf")

        assert resp.status_code == 200
        assert resp.json() == {
            "status": "ok",
            "gcs_path": "signed_documents/deal-1/signed_7.pdf",
        }
        note = db.collections["deal_notes"]["deal-1_esign_7"]
        assert note["gcs_path"] == "signed_documents/deal-1/signed_7.pdf"

    def test_submission_completed_uses_submission_id(self, monkeypatch):
        client, _main, db, marked = _base_setup(monkeypatch, fake_db=True)
        calls: dict = {}
        _stub_httpx(monkeypatch, calls)
        _stub_gcs_upload(monkeypatch, calls)
        _stub_drive(monkeypatch, calls)
        event = _completed_event("submission.completed")
        event["data"].pop("submission_id")  # submission.completed carries id only

        try:
            resp = _post_webhook(client, event)
        finally:
            os.remove("/tmp/signed_7.pdf")

        assert resp.status_code == 200
        assert marked == [("deal-1", 7, "completed")] or marked == [("deal-1", "7", "completed")]

    def test_missing_submission_id_skips_mark(self, monkeypatch):
        client, _main, db, marked = _base_setup(monkeypatch, fake_db=True)
        calls: dict = {}
        _stub_httpx(monkeypatch, calls)
        _stub_gcs_upload(monkeypatch, calls)
        _stub_drive(monkeypatch, calls)
        event = _completed_event()
        event["data"].pop("submission_id")

        try:
            resp = _post_webhook(client, event)
        finally:
            os.remove("/tmp/signed_7.pdf")

        assert resp.status_code == 200
        assert marked == []

    def test_mark_failure_is_swallowed(self, monkeypatch):
        client, main, db, _marked = _base_setup(monkeypatch, fake_db=True)

        def _boom(deal_id, submission_id, status):
            raise RuntimeError("db down")

        monkeypatch.setattr(main, "_mark_esign_request", _boom)
        calls: dict = {}
        _stub_httpx(monkeypatch, calls)
        _stub_gcs_upload(monkeypatch, calls)
        _stub_drive(monkeypatch, calls)

        try:
            resp = _post_webhook(client, _completed_event())
        finally:
            os.remove("/tmp/signed_7.pdf")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_note_write_failure_is_swallowed(self, monkeypatch):
        client, main, _db, _marked = _base_setup(monkeypatch, fake_db=True)
        calls: dict = {}

        def _boom(name):
            raise RuntimeError("firestore down")

        monkeypatch.setattr(
            main, "get_database", lambda: types.SimpleNamespace(collection=_boom)
        )
        _stub_httpx(monkeypatch, calls)
        _stub_gcs_upload(monkeypatch, calls)
        _stub_drive(monkeypatch, calls)

        try:
            resp = _post_webhook(client, _completed_event())
        finally:
            os.remove("/tmp/signed_7.pdf")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_drive_mirror_failure_is_swallowed(self, monkeypatch):
        client, _main, _db, _marked = _base_setup(monkeypatch, fake_db=True)
        calls: dict = {}

        def _boom(path, name, folder_id):
            raise RuntimeError("drive down")

        module = types.ModuleType("tools.drive_service")
        module.ensure_deal_folder = lambda deal_id: "folder-1"
        module.upload_to_drive = _boom
        monkeypatch.setitem(sys.modules, "tools.drive_service", module)
        import tools

        monkeypatch.setattr(tools, "drive_service", module, raising=False)

        _stub_httpx(monkeypatch, calls)
        _stub_gcs_upload(monkeypatch, calls)

        try:
            resp = _post_webhook(client, _completed_event())
        finally:
            os.remove("/tmp/signed_7.pdf")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_download_failure_returns_500_and_releases_claim(self, monkeypatch):
        client, main, _db, _marked = _base_setup(monkeypatch, fake_db=True)
        released: list = []
        monkeypatch.setattr(
            main._DOCUSEAL_REPLAY_GUARD, "release", lambda key: released.append(key)
        )
        calls: dict = {}
        _stub_httpx(monkeypatch, calls, get_error=RuntimeError("docuseal down"))
        _stub_gcs_upload(monkeypatch, calls)
        _stub_drive(monkeypatch, calls)

        resp = _post_webhook(client, _completed_event())

        assert resp.status_code == 500
        assert "Webhook processing failed" in resp.json()["message"]
        assert released == ["rk-1"]


# ─── POST /api/documents/extract-fields ───────────────────────────────────


def _stub_form_extraction(monkeypatch, result=None, error=None):
    module = types.ModuleType("tools.form_extraction")
    calls: list = []

    async def extract_form_data_from_session(session_id, template_name, runner):
        calls.append({"session_id": session_id, "template_name": template_name, "runner": runner})
        if error is not None:
            raise error
        return result if result is not None else {"extracted_data": {"name": "Bob"}}

    module.extract_form_data_from_session = extract_form_data_from_session
    monkeypatch.setitem(sys.modules, "tools.form_extraction", module)
    import tools

    monkeypatch.setattr(tools, "form_extraction", module, raising=False)
    return calls


class TestExtractFieldsFromChat:
    def test_requires_session_and_template(self, monkeypatch):
        client, _main, _db, _logger = create_client(monkeypatch)
        _stub_form_extraction(monkeypatch)

        for payload in ({"session_id": "s1"}, {"template_name": "t1"}, {}):
            resp = client.post(
                "/api/documents/extract-fields", json=payload, headers=_admin_headers(_main)
            )
            assert resp.status_code == 200, payload
            assert resp.json() == {
                "extracted_data": {},
                "message": "session_id and template_name required",
            }

    def test_runner_unavailable_returns_message(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        _stub_form_extraction(monkeypatch)

        def _boom():
            raise RuntimeError("ADK not initialized")

        monkeypatch.setattr(main, "_get_runner", _boom)

        resp = client.post(
            "/api/documents/extract-fields",
            json={"session_id": "s1", "template_name": "t1"},
            headers=_admin_headers(main),
        )

        assert resp.json() == {
            "extracted_data": {},
            "message": "AI service temporarily unavailable",
        }

    def test_extraction_result_passes_through(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        calls = _stub_form_extraction(monkeypatch, result={"extracted_data": {"bedrooms": 3}})
        monkeypatch.setattr(main, "_get_runner", lambda: "RUNNER")

        resp = client.post(
            "/api/documents/extract-fields",
            json={"session_id": "sess-a", "template_name": "credit-app"},
            headers=_admin_headers(main),
        )

        assert resp.json() == {"extracted_data": {"bedrooms": 3}}
        assert calls == [
            {"session_id": "sess-a", "template_name": "credit-app", "runner": "RUNNER"}
        ]

    def test_extraction_failure_returns_generic_message(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        _stub_form_extraction(monkeypatch, error=RuntimeError("model down"))
        monkeypatch.setattr(main, "_get_runner", lambda: "RUNNER")

        resp = client.post(
            "/api/documents/extract-fields",
            json={"session_id": "s1", "template_name": "t1"},
            headers=_admin_headers(main),
        )

        assert resp.json() == {
            "extracted_data": {},
            "message": "Field extraction failed. Please try again.",
        }


# ─── _overlay_staff_photos (direct) ───────────────────────────────────────


def _stub_image_storage(monkeypatch, grouped, validate_raises=()):
    module = types.ModuleType("tools.image_storage")

    class PhotoValidationError(Exception):
        pass

    module.PhotoValidationError = PhotoValidationError
    module.list_all_grouped = lambda: grouped

    def safe_home_id(raw):
        if raw in validate_raises:
            raise PhotoValidationError(f"bad id: {raw}")
        return raw

    module.safe_home_id = safe_home_id
    monkeypatch.setitem(sys.modules, "tools.image_storage", module)
    import tools

    monkeypatch.setattr(tools, "image_storage", module, raising=False)
    return module


def _photo(url):
    return types.SimpleNamespace(url=url)


class TestOverlayStaffPhotos:
    def test_empty_homes_noop(self, monkeypatch):
        _client, main, _db, _logger = create_client(monkeypatch)
        _stub_image_storage(monkeypatch, {"43372": [_photo("u1")]})
        homes: list = []
        main._overlay_staff_photos(homes)
        assert homes == []

    def test_empty_grouped_noop(self, monkeypatch):
        _client, main, _db, _logger = create_client(monkeypatch)
        _stub_image_storage(monkeypatch, {})
        homes = [{"id": "43372", "real_photos": ["legacy-1"]}]
        main._overlay_staff_photos(homes)
        assert homes == [{"id": "43372", "real_photos": ["legacy-1"]}]

    def test_staff_photos_prepend_and_dedupe(self, monkeypatch):
        _client, main, _db, _logger = create_client(monkeypatch)
        grouped = {
            "43372": [_photo("staff-1.jpg"), _photo("staff-2.jpg")],
        }
        _stub_image_storage(monkeypatch, grouped)
        homes = [
            {"id": "43372", "real_photos": ["staff-2.jpg", "legacy-1.jpg"]},
            {"id": "  "},  # blank id → skipped
            {"name": "no id at all"},  # missing id → skipped
        ]

        main._overlay_staff_photos(homes)

        merged = ["staff-1.jpg", "staff-2.jpg", "legacy-1.jpg"]
        assert homes[0]["real_photos"] == merged
        assert homes[0]["gallery_images"] == merged[:3]
        assert homes[0]["image_url"] == "staff-1.jpg"
        assert homes[0]["has_staff_photos"] is True
        assert homes[1] == {"id": "  "}
        assert homes[2] == {"name": "no id at all"}

    def test_invalid_home_id_skipped_and_non_list_existing_replaced(self, monkeypatch):
        _client, main, _db, _logger = create_client(monkeypatch)
        grouped = {"bad": [_photo("x.jpg")], "43372": [_photo("staff-9.jpg")]}
        _stub_image_storage(monkeypatch, grouped, validate_raises={"bad"})
        homes = [
            {"id": "bad", "real_photos": ["keep-me.jpg"]},  # validation error → untouched
            {"id": "43372", "real_photos": "not-a-list"},  # non-list existing → replaced
            {"id": "99999"},  # no staff photos → untouched
        ]

        main._overlay_staff_photos(homes)

        assert homes[0] == {"id": "bad", "real_photos": ["keep-me.jpg"]}
        assert homes[1]["real_photos"] == ["staff-9.jpg"]
        assert homes[1]["image_url"] == "staff-9.jpg"
        assert homes[2] == {"id": "99999"}
