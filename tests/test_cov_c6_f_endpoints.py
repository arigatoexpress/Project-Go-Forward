"""Tests for chat/esign/inventory/lead/share-link coverage-gap targets (swarm F).

Targets (Python-3.12 honest-coverage campaign, final tail — main @ f65c537):

- ``get_public_chat_session`` — history-store failure envelope (500)
- ``_legacy_inventory_context_for_admin_media`` — TypeError shim fallback and
  snapshot-import failure fallback
- ``_load_legacy_inventory_media_index`` — lazy photo-classifier import
  failure and non-dict home skip
- ``_deal_document_data`` — Deal → document-data round trip
- ``_handle_stage_change_signing`` — auto-send failure queues staff review
- ``_send_reviewed_signing`` / ``preview_deal_signing`` — deal-not-found 404s
- ``cancel_deal_signing_request`` / ``docuseal_send`` — not-configured 501s
- ``_inventory_firestore_min_homes`` — non-numeric env fallback
- ``_firestore_inventory_context`` — asset enrichment: image categories,
  matterport id/url backfill
- ``_annotate_inventory_context`` — non-list warnings wrap
- ``upload_listing_photos`` — no-files / too-many / validation-error /
  success returns (raw function via ``__wrapped__``; the slowapi limiter
  wrapper rejects non-Starlette requests on direct calls)
- ``_lead_home_stock_label`` / ``_find_public_home`` / ``_lead_home_summary``
  — empty stock, non-dict homes, invalid stock token
- ``_shared_home_query_target`` — non-GET/HEAD and non-catalog path guards

Handlers are called directly with a minimal request stand-in where the
suite's ``create_client`` body-schema swap would interfere; the chat-session
route goes through TestClient so the owner-cookie HMAC is minted for real.
Audit helpers are no-op patched so the Firestore emulator is never needed.

Run: python -m pytest tests/test_cov_c6_f_endpoints.py -v
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import types
from pathlib import Path
from urllib.parse import parse_qsl

import pytest
from fastapi import HTTPException, UploadFile

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(Path(__file__).parent))
from test_api_v1 import create_client  # noqa: E402


class _Req:
    """Minimal request stand-in: ``await request.json()`` returns the payload."""

    def __init__(self, payload=None):
        self._payload = payload

    async def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload if self._payload is not None else {}


def _client(monkeypatch):
    client, main, fake_db, _logger = create_client(monkeypatch)
    monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
    return client, main, fake_db


def _run(coro):
    return asyncio.run(coro)


def _json(resp):
    """Decode a JSONResponse/FastAPI response into (status, body)."""
    return resp.status_code, json.loads(resp.body.decode())


def _stub_image_storage(monkeypatch):
    """Replace tools.image_storage with a controllable in-memory fake."""
    fake = types.ModuleType("tools.image_storage")

    class PhotoValidationError(Exception):
        pass

    class PhotoStorageError(Exception):
        pass

    fake.PhotoValidationError = PhotoValidationError
    fake.PhotoStorageError = PhotoStorageError
    fake.safe_home_id = lambda home_id: str(home_id).strip().lower()
    monkeypatch.setitem(sys.modules, "tools.image_storage", fake)
    import tools

    monkeypatch.setattr(tools, "image_storage", fake, raising=False)
    return fake


def _upload(filename: str, data: bytes = b"fake-jpeg-bytes", content_type: str = "image/jpeg"):
    return UploadFile(file=io.BytesIO(data), filename=filename, headers=None)


class _FakeDeal:
    """Stand-in for the Deal model: preserves constructor kwargs."""

    def __init__(self, **kwargs):
        self._kwargs = dict(kwargs)

    def model_dump(self):
        return dict(self._kwargs)

    def to_document_data(self):
        return dict(self._kwargs)


# ─── get_public_chat_session ─────────────────────────────────────────────────


class TestPublicChatSession:
    def test_history_failure_returns_500_envelope(self, monkeypatch):
        client, main, _db = _client(monkeypatch)

        async def _boom(session_id):
            raise RuntimeError("history store down")

        monkeypatch.setattr(main.chat_history, "get_session", _boom, raising=False)

        # Mint a real owner cookie by starting a session as this "browser".
        created = client.post("/api/chat/session")
        assert created.status_code == 200
        session_id = created.json()["session_id"]

        response = client.get(f"/api/chat/session/{session_id}")

        assert response.status_code == 500
        assert response.json() == {"success": False, "error": "Failed to retrieve chat history"}


# ─── _legacy_inventory_context_for_admin_media ───────────────────────────────


class TestLegacyInventoryContextForAdminMedia:
    def test_typeerror_shim_fallback(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        def loader(limit):  # noqa: ARG001 — narrow signature forces TypeError
            return {"success": True, "homes": [{"id": "h1"}], "source": "shim"}

        monkeypatch.setattr(main, "load_legacy_inventory_context", loader)

        result = main._legacy_inventory_context_for_admin_media()

        # First call (kwargs) raised TypeError; the limit-only retry succeeded.
        assert result == {"success": True, "homes": [{"id": "h1"}], "source": "shim"}

    def test_snapshot_import_failure_returns_empty(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        # No loader shim → falls through to the snapshot import, which fails
        # because load_app's tools.legacy_site_crawler stub lacks
        # _load_snapshot_context.
        monkeypatch.setattr(main, "load_legacy_inventory_context", None)

        assert main._legacy_inventory_context_for_admin_media() == {}


# ─── _load_legacy_inventory_media_index ──────────────────────────────────────


class TestLoadLegacyInventoryMediaIndex:
    def test_classifier_import_failure_and_non_dict_home_skip(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        # Defeat both the eager import and the lazy one so classifier is None.
        monkeypatch.setattr(main, "apply_classifier_to_home", None)
        broken = types.ModuleType("tools.photo_classifier")  # no attrs at all
        monkeypatch.setitem(sys.modules, "tools.photo_classifier", broken)
        import tools

        monkeypatch.setattr(tools, "photo_classifier", broken, raising=False)
        # A prior test may have populated the module-level TTL cache; the
        # index must be rebuilt for this call.
        main._INVENTORY_MEDIA_INDEX_CACHE.clear()
        monkeypatch.setattr(
            main,
            "load_legacy_inventory_context",
            lambda **kwargs: {
                "success": True,
                "homes": [
                    {"id": "Home 1", "model_name": "Creole 3256"},
                    "not-a-home-dict",  # skipped by the isinstance guard
                ],
            },
        )

        index = main._load_legacy_inventory_media_index()

        assert "home1" in index
        assert "creole3256" in index
        assert len(index) == 2  # the non-dict entry contributed no keys


# ─── e-sign deal-signing endpoints ───────────────────────────────────────────


class _PreparedPacket:
    def __init__(self, template_name):
        self._template = template_name

    def as_dict(self):
        return {
            "ready": True,
            "review_token": "review-token",
            "signer_email": "buyer@example.com",
            "signer_name": "Bob Buyer",
            "template_name": self._template,
            "values": {"sales_price": "100000"},
            "document_label": "Sales Contract",
        }


def _esign_client(monkeypatch):
    """Client with a configured fake DocuSeal and a controllable deal lookup."""
    _client_, main, fake_db = _client(monkeypatch)
    monkeypatch.setattr(main, "docuseal_is_configured", lambda: True)
    return main, fake_db


class TestDealDocumentData:
    def test_round_trip(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.setattr(main, "Deal", _FakeDeal)

        data = main._deal_document_data({"id": "d1", "buyer_first_name": "Bob"})

        assert data == {"id": "d1", "buyer_first_name": "Bob"}


class TestHandleStageChangeSigning:
    def test_auto_send_failure_queues_review(self, monkeypatch):
        main, _db = _esign_client(monkeypatch)
        import esign_review

        monkeypatch.setattr(esign_review, "auto_send_enabled", lambda: True)
        monkeypatch.setattr(main._deal_db, "get_deal", lambda deal_id: {"id": deal_id})
        monkeypatch.setattr(
            main,
            "_prepare_deal_signing",
            lambda deal_data, template_name: _PreparedPacket(template_name).as_dict(),
        )

        async def _failed_send(**kwargs):
            return {"success": False, "error": "dispatch rejected"}

        monkeypatch.setattr(main, "_send_prepared_signing", _failed_send)

        result = _run(main._handle_stage_change_signing("deal-1", "contract", _Req({})))

        # The auto-send failed, so a staff review task was queued instead.
        assert result == "queued_for_review"
        assert any("esign-review-deal-1" in key for key in main._crm_tasks)


class TestSendReviewedSigning:
    def test_deal_not_found_404(self, monkeypatch):
        main, _db = _esign_client(monkeypatch)
        monkeypatch.setattr(main._deal_db, "get_deal", lambda deal_id: None)

        resp = _run(main._send_reviewed_signing("missing-deal", {"confirm": True}, _Req({})))

        status, body = _json(resp)
        assert status == 404
        assert body == {"success": False, "message": "Deal not found."}


class TestPreviewDealSigning:
    def test_deal_not_found_404(self, monkeypatch):
        main, _db = _esign_client(monkeypatch)
        monkeypatch.setattr(main._deal_db, "get_deal", lambda deal_id: None)

        resp = _run(main.preview_deal_signing("missing-deal", _Req({})))

        status, body = _json(resp)
        assert status == 404
        assert body == {"success": False, "message": "Deal not found."}


class TestCancelDealSigningRequest:
    def test_not_configured_501(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.setattr(main, "docuseal_is_configured", lambda: False)
        monkeypatch.setattr(
            main,
            "_load_esign_request",
            lambda key: {"id": key, "deal_id": "deal-1", "status": "pending"},
        )

        resp = _run(main.cancel_deal_signing_request("deal-1", "sub-1", _Req({})))

        status, body = _json(resp)
        assert status == 501
        assert body["status"] == "not_configured"


class TestDocusealSend:
    def test_not_configured_501(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.setattr(main, "docuseal_is_configured", lambda: False)

        resp = _run(main.docuseal_send(_Req({"deal_id": "deal-1"})))

        status, body = _json(resp)
        assert status == 501
        assert body["status"] == "not_configured"


# ─── inventory source helpers ────────────────────────────────────────────────


class TestInventoryFirestoreMinHomes:
    def test_non_numeric_env_falls_back_to_one(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.setenv("INVENTORY_FIRESTORE_MIN_HOMES", "not-a-number")

        assert main._inventory_firestore_min_homes() == 1


class TestFirestoreInventoryContext:
    def test_asset_enrichment_categories_and_matterport(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        asset_scraper = sys.modules["tools.asset_scraper"]
        asset = {
            "images": ["https://cdn.example.com/ext-1.jpg", "https://cdn.example.com/ext-2.jpg"],
            "image_categories": {"exterior": ["https://cdn.example.com/ext-1.jpg"]},
            "floor_plan": "https://cdn.example.com/floorplan.jpg",
            "matterport_id": "mp-123",
        }
        monkeypatch.setattr(asset_scraper, "get_assets_for_home", lambda name: asset)
        preloaded = {
            "success": True,
            "source": "staff_inventory",
            "homes": [
                {
                    "id": "fs-1",
                    "model_name": "Creole 3256",
                    "status": "Available",
                    "is_new": True,
                    # No photo fields at all: no real photos yet, so the
                    # enrichment gate (no real photo, not pre-owned) opens.
                }
            ],
            "total_inventory": 1,
        }

        result = main._firestore_inventory_context(preloaded)

        home = result["homes"][0]
        assert home["image_categories"] == asset["image_categories"]
        assert home["matterport_id"] == "mp-123"
        assert "matterport_url" in home
        assert home["floor_plan_url"] == asset["floor_plan"]
        assert home["real_photos"] == asset["images"]


class TestAnnotateInventoryContext:
    def test_non_list_warnings_are_wrapped(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.setattr(main, "warning_code", lambda status: "inventory_source_stale")
        result = {
            "success": True,
            "source": "legacy",
            "homes": [{"id": "h1", "model_name": "Creole 3256", "classification": "Double Wide"}],
            "warnings": "single-string-warning",
        }

        annotated = main._annotate_inventory_context(
            result,
            requested="legacy",
            selected_path="legacy",
        )

        assert annotated["warnings"] == ["single-string-warning", "inventory_source_stale"]
        assert annotated["source_status"] is not None


# ─── upload_listing_photos ───────────────────────────────────────────────────


class TestUploadListingPhotos:
    """Raw-function tests via ``__wrapped__`` (the slowapi limiter wrapper
    rejects non-Starlette request stand-ins on direct calls)."""

    def _raw(self, main):
        return main.upload_listing_photos.__wrapped__

    def test_no_files_400(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        _stub_image_storage(monkeypatch)

        with pytest.raises(HTTPException) as exc_info:
            _run(self._raw(main)(_Req({}), "home-1", files=[]))

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "No files were uploaded."

    def test_too_many_files_400(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        _stub_image_storage(monkeypatch)
        files = [_upload(f"photo-{i}.jpg") for i in range(main._MAX_PHOTOS_PER_UPLOAD + 1)]

        with pytest.raises(HTTPException) as exc_info:
            _run(self._raw(main)(_Req({}), "home-1", files=files))

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail.startswith("Too many files at once")

    def test_validation_error_recorded_and_success_returned(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        fake = _stub_image_storage(monkeypatch)

        def store_photo(home_id, data, original_name=None, declared_content_type=None):
            if original_name == "bad.exe":
                raise fake.PhotoValidationError("Unsupported file type.")
            return types.SimpleNamespace(filename=original_name, url=f"https://cdn/{original_name}", size_bytes=len(data))

        fake.store_photo = store_photo

        result = _run(
            self._raw(main)(
                _Req({}),
                "home-1",
                files=[_upload("front.jpg", b"good"), _upload("bad.exe", b"evil")],
            )
        )

        # 6263: plain dict return when storage stayed available.
        assert result["success"] is True
        assert result["home_id"] == "home-1"
        assert [p["filename"] for p in result["uploaded"]] == ["front.jpg"]
        # 6194: per-file validation failure is collected, not raised.
        assert result["errors"] == [{"name": "bad.exe", "error": "Unsupported file type."}]


# ─── lead home helpers ───────────────────────────────────────────────────────


class TestLeadHomeHelpers:
    def test_stock_label_empty(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        assert main._lead_home_stock_label("") == ""
        assert main._lead_home_stock_label(None) == ""

    def test_find_public_home_empty_id_and_non_dict_homes(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        assert main._find_public_home("") is None  # 6393 guard

        monkeypatch.setattr(
            main,
            "_resolve_public_inventory_context",
            lambda: {"homes": ["not-a-home-dict", {"id": "other-home"}]},
        )
        assert main._find_public_home("missing-home") is None  # 6401 skip + no match

    def test_summary_invalid_stock_token(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.setattr(main, "_find_public_home", lambda home_id: None)

        summary = main._lead_home_summary(
            {"home_id": "home-9", "home_model": "Creole 3256", "home_stock": "bad stock!"}
        )

        # 6449: the token regex rejected the stock, so it is blanked out.
        assert summary["stock_id"] is None
        assert summary["stock"] == ""
        assert summary["label"] == "Creole 3256"
        assert summary["note"]


# ─── _shared_home_query_target ──────────────────────────────────────────────


class _FakeQueryParams:
    """Minimal stand-in for starlette QueryParams."""

    def __init__(self, query_string: str):
        self._pairs = parse_qsl(query_string)

    def get(self, key, default=None):
        for k, v in self._pairs:
            if k == key:
                return v
        return default

    def multi_items(self):
        return list(self._pairs)


class TestSharedHomeQueryTarget:
    def test_non_get_head_returns_none(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        request = types.SimpleNamespace(method="POST")

        assert main._shared_home_query_target(request) is None

    def test_non_catalog_path_returns_none(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        request = types.SimpleNamespace(
            method="GET",
            query_params=_FakeQueryParams("home=123"),
            url=types.SimpleNamespace(path="/homes/creole-3256"),
        )

        assert main._shared_home_query_target(request) is None
