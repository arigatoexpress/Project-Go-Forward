"""Tests for two coverage-gap targets (Python-3.12 honest-coverage backlog).

Targets (see the 2026-10 3.12 re-measurement in docs/MAIN_COVERAGE_INVESTIGATION.md):

- ``POST /api/admin/staff`` — malformed JSON, non-object body, unknown action,
  invalid email, staff-directory unavailable (503), and directory ValueError
  (400) branches of the team-list mutation endpoint
- ``POST /api/v1/webhooks/notify`` — invalid JSON 400, missing event/deal_id
  400s, idempotency-lookup failure (proceeds to log), and activity-write
  failure (500) branches of the partner webhook intake

Both routes carry slowapi caps, disabled per test via ``main.limiter.enabled``.
The staff endpoint re-imports ``auth.staff_directory`` inside the handler, so
its attributes are patched on the real module. The webhook route writes via
``main._db.db.collection("activities")``; the Firestore-emulator-free scripted
collection stands in for the two failure branches (happy paths are covered by
tests/test_api_v1.py). Audit helpers are no-op patched.

Run: python -m pytest tests/test_admin_staff_webhook_gaps.py -v
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(Path(__file__).parent))
from test_api_v1 import create_client  # noqa: E402

import auth.staff_directory  # noqa: E402


def _admin_client(monkeypatch):
    client, main, fake_db, _logger = create_client(monkeypatch)
    monkeypatch.setattr(main.limiter, "enabled", False)
    monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
    monkeypatch.setattr(main, "log_user_action", lambda **kwargs: None)
    headers = {"X-Admin-Token": main._create_admin_token()}
    return client, main, headers


# ─── POST /api/admin/staff ─────────────────────────────────────────────────


class TestUpdateAdminStaffGaps:
    def test_malformed_json_returns_400(self, monkeypatch):
        client, _main, headers = _admin_client(monkeypatch)

        resp = client.post(
            "/api/admin/staff",
            content="{not json",
            headers={**headers, "Content-Type": "application/json"},
        )

        assert resp.status_code == 400
        assert resp.json() == {"success": False, "error": "Malformed JSON body."}

    def test_non_object_body_returns_400(self, monkeypatch):
        client, _main, headers = _admin_client(monkeypatch)

        resp = client.post("/api/admin/staff", json=["not", "object"], headers=headers)

        assert resp.status_code == 400
        assert resp.json() == {
            "success": False,
            "error": "Request body must be a JSON object.",
        }

    def test_unknown_action_returns_400(self, monkeypatch):
        client, _main, headers = _admin_client(monkeypatch)

        resp = client.post(
            "/api/admin/staff",
            json={"email": "helper@texashomeoutlet.com", "action": "promote"},
            headers=headers,
        )

        assert resp.status_code == 400
        assert resp.json() == {
            "success": False,
            "error": "Action must be allow, block, or reset.",
        }

    def test_invalid_email_returns_400(self, monkeypatch):
        client, _main, headers = _admin_client(monkeypatch)

        resp = client.post(
            "/api/admin/staff",
            json={"email": "not-an-email", "action": "allow"},
            headers=headers,
        )

        assert resp.status_code == 400
        assert resp.json() == {"success": False, "error": "Enter a valid email address."}

    def test_directory_unavailable_returns_503(self, monkeypatch):
        client, _main, headers = _admin_client(monkeypatch)

        def _boom(email, status):
            raise auth.staff_directory.StaffDirectoryUnavailable("firestore down")

        monkeypatch.setattr(auth.staff_directory, "set_override", _boom)

        resp = client.post(
            "/api/admin/staff",
            json={"email": "helper@texashomeoutlet.com", "action": "allow"},
            headers=headers,
        )

        assert resp.status_code == 503
        assert resp.json() == {
            "success": False,
            "error": "Team list is unavailable right now.",
        }

    def test_directory_value_error_returns_400(self, monkeypatch):
        client, _main, headers = _admin_client(monkeypatch)

        def _boom(email, status):
            raise ValueError("unsupported status")

        monkeypatch.setattr(auth.staff_directory, "set_override", _boom)

        resp = client.post(
            "/api/admin/staff",
            json={"email": "helper@texashomeoutlet.com", "action": "allow"},
            headers=headers,
        )

        assert resp.status_code == 400
        assert resp.json() == {"success": False, "error": "Could not update that email."}


# ─── POST /api/v1/webhooks/notify ──────────────────────────────────────────


class _ScriptedActivitiesCollection:
    """Activities collection with scripted lookup/set failures."""

    def __init__(self, *, lookup_error=None, set_error=None):
        self.lookup_error = lookup_error
        self.set_error = set_error
        self.docs: dict = {}

    def where(self, field, op, value):
        return self

    def limit(self, n):
        return self

    def get(self, timeout=None):
        if self.lookup_error is not None:
            raise self.lookup_error
        return []

    def document(self, doc_id):
        collection = self

        class _Ref:
            def set(self, data, merge=False, timeout=None):
                if collection.set_error is not None:
                    raise collection.set_error
                collection.docs[doc_id] = data

        return _Ref()


def _partner_client(monkeypatch, collection):
    client, main, fake_db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    monkeypatch.setattr(main.limiter, "enabled", False)
    monkeypatch.setattr(
        main,
        "_db",
        types.SimpleNamespace(db=types.SimpleNamespace(collection=lambda name: collection)),
    )
    headers = {"Authorization": "Bearer tho-secret"}
    return client, main, headers


class TestV1WebhookNotifyGaps:
    def test_invalid_json_returns_400(self, monkeypatch):
        collection = _ScriptedActivitiesCollection()
        client, _main, headers = _partner_client(monkeypatch, collection)

        resp = client.post(
            "/api/v1/webhooks/notify",
            content="{not json",
            headers={**headers, "Content-Type": "application/json"},
        )

        assert resp.status_code == 400
        assert resp.json()["detail"] == "Invalid JSON payload"

    def test_missing_event_returns_400(self, monkeypatch):
        collection = _ScriptedActivitiesCollection()
        client, _main, headers = _partner_client(monkeypatch, collection)

        resp = client.post(
            "/api/v1/webhooks/notify",
            headers=headers,
            json={"deal_id": "deal-9"},
        )

        assert resp.status_code == 400
        assert resp.json()["detail"] == "event is required"

    def test_missing_deal_id_returns_400(self, monkeypatch):
        collection = _ScriptedActivitiesCollection()
        client, _main, headers = _partner_client(monkeypatch, collection)

        resp = client.post(
            "/api/v1/webhooks/notify",
            headers=headers,
            json={"event": "deal.funded"},
        )

        assert resp.status_code == 400
        assert resp.json()["detail"] == "deal_id is required"

    def test_idempotency_lookup_failure_still_logs_activity(self, monkeypatch):
        collection = _ScriptedActivitiesCollection(lookup_error=RuntimeError("lookup down"))
        client, _main, headers = _partner_client(monkeypatch, collection)

        resp = client.post(
            "/api/v1/webhooks/notify",
            headers=headers,
            json={
                "event": "deal.funded",
                "deal_id": "deal-4",
                "idempotency_key": "run-xyz",
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["logged"] is True
        assert body["idempotent_replay"] is False
        assert len(collection.docs) == 1  # lookup failed but the write went through

    def test_activity_write_failure_returns_500(self, monkeypatch):
        collection = _ScriptedActivitiesCollection(set_error=RuntimeError("write down"))
        client, _main, headers = _partner_client(monkeypatch, collection)

        resp = client.post(
            "/api/v1/webhooks/notify",
            headers=headers,
            json={"event": "deal.funded", "deal_id": "deal-5"},
        )

        assert resp.status_code == 500
        assert resp.json()["detail"] == "Failed to record webhook activity"
        assert collection.docs == {}
