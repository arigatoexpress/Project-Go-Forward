"""Tests for three admin endpoints with zero prior coverage.

Targets flagged by the Python-3.12 honest coverage re-measurement (see
docs/MAIN_COVERAGE_INVESTIGATION.md):

- ``GET /api/analytics/leads``               — lead funnel analytics + time series
- ``PUT /api/deals/{deal_id}/status``        — bounded deal status transition
- ``PUT /api/inventory/{home_id}/photos/order`` — listing photo reorder

Audit logging is no-op-patched where the endpoint would otherwise write to
Firestore (the emulator is absent in standalone runs; audit behavior itself is
covered by tests/test_audit_log.py). ``tools.partner_webhooks`` and
``tools.image_storage`` are stubbed in ``sys.modules`` because both endpoints
import them lazily inside the handler; the real modules would touch GCS /
Firestore.

Run: python -m pytest tests/test_lead_deal_photo_endpoints.py -v
"""

from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(Path(__file__).parent))
from test_api_v1 import FakeLead, create_client  # noqa: E402


def _admin_headers(main) -> dict:
    return {"X-Admin-Token": main._create_admin_token()}


def _silence_audit(monkeypatch, main) -> None:
    """Keep Firestore-writing audit helpers out of these tests."""
    monkeypatch.setattr(main, "log_user_action", lambda **kwargs: None)
    monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
    import audit_log

    monkeypatch.setattr(audit_log, "log_admin_action", lambda **kwargs: None)


