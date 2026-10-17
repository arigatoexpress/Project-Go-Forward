"""Tests for eight coverage-gap targets (Python-3.12 honest-coverage backlog).

Targets (2026-10-17 re-measurement on main @ 69db7ab, 433 missing lines in
main.py — see docs/MAIN_COVERAGE_INVESTIGATION.md):

- ``_apply_inventory_media_fallback`` — lazy photo-classifier import failure,
  manufacturer-media-recovery warning swallow
- ``admin_inventory_analytics`` — cache-hit short-circuit, pending/reserved
  statuses, non-numeric price fallback, outer failure envelope
- ``generate_packet_endpoint`` — document-quality-failure and engine-failure
  responses, outer failure envelope
- ``generate_batch_endpoint`` — no-templates rejection, success-with-audit,
  outer failure envelope
- ``serve_listing_photo`` — happy path 200 + cache header, 404, 400, 503
- ``delete_listing_photo`` — success, 404, 400, 503 (via the resilient
  ``{success, status_code, message}`` envelope)
- ``_validate_public_contact_payload`` — type guards, None coalescing, and
  the name/phone/message length caps
- ``get_available_slots`` — passthrough and failure envelope

Handlers are called directly with a minimal request stand-in where the suite's
``create_client`` body-schema swap would interfere; photo endpoints go through
TestClient (one public GET, one admin DELETE). Audit helpers are no-op
patched; caching is patched per test for isolation.

Run: python -m pytest tests/test_inventory_doc_photo_slots_gaps.py -v
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(Path(__file__).parent))
from test_api_v1 import create_client  # noqa: E402


class _Req:
    """Minimal request stand-in: ``await request.json()`` returns the payload."""

    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _client(monkeypatch):
    client, main, fake_db, _logger = create_client(monkeypatch)
    monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
    return client, main, fake_db


def _run(coro):
    return asyncio.run(coro)


def _admin_headers(main) -> dict:
    return {"X-Admin-Token": main._create_admin_token()}


def _patch_cache(monkeypatch, hit=None, calls=None):
    """Patch caching.cache_get/cache_set wherever the module currently lives."""
    caching_mod = sys.modules.get("caching")
    if caching_mod is None:
        import caching as caching_mod  # type: ignore[no-redef]

    def cache_get(key):
        if calls is not None:
            calls.append(("get", key))
        return hit

    def cache_set(key, value, ttl_seconds=None):
        if calls is not None:
            calls.append(("set", key))

    monkeypatch.setattr(caching_mod, "cache_get", cache_get, raising=False)
    monkeypatch.setattr(caching_mod, "cache_set", cache_set, raising=False)


# ─── _apply_inventory_media_fallback ───────────────────────────────────────


class TestApplyInventoryMediaFallback:
    def _classifier_module(self, monkeypatch):
        """Fresh photo_classifier stub with controllable attrs."""
        fake = types.ModuleType("tools.photo_classifier")
        fake.apply_classifier_to_home = lambda media: None
        fake.has_real_photo = lambda media: False
        monkeypatch.setitem(sys.modules, "tools.photo_classifier", fake)
        import tools

        monkeypatch.setattr(tools, "photo_classifier", fake, raising=False)
        return fake

    def test_classifier_import_failure_returns_result_unchanged(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        # The eager module-level import makes the None-guard unreachable in
        # production; remove the globals and make the lazy import fail.
        monkeypatch.delattr(main, "apply_classifier_to_home", raising=False)
        monkeypatch.delattr(main, "has_real_photo", raising=False)
        broken = types.ModuleType("tools.photo_classifier")
        broken.apply_classifier_to_home = lambda media: None  # present…
        # …but has_real_photo missing → the second from-import raises
        monkeypatch.setitem(sys.modules, "tools.photo_classifier", broken)
        import tools

        monkeypatch.setattr(tools, "photo_classifier", broken, raising=False)

        result = {"id": "home-1"}
        out = main._apply_inventory_media_fallback(result, {"id": "home-1"}, {})

        assert out is result
        assert "media_recovery" not in out

    def test_manufacturer_recovery_failure_warns_and_continues(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        self._classifier_module(monkeypatch)
        recovery = types.ModuleType("tools.manufacturer_media_recovery")

        def boom(media):
            raise RuntimeError("manufacturer feed down")

        recovery.apply_manufacturer_media_recovery = boom
        monkeypatch.setitem(sys.modules, "tools.manufacturer_media_recovery", recovery)
        import tools

        monkeypatch.setattr(tools, "manufacturer_media_recovery", recovery, raising=False)
        # No legacy match, no asset match: exercise only the recovery guard.
        monkeypatch.setattr(main, "_asset_media_for_inventory_item", lambda *a, **k: None)

        result = {"id": "home-1"}
        out = main._apply_inventory_media_fallback(result, {"id": "home-1"}, {})

        assert out is result
        assert "media_recovery" not in out


# ─── admin_inventory_analytics ─────────────────────────────────────────────


class TestAdminInventoryAnalytics:
    def test_cache_hit_short_circuits(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        sentinel = {"success": True, "cached": True}
        _patch_cache(monkeypatch, hit=sentinel, calls=[])

        result = _run(main.admin_inventory_analytics())

        assert result is sentinel

    def test_full_breakdown_with_edge_statuses_and_bad_price(self, monkeypatch):
        _client_, main, fake_db = _client(monkeypatch)
        _patch_cache(monkeypatch, hit=None, calls=[])
        fake_db.collections["inventory"] = {
            "a1": {
                "status": "AVAILABLE",
                "sale_price": 80000,
                "manufacturer": "Champion",
                "date_added": "2026-10-01T00:00:00",
            },
            "p1": {"status": "PENDING", "sale_price": 70000, "manufacturer": "Clayton"},
            "r1": {"status": "RESERVED", "sale_price": 65000, "manufacturer": "Clayton"},
            "s1": {
                "status": "SOLD",
                "msrp": 90000,  # sale_price absent → msrp fallback
                "manufacturer": "Champion",
                "created_at": "2026-09-01T00:00:00",
                "updated_at": "2026-10-10T00:00:00",
            },
            "b1": {"status": "AVAILABLE", "sale_price": "oops", "manufacturer": ""},
        }

        result = _run(main.admin_inventory_analytics())

        assert result["success"] is True
        totals = result["totals"]
        assert totals == {
            "total": 5,
            "available": 2,
            "pending": 1,
            "reserved": 1,
            "sold_total": 1,
            "sold_last_30d": 1,
        }
        # "oops" price skipped; median of [80000, 70000, 65000, 90000] = 75000
        assert result["median_sale_price"] == 75000.0
        by_mfr = {m["manufacturer"]: m for m in result["by_manufacturer"]}
        assert by_mfr["Champion"]["count"] == 2
        assert by_mfr["Clayton"]["available"] == 0
        assert by_mfr["Unknown"]["count"] == 1

    def test_failure_envelope(self, monkeypatch):
        _client_, main, fake_db = _client(monkeypatch)
        _patch_cache(monkeypatch, hit=None, calls=[])

        def boom(name):
            raise RuntimeError("firestore down")

        monkeypatch.setattr(fake_db.db, "collection", boom)

        result = _run(main.admin_inventory_analytics())

        assert result == {"success": False, "error": "Failed to compute inventory analytics."}


# ─── generate_packet_endpoint / generate_batch_endpoint ────────────────────


class TestGeneratePacketEndpoint:
    def _setup(self, monkeypatch, engine_result=None, engine_exc=None):
        _client_, main, _db = _client(monkeypatch)
        monkeypatch.setattr(main, "validate_for_documents", lambda data: {})
        monkeypatch.setattr(main, "send_document_email", lambda **kwargs: {"success": True})

        def fake_engine(**kwargs):
            if engine_exc is not None:
                raise engine_exc
            return engine_result

        monkeypatch.setattr(main, "engine_generate_packet", fake_engine)
        body = types.SimpleNamespace(
            packet_name="standard_closing",
            customer_email=None,
            customer_name=None,
            data={},
        )
        return main, body

    def test_quality_failure_response(self, monkeypatch):
        main, body = self._setup(monkeypatch, engine_result={"success": False})
        from fastapi.responses import JSONResponse

        sentinel = JSONResponse({"quality": True}, status_code=422)
        monkeypatch.setattr(main, "_is_document_quality_failure", lambda result: True)
        monkeypatch.setattr(main, "_document_quality_json_response", lambda result: sentinel)

        result = _run(main.generate_packet_endpoint(body, _Req({})))

        assert result is sentinel

    def test_engine_failure_envelope(self, monkeypatch):
        main, body = self._setup(monkeypatch, engine_result={"success": False, "message": "no template"})
        monkeypatch.setattr(main, "_is_document_quality_failure", lambda result: False)

        result = _run(main.generate_packet_endpoint(body, _Req({})))

        assert result == {"success": False, "error": "no template"}

    def test_outer_exception_envelope(self, monkeypatch):
        main, body = self._setup(monkeypatch, engine_exc=RuntimeError("synthetic crash"))
        monkeypatch.setattr(main, "_is_document_quality_failure", lambda result: False)

        result = _run(main.generate_packet_endpoint(body, _Req({})))

        assert result == {"success": False, "error": "Packet generation failed. Please try again."}


class TestGenerateBatchEndpoint:
    def test_no_templates_rejected(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        result = _run(main.generate_batch_endpoint(_Req({"templates": [], "data": {}})))

        assert result == {"success": False, "error": "No templates selected"}

    def test_success_returns_engine_result(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        engine_result = {"success": True, "filename": "batch.pdf", "merged": True}
        monkeypatch.setattr(
            main, "engine_generate_batch", lambda **kwargs: engine_result
        )
        monkeypatch.setattr(main, "_is_document_quality_failure", lambda result: False)

        result = _run(
            main.generate_batch_endpoint(_Req({"templates": ["a", "b"], "data": {}, "merge": True}))
        )

        assert result is engine_result

    def test_outer_exception_envelope(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        def boom(**kwargs):
            raise RuntimeError("engine exploded")

        monkeypatch.setattr(main, "engine_generate_batch", boom)
        monkeypatch.setattr(main, "_is_document_quality_failure", lambda result: False)

        result = _run(main.generate_batch_endpoint(_Req({"templates": ["a"], "data": {}})))

        assert result == {"success": False, "error": "Batch generation failed. Please try again."}


# ─── serve_listing_photo / delete_listing_photo ────────────────────────────


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
    fake.get_photo = lambda home_id, filename: None
    fake.delete_photo = lambda home_id, filename: False
    monkeypatch.setitem(sys.modules, "tools.image_storage", fake)
    import tools

    monkeypatch.setattr(tools, "image_storage", fake, raising=False)
    return fake


class TestServeListingPhoto:
    def test_photo_served_with_cache_header(self, monkeypatch):
        client, main, _db = _client(monkeypatch)
        fake = _stub_image_storage(monkeypatch)
        fake.get_photo = lambda home_id, filename: (b"jpeg-bytes", "image/jpeg")

        resp = client.get("/api/inventory/photos/home-1/front.jpg")

        assert resp.status_code == 200
        assert resp.content == b"jpeg-bytes"
        assert resp.headers["content-type"].startswith("image/jpeg")
        assert resp.headers["cache-control"] == "public, max-age=86400"

    def test_missing_photo_returns_404(self, monkeypatch):
        client, main, _db = _client(monkeypatch)
        _stub_image_storage(monkeypatch)  # default get_photo → None

        resp = client.get("/api/inventory/photos/home-1/ghost.jpg")

        assert resp.status_code == 404
        assert resp.json() == {"error": "Photo not found"}

    def test_validation_error_returns_400(self, monkeypatch):
        client, main, _db = _client(monkeypatch)
        fake = _stub_image_storage(monkeypatch)

        def boom(home_id, filename):
            raise fake.PhotoValidationError("bad filename")

        fake.get_photo = boom

        resp = client.get("/api/inventory/photos/home-1/evil%20name.jpg")

        assert resp.status_code == 400
        assert resp.json() == {"error": "Invalid request"}

    def test_storage_error_returns_503(self, monkeypatch):
        client, main, _db = _client(monkeypatch)
        fake = _stub_image_storage(monkeypatch)

        def boom(home_id, filename):
            raise fake.PhotoStorageError("gcs unreachable")

        fake.get_photo = boom

        resp = client.get("/api/inventory/photos/home-1/front.jpg")

        assert resp.status_code == 503
        assert resp.json() == {"error": "Photo storage is temporarily unavailable."}


class TestDeleteListingPhoto:
    def test_delete_success(self, monkeypatch):
        client, main, _db = _client(monkeypatch)
        fake = _stub_image_storage(monkeypatch)
        fake.delete_photo = lambda home_id, filename: True

        resp = client.delete("/api/inventory/home-1/photos/front.jpg", headers=_admin_headers(main))

        assert resp.status_code == 200
        assert resp.json() == {"success": True}

    def test_missing_photo_returns_404_envelope(self, monkeypatch):
        client, main, _db = _client(monkeypatch)
        _stub_image_storage(monkeypatch)  # default delete_photo → False

        resp = client.delete("/api/inventory/home-1/photos/ghost.jpg", headers=_admin_headers(main))

        assert resp.status_code == 404
        assert resp.json()["message"] == "Photo not found."

    def test_validation_error_returns_400_envelope(self, monkeypatch):
        client, main, _db = _client(monkeypatch)
        fake = _stub_image_storage(monkeypatch)

        def boom(home_id, filename):
            raise fake.PhotoValidationError("bad filename")

        fake.delete_photo = boom

        resp = client.delete("/api/inventory/home-1/photos/x.jpg", headers=_admin_headers(main))

        assert resp.status_code == 400
        assert resp.json()["message"] == "bad filename"

    def test_storage_error_returns_503_envelope(self, monkeypatch):
        client, main, _db = _client(monkeypatch)
        fake = _stub_image_storage(monkeypatch)

        def boom(home_id, filename):
            raise fake.PhotoStorageError("gcs unreachable")

        fake.delete_photo = boom

        resp = client.delete("/api/inventory/home-1/photos/x.jpg", headers=_admin_headers(main))

        assert resp.status_code == 503
        assert resp.json()["message"] == "Durable photo storage is temporarily unavailable."


# ─── _validate_public_contact_payload ──────────────────────────────────────


def _err(resp):
    return resp.status_code, __import__("json").loads(resp.body.decode())


class TestValidatePublicContactPayload:
    def _call(self, monkeypatch, payload):
        _client_, main, _db = _client(monkeypatch)
        return main._validate_public_contact_payload(payload)

    def test_non_dict_rejected(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        data, resp = main._validate_public_contact_payload(["not", "a", "dict"])
        status, body = _err(resp)
        assert data == {}
        assert status == 400
        assert body == {"success": False, "error": "Invalid request."}

    def test_non_string_name_rejected(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        data, resp = main._validate_public_contact_payload({"name": 123, "phone": "5125551234"})
        status, body = _err(resp)
        assert status == 400
        assert body == {"success": False, "error": "Name and phone are required"}

    def test_none_email_and_message_coalesced(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        data, resp = main._validate_public_contact_payload(
            {"name": "Bob", "phone": "5125551234", "email": None, "message": None}
        )
        assert resp is None
        assert data["email"] == ""
        assert data["message"] == ""

    def test_name_too_long(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        data, resp = main._validate_public_contact_payload(
            {"name": "B" * 121, "phone": "5125551234"}
        )
        status, body = _err(resp)
        assert status == 400
        assert body == {"success": False, "error": "Name must be 120 characters or fewer."}

    def test_phone_too_long(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        data, resp = main._validate_public_contact_payload(
            {"name": "Bob", "phone": "5" * 41}
        )
        status, body = _err(resp)
        assert status == 400
        assert body == {"success": False, "error": "Please enter a valid 10-digit phone number."}

    def test_message_too_long(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        data, resp = main._validate_public_contact_payload(
            {"name": "Bob", "phone": "5125551234", "message": "x" * 5001}
        )
        status, body = _err(resp)
        assert status == 400
        assert "Message must be" in body["error"]


# ─── get_available_slots ───────────────────────────────────────────────────


class TestGetAvailableSlots:
    def test_slots_passthrough(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        async def fake_slots(date):
            return {"date": date, "slots": ["09:00", "10:00"]}

        monkeypatch.setattr(
            main.appointment_manager, "get_available_slots", fake_slots, raising=False
        )

        result = _run(main.get_available_slots(date="2026-10-20"))

        assert result == {"date": "2026-10-20", "slots": ["09:00", "10:00"]}

    def test_failure_envelope(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        async def boom(date):
            raise RuntimeError("calendar down")

        monkeypatch.setattr(
            main.appointment_manager, "get_available_slots", boom, raising=False
        )

        result = _run(main.get_available_slots(date="2026-10-20"))

        assert result == {"error": "Failed to load available slots. Please try again."}
