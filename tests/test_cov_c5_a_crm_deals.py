"""Tests for CRM / deals / customers coverage-gap targets (swarm A).

Targets (Python-3.12 honest-coverage backlog, main.py @ 46c8afa):

- ``list_leads_api`` / ``update_lead_api`` / ``transition_lead_lifecycle_api``
  — success envelopes, 404s, validation 400s, and the generic failure envelopes
- ``list_deals`` / ``create_deal`` / ``update_deal`` — success paths (including
  the datetime-serialization branch) and the failure envelopes
- ``_parse_iso_datetime`` / ``_strip_pii_from_deal`` / ``_deal_serial`` /
  ``_enrich_deals_with_home`` — all parse/strip/serial branches, including the
  non-dict guards
- ``search_customers`` / ``customer_stats`` / ``customer_count`` /
  ``create_customer`` — success + failure envelopes, the short-name 400, the
  invalid-status fallback, and the persistence-failure 500
- ``_strip_sensitive_customer_payload`` / ``_masked_ssn`` — non-string-key skip
  and the <4-digit SSN fallback
- ``v1_list_customers`` / ``v1_get_customer`` / ``v1_create_customer`` /
  ``v1_list_leads`` / ``v1_stats`` — partner-route error branches (404/500)
  plus the invalid-status fallback
- ``run_lead_nurture_endpoint`` — success plus the audit-log ImportError and
  generic-failure fallbacks
- CRM task store helpers (``_crm_task_collection``, ``_load_crm_tasks_from_store``,
  ``_save_crm_task``, ``_load_crm_task``, ``create_crm_task``) — in-memory
  fallbacks, Firestore failure warnings, and the body-validation 400s

Handlers are called directly with a minimal request stand-in: the suite's
``create_client`` swaps body schemas and auth deps, so direct calls sidestep
both. Audit helpers are no-op patched so the Firestore emulator is never
needed. ``tools.lead_nurture`` and ``audit_log`` are replaced per test via
``sys.modules`` entries (with the parent ``tools`` package attribute patched
too) so the heavy real modules are never imported.

Run: python -m pytest tests/test_cov_c5_a_crm_deals.py -v
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from datetime import UTC, datetime
from json import JSONDecodeError
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(Path(__file__).parent))
from test_api_v1 import create_client  # noqa: E402


class _Req:
    """Minimal request stand-in: ``await request.json()`` returns the payload."""

    def __init__(self, payload, headers=None):
        self._payload = payload
        self.headers = headers or {}

    async def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _client(monkeypatch):
    client, main, fake_db, fake_logger = create_client(monkeypatch)
    monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
    return client, main, fake_db, fake_logger


def _run(coro):
    return asyncio.run(coro)


# ─── list_leads_api / update_lead_api / transition_lead_lifecycle_api ──────


class TestListLeadsApi:
    def test_success_envelope(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)

        result = _run(main.list_leads_api())

        assert result["success"] is True
        assert result["count"] == 1
        assert result["leads"][0]["lead_id"] == "lead-1"

    def test_failure_envelope(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)

        async def boom(status=None, limit=100):
            # Suspend like the suite's FakeLeadManager so coverage records fully.
            def _raise():
                raise RuntimeError("firestore down")

            return await asyncio.to_thread(_raise)

        monkeypatch.setattr(main.lead_manager, "list_leads", boom)

        result = _run(main.list_leads_api())

        assert result == {"success": False, "error": "Failed to load leads. Please try again."}


class TestUpdateLeadApi:
    def test_unknown_lead_is_404(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)

        with pytest.raises(HTTPException) as excinfo:
            _run(main.update_lead_api("ghost", _Req({"priority": "high"})))

        assert excinfo.value.status_code == 404
        assert excinfo.value.detail == "Lead not found"

    def test_disallowed_field_is_400(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)

        with pytest.raises(HTTPException) as excinfo:
            _run(main.update_lead_api("lead-1", _Req({"status": "contacted"})))

        assert excinfo.value.status_code == 400

    def test_persistence_failure_envelope(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)

        async def boom(lead):
            def _raise():
                raise RuntimeError("write failed")

            return await asyncio.to_thread(_raise)

        monkeypatch.setattr(main.lead_manager, "update_lead", boom)

        result = _run(main.update_lead_api("lead-1", _Req({"priority": "high"})))

        assert result == {"success": False, "error": "Failed to update lead. Please try again."}


class TestTransitionLeadLifecycleApi:
    def test_non_string_status_is_400(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)

        with pytest.raises(HTTPException) as excinfo:
            _run(main.transition_lead_lifecycle_api("lead-1", _Req({"status": 42})))

        assert excinfo.value.status_code == 400
        assert excinfo.value.detail == "Status must be a string."

    def test_unknown_lead_is_404(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)

        with pytest.raises(HTTPException) as excinfo:
            _run(main.transition_lead_lifecycle_api("ghost", _Req({"status": "archived"})))

        assert excinfo.value.status_code == 404
        assert excinfo.value.detail == "Lead not found"

    def test_unexpected_failure_envelope(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)

        async def boom(lead_id, new_status, *, actor):
            def _raise():
                raise RuntimeError("transition store down")

            return await asyncio.to_thread(_raise)

        monkeypatch.setattr(main.lead_manager, "transition_lead_status", boom)

        result = _run(main.transition_lead_lifecycle_api("lead-1", _Req({"status": "contacted"})))

        assert result == {
            "success": False,
            "error": "Failed to update lead status. Please try again.",
        }


# ─── Deal helpers ──────────────────────────────────────────────────────────


class TestParseIsoDatetime:
    def test_naive_datetime_passthrough(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)
        naive = datetime(2026, 4, 1, 12, 30, 0)

        assert main._parse_iso_datetime(naive) is naive

    def test_aware_datetime_normalized_to_utc(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)
        from datetime import timedelta

        tz_plus2 = UTC.__class__(timedelta(hours=2))  # fixed +02:00 zone
        aware = datetime(2026, 4, 1, 14, 30, 0, tzinfo=tz_plus2)

        assert main._parse_iso_datetime(aware) == datetime(2026, 4, 1, 12, 30, 0)

    def test_non_string_non_datetime_returns_none(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)

        assert main._parse_iso_datetime(1711957200) is None

    def test_blank_string_returns_none(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)

        assert main._parse_iso_datetime("   ") is None


class TestStripPiiFromDeal:
    def test_dict_has_ssn_keys_removed(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)
        deal = {"id": "d1", "buyer_ssn": "123-45-6789", "co_buyer_ssn": "987-65-4321", "model": "M"}

        assert main._strip_pii_from_deal(deal) == {"id": "d1", "model": "M"}

    def test_non_dict_passthrough(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)

        assert main._strip_pii_from_deal("raw-deal") == "raw-deal"


class TestDealSerial:
    def test_non_dict_returns_none(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)

        assert main._deal_serial(types.SimpleNamespace(serial_number_1="SN1")) is None

    def test_serial_number_1_preferred_then_legacy_key(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)

        assert main._deal_serial({"serial_number_1": " SN-9 ", "serial_number": "SN8"}) == "SN-9"
        assert main._deal_serial({"serial_number": "SN8"}) == "SN8"
        assert main._deal_serial({"id": "d1"}) is None


class TestEnrichDealsWithHome:
    def test_non_dict_deals_skipped_in_both_passes(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)
        deals = [
            types.SimpleNamespace(id="not-a-dict"),
            {"id": "d9", "serial_number_1": "SN9"},
        ]
        db = types.SimpleNamespace(
            get_inventory_by_serials=lambda serials: {
                "SN9": {"model_name": "Model Nine", "manufacturer": "Champion"}
            }
        )

        enriched = main._enrich_deals_with_home(deals, db)

        assert enriched[0].id == "not-a-dict"  # untouched non-dict
        assert enriched[1]["home_label"] == "Model Nine"
        assert enriched[1]["home_manufacturer"] == "Champion"

    def test_lookup_failure_falls_back_to_raw_deals(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)
        deals = [{"id": "d9", "serial_number_1": "SN9"}]
        db = types.SimpleNamespace(
            get_inventory_by_serials=lambda serials: (_ for _ in ()).throw(RuntimeError("batch down"))
        )

        enriched = main._enrich_deals_with_home(deals, db)

        assert enriched == deals
        assert "home_label" not in enriched[0]


class TestDealPayloadError:
    @pytest.mark.parametrize(
        ("field_names", "expected_detail"),
        [
            (
                ["sales_price"],
                "Could not save this deal. Check the field: sales_price.",
            ),
            (
                ["sales_price", "buyer_email"],
                "Could not save this deal. Check the fields: sales_price, buyer_email.",
            ),
        ],
    )
    def test_invalid_fields_are_named_in_422(self, monkeypatch, field_names, expected_detail):
        _c, main, _db, _log = _client(monkeypatch)

        error = main._deal_payload_error(
            "update", main.DealPayloadError(field_names, kind="invalid")
        )

        assert isinstance(error, HTTPException)
        assert error.status_code == 422
        assert error.detail == expected_detail

    def test_shape_error_uses_json_object_fallback(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)

        error = main._deal_payload_error("create", main.DealPayloadError([], kind="shape"))

        assert isinstance(error, HTTPException)
        assert error.status_code == 422
        assert error.detail == "Could not save this deal. Send the deal fields as a JSON object."


# ─── list_deals / create_deal / update_deal ────────────────────────────────


class TestListDeals:
    def test_success_enriches_and_strips_pii(self, monkeypatch):
        _c, main, fake_db, _log = _client(monkeypatch)
        deals = [
            {"id": "d1", "buyer_ssn": "123-45-6789", "serial_number": "SN1"},
            {"id": "d2", "buyer_ssn": "111-22-3333", "model": "Creole", "manufacturer": "Champion"},
        ]
        monkeypatch.setattr(main._deal_db, "search_deals", lambda **kw: deals, raising=False)
        monkeypatch.setattr(
            main._deal_db,
            "get_inventory_by_serials",
            lambda serials: {"SN1": {"model_name": "Model One", "manufacturer": "Champion"}},
            raising=False,
        )

        result = _run(main.list_deals(status="pending", q="bob", limit=10))

        assert result["success"] is True
        assert result["count"] == 2
        assert "buyer_ssn" not in result["deals"][0]
        assert result["deals"][0]["home_label"] == "Model One"
        assert result["deals"][1]["home_label"] == "Creole"

    def test_failure_envelope(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)

        def boom(**kwargs):
            raise RuntimeError("deal store down")

        monkeypatch.setattr(main._deal_db, "search_deals", boom, raising=False)

        result = _run(main.list_deals())

        assert result == {"success": False, "error": "Failed to load deals. Please try again."}


class TestCreateDeal:
    def test_success_serializes_timestamps_and_audits(self, monkeypatch):
        _c, main, fake_db, _log = _client(monkeypatch)
        created_at = datetime(2026, 4, 1, 8, 0, 0, tzinfo=UTC)
        seen = {}

        def fake_create_deal(data):
            seen.update(data)
            return "deal-new"

        monkeypatch.setattr(main._deal_db, "create_deal", fake_create_deal, raising=False)
        fake_db.collections["deals"]["deal-new"] = {"id": "deal-new", "status": "pending"}

        result = _run(
            main.create_deal(_Req({"buyer_first_name": "Bob", "created_at": created_at}))
        )

        assert result["success"] is True
        assert result["deal_id"] == "deal-new"
        assert result["deal"] == {"id": "deal-new", "status": "pending"}
        assert seen["created_at"] == created_at.isoformat()

    def test_persistence_failure_envelope(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)

        def boom(data):
            raise RuntimeError("write failed")

        monkeypatch.setattr(main._deal_db, "create_deal", boom, raising=False)

        result = _run(main.create_deal(_Req({"buyer_first_name": "Bob"})))

        assert result == {"success": False, "error": "Failed to create deal. Please try again."}

    def test_unreadable_body_envelope(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)

        result = _run(main.create_deal(_Req(RuntimeError("body unreadable"))))

        assert result == {"success": False, "error": "Failed to create deal. Please try again."}


class TestUpdateDeal:
    def test_success(self, monkeypatch):
        _c, main, fake_db, _log = _client(monkeypatch)
        seen = {}
        monkeypatch.setattr(
            main._deal_db, "update_deal", lambda deal_id, data: seen.update(data), raising=False
        )

        result = _run(main.update_deal("deal-1", _Req({"id": "forged", "salesrep": "Sam", "status": "approved"})))

        assert result == {"success": True, "message": "Deal updated successfully"}
        assert "id" not in seen  # id stripped before persistence

    def test_failure_envelope(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)
        monkeypatch.setattr(
            main._deal_db,
            "update_deal",
            lambda deal_id, data: (_ for _ in ()).throw(RuntimeError("write failed")),
            raising=False,
        )

        result = _run(main.update_deal("deal-1", _Req({"salesrep": "Sam"})))

        assert result == {"success": False, "error": "Failed to update deal. Please try again."}

    def test_unreadable_body_envelope_without_writing(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)
        writes = []

        def record_write(deal_id, data):
            writes.append((deal_id, data))

        monkeypatch.setattr(main._deal_db, "update_deal", record_write, raising=False)

        result = _run(main.update_deal("deal-1", _Req(RuntimeError("body unreadable"))))

        assert result == {"success": False, "error": "Failed to update deal. Please try again."}
        assert writes == []


# ─── Customers: search / stats / count / create ────────────────────────────


class TestSearchCustomers:
    def test_failure_envelope(self, monkeypatch):
        _c, main, fake_db, _log = _client(monkeypatch)

        def boom(**kwargs):
            raise RuntimeError("search index down")

        monkeypatch.setattr(fake_db, "search_customers", boom)

        result = _run(main.search_customers(q="bob"))

        assert result == {
            "customers": [],
            "total": 0,
            "source": "firestore",
            "error": "Search failed",
        }


class TestCustomerStats:
    def test_success(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)

        result = _run(main.customer_stats())

        assert result["migrated"] is True
        assert result["total"] >= 1
        assert "LEAD" in result["by_status"]

    def test_failure_envelope(self, monkeypatch):
        _c, main, fake_db, _log = _client(monkeypatch)

        def boom():
            raise RuntimeError("stats down")

        monkeypatch.setattr(fake_db, "count_customers", boom)

        result = _run(main.customer_stats())

        assert result == {"migrated": False, "error": "Stats unavailable"}


class TestCustomerCount:
    def test_success(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)

        result = _run(main.customer_count())

        assert result["total"] >= 1
        assert "LEAD" in result["by_status"]

    def test_failure_envelope(self, monkeypatch):
        _c, main, fake_db, _log = _client(monkeypatch)

        def boom():
            raise RuntimeError("count down")

        monkeypatch.setattr(fake_db, "count_customers", boom)

        result = _run(main.customer_count())

        assert result == {"total": 0, "by_status": {}, "error": "Count unavailable"}


class TestCreateCustomer:
    def test_short_name_is_400(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)

        with pytest.raises(HTTPException) as excinfo:
            _run(main.create_customer(_Req({"full_name": "A"})))

        assert excinfo.value.status_code == 400
        assert "full_name" in excinfo.value.detail

    def test_invalid_status_falls_back_to_lead_and_masks_ssn(self, monkeypatch):
        _c, main, fake_db, _log = _client(monkeypatch)

        result = _run(
            main.create_customer(
                _Req(
                    {
                        "full_name": "Bob Buyer",
                        "status": "BOGUS",
                        "buyer_ssn": "123-45-9999",
                        "co_buyer": {"name": "Coco", "ssn": "999-88-7777"},
                    }
                )
            )
        )

        assert result["success"] is True
        customer = result["customer"]
        assert customer["status"] == "LEAD"
        assert customer["ssn_masked"] == "***-**-9999"
        assert "buyer_ssn" not in customer
        assert customer["co_buyer"] == {"name": "Coco"}  # nested SSN stripped

    def test_persistence_failure_is_500(self, monkeypatch):
        _c, main, fake_db, _log = _client(monkeypatch)

        def boom(data, doc_id=None):
            raise RuntimeError("write failed")

        monkeypatch.setattr(fake_db, "create_customer", boom)

        with pytest.raises(HTTPException) as excinfo:
            _run(main.create_customer(_Req({"full_name": "Bob Buyer"})))

        assert excinfo.value.status_code == 500
        assert "Failed to create customer" in excinfo.value.detail


class TestCustomerPayloadHelpers:
    def test_non_string_keys_skipped(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)

        cleaned = main._strip_sensitive_customer_payload({42: "kept?", "SSN": "gone", "ok": "yes"})

        assert cleaned == {"ok": "yes"}

    def test_short_ssn_sanitized_not_masked(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)

        assert main._masked_ssn("12") == "12"
        assert main._masked_ssn("123-45-6789") == "***-**-6789"


# ─── Partner v1 routes ─────────────────────────────────────────────────────
#
# These handlers are wrapped in ``@limiter.limit``, whose wrapper validates
# that the request argument is a real starlette Request — so unlike the admin
# routes above they must go through the TestClient with a partner API key.

_PARTNER_HEADERS = {"Authorization": "Bearer tho-secret"}


class TestV1ListCustomers:
    def test_failure_raises_500(self, monkeypatch):
        client, main, fake_db, _log = _client(monkeypatch)

        def boom(**kwargs):
            raise RuntimeError("list down")

        monkeypatch.setattr(fake_db, "search_customers", boom)

        response = client.get("/api/v1/customers", headers=_PARTNER_HEADERS)

        assert response.status_code == 500
        assert response.json()["detail"] == "Failed to list customers"


class TestV1GetCustomer:
    def test_missing_customer_is_404(self, monkeypatch):
        client, main, _db, _log = _client(monkeypatch)

        response = client.get("/api/v1/customers/ghost", headers=_PARTNER_HEADERS)

        assert response.status_code == 404
        assert response.json()["detail"] == "Customer not found"

    def test_unexpected_failure_is_500(self, monkeypatch):
        client, main, fake_db, _log = _client(monkeypatch)

        def boom(customer_id):
            raise RuntimeError("get down")

        monkeypatch.setattr(fake_db, "get_customer", boom)

        response = client.get("/api/v1/customers/cust-1", headers=_PARTNER_HEADERS)

        assert response.status_code == 500
        assert response.json()["detail"] == "Failed to retrieve customer"


class TestV1CreateCustomer:
    def test_invalid_status_falls_back_to_lead(self, monkeypatch):
        client, main, fake_db, _log = _client(monkeypatch)

        response = client.post(
            "/api/v1/customers",
            headers=_PARTNER_HEADERS,
            json={"full_name": "Partner Buyer", "status": "weird", "email": "p@example.com"},
        )

        assert response.status_code == 201
        created_id = response.json()["id"]
        stored = fake_db.collections["customers"][created_id]
        assert stored["status"] == "LEAD"
        assert stored["email"] == "p@example.com"

    def test_ssn_shaped_fields_dropped_from_nested_payloads(self, monkeypatch):
        client, main, fake_db, _log = _client(monkeypatch)

        response = client.post(
            "/api/v1/customers",
            headers=_PARTNER_HEADERS,
            json={
                "full_name": "Partner Buyer",
                "co_buyer": {"name": "Coco", "buyer_ssn": "111-22-3333"},
            },
        )

        assert response.status_code == 201
        stored = fake_db.collections["customers"][response.json()["id"]]
        assert stored["co_buyer"] == {"name": "Coco"}

    def test_persistence_failure_is_500(self, monkeypatch):
        client, main, fake_db, _log = _client(monkeypatch)

        def boom(data, doc_id=None):
            raise RuntimeError("write failed")

        monkeypatch.setattr(fake_db, "create_customer", boom)

        response = client.post(
            "/api/v1/customers", headers=_PARTNER_HEADERS, json={"full_name": "Partner Buyer"}
        )

        assert response.status_code == 500
        assert response.json()["detail"] == "Failed to create customer"


class TestV1ListLeads:
    def test_failure_raises_500(self, monkeypatch):
        client, main, _db, _log = _client(monkeypatch)

        async def boom(status=None, limit=100):
            def _raise():
                raise RuntimeError("lead store down")

            return await asyncio.to_thread(_raise)

        monkeypatch.setattr(main.lead_manager, "list_leads", boom)

        response = client.get("/api/v1/leads", headers=_PARTNER_HEADERS)

        assert response.status_code == 500
        assert response.json()["detail"] == "Failed to list leads"


class TestV1Stats:
    def test_failure_raises_500(self, monkeypatch):
        client, main, fake_db, _log = _client(monkeypatch)

        def boom():
            raise RuntimeError("stats down")

        monkeypatch.setattr(fake_db, "count_customers", boom)

        response = client.get("/api/v1/stats", headers=_PARTNER_HEADERS)

        assert response.status_code == 500
        assert response.json()["detail"] == "Failed to load stats"


# ─── CRM task store helpers + create_crm_task ──────────────────────────────


class TestCrmTaskCollection:
    def test_missing_db_client_returns_none(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)
        monkeypatch.setattr(main, "_db", types.SimpleNamespace(db=None))

        assert main._crm_task_collection() is None

    def test_db_client_error_returns_none(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)

        class _BoomDb:
            @property
            def db(self):
                raise RuntimeError("no firestore")

        monkeypatch.setattr(main, "_db", _BoomDb())

        assert main._crm_task_collection() is None


class _BoomStreamCollection:
    """Collection whose limited stream always raises (Firestore outage)."""

    def limit(self, n):
        return self

    def stream(self, timeout=None):
        raise RuntimeError("stream down")


class _BoomWriteCollection:
    """Collection whose document writes always raise (Firestore outage)."""

    class _Ref:
        def set(self, data, timeout=None):
            raise RuntimeError("write down")

    def document(self, doc_id):
        return self._Ref()


class _BoomReadCollection:
    """Collection whose document reads always raise (Firestore outage)."""

    class _Ref:
        def get(self, timeout=None):
            raise RuntimeError("read down")

    def document(self, doc_id):
        return self._Ref()


class TestLoadCrmTasksFromStore:
    def test_no_collection_returns_in_memory_tasks(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)
        monkeypatch.setattr(main, "_db", types.SimpleNamespace(db=None))
        monkeypatch.setattr(main, "_crm_tasks", {"t1": {"task_id": "t1", "title": "Call Bob"}})

        tasks = main._load_crm_tasks_from_store()

        assert tasks == [{"task_id": "t1", "title": "Call Bob"}]

    def test_stream_failure_falls_back_to_in_memory(self, monkeypatch):
        _c, main, _db, fake_logger = _client(monkeypatch)
        monkeypatch.setattr(main, "_crm_task_collection", lambda: _BoomStreamCollection())
        monkeypatch.setattr(main, "_crm_tasks", {"t1": {"task_id": "t1", "title": "Call Bob"}})

        tasks = main._load_crm_tasks_from_store()

        assert tasks == [{"task_id": "t1", "title": "Call Bob"}]
        assert any(
            entry["message"] == "CRM task Firestore list failed" for entry in fake_logger.entries
        )


class TestSaveCrmTask:
    def test_write_failure_still_keeps_in_memory_copy(self, monkeypatch):
        _c, main, _db, fake_logger = _client(monkeypatch)
        monkeypatch.setattr(main, "_crm_task_collection", lambda: _BoomWriteCollection())
        in_memory = {}
        monkeypatch.setattr(main, "_crm_tasks", in_memory)

        main._save_crm_task({"task_id": "t9", "title": "Follow up"})

        assert in_memory["t9"]["title"] == "Follow up"
        assert any(
            entry["message"] == "CRM task Firestore save failed" for entry in fake_logger.entries
        )


class TestLoadCrmTask:
    def test_read_failure_returns_in_memory_copy(self, monkeypatch):
        _c, main, _db, fake_logger = _client(monkeypatch)
        monkeypatch.setattr(main, "_crm_task_collection", lambda: _BoomReadCollection())
        monkeypatch.setattr(main, "_crm_tasks", {"t1": {"task_id": "t1", "title": "Call Bob"}})

        task = main._load_crm_task("t1")

        assert task == {"task_id": "t1", "title": "Call Bob"}
        assert any(
            entry["message"] == "CRM task Firestore fetch failed" for entry in fake_logger.entries
        )


class TestCreateCrmTask:
    def test_malformed_json_is_400(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)

        resp = _run(main.create_crm_task(_Req(JSONDecodeError("bad", "doc", 0))))

        assert isinstance(resp, JSONResponse)
        assert resp.status_code == 400
        assert json.loads(resp.body) == {"success": False, "error": "Malformed JSON body."}

    def test_non_object_body_is_400(self, monkeypatch):
        _c, main, _db, _log = _client(monkeypatch)

        resp = _run(main.create_crm_task(_Req(["not", "an", "object"])))

        assert resp.status_code == 400
        assert json.loads(resp.body) == {
            "success": False,
            "error": "Request body must be a JSON object.",
        }

    def test_invalid_priority_falls_back_to_medium(self, monkeypatch):
        _c, main, fake_db, _log = _client(monkeypatch)

        result = _run(main.create_crm_task(_Req({"title": "Call back", "priority": "URGENT-MAX"})))

        assert result["success"] is True
        assert result["task"]["priority"] == "medium"
        assert result["task"]["title"] == "Call back"
        # Persisted to the fake Firestore collection too.
        assert fake_db.collections["crm_tasks"][result["task"]["task_id"]]["priority"] == "medium"


# ─── run_lead_nurture_endpoint ─────────────────────────────────────────────


def _nurture_client(monkeypatch, audit_module):
    """Wire a fake ``tools.lead_nurture`` (never import the real heavy module)."""
    _c, main, _db, fake_logger = _client(monkeypatch)
    import tools  # real package, already imported by main

    fake_ln = types.ModuleType("tools.lead_nurture")
    fake_ln.run_nurture_batch = lambda **kwargs: {
        "dry_run": kwargs["dry_run"],
        "cohort_size": 3,
        "sent": 0,
        "failed": 0,
    }
    monkeypatch.setitem(sys.modules, "tools.lead_nurture", fake_ln)
    monkeypatch.setattr(tools, "lead_nurture", fake_ln, raising=False)
    if audit_module is not None:
        monkeypatch.setitem(sys.modules, "audit_log", audit_module)
    return main, fake_logger


class TestRunLeadNurtureEndpoint:
    def test_success_with_audit_log(self, monkeypatch):
        audit = types.ModuleType("audit_log")
        audit.log_admin_action = lambda **kwargs: None
        main, _log = _nurture_client(monkeypatch, audit)

        result = _run(main.run_lead_nurture_endpoint(_Req({"dry_run": True, "days_inactive": 30})))

        assert result["dry_run"] is True
        assert result["cohort_size"] == 3

    def test_missing_audit_log_falls_back_to_structured_logging(self, monkeypatch):
        audit = types.ModuleType("audit_log")  # no log_admin_action → ImportError
        main, fake_logger = _nurture_client(monkeypatch, audit)

        result = _run(main.run_lead_nurture_endpoint(_Req({})))

        assert result["cohort_size"] == 3  # audit failure never blocks the response
        assert any(
            entry["message"] == "Lead nurture batch (audit_log not yet merged)"
            for entry in fake_logger.entries
        )

    def test_audit_write_failure_is_swallowed(self, monkeypatch):
        audit = types.ModuleType("audit_log")

        def boom(**kwargs):
            raise RuntimeError("audit store down")

        audit.log_admin_action = boom
        main, fake_logger = _nurture_client(monkeypatch, audit)

        result = _run(main.run_lead_nurture_endpoint(_Req({})))

        assert result["cohort_size"] == 3
        assert any(
            entry["message"] == "Audit log write failed for lead_nurture"
            for entry in fake_logger.entries
        )