def _seed_leads(main, now: datetime) -> None:
    """A controlled mix exercising every parse_created_at branch."""
    main.lead_manager.leads = [
        FakeLead(
            lead_id="L-now",
            user_id="u1",
            session_id="s1",
            status="new",
            email="now@example.com",
            phone="555-111-2222",
            appointment_requested=True,
            financing_discussed=True,
            created_at=now.isoformat(),
        ),
        # "Z"-suffixed ISO string → tz-aware parse branch.
        FakeLead(
            lead_id="L-week",
            user_id="u2",
            session_id="s2",
            status="contacted",
            created_at=(now - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        ),
        # Old enough to fall outside the 90-day window.
        FakeLead(
            lead_id="L-old",
            user_id="u3",
            session_id="s3",
            status="converted",
            created_at=(now - timedelta(days=120)).isoformat(),
        ),
        # Unparseable string and None → excluded from every range.
        FakeLead(
            lead_id="L-bad",
            user_id="u4",
            session_id="s4",
            status="new",
            created_at="not-a-date",
        ),
        FakeLead(
            lead_id="L-none",
            user_id="u5",
            session_id="s5",
            status="new",
            created_at=None,
        ),
        # Blank string → stripped-empty early return.
        FakeLead(
            lead_id="L-empty",
            user_id="u7",
            session_id="s7",
            status="new",
            created_at="   ",
        ),
        # Naive datetime object → isinstance(datetime) branch.
        FakeLead(
            lead_id="L-naive",
            user_id="u6",
            session_id="s6",
            status="qualified",
            created_at=now - timedelta(days=3),
        ),
    ]


class TestLeadAnalytics:
    def test_default_30d_window_stats_and_series(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)
        _seed_leads(main, datetime.now())

        resp = client.get("/api/analytics/leads", headers=_admin_headers(main))

        assert resp.status_code == 200
        stats = resp.json()
        # L-old is outside 30d; L-bad / L-none never parse.
        assert stats["total"] == 3
        assert stats["by_status"] == {"new": 1, "contacted": 1, "qualified": 1}
        assert stats["with_contact_info"] == 1
        assert stats["appointment_requested"] == 1
        assert stats["financing_discussed"] == 1
        assert stats["new_this_week"] == 2  # L-now + L-naive
        assert len(stats["time_series"]) == 30
        assert sum(point["count"] for point in stats["time_series"]) == 3

    def test_7d_range_shortens_series(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)
        _seed_leads(main, datetime.now())

        resp = client.get("/api/analytics/leads?range=7d", headers=_admin_headers(main))

        assert resp.status_code == 200
        stats = resp.json()
        assert stats["total"] == 2  # L-now + L-naive
        assert len(stats["time_series"]) == 7

    def test_90d_range_includes_older_leads(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)
        _seed_leads(main, datetime.now())

        resp = client.get("/api/analytics/leads?range=90d", headers=_admin_headers(main))

        assert resp.status_code == 200
        stats = resp.json()
        # L-old (120d) is still out, but the 10-day lead counts here as usual.
        assert stats["total"] == 3
        assert len(stats["time_series"]) == 90

    def test_all_range_skips_date_filtering(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)
        _seed_leads(main, datetime.now())

        resp = client.get("/api/analytics/leads?range=all", headers=_admin_headers(main))

        assert resp.status_code == 200
        stats = resp.json()
        # Everything parseable, including the 120-day-old lead.
        assert stats["total"] == 4
        assert len(stats["time_series"]) == min(30, 4)

    def test_unknown_range_uses_min_start_and_capped_series(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)
        _seed_leads(main, datetime.now())

        resp = client.get("/api/analytics/leads?range=quarter", headers=_admin_headers(main))

        assert resp.status_code == 200
        stats = resp.json()
        # datetime.min start date keeps all parseable leads; series caps at 30.
        assert stats["total"] == 4
        assert len(stats["time_series"]) == 4

    def test_list_failure_returns_error_payload(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)

        async def _boom(status=None, limit=100):
            raise RuntimeError("firestore unavailable")

        monkeypatch.setattr(main.lead_manager, "list_leads", _boom)

        resp = client.get("/api/analytics/leads", headers=_admin_headers(main))

        assert resp.status_code == 200
        assert resp.json() == {"error": "Failed to load lead analytics"}


def _stub_partner_webhooks(monkeypatch) -> list:
    """Record dispatched partner events without touching the network."""
    events: list = []
    module = types.ModuleType("tools.partner_webhooks")

    def _dispatch(event_type, payload, db=None):
        events.append((event_type, payload))

    module.dispatch_partner_event = _dispatch
    monkeypatch.setitem(sys.modules, "tools.partner_webhooks", module)
    return events


def _noop_esign(monkeypatch, main, outcome=None):
    async def _fake(deal_id, new_status, request):
        return outcome

    monkeypatch.setattr(main, "_handle_stage_change_signing", _fake)


class TestUpdateDealStatus:
    def test_invalid_status_rejected_without_write(self, monkeypatch):
        client, main, db, _logger = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)
        _noop_esign(monkeypatch, main)

        resp = client.put(
            "/api/deals/deal-1/status",
            json={"status": "bogus"},
            headers=_admin_headers(main),
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "Invalid status" in body["error"]
        assert db.collections["deals"]["deal-1"]["status"] == "pending"

    def test_transition_success_with_esign_outcome(self, monkeypatch):
        client, main, db, _logger = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)
        events = _stub_partner_webhooks(monkeypatch)
        _noop_esign(monkeypatch, main, outcome={"envelope_id": "env-1", "sign_url": "https://x"})

        resp = client.put(
            "/api/deals/deal-1/status",
            json={"status": "approved"},
            headers=_admin_headers(main),
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "approved" in body["message"]
        assert body["esign"] == {"envelope_id": "env-1", "sign_url": "https://x"}
        assert db.collections["deals"]["deal-1"]["status"] == "approved"
        assert [event for event, _payload in events] == ["deal.status_changed"]
        assert events[0][1]["from"] == "pending"
        assert events[0][1]["to"] == "approved"

    def test_funded_deal_emails_buyer_and_dispatches_funded_event(self, monkeypatch):
        client, main, db, _logger = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)
        events = _stub_partner_webhooks(monkeypatch)
        _noop_esign(monkeypatch, main)

        db.collections["deals"]["deal-4"] = {
            "id": "deal-4",
            "status": "contract",
            "buyer_email": "buyer@example.com",
            "buyer_first_name": "Bob",
            "buyer_last_name": "Buyer",
            "model": "Model One",
        }

        emails: list = []
        monkeypatch.setattr(
            main,
            "send_deal_status_update",
            lambda **kwargs: emails.append(kwargs),
        )

        resp = client.put(
            "/api/deals/deal-4/status",
            json={"status": "funded"},
            headers=_admin_headers(main),
        )

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert len(emails) == 1
        assert emails[0]["to"] == "buyer@example.com"
        assert emails[0]["customer_name"] == "Bob Buyer"
        assert emails[0]["new_status"] == "funded"
        assert emails[0]["home_name"] == "Model One"
        assert [event for event, _payload in events] == [
            "deal.status_changed",
            "deal.funded",
        ]

    def test_complete_status_dispatches_complete_event_without_email(self, monkeypatch):
        client, main, db, _logger = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)
        events = _stub_partner_webhooks(monkeypatch)
        _noop_esign(monkeypatch, main)

        emails: list = []
        monkeypatch.setattr(
            main,
            "send_deal_status_update",
            lambda **kwargs: emails.append(kwargs),
        )

        resp = client.put(
            "/api/deals/deal-2/status",
            json={"status": "complete"},
            headers=_admin_headers(main),
        )

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert emails == []  # seeded deals carry no buyer_email
        assert [event for event, _payload in events] == [
            "deal.status_changed",
            "deal.complete",
        ]

    def test_esign_failure_still_reports_success(self, monkeypatch):
        client, main, db, _logger = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)
        _stub_partner_webhooks(monkeypatch)

        async def _boom(deal_id, new_status, request):
            raise RuntimeError("docuseal down")

        monkeypatch.setattr(main, "_handle_stage_change_signing", _boom)

        resp = client.put(
            "/api/deals/deal-1/status",
            json={"status": "approved"},
            headers=_admin_headers(main),
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "esign" not in body
        assert db.collections["deals"]["deal-1"]["status"] == "approved"

    def test_unknown_deal_still_reports_success(self, monkeypatch):
        client, main, db, _logger = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)
        events = _stub_partner_webhooks(monkeypatch)
        _noop_esign(monkeypatch, main)

        resp = client.put(
            "/api/deals/deal-999/status",
            json={"status": "archived"},
            headers=_admin_headers(main),
        )

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert "deal-999" not in db.collections["deals"]
        # prior status was None for the missing deal; webhooks still fired.
        assert events[0][1]["from"] is None
        assert events[0][1]["to"] == "archived"

    def test_email_failure_swallowed(self, monkeypatch):
        client, main, db, _logger = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)
        _stub_partner_webhooks(monkeypatch)
        _noop_esign(monkeypatch, main)

        db.collections["deals"]["deal-5"] = {
            "id": "deal-5",
            "status": "approved",
            "buyer_email": "buyer@example.com",
        }

        def _boom(**kwargs):
            raise RuntimeError("resend down")

        monkeypatch.setattr(main, "send_deal_status_update", _boom)

        resp = client.put(
            "/api/deals/deal-5/status",
            json={"status": "funded"},
            headers=_admin_headers(main),
        )

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert db.collections["deals"]["deal-5"]["status"] == "funded"

    def test_database_failure_returns_generic_error(self, monkeypatch):
        client, main, db, _logger = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)
        _noop_esign(monkeypatch, main)

        def _boom(deal_id, data):
            raise RuntimeError("firestore down")

        monkeypatch.setattr(db, "update_deal", _boom)

        resp = client.put(
            "/api/deals/deal-1/status",
            json={"status": "approved"},
            headers=_admin_headers(main),
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "Please try again" in body["error"]

    def test_webhook_dispatch_failure_swallowed(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)
        _noop_esign(monkeypatch, main)

        module = types.ModuleType("tools.partner_webhooks")

        def _boom(event_type, payload, db=None):
            raise RuntimeError("webhook endpoint down")

        module.dispatch_partner_event = _boom
        monkeypatch.setitem(sys.modules, "tools.partner_webhooks", module)

        resp = client.put(
            "/api/deals/deal-1/status",
            json={"status": "approved"},
            headers=_admin_headers(main),
        )

        assert resp.status_code == 200
        assert resp.json()["success"] is True


def _stub_image_storage(monkeypatch) -> dict:
    """Replace tools.image_storage with an in-memory recorder."""
    calls: dict = {"orders": []}
    module = types.ModuleType("tools.image_storage")

    class PhotoValidationError(Exception):
        pass

    class PhotoStorageError(Exception):
        pass

    def set_photo_order(home_id, order):
        calls["orders"].append((home_id, list(order)))
        return [
            types.SimpleNamespace(filename=name, url=f"https://cdn.example.com/{name}", size_bytes=100)
            for name in order
        ]

    module.PhotoValidationError = PhotoValidationError
    module.PhotoStorageError = PhotoStorageError
    module.set_photo_order = set_photo_order
    module.safe_home_id = lambda home_id: str(home_id)
    monkeypatch.setitem(sys.modules, "tools.image_storage", module)
    # If an earlier test already imported the real submodule, the parent
    # package carries an `image_storage` attribute that `from tools import
    # image_storage` resolves before sys.modules — override it too.
    import tools

    monkeypatch.setattr(tools, "image_storage", module, raising=False)
    return calls


class TestReorderListingPhotos:
    def test_reorder_happy_path(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        logged: list = []
        monkeypatch.setattr(main, "log_user_action", lambda **kwargs: None)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: logged.append(kwargs))
        calls = _stub_image_storage(monkeypatch)

        resp = client.put(
            "/api/inventory/inv-1/photos/order",
            json={"order": ["b.jpg", "a.jpg"]},
            headers=_admin_headers(main),
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["home_id"] == "inv-1"
        assert [photo["filename"] for photo in body["photos"]] == ["b.jpg", "a.jpg"]
        assert body["photos"][0]["url"] == "https://cdn.example.com/b.jpg"
        assert calls["orders"] == [("inv-1", ["b.jpg", "a.jpg"])]
        assert logged and logged[0]["action"] == "inventory.photos_reorder"
        assert logged[0]["target_id"] == "inv-1"

    def test_malformed_json_rejected(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)
        _stub_image_storage(monkeypatch)

        resp = client.put(
            "/api/inventory/inv-1/photos/order",
            content="{not json",
            headers={**_admin_headers(main), "Content-Type": "application/json"},
        )

        assert resp.status_code == 400
        assert "order" in resp.json()["message"]

    def test_order_must_be_list_of_strings(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)
        _stub_image_storage(monkeypatch)

        for bad_body in ({"order": "a.jpg"}, {"order": ["ok.jpg", 7]}, {"other": []}):
            resp = client.put(
                "/api/inventory/inv-1/photos/order",
                json=bad_body,
                headers=_admin_headers(main),
            )
            assert resp.status_code == 400, bad_body
            assert "order" in resp.json()["message"]

    def test_validation_error_maps_to_400(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)
        calls = _stub_image_storage(monkeypatch)
        import sys as _sys

        module = _sys.modules["tools.image_storage"]

        def _raise(home_id, order):
            raise module.PhotoValidationError("unknown photo: ghost.jpg")

        monkeypatch.setattr(module, "set_photo_order", _raise)

        resp = client.put(
            "/api/inventory/inv-1/photos/order",
            json={"order": ["ghost.jpg"]},
            headers=_admin_headers(main),
        )

        assert resp.status_code == 400
        assert "ghost.jpg" in resp.json()["message"]
        assert calls["orders"] == []

    def test_storage_error_maps_to_503(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        _silence_audit(monkeypatch, main)
        _stub_image_storage(monkeypatch)
        import sys as _sys

        module = _sys.modules["tools.image_storage"]

        def _raise(home_id, order):
            raise module.PhotoStorageError("gcs down")

        monkeypatch.setattr(module, "set_photo_order", _raise)

        resp = client.put(
            "/api/inventory/inv-1/photos/order",
            json={"order": ["a.jpg"]},
            headers=_admin_headers(main),
        )

        assert resp.status_code == 503
        assert "unavailable" in resp.json()["message"]
