"""Tests for four coverage-gap targets (Python-3.12 honest-coverage backlog).

Targets (2026-10-16 re-measurement on current main, 468 missing lines in
main.py — see docs/MAIN_COVERAGE_INVESTIGATION.md):

- ``admin_lead_sources`` — bad-``days`` fallback, cache-hit short-circuit,
  deal-attribution edges (id-less deals skipped, non-numeric revenue), and
  the outer failure envelope
- ``bulk_import_inventory`` — empty-items rejection, id-less item skip,
  update-vs-import paths, and the outer failure envelope
- ``generate_packet_from_deal`` — deal-not-found, e-sign dispatch exception,
  engine failure, and the outer failure envelope
- ``api_generate_flyer`` — success passthrough and the failure envelope

Handlers are called directly with a minimal request stand-in: the suite's
``create_client`` swaps body schemas and several ``tools`` modules, so direct
calls sidestep both. Audit helpers are no-op patched so the Firestore
emulator is never needed. The caching module's cache_get/cache_set are
patched per test both for determinism and cross-test isolation (the shared
in-memory cache would otherwise leak results between tests using the same
``days`` key).

Run: python -m pytest tests/test_lead_sources_bulk_packet_flyer_gaps.py -v
"""

from __future__ import annotations

import asyncio
import sys
import types
from json import JSONDecodeError
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


# ─── admin_lead_sources ────────────────────────────────────────────────────


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


def _patch_leads(monkeypatch, main, leads):
    async def fake_list_leads(status=None, limit=100):
        if isinstance(leads, Exception):
            raise leads
        return leads

    monkeypatch.setattr(main.lead_manager, "list_leads", fake_list_leads, raising=False)


