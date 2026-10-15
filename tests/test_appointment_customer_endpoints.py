"""Tests for four admin/operational endpoints with zero prior coverage.

Targets flagged by the Python-3.12 honest coverage re-measurement (see
docs/MAIN_COVERAGE_INVESTIGATION.md):

- ``POST /api/appointments/{id}/cancel`` — phone-verified public cancellation
- ``GET /api/analytics/customers``    — customer segmentation analytics
- ``PUT /api/customers/{id}``         — bounded-field customer update
- ``POST /api/admin/lead-nurture/run`` — dry-run-by-default nurture batch

Audit/activity logging is no-op-patched per test: those helpers write to
Firestore, which makes this file hang standalone without the emulator (same
known standalone quirk as tests/test_api_v1.py), and their own behavior is
covered by tests/test_audit_log.py and tests/test_user_activity_log.py.

Run: python -m pytest tests/test_appointment_customer_endpoints.py -v
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(Path(__file__).parent))
from test_api_v1 import create_client  # noqa: E402


def _admin_headers(main) -> dict:
    return {"X-Admin-Token": main._create_admin_token()}


def _silence_audit(monkeypatch, main) -> None:
    """Keep Firestore-writing audit/activity helpers out of these tests."""
    monkeypatch.setattr(main, "log_user_action", lambda **kwargs: None)
    monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
    # run_lead_nurture_endpoint soft-imports log_admin_action from the
    # audit_log module directly (not via main's global), so patch the module
    # attribute too or the call reaches real Firestore and stalls on RPC
    # retries when the emulator is absent.
    import audit_log

    monkeypatch.setattr(audit_log, "log_admin_action", lambda **kwargs: None)


class TestCancelAppointment:
    def test_cancel_happy_path(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)

        resp = client.post(
            "/api/appointments/appt-1/cancel",
            json={"phone": "555-111-2222"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "2026-04-22" in body["message"]
        assert "10:00" in body["message"]

    def test_cancel_rejects_wrong_phone(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)

        resp = client.post(
            "/api/appointments/appt-1/cancel",
            json={"phone": "555-999-0000"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "does not match" in body["error"]

    def test_cancel_requires_phone(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)

        resp = client.post("/api/appointments/appt-1/cancel", json={})

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "required" in body["error"]

    def test_cancel_unknown_appointment(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)

        async def _not_found(appointment_id):
            return None

        monkeypatch.setattr(main.appointment_manager, "get_appointment", _not_found)

        resp = client.post(
            "/api/appointments/ghost/cancel",
            json={"phone": "555-111-2222"},
        )

        assert resp.status_code == 200
        assert resp.json() == {"success": False, "error": "Appointment not found."}

    def test_cancel_failure_returns_generic_error(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)

        async def _boom(appointment_id):
            raise RuntimeError("firestore down")

        monkeypatch.setattr(main.appointment_manager, "get_appointment", _boom)

        resp = client.post(
            "/api/appointments/appt-1/cancel",
            json={"phone": "555-111-2222"},
        )

        assert resp.status_code == 200
        assert resp.json() == {
            "success": False,
            "error": "Failed to cancel appointment. Please try again.",
        }


class TestCustomerAnalytics:
    def _seed_customers(self, fake_db) -> None:
        fake_db.collections["customers"].update(
            {
                "cust-2": {
                    "id": "cust-2",
                    "full_name": "Bob Buyer",
                    "status": "ENROLLED",
                    "city": "dallas",
                    "salesrep": "jane smith",
                    "created_at": "2026-03-10T09:00:00",
                },
                "cust-3": {
                    "id": "cust-3",
                    "full_name": "Cara Customer",
                    "status": "LEAD",
                    "city": "Austin",
                    "salesrep": None,
                    "created_at": "2026-03-22T15:00:00",
                },
            }
        )

    def test_analytics_aggregates_segments(self, monkeypatch):
        client, main, _fake_db, _logger = create_client(monkeypatch)
        self._seed_customers(_fake_db)

        resp = client.get("/api/analytics/customers", headers=_admin_headers(main))

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 3
        assert body["by_status"] == {"LEAD": 2, "ENROLLED": 1}
        # 1 of 3 enrolled → 33.3%
        assert body["conversion_rate"] == 33.3
        # City normalization: "dallas" stays as-is (no .title() on cities),
        # both Austin records aggregate.
        city_counts = {entry["city"]: entry["count"] for entry in body["top_cities"]}
        assert city_counts == {"Austin": 2, "dallas": 1}
        # Salesrep normalized to title case and "None" string excluded.
        rep_names = [entry["name"] for entry in body["top_salesreps"]]
        assert "Jane Smith" in rep_names
        assert "None" not in rep_names
        assert "Unassigned" in rep_names  # None salesrep → Unassigned bucket
        # Timeline buckets by YYYY-MM, sorted.
        assert body["by_month"] == {"2026-03": 2, "2026-04": 1}
        # Recent leads are LEAD-status only.
        assert len(body["recent_leads"]) == 2
        assert all(lead["name"] in {"Alice Example", "Cara Customer"} for lead in body["recent_leads"])

    def test_analytics_failure_returns_error(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)

        def _boom(**kwargs):
            raise RuntimeError("firestore down")

        monkeypatch.setattr(main._db, "search_customers", _boom)

        resp = client.get("/api/analytics/customers", headers=_admin_headers(main))

        assert resp.status_code == 200
        assert resp.json() == {"error": "Request failed"}


class TestUpdateCustomer:
    def test_update_applies_allowed_fields(self, monkeypatch):
        client, main, _fake_db, _logger = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)

        resp = client.put(
            "/api/customers/cust-1",
            json={"full_name": "Alice Renamed", "city": "Dallas"},
            headers=_admin_headers(main),
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["customer"]["full_name"] == "Alice Renamed"
        assert body["customer"]["city"] == "Dallas"
        assert body["customer"]["_name_lower"] == "alice renamed"

    def test_update_strips_disallowed_fields(self, monkeypatch):
        client, main, _fake_db, _logger = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)

        resp = client.put(
            "/api/customers/cust-1",
            json={"full_name": "Alice Again", "ssn_hash": "evil", "balance": -10},
            headers=_admin_headers(main),
        )

        assert resp.status_code == 200
        stored = main._db.get_customer("cust-1")
        assert stored["full_name"] == "Alice Again"
        # ssn_hash is not in the updatable list; the seeded value survives.
        assert stored["ssn_hash"] == "hash-1"
        assert "balance" not in stored

    def test_update_unknown_customer_404(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)

        resp = client.put(
            "/api/customers/ghost",
            json={"city": "Nowhere"},
            headers=_admin_headers(main),
        )

        assert resp.status_code == 404

    def test_update_without_allowed_fields_400(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)

        resp = client.put(
            "/api/customers/cust-1",
            json={"hacker_field": "nope"},
            headers=_admin_headers(main),
        )

        assert resp.status_code == 400

    def test_update_strips_sensitive_nested_payload(self, monkeypatch):
        client, main, _fake_db, _logger = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)

        resp = client.put(
            "/api/customers/cust-1",
            json={
                "co_buyer": {"full_name": "Co Buyer", "ssn": "123-45-6789"},
                "references": [
                    {"name": "Ref One", "ssn": "111-22-3333"},
                    {"name": "Ref Two"},
                ],
            },
            headers=_admin_headers(main),
        )

        assert resp.status_code == 200
        stored = main._db.get_customer("cust-1")
        assert stored["co_buyer"] == {"full_name": "Co Buyer"}
        assert stored["references"] == [{"name": "Ref One"}, {"name": "Ref Two"}]

    def test_update_failure_returns_500(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)

        def _boom(customer_id, data):
            raise RuntimeError("firestore down")

        monkeypatch.setattr(main._db, "update_customer", _boom)

        resp = client.put(
            "/api/customers/cust-1",
            json={"city": "Dallas"},
            headers=_admin_headers(main),
        )

        assert resp.status_code == 500


class TestLeadNurtureRun:
    def _stub_nurture(self, monkeypatch, result=None, error=None):
        calls: list[dict] = []
        stub = types.ModuleType("tools.lead_nurture")

        def _run_nurture_batch(**kwargs):
            calls.append(kwargs)
            if error is not None:
                raise error
            return result or {"cohort_size": 3, "sent": 0}

        stub.run_nurture_batch = _run_nurture_batch
        monkeypatch.setitem(sys.modules, "tools.lead_nurture", stub)
        return calls

    def test_defaults_to_dry_run(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)
        calls = self._stub_nurture(monkeypatch, result={"cohort_size": 3, "sent": 0})

        resp = client.post("/api/admin/lead-nurture/run", json={}, headers=_admin_headers(main))

        assert resp.status_code == 200
        assert resp.json() == {"cohort_size": 3, "sent": 0}
        assert calls == [{"dry_run": True, "days_inactive": 14, "max_results": 50}]

    def test_params_coerced_and_forwarded(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)
        calls = self._stub_nurture(monkeypatch)

        resp = client.post(
            "/api/admin/lead-nurture/run",
            json={"dry_run": False, "days_inactive": "30", "max_results": 200},
            headers=_admin_headers(main),
        )

        assert resp.status_code == 200
        assert calls == [{"dry_run": False, "days_inactive": 30, "max_results": 200}]

    def test_invalid_params_fall_back_to_defaults(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)
        calls = self._stub_nurture(monkeypatch)

        resp = client.post(
            "/api/admin/lead-nurture/run",
            json={"days_inactive": "abc", "max_results": None},
            headers=_admin_headers(main),
        )

        assert resp.status_code == 200
        assert calls == [{"dry_run": True, "days_inactive": 14, "max_results": 50}]

    def test_batch_failure_raises_500(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)
        self._stub_nurture(monkeypatch, error=RuntimeError("smtp down"))

        resp = client.post("/api/admin/lead-nurture/run", json={}, headers=_admin_headers(main))

        assert resp.status_code == 500

    def test_malformed_json_body_falls_back_to_defaults(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)
        calls = self._stub_nurture(monkeypatch)

        resp = client.post(
            "/api/admin/lead-nurture/run",
            content=b"not-json",
            headers={**_admin_headers(main), "Content-Type": "application/json"},
        )

        assert resp.status_code == 200
        assert calls == [{"dry_run": True, "days_inactive": 14, "max_results": 50}]

    def test_non_dict_json_body_falls_back_to_defaults(self, monkeypatch):
        client, main, *_ = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)
        calls = self._stub_nurture(monkeypatch)

        resp = client.post(
            "/api/admin/lead-nurture/run",
            json=["not", "a", "dict"],
            headers=_admin_headers(main),
        )

        assert resp.status_code == 200
        assert calls == [{"dry_run": True, "days_inactive": 14, "max_results": 50}]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
