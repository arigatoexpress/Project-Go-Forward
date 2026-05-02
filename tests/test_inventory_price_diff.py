"""Tests for inventory price-diff audit logic and the cron endpoint.

Price-diff tests (3):
  - boundary: exactly 15% change must NOT trigger (threshold is strictly > 15%)
  - just-under: 14.9% must NOT trigger
  - just-over: 15.1% MUST trigger

Cron endpoint tests (2):
  - auth required: unauthenticated POST returns 401
  - circuit-breaker: second call within cooldown returns 429
"""

import sys
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _load_price_threshold_fn():
    """Import only `price_change_exceeds_threshold` from inventory_sync.

    Uses sys.modules stubs to satisfy inventory_sync's top-level imports
    (bs4, requests, pydantic, google-cloud-firestore) without installing them.
    This keeps the test runnable in a minimal CI environment.
    """
    stubs = {
        "bs4": types.ModuleType("bs4"),
        "requests": types.ModuleType("requests"),
        "pydantic": types.ModuleType("pydantic"),
        "google": types.ModuleType("google"),
        "google.cloud": types.ModuleType("google.cloud"),
        "google.cloud.firestore": types.ModuleType("google.cloud.firestore"),
    }
    stubs["bs4"].BeautifulSoup = MagicMock()
    stubs["pydantic"].ValidationError = Exception
    stubs["google"].cloud = stubs["google.cloud"]
    stubs["google.cloud"].firestore = stubs["google.cloud.firestore"]
    stubs["google.cloud.firestore"].Client = MagicMock()

    # Stub the internal database.models import
    db_models = types.ModuleType("database.models")
    db_models.Inventory = MagicMock()

    with patch.dict(sys.modules, {**stubs, "database": types.ModuleType("database"), "database.models": db_models}):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_inventory_sync_isolated",
            Path(__file__).parent.parent / "tools" / "inventory_sync.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.price_change_exceeds_threshold


_fn = _load_price_threshold_fn()


# ─── Price-diff threshold tests ───────────────────────────────────────────────


def test_price_change_exactly_15_percent_does_not_trigger():
    """A change of exactly 15.0 % must NOT log (threshold is strictly > 15 %)."""
    assert not _fn(100_000.0, 115_000.0)  # +15.000 %


def test_price_change_just_under_15_percent_does_not_trigger():
    """A 14.9 % increase must NOT log."""
    assert not _fn(100_000.0, 114_900.0)  # +14.9 %


def test_price_change_just_over_15_percent_triggers():
    """A 15.1 % increase MUST log an audit record."""
    assert _fn(100_000.0, 115_100.0)  # +15.1 %


def test_price_decrease_over_15_percent_triggers():
    """A 20 % price drop must also log (abs value used)."""
    assert _fn(100_000.0, 80_000.0)  # -20 %


def test_zero_old_price_does_not_trigger():
    """A zero old price must never trigger (avoids ZeroDivisionError)."""
    assert not _fn(0.0, 100_000.0)


# ─── Cron endpoint tests ──────────────────────────────────────────────────────


def _make_admin_token() -> str:
    import base64, hashlib, hmac, struct

    pin_hash = hashlib.sha256(b"4832").hexdigest()
    secret = hashlib.sha256(f"sapphire-jwt-{pin_hash[:16]}".encode()).digest()
    expires = int(time.time()) + 7200
    payload = struct.pack(">Q", expires)
    sig = hmac.new(secret, payload, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(payload + sig).decode().rstrip("=")


@pytest.fixture(scope="module")
def app_client():
    """TestClient for the FastAPI app with all GCP singletons mocked."""
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    # Patch google-cloud modules so main.py can be imported without credentials
    gcp_stubs = {
        "google": types.ModuleType("google"),
        "google.cloud": types.ModuleType("google.cloud"),
        "google.cloud.firestore": types.ModuleType("google.cloud.firestore"),
        "google.cloud.aiplatform": types.ModuleType("google.cloud.aiplatform"),
        "google.auth": types.ModuleType("google.auth"),
    }
    gcp_stubs["google.cloud.firestore"].Client = MagicMock(return_value=MagicMock())

    with patch.dict(sys.modules, gcp_stubs):
        # Prevent the real DB init in firestore_client
        with patch("database.firestore_client.firestore.Client", return_value=MagicMock()):
            import importlib
            import main as app_module

            yield TestClient(app_module.app), app_module


def test_inventory_sync_endpoint_requires_auth(app_client):
    """POST /api/admin/jobs/inventory-sync without a token must return 401."""
    client, _ = app_client
    response = client.post("/api/admin/jobs/inventory-sync")
    assert response.status_code == 401


def test_inventory_sync_circuit_breaker_returns_429(app_client):
    """A second call within the 30-minute cooldown must return 429."""
    from datetime import datetime, timezone, timedelta

    client, app_module = app_client
    token = _make_admin_token()
    headers = {"X-Admin-Token": token}

    # Simulate a recent last_run_at (5 minutes ago)
    recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    state_doc = MagicMock()
    state_doc.exists = True
    state_doc.to_dict.return_value = {"last_run_at": recent_ts}

    app_module._db.db.collection.return_value.document.return_value.get.return_value = state_doc

    response = client.post("/api/admin/jobs/inventory-sync", headers=headers)
    assert response.status_code == 429
    body = response.json()
    assert body["success"] is False
    assert "throttled" in body["error"].lower()
