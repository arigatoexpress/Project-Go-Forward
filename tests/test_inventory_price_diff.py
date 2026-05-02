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


def _make_fresh_app():
    """Import main.py fresh with all GCP / StaticFiles dependencies stubbed.

    Follows the same pattern as test_deal_document_validation.py so CI
    (which has no GCP credentials) can import the app without network calls.
    Returns (TestClient, main_module, fake_db).
    """
    import importlib

    from fastapi.testclient import TestClient
    from google.cloud import firestore as _fs_mod

    REPO_ROOT = Path(__file__).resolve().parent.parent

    # main.py mounts ImmutableStaticFiles on frontend/dist/assets at import
    # time; ensure the directory and a stub index.html exist.
    dist = REPO_ROOT / "frontend" / "dist"
    (dist / "assets").mkdir(parents=True, exist_ok=True)
    if not (dist / "index.html").exists():
        (dist / "index.html").write_text("<html><body>test</body></html>")

    # Replace firestore.Client with a no-op so ADC lookup never happens.
    orig_fs_client = _fs_mod.Client
    fake_db = MagicMock()
    _fs_mod.Client = MagicMock(return_value=fake_db)

    # Swap in a fake database.firestore_client module so _db is our mock.
    fake_fc_mod = types.ModuleType("database.firestore_client")
    fake_fc_mod.get_database = lambda: fake_db
    fake_fc_mod.THODatabase = type("THODatabase", (), {})
    orig_fc = sys.modules.get("database.firestore_client")
    sys.modules["database.firestore_client"] = fake_fc_mod

    # Force a clean import so patches take effect.
    sys.modules.pop("main", None)
    main_mod = importlib.import_module("main")

    # Explicitly set module-level db references to our mock.
    main_mod._db = fake_db
    main_mod._deal_db = fake_db

    client = TestClient(main_mod.app)

    # Restore originals after yielding (caller responsible for cleanup).
    return client, main_mod, fake_db, orig_fs_client, orig_fc, _fs_mod


def test_inventory_sync_endpoint_requires_auth():
    """POST /api/admin/jobs/inventory-sync without a token must return 401."""
    pytest.importorskip("fastapi")
    client, main_mod, fake_db, orig_fs, orig_fc, fs_mod = _make_fresh_app()
    try:
        response = client.post("/api/admin/jobs/inventory-sync")
        assert response.status_code == 401
    finally:
        fs_mod.Client = orig_fs
        if orig_fc is not None:
            sys.modules["database.firestore_client"] = orig_fc
        else:
            sys.modules.pop("database.firestore_client", None)
        sys.modules.pop("main", None)


def test_inventory_sync_circuit_breaker_returns_429():
    """A second call within the 30-minute cooldown must return 429."""
    from datetime import datetime, timezone, timedelta

    pytest.importorskip("fastapi")
    client, main_mod, fake_db, orig_fs, orig_fc, fs_mod = _make_fresh_app()
    try:
        token = _make_admin_token()
        # Patch _verify_admin_token so the token passes auth.
        main_mod._verify_admin_token = lambda t: True

        # Simulate a recent last_run_at (5 minutes ago) in Firestore state doc.
        recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        state_doc = MagicMock()
        state_doc.exists = True
        state_doc.to_dict.return_value = {"last_run_at": recent_ts}
        fake_db.db.collection.return_value.document.return_value.get.return_value = state_doc

        response = client.post(
            "/api/admin/jobs/inventory-sync",
            headers={"X-Admin-Token": token},
        )
        assert response.status_code == 429
        body = response.json()
        assert body["success"] is False
        assert "throttled" in body["error"].lower()
    finally:
        fs_mod.Client = orig_fs
        if orig_fc is not None:
            sys.modules["database.firestore_client"] = orig_fc
        else:
            sys.modules.pop("database.firestore_client", None)
        sys.modules.pop("main", None)