class TestAdminLeadSources:
    def test_bad_days_falls_back_to_30(self, monkeypatch):
        _client_, main, fake_db = _client(monkeypatch)
        _patch_cache(monkeypatch, hit=None, calls=[])
        _patch_leads(monkeypatch, main, [])
        fake_db.collections["deals"] = {}

        result = _run(main.admin_lead_sources(days="junk"))

        assert result["success"] is True
        assert result["window_days"] == 30
        assert result["total_leads"] == 0
        assert result["categories"] == []

    def test_cache_hit_short_circuits(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        sentinel = {"success": True, "cached": True}
        _patch_cache(monkeypatch, hit=sentinel, calls=[])
        _patch_leads(monkeypatch, main, RuntimeError("must not be reached"))

        result = _run(main.admin_lead_sources(days=7))

        assert result is sentinel

    def test_attribution_edges_idless_deals_and_bad_revenue(self, monkeypatch):
        _client_, main, fake_db = _client(monkeypatch)
        _patch_cache(monkeypatch, hit=None, calls=[])
        lead = types.SimpleNamespace(
            source="chat",
            email="BOB@example.com",
            phone="(512) 555-1234",
            created_at=None,
        )
        _patch_leads(monkeypatch, main, [lead])
        fake_db.collections["deals"] = {
            # Fully attributable: email case-insensitive + phone last-10 match.
            "d1": {
                "id": "d1",
                "status": "funded",
                "buyer_email": "bob@example.com",
                "buyer_phone": "5125551234",
                "sale_price": 95000,
            },
            # No id → skipped by the dedup/id guard.
            "d2": {"status": "funded", "buyer_email": "bob@example.com", "sale_price": 5},
            # Id present but revenue is non-numeric → deal counted, revenue skipped.
            "d3": {"id": "d3", "status": "funded", "buyer_email": "bob@example.com", "sale_price": "n/a"},
            # Non-revenue status → never indexed.
            "d4": {"id": "d4", "status": "pending", "buyer_email": "bob@example.com"},
        }

        result = _run(main.admin_lead_sources(days=30))

        assert result["success"] is True
        assert result["total_leads"] == 1
        (cat,) = result["categories"]
        assert cat["category"] == "chat"
        assert cat["count"] == 1
        assert cat["attributed_deals"] == 2  # d1 + d3; d2 has no id, d4 not funded
        assert cat["attributed_revenue"] == 95000.0  # d3's "n/a" revenue skipped

    def test_failure_envelope(self, monkeypatch):
        _client_, main, fake_db = _client(monkeypatch)
        _patch_cache(monkeypatch, hit=None, calls=[])
        _patch_leads(monkeypatch, main, RuntimeError("firestore down"))
        fake_db.collections["deals"] = {}

        result = _run(main.admin_lead_sources(days=30))

        assert result == {
            "success": False,
            "error": "Failed to compute lead source attribution.",
        }


# ─── bulk_import_inventory ─────────────────────────────────────────────────


def _valid_item(inventory_id: str, **overrides) -> dict:
    item = {
        "id": inventory_id,
        "model_name": "Model Nine",
        "manufacturer": "Champion",
        "msrp": 80000,
        "sale_price": 75000,
        "bedrooms": 3,
        "bathrooms": 2,
        "sqft": 1200,
        "status": "AVAILABLE",
        "photos": [],
    }
    item.update(overrides)
    return item


class TestBulkImportInventory:
    def test_empty_items_rejected(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        result = _run(main.bulk_import_inventory(_Req({"items": []})))

        assert result == {"success": False, "error": "No items provided"}

    def test_missing_id_skipped_existing_updated_new_imported(self, monkeypatch):
        _client_, main, fake_db = _client(monkeypatch)

        result = _run(
            main.bulk_import_inventory(
                _Req(
                    {
                        "items": [
                            {"model_name": "Ghost"},  # no id → error + skip
                            _valid_item("inv-9"),
                            _valid_item("inv-1", model_name="Model One R2", sale_price=70000),
                        ]
                    }
                )
            )
        )

        assert result["success"] is True
        assert result["imported"] == 1
        assert result["updated"] == 1
        assert result["errors"] == ["Skipping item without ID: Ghost"]
        assert fake_db.collections["inventory"]["inv-9"]["model_name"] == "Model Nine"
        assert fake_db.collections["inventory"]["inv-1"]["model_name"] == "Model One R2"

    def test_request_failure_envelope(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        result = _run(main.bulk_import_inventory(_Req(RuntimeError("body unreadable"))))

        assert result == {"success": False, "error": "body unreadable"}


# ─── generate_packet_from_deal ─────────────────────────────────────────────


class _FakeDeal:
    """Stand-in for the Deal model: preserves constructor kwargs."""

    def __init__(self, **kwargs):
        self._kwargs = dict(kwargs)

    def model_dump(self):
        return dict(self._kwargs)

    def to_document_data(self):
        return dict(self._kwargs)


def _packet_client(monkeypatch, deal_data, engine_result=None, engine_exc=None):
    _client_, main, _db = _client(monkeypatch)
    monkeypatch.setattr(main, "send_document_email", lambda **kwargs: {"success": True})
    monkeypatch.setattr(main._deal_db, "get_deal", lambda deal_id: deal_data, raising=False)
    monkeypatch.setattr(main, "Deal", _FakeDeal)
    # Isolate these paths from document-readiness requirements; the
    # validation branch has its own coverage elsewhere.
    monkeypatch.setattr(main, "validate_for_documents", lambda data: {})

    def fake_engine(**kwargs):
        if engine_exc is not None:
            raise engine_exc
        return engine_result

    monkeypatch.setattr(main, "engine_generate_packet", fake_engine)
    return main


_PACKET_FIELDS = {
    "success": True,
    "filename": "packet_d1.pdf",
    "download_url": "https://storage.example.com/packet_d1.pdf?sig=1",
    "message": "Packet ready",
    "page_count": 4,
    "documents_included": ["sales_contract", "disclosures"],
}


class TestGeneratePacketFromDeal:
    def test_deal_not_found(self, monkeypatch):
        main = _packet_client(monkeypatch, deal_data=None)

        result = _run(main.generate_packet_from_deal("missing", _Req({})))

        assert result == {"success": False, "error": "Deal not found"}

    def test_success_without_esign_dispatch(self, monkeypatch):
        main = _packet_client(monkeypatch, deal_data={"id": "d1"}, engine_result=dict(_PACKET_FIELDS))
        monkeypatch.setattr(main.esign_review, "auto_send_enabled", lambda: False, raising=False)

        result = _run(
            main.generate_packet_from_deal(
                "d1",
                _Req({"packet_name": "standard_closing", "overrides": {"buyer_email": "bob@example.com"}}),
            )
        )

        assert result["success"] is True
        assert result["filename"] == "packet_d1.pdf"
        assert result["esign_dispatched"] is False
        assert "esign_error" not in result

    def test_esign_dispatch_exception_reports_error(self, monkeypatch):
        main = _packet_client(monkeypatch, deal_data={"id": "d1"}, engine_result=dict(_PACKET_FIELDS))
        monkeypatch.setattr(main.esign_review, "auto_send_enabled", lambda: True, raising=False)
        monkeypatch.setattr(main.esign_review, "money_problems", lambda data: [], raising=False)

        async def boom(**kwargs):
            raise RuntimeError("docuseal unreachable")

        monkeypatch.setattr(main, "docuseal_send_file_for_signature", boom)

        result = _run(
            main.generate_packet_from_deal(
                "d1",
                _Req({"overrides": {"buyer_email": "bob@example.com", "buyer_first_name": "Bob"}}),
            )
        )

        assert result["success"] is True
        assert result["esign_dispatched"] is False
        assert result["esign_error"] == "dispatch_failed"

    def test_engine_failure_envelope(self, monkeypatch):
        main = _packet_client(
            monkeypatch,
            deal_data={"id": "d1"},
            engine_result={"success": False, "message": "template engine exploded"},
        )

        result = _run(main.generate_packet_from_deal("d1", _Req({})))

        assert result == {"success": False, "error": "template engine exploded"}

    def test_outer_exception_envelope(self, monkeypatch):
        main = _packet_client(
            monkeypatch,
            deal_data={"id": "d1"},
            engine_exc=RuntimeError("synthetic engine crash"),
        )

        result = _run(main.generate_packet_from_deal("d1", _Req({})))

        assert result == {
            "success": False,
            "error": "Failed to generate closing packet. Please try again.",
        }


# ─── api_generate_flyer ────────────────────────────────────────────────────


def _patch_flyer(monkeypatch, func):
    mod = sys.modules.get("tools.marketing_tools")
    assert mod is not None, "load_app should have stubbed tools.marketing_tools"
    monkeypatch.setattr(mod, "generate_ad_flyer", func, raising=False)


class TestApiGenerateFlyer:
    def test_success_passthrough_and_default_home_name(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        seen = {}

        def fake_flyer(**kwargs):
            seen.update(kwargs)
            return {"success": True, "image_url": "https://cdn.example.com/flyer.png"}

        _patch_flyer(monkeypatch, fake_flyer)

        result = _run(main.api_generate_flyer(_Req({"home_price": 75000})))

        assert result == {"success": True, "image_url": "https://cdn.example.com/flyer.png"}
        assert seen["home_name"] == "Featured Home"  # default when omitted
        assert seen["home_price"] == 75000

    def test_failure_envelope(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)

        def boom(**kwargs):
            raise RuntimeError("pillow missing")

        _patch_flyer(monkeypatch, boom)

        result = _run(main.api_generate_flyer(_Req({"home_name": "Creole"})))

        assert result == {
            "success": False,
            "error": "Flyer generation failed. Please try again.",
        }

    def test_invalid_json_envelope(self, monkeypatch):
        _client_, main, _db = _client(monkeypatch)
        _patch_flyer(monkeypatch, lambda **kwargs: {"success": True})

        result = _run(main.api_generate_flyer(_Req(JSONDecodeError("bad", "doc", 0))))

        assert result == {
            "success": False,
            "error": "Flyer generation failed. Please try again.",
        }
