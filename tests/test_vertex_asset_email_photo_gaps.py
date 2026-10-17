"""Tests for four coverage-gap targets (Python-3.12 honest-coverage backlog).

Targets (see the 2026-10 3.12 re-measurement in docs/MAIN_COVERAGE_INVESTIGATION.md):

- ``_init_vertex_ai`` / ``_get_runner`` — the ADK bootstrap: init
  short-circuit, env-over-config project/location resolution, soft-failure
  warning, runner construction from stubbed ``google.adk`` modules, caching,
  and the RuntimeError envelope
- ``_asset_media_for_inventory_item`` — the asset-catalog media recovery:
  scraper import failure, no-asset miss, matterport enrichment, and the
  photo-classifier fallbacks
- ``POST /api/email/inbound`` — the Resend inbound webhook's disabled /
  bad-signature / bad-payload / ignored / dropped branches plus lead persist
  and staff-notify failure lanes
- ``GET /api/inventory/{home_id}/photos`` — the staff photo listing success
  envelope and both storage error envelopes (400 validation / 503 storage)

From-import stubs set BOTH the ``sys.modules`` entry and the parent-package
attribute (tools, google.adk). The webhook route's limiter is disabled per
test; audit helpers are no-op patched so the Firestore emulator is never
needed.

Run: python -m pytest tests/test_vertex_asset_email_photo_gaps.py -v
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

import tools  # noqa: E402  (already imported by main; parent package for stubs)


def _main(monkeypatch):
    _client, main, _db, _logger = create_client(monkeypatch)
    monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
    monkeypatch.setattr(main, "log_user_action", lambda **kwargs: None)
    return main


# ─── _init_vertex_ai ───────────────────────────────────────────────────────


def _vertexai_module(monkeypatch, *, error=None):
    calls: list = []
    fake = types.ModuleType("vertexai")

    def fake_init(**kwargs):
        calls.append(kwargs)
        if error is not None:
            raise error

    fake.init = fake_init
    monkeypatch.setitem(sys.modules, "vertexai", fake)
    return calls


class TestInitVertexAi:
    def test_already_initialized_short_circuits(self, monkeypatch):
        main = _main(monkeypatch)
        calls = _vertexai_module(monkeypatch)
        monkeypatch.setattr(main, "_vertexai_initialized", True)

        main._init_vertex_ai()

        assert calls == []  # init not attempted
        assert main._vertexai_initialized is True

    def test_env_overrides_deployment_config(self, monkeypatch):
        main = _main(monkeypatch)
        calls = _vertexai_module(monkeypatch)
        monkeypatch.setattr(
            main,
            "get_deployment_config",
            lambda: {"project_id": "cfg-project", "region": "cfg-region"},
        )
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "env-project")
        monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-east1")

        main._init_vertex_ai()

        assert calls == [{"project": "env-project", "location": "us-east1"}]
        assert main._vertexai_initialized is True

    def test_config_fallback_without_env(self, monkeypatch):
        main = _main(monkeypatch)
        calls = _vertexai_module(monkeypatch)
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
        monkeypatch.setattr(
            main,
            "get_deployment_config",
            lambda: {"project_id": "cfg-project", "region": "cfg-region"},
        )

        main._init_vertex_ai()

        assert calls == [{"project": "cfg-project", "location": "cfg-region"}]

    def test_init_failure_warns_without_raising(self, monkeypatch):
        main = _main(monkeypatch)
        _vertexai_module(monkeypatch, error=RuntimeError("no creds"))
        monkeypatch.setattr(main, "_vertexai_initialized", False)

        main._init_vertex_ai()  # must not raise — server starts without AI

        assert main._vertexai_initialized is False


# ─── _get_runner ───────────────────────────────────────────────────────────


def _stub_adk_modules(monkeypatch):
    # Fake the whole google.adk parent chain from sys.modules: the real
    # google.adk cannot be imported in every test environment (its telemetry
    # deps skew), and nothing else touches it during the direct call.
    captured: dict = {}

    class FakeApp:
        def __init__(self, name, root_agent):
            captured["app"] = (name, root_agent)

    class FakeRunner:
        def __init__(self, app):
            captured["runner_app"] = app

    apps_module = types.ModuleType("google.adk.apps")
    apps_module.App = FakeApp
    runners_module = types.ModuleType("google.adk.runners")
    runners_module.InMemoryRunner = FakeRunner
    parent_module = types.ModuleType("google.adk")
    parent_module.apps = apps_module
    parent_module.runners = runners_module
    root_module = types.ModuleType("root_agent")
    root_module.root_agent = "FAKE_AGENT"

    monkeypatch.setitem(sys.modules, "google.adk", parent_module)
    monkeypatch.setitem(sys.modules, "google.adk.apps", apps_module)
    monkeypatch.setitem(sys.modules, "google.adk.runners", runners_module)
    monkeypatch.setitem(sys.modules, "root_agent", root_module)
    return captured


def _fresh_runner_state(monkeypatch, main):
    monkeypatch.setattr(main, "_runner", None)
    monkeypatch.setattr(main, "_adk_app", None)
    monkeypatch.setattr(main, "_root_agent", None)
    monkeypatch.setattr(main, "_vertexai_initialized", True)  # skip vertex init
    monkeypatch.setattr(main, "_init_vertex_ai", lambda: None)


class TestGetRunner:
    def test_builds_and_caches_runner(self, monkeypatch):
        main = _main(monkeypatch)
        captured = _stub_adk_modules(monkeypatch)
        _fresh_runner_state(monkeypatch, main)

        first = main._get_runner()
        second = main._get_runner()

        assert first is second
        assert captured["app"] == ("root_agent", "FAKE_AGENT")
        assert captured["runner_app"] is main._adk_app
        assert main._root_agent == "FAKE_AGENT"

    def test_import_failure_raises_runtime_error(self, monkeypatch):
        main = _main(monkeypatch)
        _fresh_runner_state(monkeypatch, main)
        # Module without the App attribute → from-import raises ImportError.
        # Fake the google.adk parent chain (real one not importable everywhere).
        apps_module = types.ModuleType("google.adk.apps")  # no App attribute
        parent_module = types.ModuleType("google.adk")
        parent_module.apps = apps_module
        monkeypatch.setitem(sys.modules, "google.adk", parent_module)
        monkeypatch.setitem(sys.modules, "google.adk.apps", apps_module)

        with pytest.raises(RuntimeError, match="AI services not available"):
            main._get_runner()

        assert main._runner is None  # failed init must not cache


# ─── _asset_media_for_inventory_item (direct) ──────────────────────────────


def _stub_asset_scraper(monkeypatch, *, assets=None, import_fails=False):
    fake = types.ModuleType("tools.asset_scraper")
    if not import_fails:
        fake.get_assets_for_home = lambda name: (assets or {}).get(name)
        fake.get_matterport_url = lambda mid: f"https://matterport.example/{mid}"
    # import_fails: module without the attrs → from-import raises ImportError.
    monkeypatch.setitem(sys.modules, "tools.asset_scraper", fake)
    monkeypatch.setattr(tools, "asset_scraper", fake, raising=False)


class TestAssetMediaForInventoryItem:
    def test_scraper_import_failure_returns_none(self, monkeypatch):
        main = _main(monkeypatch)
        _stub_asset_scraper(monkeypatch, import_fails=True)

        result = main._asset_media_for_inventory_item({"model_name": "M1"}, {})

        assert result is None

    def test_no_asset_found_returns_none(self, monkeypatch):
        main = _main(monkeypatch)
        _stub_asset_scraper(monkeypatch, assets={"Other Model": {"images": ["a.jpg"]}})

        result = main._asset_media_for_inventory_item({"model_name": "M1"}, {})

        assert result is None

    def test_media_recovery_with_matterport_and_classifier(self, monkeypatch):
        main = _main(monkeypatch)
        asset = {
            "name": "Creole 3256",
            "manufacturer": "Jessup",
            "images": ["a.jpg", "b.jpg", "c.jpg", "d.jpg"],
            "floor_plan": "fp.pdf",
            "image_categories": {"exterior": ["a.jpg"]},
            "matterport_id": "mp-1",
        }
        _stub_asset_scraper(monkeypatch, assets={"Creole 3256": asset})
        classified: list = []
        monkeypatch.setattr(
            main, "apply_classifier_to_home", classified.append, raising=False
        )

        result = main._asset_media_for_inventory_item(
            {"model_name": "Creole 3256"}, {"model": "ignored"}
        )

        assert result is not None
        assert result["image_url"] == "a.jpg"
        assert result["gallery_images"] == ["a.jpg", "b.jpg", "c.jpg"]  # capped at 3
        assert result["floorplan_urls"] == ["fp.pdf"]
        assert result["matterport_url"] == "https://matterport.example/mp-1"
        assert result["media_recovery"]["source"] == "asset_catalog"
        assert classified == [result]

    def test_classifier_import_failure_returns_media(self, monkeypatch):
        main = _main(monkeypatch)
        asset = {"name": "M1", "images": ["a.jpg"]}
        _stub_asset_scraper(monkeypatch, assets={"M1": asset})
        # The classifier global is eagerly imported by main (module-level
        # ``from tools.photo_classifier import ...``), so the None-branch is
        # defensive dead code in practice — remove the global to exercise it.
        monkeypatch.delattr(main, "apply_classifier_to_home", raising=False)
        # tools.photo_classifier module WITHOUT the attribute → ImportError path.
        fake_classifier_module = types.ModuleType("tools.photo_classifier")
        monkeypatch.setitem(sys.modules, "tools.photo_classifier", fake_classifier_module)
        monkeypatch.setattr(tools, "photo_classifier", fake_classifier_module, raising=False)

        result = main._asset_media_for_inventory_item({"model_name": "M1"}, {})

        assert result is not None
        assert result["image_url"] == "a.jpg"  # media returned without classification


# ─── POST /api/email/inbound ───────────────────────────────────────────────


def _inbound_client(monkeypatch, *, secret="whsec-test", signature_ok=True, sender_allowed=True):
    client, main, fake_db, _logger = create_client(monkeypatch)
    monkeypatch.setattr(main.limiter, "enabled", False)
    monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
    monkeypatch.setattr(main, "log_user_action", lambda **kwargs: None)
    if secret is None:
        monkeypatch.delenv("RESEND_WEBHOOK_SECRET", raising=False)
    else:
        monkeypatch.setenv("RESEND_WEBHOOK_SECRET", secret)
    monkeypatch.setattr(
        main, "_verify_svix_signature", lambda secret, headers, body: signature_ok
    )
    monkeypatch.setattr(main, "_inbound_sender_allowed", lambda sender: sender_allowed)
    monkeypatch.setattr(main, "notify_new_lead", lambda **kwargs: {"success": True})
    return client, main


class TestEmailInboundWebhook:
    def test_disabled_without_secret(self, monkeypatch):
        client, _main = _inbound_client(monkeypatch, secret=None)

        resp = client.post("/api/email/inbound", content=b"{}", headers={"svix-id": "x"})

        assert resp.status_code == 200
        assert resp.json() == {"status": "disabled"}

    def test_bad_signature_returns_401(self, monkeypatch):
        client, _main = _inbound_client(monkeypatch, signature_ok=False)

        resp = client.post("/api/email/inbound", content=b"{}", headers={"svix-id": "x"})

        assert resp.status_code == 401
        assert resp.json() == {"status": "invalid_signature"}

    def test_bad_payload_returns_200(self, monkeypatch):
        client, _main = _inbound_client(monkeypatch)

        resp = client.post("/api/email/inbound", content=b"{not json", headers={"svix-id": "x"})

        assert resp.status_code == 200
        assert resp.json() == {"status": "bad_payload"}

    def test_unrelated_event_type_ignored(self, monkeypatch):
        client, _main = _inbound_client(monkeypatch)

        resp = client.post(
            "/api/email/inbound",
            json={"type": "email.bounced", "data": {}},
        )

        assert resp.json() == {"status": "ignored"}

    def test_non_allowlisted_sender_dropped(self, monkeypatch):
        client, _main = _inbound_client(monkeypatch, sender_allowed=False)

        resp = client.post(
            "/api/email/inbound",
            json={
                "type": "email.received",
                "data": {"from": "spam@example.com", "subject": "hi"},
            },
        )

        assert resp.json() == {"status": "dropped"}

    def test_allowed_sender_captures_lead(self, monkeypatch):
        client, main = _inbound_client(monkeypatch)

        resp = client.post(
            "/api/email/inbound",
            json={
                "type": "email.received",
                "data": {
                    # NOTE: the "Name <addr>" display form is stripped to just
                    # the name by InputSanitizationMiddleware before the
                    # handler runs (HTML_TAG_PATTERN removes <addr> from every
                    # JSON string on non-v1 /api routes) — a known data-loss
                    # bug reported to the operator; asserted here via the
                    # plain-address form until it is fixed.
                    "from": "bob@example.com",
                    "subject": "Tour on Friday?",
                    "email_id": "msg-1",
                    "text": "Can I see the Creole?",
                },
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "processed"
        assert "triage" in body
        stored = main.lead_manager.leads[-1]
        assert stored.source == "email"
        assert stored.email == "bob@example.com"
        assert "Tour on Friday?" in stored.triage_notes

    def test_persist_failure_still_notifies_staff(self, monkeypatch):
        client, main = _inbound_client(monkeypatch)
        notified: list = []
        monkeypatch.setattr(
            main, "notify_new_lead", lambda **kwargs: notified.append(kwargs) or {"success": True}
        )

        async def boom(lead):
            raise RuntimeError("firestore down")

        monkeypatch.setattr(main.lead_manager, "create_lead", boom, raising=False)

        resp = client.post(
            "/api/email/inbound",
            json={
                "type": "email.received",
                "data": {"from": "bob@example.com", "subject": "hi"},
            },
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "processed"  # lead capture never breaks
        assert notified and notified[0]["email"] == "bob@example.com"

    def test_notify_failure_still_processes(self, monkeypatch):
        client, main = _inbound_client(monkeypatch)

        def _boom(**kwargs):
            raise RuntimeError("resend down")

        monkeypatch.setattr(main, "notify_new_lead", _boom)

        resp = client.post(
            "/api/email/inbound",
            json={
                "type": "email.received",
                "data": {"from": "bob@example.com", "subject": "hi"},
            },
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "processed"


# ─── GET /api/inventory/{home_id}/photos ───────────────────────────────────


def _stub_image_storage(monkeypatch):
    fake = types.ModuleType("tools.image_storage")

    class PhotoValidationError(Exception):
        pass

    class PhotoStorageError(Exception):
        pass

    fake.PhotoValidationError = PhotoValidationError
    fake.PhotoStorageError = PhotoStorageError
    fake.safe_home_id = lambda home_id: home_id.strip().lower()
    monkeypatch.setitem(sys.modules, "tools.image_storage", fake)
    monkeypatch.setattr(tools, "image_storage", fake, raising=False)
    return fake


class TestListListingPhotos:
    def test_photos_listed(self, monkeypatch):
        client, main = _inbound_client_base(monkeypatch)
        fake = _stub_image_storage(monkeypatch)
        fake.list_photos = lambda home_id: [
            types.SimpleNamespace(filename="p1.jpg", url="/u/1", size_bytes=100)
        ]

        resp = client.get("/api/inventory/HOME-1/photos", headers=_admin_headers(main))

        assert resp.status_code == 200
        assert resp.json() == {
            "success": True,
            "home_id": "home-1",
            "photos": [{"filename": "p1.jpg", "url": "/u/1", "size_bytes": 100}],
        }

    def test_validation_error_returns_400(self, monkeypatch):
        client, main = _inbound_client_base(monkeypatch)
        fake = _stub_image_storage(monkeypatch)

        def boom(home_id):
            raise fake.PhotoValidationError("invalid home id")

        fake.list_photos = boom

        resp = client.get("/api/inventory/bad/photos", headers=_admin_headers(main))

        assert resp.status_code == 400
        assert "invalid home id" in resp.json()["message"]

    def test_storage_error_returns_503(self, monkeypatch):
        client, main = _inbound_client_base(monkeypatch)
        fake = _stub_image_storage(monkeypatch)

        def boom(home_id):
            raise fake.PhotoStorageError("gcs down")

        fake.list_photos = boom

        resp = client.get("/api/inventory/home-1/photos", headers=_admin_headers(main))

        assert resp.status_code == 503
        assert resp.json()["message"] == "Durable photo storage is temporarily unavailable."


def _inbound_client_base(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch)
    monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
    return client, main


def _admin_headers(main) -> dict:
    return {"X-Admin-Token": main._create_admin_token()}
