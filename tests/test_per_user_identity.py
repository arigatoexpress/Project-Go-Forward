"""Tests for per-user admin identity scaffold.

Covers:
  - User model serialisation and validation  (no server needed)
  - JWT token helpers (_create_admin_jwt, _decode_admin_token)  (no server needed)
  - /api/admin/verify with and without email  (FastAPI TestClient)
  - /api/admin/me with per-user JWT and legacy token  (FastAPI TestClient)

Run: python -m pytest tests/test_per_user_identity.py -v
"""

from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import importlib
import importlib.util
import json
import os
import struct
import sys
import time
import types
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from enum import Enum as _Enum
from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parent.parent


# Minimal Pydantic stubs used as FastAPI body types — MagicMock() breaks FastAPI
class _DummyBody(BaseModel):
    model_config = {"extra": "allow"}


class _FakeDealStatus(str, _Enum):
    PENDING = "pending"
    APPROVED = "approved"
    CONTRACT = "contract"
    FUNDED = "funded"
    COMPLETE = "complete"
    DENIED = "denied"
    ARCHIVED = "archived"


class _FakeDeal(BaseModel):
    model_config = {"extra": "allow"}

    id: str = ""
    status: str = "pending"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PIN = "test-pin-per-user"
PIN_HASH = hashlib.sha256(PIN.encode()).hexdigest()
JWT_SECRET = hashlib.sha256(f"sapphire-jwt-{PIN_HASH[:16]}".encode()).digest()

# ── Load database.models without triggering firestore_client import ────────────

