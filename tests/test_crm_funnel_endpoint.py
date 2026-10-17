"""Tests for the CRM funnel analytics endpoint (zero prior coverage).

Target flagged by the Python-3.12 honest coverage re-measurement (see
docs/MAIN_COVERAGE_INVESTIGATION.md):

- ``GET /api/admin/crm/funnel`` — customer-stage counts, deal-stage counts,
  conversion percentages, median time-in-stage proxies, 5-minute cache.

Customers are seeded through the shared in-memory ``FakeTHODatabase``; deals
are streamed through its ``FakeFirestoreDB`` snapshot stream. ``caching`` is
the real module with its in-memory fallback, so ``clear_local_cache()`` runs
per test (same pattern as tests/test_api_v1.py) to keep the 5-minute cache
from leaking staged data across tests. Audit helpers are no-op-patched.

Run: python -m pytest tests/test_crm_funnel_endpoint.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(Path(__file__).parent))
from test_api_v1 import create_client  # noqa: E402


def _admin_headers(main) -> dict:
    return {"X-Admin-Token": main._create_admin_token()}


def _clear_cache() -> None:
    from caching import clear_local_cache

    clear_local_cache()


def _seed_customers(db, customers: list[dict]) -> None:
    db.collections["customers"] = {c["id"]: c for c in customers}


def _seed_deals(db, deals: list[dict]) -> None:
    db.collections["deals"] = {d["id"]: d for d in deals}


def _customer(cid: str, status: str, created_at=None, updated_at=None) -> dict:
    return {
        "id": cid,
        "full_name": f"Customer {cid}",
        "status": status,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _deal(did: str, status: str, created_at=None, updated_at=None) -> dict:
    return {
        "id": did,
        "status": status,
        "created_at": created_at,
        "updated_at": updated_at,
    }


class TestAdminCrmFunnel:
    def test_funnel_computes_stages_medians_and_totals(self, monkeypatch):
        _clear_cache()
        client, main, db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
        monkeypatch.setattr(main, "log_user_action", lambda **kwargs: None)

        _seed_customers(
            db,
            [
                _customer("c-lead", "LEAD"),
                _customer("c-lead2", "lead"),  # lower-case normalizes
                _customer(
                    "c-enrolled",
                    "ENROLLED",
                    created_at="2026-01-01T00:00:00Z",
                    updated_at="2026-01-11T00:00:00Z",  # 10 days
                ),
                _customer(
                    "c-sold",
                    "SOLD",
                    created_at="2026-02-01T00:00:00",
                    updated_at="2026-02-16T12:00:00",  # 15.5 days
                ),
                _customer(
                    "c-backwards",
                    "ENROLLED",
                    created_at="2026-03-01T00:00:00",
                    updated_at="2026-02-01T00:00:00",  # updated < created → skipped
                ),
                _customer("c-cold", "COLD"),  # none of the counted stages
                _customer("c-bad-dates", "SOLD", created_at="garbage", updated_at=None),
            ],
        )
        _seed_deals(
            db,
            [
                _deal("d-pending", "pending"),
                _deal("d-approved", "approved"),
                _deal("d-contract", "contract"),
                _deal(
                    "d-funded",
                    "funded",
                    created_at="2026-01-01T00:00:00Z",
                    updated_at="2026-01-21T00:00:00Z",  # 20 days
                ),
                _deal("d-complete", "complete"),  # no timestamps → median guard
                _deal("d-denied", "denied"),
                _deal("d-weird", "on_hold"),  # unmapped status → ignored
                _deal("d-none-status", None),
            ],
        )

        resp = client.get("/api/admin/crm/funnel", headers=_admin_headers(main))

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True

        stages = {stage["key"]: stage for stage in body["stages"]}
        # funnel_lead = 2 LEAD + 2 ENROLLED + 2 SOLD (COLD ignored; the
        # backwards-dated ENROLLED and bad-dated SOLD still count as people).
        assert stages["LEAD"]["count"] == 6
        assert stages["LEAD"]["conversion_pct"] == 100.0
        # funnel_enrolled = 2 ENROLLED + 2 SOLD
        assert stages["ENROLLED"]["count"] == 4
        assert stages["ENROLLED"]["conversion_pct"] == 66.7
        # funnel_deal = 3 active + 2 closed
        assert stages["DEAL"]["count"] == 5
        assert stages["DEAL"]["conversion_pct"] == 83.3
        # funnel_closed = 2 closed + 2 sold customers
        assert stages["CLOSED"]["count"] == 4

        # Median of [10.0, 15.5] = 12.8 (only valid ENROLLED/SOLD pairs count;
        # the backwards-dated customer was skipped).
        assert body["median_days_in_stage"]["LEAD_to_ENROLLED"] == 12.8
        # Only d-funded contributed a valid close duration.
        assert body["median_days_in_stage"]["DEAL_to_CLOSED"] == 20.0

        assert body["totals"] == {
            "customers_total": 7,
            "deals_active": 3,
            "deals_closed": 2,
            "deals_denied": 1,
        }
        assert "generated_at" in body

    def test_empty_collections_yield_zero_funnel_and_null_medians(self, monkeypatch):
        _clear_cache()
        client, main, db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
        _seed_customers(db, [])
        _seed_deals(db, [])

        resp = client.get("/api/admin/crm/funnel", headers=_admin_headers(main))

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        stages = {stage["key"]: stage for stage in body["stages"]}
        assert stages["LEAD"]["count"] == 0
        assert stages["LEAD"]["conversion_pct"] == 0.0  # pct guard with d=top=1
        assert body["median_days_in_stage"] == {
            "LEAD_to_ENROLLED": None,
            "DEAL_to_CLOSED": None,
        }
        assert body["totals"]["customers_total"] == 0

    def test_second_call_returns_cached_payload(self, monkeypatch):
        _clear_cache()
        client, main, db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
        _seed_customers(db, [_customer("c-1", "LEAD")])
        _seed_deals(db, [])

        first = client.get("/api/admin/crm/funnel", headers=_admin_headers(main)).json()
        # Mutate the backing store: a fresh compute would differ.
        _seed_customers(db, [_customer("c-1", "LEAD"), _customer("c-2", "LEAD")])
        second = client.get("/api/admin/crm/funnel", headers=_admin_headers(main)).json()

        assert second == first
        assert second["totals"]["customers_total"] == 1

    def test_computation_failure_returns_error_payload(self, monkeypatch):
        _clear_cache()
        client, main, db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)

        def _boom(*args, **kwargs):
            raise RuntimeError("firestore down")

        monkeypatch.setattr(db, "search_customers", _boom)

        resp = client.get("/api/admin/crm/funnel", headers=_admin_headers(main))

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "Failed to compute CRM funnel" in body["error"]