def _load_db_models():
    spec = importlib.util.spec_from_file_location(
        "_test_db_models",
        REPO_ROOT / "database" / "models.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_db_models = _load_db_models()
Role = _db_models.Role
User = _db_models.User


# ── Shared load_app helper (mirrors test_api_v1.create_client) ─────────────────

class _FakeLogger:
    def info(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def request(self, *a, **kw): pass
    def response(self, *a, **kw): pass


class _FakeDB:
    def collection(self, *a, **kw): return MagicMock()


def _load_app(monkeypatch):
    """Import main.py with all heavy deps mocked out — matching test_api_v1 pattern."""
    # Ensure frontend dist exists (main.py mounts it at module load time)
    dist = REPO_ROOT / "frontend" / "dist"
    (dist / "assets").mkdir(parents=True, exist_ok=True)
    if not (dist / "index.html").exists():
        (dist / "index.html").write_text("<html><body>test</body></html>")

    monkeypatch.setenv("ADMIN_PIN_HASH", PIN_HASH)

    fake_logger = _FakeLogger()
    fake_db = _FakeDB()

    # Remove previously cached main so env vars are picked up fresh
    for mod in list(sys.modules):
        if mod in ("main",) or mod.startswith("tools.") or mod in (
            "structured_logging", "conversation_memory", "chat_history",
            "lead_management", "appointment_manager", "email_service",
            "database.firestore_client", "database.models",
            "schemas.document_schemas", "pm_routes",
        ):
            sys.modules.pop(mod, None)

    def _stub(name, **attrs):
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        monkeypatch.setitem(sys.modules, name, m)

    _stub("structured_logging", logger=fake_logger)
    _stub("conversation_memory", ConversationMemory=MagicMock())
    _stub("chat_history", ChatHistory=MagicMock())
    _stub("lead_management", LeadManager=MagicMock(), Lead=MagicMock())
    _stub("appointment_manager", AppointmentManager=MagicMock(), Appointment=MagicMock())
    _stub(
        "email_service",
        send_appointment_confirmation=lambda *a, **k: {},
        send_lead_welcome=lambda *a, **k: {},
        send_deal_status_update=lambda *a, **k: {},
        send_custom_email=lambda *a, **k: {},
        get_email_log=lambda *a, **k: [],
        notify_new_lead=lambda *a, **k: {},
        notify_new_appointment=lambda *a, **k: {},
    )
    _stub("database.firestore_client", get_database=lambda: fake_db, THODatabase=MagicMock())

    # Provide a complete database.models stub — must include everything that
    # database/__init__.py re-exports (Customer, Property, etc.) plus our new types.
    db_models_stub = types.ModuleType("database.models")
    for attr in (
        "User", "Role",
        "Customer", "CustomerStatus",
        "Property",
        "Inventory", "InventoryStatus",
        "Sale", "Lease", "TaxPayment", "ServiceRequest",
        "WorkflowState", "TaskPriority", "ProjectType",
        "Project", "Task", "Cycle", "Activity", "View",
    ):
        setattr(db_models_stub, attr, getattr(_db_models, attr, MagicMock()))
    db_models_stub.Deal = _FakeDeal
    db_models_stub.DealStatus = _FakeDealStatus
    monkeypatch.setitem(sys.modules, "database.models", db_models_stub)
    # Also stub database/__init__ so it doesn't re-run and fail on firestore_client
    db_pkg_stub = types.ModuleType("database")
    for attr in dir(db_models_stub):
        if not attr.startswith("_"):
            setattr(db_pkg_stub, attr, getattr(db_models_stub, attr))
    db_pkg_stub.THODatabase = MagicMock()
    db_pkg_stub.get_database = lambda: fake_db
    monkeypatch.setitem(sys.modules, "database", db_pkg_stub)
    # Stub deal_validation which main.py imports separately
    deal_val_stub = types.ModuleType("database.deal_validation")
    deal_val_stub.validate_for_documents = lambda *a, **k: {"valid": True, "errors": []}
    monkeypatch.setitem(sys.modules, "database.deal_validation", deal_val_stub)

    _stub("schemas.document_schemas",
          SalesContractForm=_DummyBody,
          GenerateDocumentRequest=_DummyBody,
          GeneratePacketRequest=_DummyBody)
    _stub("tools.document_tools",
          OUTPUT_DIR=str(REPO_ROOT / "tmp_test_output"),
          generate_sales_contract_pdf=lambda *a, **k: {"success": True, "filename": "ok.pdf"},
          generate_work_order_pdf=lambda *a, **k: {"success": True},
          generate_service_ticket=lambda *a, **k: {"success": True},
          generate_customer_email=lambda *a, **k: {"success": True},
          download_from_gcs=lambda *a, **k: False,
          list_gcs_documents=lambda *a, **k: [])
    _stub("tools.document_engine",
          generate_document=lambda *a, **k: {"success": True, "file_path": "d.pdf", "filename": "d.pdf"},
          generate_packet=lambda *a, **k: {"success": True, "file_path": "p.pdf", "filename": "p.pdf"},
          generate_batch=lambda *a, **k: {"success": True},
          list_available_templates=lambda: [],
          list_available_packets=lambda: [],
          get_template_fields=lambda *a, **k: [],
          get_all_field_definitions=lambda: [])
    _stub("tools.inventory_tools", search_inventory=lambda *a, **k: [])
    _stub("tools.service_tools",
          check_warranty_status=lambda *a, **k: {},
          analyze_defect_image=lambda *a, **k: {},
          generate_invoice_pdf=lambda *a, **k: {"success": True})
    _stub("tools.crm_tools",
          book_appointment=lambda *a, **k: {},
          get_business_hours=lambda *a, **k: {},
          save_lead=lambda *a, **k: {},
          check_available_slots=lambda *a, **k: [],
          cancel_appointment=lambda *a, **k: {"success": True},
          get_current_datetime=lambda *a, **k: datetime.now(timezone.utc).isoformat())
    _stub("tools.marketing_tools",
          generate_content_script=lambda **k: {"success": True},
          get_trending_content_ideas=lambda **k: [],
          schedule_social_post=lambda **k: {"success": True},
          analyze_content_performance=lambda **k: {},
          generate_ad_image=lambda **k: {"success": True},
          get_inventory_for_ads=lambda **k: {"success": True, "homes": [], "total_inventory": 0},
          GENERATED_ADS_DIR=str(REPO_ROOT / "generated_ads"))
    _stub("tools.asset_scraper",
          get_all_assets=lambda *a, **k: [],
          get_assets_for_home=lambda *a, **k: None,
          PROPERTY_ASSETS={},
          get_matterport_url=lambda *a, **k: None)
    _stub("tools.video_generator",
          generate_ad_video=lambda *a, **k: {"success": True, "filename": "video.mp4"},
          GENERATED_VIDEOS_DIR=str(REPO_ROOT / "generated_videos"))
    _stub("pm_routes", router=APIRouter())

    importlib.invalidate_caches()
    m = importlib.import_module("main")
    # Patch token vars to use our test PIN
    m.ADMIN_PIN_HASH = PIN_HASH
    m._JWT_SECRET = JWT_SECRET
    return m


# ── Inline token helpers (no main.py import needed) ───────────────────────────

ADMIN_TOKEN_TTL = 7200


def _create_jwt(user_id: str, secret: bytes = JWT_SECRET, ttl: int = ADMIN_TOKEN_TTL) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip("=")
    payload_data = json.dumps({"exp": int(time.time()) + ttl, "sub": user_id}, separators=(",", ":"))
    payload = base64.urlsafe_b64encode(payload_data.encode()).decode().rstrip("=")
    signing_input = f"{header}.{payload}".encode()
    sig = base64.urlsafe_b64encode(
        _hmac.new(secret, signing_input, hashlib.sha256).digest()
    ).decode().rstrip("=")
    return f"{header}.{payload}.{sig}"


def _create_legacy(secret: bytes = JWT_SECRET, ttl: int = ADMIN_TOKEN_TTL) -> str:
    expires = int(time.time()) + ttl
    payload = struct.pack(">Q", expires)
    sig = _hmac.new(secret, payload, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(payload + sig).decode().rstrip("=")


def _decode(token: str, secret: bytes = JWT_SECRET) -> str | None:
    if not token:
        return None
    if token.count(".") == 2:
        try:
            header_b64, payload_b64, sig_b64 = token.split(".")
            signing_input = f"{header_b64}.{payload_b64}".encode()
            expected_sig = _hmac.new(secret, signing_input, hashlib.sha256).digest()
            pad = 4 - len(sig_b64) % 4
            actual_sig = base64.urlsafe_b64decode(sig_b64 + ("=" * pad if pad != 4 else ""))
            if not _hmac.compare_digest(expected_sig, actual_sig):
                return None
            pad = 4 - len(payload_b64) % 4
            claims = json.loads(base64.urlsafe_b64decode(payload_b64 + ("=" * pad if pad != 4 else "")))
            if time.time() >= claims.get("exp", 0):
                return None
            return claims.get("sub") or "admin"
        except Exception:
            return None
    # Legacy binary
    try:
        pad = 4 - len(token) % 4
        raw = base64.urlsafe_b64decode(token + ("=" * pad if pad != 4 else ""))
        if len(raw) != 24:
            return None
        p, s = raw[:8], raw[8:]
        expected = _hmac.new(secret, p, hashlib.sha256).digest()[:16]
        if not _hmac.compare_digest(s, expected):
            return None
        exp = struct.unpack(">Q", p)[0]
        return "admin" if time.time() < exp else None
    except Exception:
        return None


# ═══════════════════════════════════════════════��══════════════════════════════
# 1. User model
# ══════════════════════════════════════════════════��═══════════════════════════

class TestUserModel:
    def test_valid_user_serialises(self):
        u = User(email="mark@tho.com", display_name="Mark")
        assert u.email == "mark@tho.com"
        assert u.display_name == "Mark"
        assert u.role == Role.STAFF.value
        assert u.disabled is False
        assert uuid.UUID(u.id)

    def test_email_normalised_to_lowercase(self):
        u = User(email="Mark@THO.COM", display_name="Mark")
        assert u.email == "mark@tho.com"

    def test_invalid_email_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            User(email="not-an-email", display_name="Bad")

    def test_role_enum_admin_and_staff(self):
        assert Role.ADMIN.value == "admin"
        assert Role.STAFF.value == "staff"

    def test_admin_role_roundtrips(self):
        u = User(email="celeste@tho.com", display_name="Celeste", role=Role.ADMIN)
        assert u.role == "admin"

    def test_disabled_defaults_false(self):
        u = User(email="ben@tho.com", display_name="Ben")
        assert u.disabled is False


# ═════════════════════════════════════════════════════��════════════════════���═══
# 2. JWT token helpers (inline — no main.py import required)
# ═════════════════════════════════════════════════════════════════════════════��

class TestAdminJWT:
    def test_jwt_has_three_dot_separated_parts(self):
        assert _create_jwt("user-123").count(".") == 2

    def test_jwt_sub_claim_matches_user_id(self):
        uid = str(uuid.uuid4())
        assert _decode(_create_jwt(uid)) == uid

    def test_jwt_tampered_signature_rejected(self):
        parts = _create_jwt("user-abc").split(".")
        parts[2] = parts[2][:-4] + "XXXX"
        assert _decode(".".join(parts)) is None

    def test_jwt_expired_token_rejected(self):
        token = _create_jwt("user-exp", ttl=-1)  # already expired
        assert _decode(token) is None

    def test_legacy_binary_token_returns_admin(self):
        legacy = _create_legacy()
        assert "." not in legacy
        assert _decode(legacy) == "admin"

    def test_garbage_strings_return_none(self):
        assert _decode("not.a.real.token") is None
        assert _decode("garbage") is None
        assert _decode("") is None

    def test_wrong_key_rejected(self):
        other_secret = hashlib.sha256(b"wrong-key").digest()
        token = _create_jwt("user-x", secret=other_secret)
        assert _decode(token, secret=JWT_SECRET) is None


# ════════════════════���═════════════════════════════════════════════════════════
# 3. /api/admin/verify with email
# ═════════════════════════════════════════════════���═════════════════════════��══

class TestVerifyWithEmail:
    def test_known_email_returns_user_id_and_jwt(self, monkeypatch):
        main = _load_app(monkeypatch)
        client = TestClient(main.app)
        fake_id = str(uuid.uuid4())
        fake_user = User(id=fake_id, email="mark@tho.com", display_name="Mark", role=Role.ADMIN)
        with patch("auth.users.get_or_create_user", return_value=fake_user):
            resp = client.post("/api/admin/verify", json={"pin": PIN, "email": "mark@tho.com"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["user_id"] == fake_id
        assert body["token"].count(".") == 2  # JWT format

    def test_unrecognised_email_rejected_with_403(self, monkeypatch):
        main = _load_app(monkeypatch)
        client = TestClient(main.app)
        with patch("auth.users.get_or_create_user", return_value=None):
            resp = client.post("/api/admin/verify", json={"pin": PIN, "email": "hacker@evil.com"})
        assert resp.status_code == 403
        assert resp.json()["success"] is False

    def test_wrong_pin_always_rejected_regardless_of_email(self, monkeypatch):
        main = _load_app(monkeypatch)
        client = TestClient(main.app)
        resp = client.post("/api/admin/verify", json={"pin": "wrong-pin", "email": "mark@tho.com"})
        assert resp.status_code == 401

    def test_no_email_uses_legacy_binary_token(self, monkeypatch):
        main = _load_app(monkeypatch)
        client = TestClient(main.app)
        resp = client.post("/api/admin/verify", json={"pin": PIN})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "user_id" not in body          # legacy path doesn't return user_id
        assert body["token"].count(".") == 0  # binary format


# ═════════════════════════════════════════════���════════════════════════════════
# 4. /api/admin/me
# ══════════════════════════════════════════════════════════════════════════════

class TestAdminMe:
    def test_per_user_jwt_returns_full_identity(self, monkeypatch):
        main = _load_app(monkeypatch)
        client = TestClient(main.app)
        fake_id = str(uuid.uuid4())
        fake_user = User(id=fake_id, email="mark@tho.com", display_name="Mark", role=Role.ADMIN)
        token = _create_jwt(fake_id)
        with patch("auth.users.get_user_by_id", return_value=fake_user):
            resp = client.get("/api/admin/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_id"] == fake_id
        assert body["email"] == "mark@tho.com"
        assert body["display_name"] == "Mark"
        assert body["identity_source"] == "per_user_jwt"

    def test_legacy_token_returns_admin_identity(self, monkeypatch):
        main = _load_app(monkeypatch)
        client = TestClient(main.app)
        token = _create_legacy()
        resp = client.get("/api/admin/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_id"] == "admin"
        assert body["identity_source"] == "legacy_pin"

    def test_unauthenticated_request_returns_401(self, monkeypatch):
        main = _load_app(monkeypatch)
        client = TestClient(main.app)
        assert client.get("/api/admin/me").status_code == 401

    def test_missing_user_record_returns_bare_user_id(self, monkeypatch):
        main = _load_app(monkeypatch)
        client = TestClient(main.app)
        orphan_id = str(uuid.uuid4())
        token = _create_jwt(orphan_id)
        with patch("auth.users.get_user_by_id", return_value=None):
            resp = client.get("/api/admin/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_id"] == orphan_id
        assert body["email"] is None
        assert body["identity_source"] == "per_user_jwt"
