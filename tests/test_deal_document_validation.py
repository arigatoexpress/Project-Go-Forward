"""Tests for deal -> document validation.

Covers:
  * `validate_for_documents` helper (5 scenarios: complete, missing buyer,
    missing home, missing financing, missing installation address, plus
    extras for accepting Pydantic models and treating numeric 0 as present).
  * `POST /api/deals/{id}/generate-document` returns the structured
    `{"error": "missing_required_fields", "missing": {...}}` envelope with
    a 400 status code when the deal is incomplete.
  * Happy-path requests still flow through to the document engine.

Run: python -m pytest tests/test_deal_document_validation.py -v
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ─────────────────────────────────────────────────────────────────────────────
# Helpers and fixtures shared across tests
# ─────────────────────────────────────────────────────────────────────────────


def _complete_deal_dict() -> dict:
    """A Deal-shaped dict with every required field populated.

    Mirrors the field names emitted by `database.models.Deal.to_document_data`
    so we can confidently round-trip it through the validator.
    """
    return {
        "id": "deal-complete",
        "buyer_first_name": "Alice",
        "buyer_last_name": "Buyer",
        "buyer_address": "123 Main St",
        "buyer_city": "Houston",
        "buyer_zip": "77001",
        "manufacturer": "Clayton",
        "manufacturer_address": "500 Factory Road",
        "manufacturer_city": "Fort Worth",
        "manufacturer_state": "TX",
        "manufacturer_zip": "76101",
        "model": "TruMH 1680",
        "serial_number_1": "SN-12345",
        "sales_price": 80000.0,
        "down_payment": 5000.0,
        "creditor_name": "21st Mortgage",
        "loan_term": "240",
        "apr": "8.5",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Pure-function tests for validate_for_documents
# ─────────────────────────────────────────────────────────────────────────────


class TestValidateForDocuments:
    """Cover the 5+ scenarios called out in the task spec."""

    def test_complete_deal_returns_empty_report(self):
        from database.deal_validation import validate_for_documents

        report = validate_for_documents(_complete_deal_dict())
        assert report == {}, f"Expected empty report for complete deal, got {report!r}"

    def test_missing_buyer_identity(self):
        from database.deal_validation import validate_for_documents

        deal = _complete_deal_dict()
        del deal["buyer_first_name"]
        deal["buyer_last_name"] = "   "  # whitespace counts as missing too

        report = validate_for_documents(deal)
        assert "buyer_identity" in report
        assert sorted(report["buyer_identity"]) == [
            "buyer_first_name",
            "buyer_last_name",
        ]
        # Other groups still satisfied:
        assert "home_identity" not in report
        assert "financing" not in report

    def test_missing_home_identity(self):
        from database.deal_validation import validate_for_documents

        deal = _complete_deal_dict()
        deal["manufacturer"] = None
        deal["model"] = ""
        deal["serial_number_1"] = None

        report = validate_for_documents(deal)
        assert sorted(report["home_identity"]) == [
            "manufacturer",
            "model",
            "serial_number_1",
        ]

    def test_missing_manufacturer_location(self):
        from database.deal_validation import validate_for_documents

        deal = _complete_deal_dict()
        deal["manufacturer_address"] = ""
        deal["manufacturer_city"] = None
        deal["manufacturer_state"] = None
        deal["manufacturer_zip"] = ""

        report = validate_for_documents(deal)
        assert sorted(report["manufacturer_location"]) == [
            "manufacturer_address",
            "manufacturer_city",
            "manufacturer_state",
            "manufacturer_zip",
        ]

    def test_missing_financing(self):
        from database.deal_validation import validate_for_documents

        deal = _complete_deal_dict()
        deal["creditor_name"] = None
        deal["loan_term"] = None
        # apr left intact -> only the two we cleared should be reported

        report = validate_for_documents(deal)
        assert sorted(report["financing"]) == ["creditor_name", "loan_term"]

    def test_missing_installation_address(self):
        from database.deal_validation import validate_for_documents

        deal = _complete_deal_dict()
        del deal["buyer_address"]
        del deal["buyer_city"]
        del deal["buyer_zip"]

        report = validate_for_documents(deal)
        assert sorted(report["installation_address"]) == [
            "buyer_address",
            "buyer_city",
            "buyer_zip",
        ]

    def test_zero_sales_price_is_present_not_missing(self):
        """A `0` value is a real number, not a missing field."""
        from database.deal_validation import validate_for_documents

        deal = _complete_deal_dict()
        deal["sales_price"] = 0
        deal["down_payment"] = 0

        report = validate_for_documents(deal)
        assert "sale_pricing" not in report

    def test_accepts_pydantic_deal_instance(self):
        """The helper should also accept a real Deal pydantic model."""
        from database.deal_validation import validate_for_documents
        from database.models import Deal

        deal = Deal()  # all-default Deal -> nearly everything missing
        report = validate_for_documents(deal)
        # An empty Deal lacks every required group:
        assert set(report.keys()) >= {
            "buyer_identity",
            "installation_address",
            "home_identity",
            "manufacturer_location",
            "sale_pricing",
            "financing",
        }

    def test_deal_document_defaults_use_legal_seller_name(self):
        from database.models import Deal

        data = Deal().to_document_data()

        assert data["seller_name"] == "Texas Home Outlet, Inc."

    def test_rejects_non_dict_non_model_input(self):
        from database.deal_validation import validate_for_documents

        with pytest.raises(TypeError):
            validate_for_documents("not a deal")  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint integration tests
# ─────────────────────────────────────────────────────────────────────────────


def _stub_admin_token(monkeypatch, main_module) -> str:
    """Bypass admin auth by patching the verifier inside `main`.

    The endpoint's `Depends(require_admin)` looks for a header token and
    calls `_verify_admin_token`. We patch that to always return True so the
    test can focus on the validation behaviour rather than auth wiring.
    """
    monkeypatch.setattr(main_module, "_verify_admin_token", lambda token: True)
    return "test-admin-token"


def _build_test_client(monkeypatch, deal_record: dict | None):
    """Import `main` with `_deal_db.get_deal` and `engine_generate_document`
    patched out, then return a TestClient bound to the FastAPI app.

    Strategy: import main fresh, then monkeypatch the module-level globals
    `_deal_db` and `engine_generate_document` on the imported module. This
    is far less invasive than reproducing the full fake-stack from
    `test_api_v1.py` and keeps these tests focused on validation behaviour.
    """
    # `main` mounts StaticFiles on `frontend/dist/assets` at import time, so
    # in a clean worktree (no `npm run build`) the import would crash before
    # the app object is exposed. Mirror the dance from `test_api_v1.py` and
    # ensure stub static files exist.
    frontend_dist = REPO_ROOT / "frontend" / "dist"
    (frontend_dist / "assets").mkdir(parents=True, exist_ok=True)
    index_html = frontend_dist / "index.html"
    if not index_html.exists():
        index_html.write_text("<html><body>test</body></html>")

    # main.py spins up several Firestore-backed services at import time
    # (conversation_memory, chat_history, lead_manager, appointment_manager,
    # and `_db = get_database()`). All of these instantiate
    # `firestore.Client(project=...)` eagerly, which triggers Google
    # Application Default Credentials lookup. CI has no GCP credentials.
    #
    # Strategy: patch `google.cloud.firestore.Client` to a no-op stub so
    # every constructor that lazily touches it succeeds without ADC, then
    # also fake the `database.firestore_client.get_database` callable so
    # `_db` ends up as our test stub.
    fake_db = types.SimpleNamespace(
        get_deal=lambda deal_id: deal_record,
    )

    class _FakeFirestoreClient:  # noqa: D401
        """Stub Firestore client that ignores arguments and never calls GCP."""

        def __init__(self, *args, **kwargs):
            pass

        def collection(self, *args, **kwargs):  # pragma: no cover - unused in these tests
            raise RuntimeError("Tests must not exercise live Firestore queries")

    from google.cloud import firestore as _firestore_module

    monkeypatch.setattr(_firestore_module, "Client", _FakeFirestoreClient)

    fake_firestore_client_module = types.ModuleType("database.firestore_client")
    fake_firestore_client_module.get_database = lambda: fake_db
    fake_firestore_client_module.THODatabase = type("THODatabase", (), {})
    monkeypatch.setitem(sys.modules, "database.firestore_client", fake_firestore_client_module)

    # Force a fresh import so previous tests do not leak module state and
    # so the freshly imported `main` picks up our patched firestore client
    # plus the faked `database.firestore_client`.
    sys.modules.pop("main", None)
    main_module = importlib.import_module("main")

    # The faked `get_database` was used at module import to populate `_db`
    # and `_deal_db`. Reassign explicitly so per-test overrides win even
    # if the import order shifts.
    monkeypatch.setattr(main_module, "_db", fake_db)
    monkeypatch.setattr(main_module, "_deal_db", fake_db)

    # Fail loudly if a document engine is hit when validation should
    # short-circuit. Tests that exercise happy paths replace these.
    def _fail_engine(**_kwargs):
        raise AssertionError("engine_generate_document should not be called when validation fails")

    def _fail_packet_engine(**_kwargs):
        raise AssertionError("engine_generate_packet should not be called when validation fails")

    monkeypatch.setattr(main_module, "engine_generate_document", _fail_engine)
    monkeypatch.setattr(main_module, "engine_generate_packet", _fail_packet_engine)

    token = _stub_admin_token(monkeypatch, main_module)
    client = TestClient(main_module.app)
    return client, main_module, token


class TestGenerateDocumentEndpointValidation:
    """The endpoint should reject incomplete deals with the documented shape."""

    def test_missing_required_fields_returns_400_with_envelope(self, monkeypatch):
        """An almost-empty deal should produce a structured 400 response."""
        deal_record = {
            "id": "deal-empty",
            # Only an id — every other required field is missing.
        }
        client, _main, token = _build_test_client(monkeypatch, deal_record)

        response = client.post(
            "/api/deals/deal-empty/generate-document",
            headers={"X-Admin-Token": token},
            json={"template_name": "TMHA_SalesContract.pdf"},
        )

        assert response.status_code == 400, response.text
        body = response.json()
        assert body["error"] == "missing_required_fields"
        assert "missing" in body and isinstance(body["missing"], dict)
        # Spot-check a couple of groups:
        assert "buyer_identity" in body["missing"]
        assert "home_identity" in body["missing"]
        # Each group is a list of field names.
        for group, fields in body["missing"].items():
            assert isinstance(fields, list), f"{group} should map to a list"
            assert all(isinstance(f, str) for f in fields)

    def test_overrides_can_satisfy_missing_fields(self, monkeypatch):
        """Request-body overrides should be merged in BEFORE validation so a
        thin deal + complete overrides still passes."""
        deal_record = {"id": "deal-thin"}
        client, main_module, token = _build_test_client(monkeypatch, deal_record)

        # Override the engine to record what it received and report success.
        captured = {}

        def _engine(template_name: str, data: dict, **kwargs):
            captured["template_name"] = template_name
            captured["data"] = data
            captured["deal_id"] = kwargs.get("deal_id")
            return {
                "success": True,
                "filename": "overridden.pdf",
                "message": "generated",
            }

        monkeypatch.setattr(main_module, "engine_generate_document", _engine)

        full_overrides = {
            "buyer_first_name": "Alice",
            "buyer_last_name": "Buyer",
            "buyer_address": "1 Test Way",
            "buyer_city": "Houston",
            "buyer_zip": "77001",
            "manufacturer": "Clayton",
            "manufacturer_address": "500 Factory Road",
            "manufacturer_city": "Fort Worth",
            "manufacturer_state": "TX",
            "manufacturer_zip": "76101",
            "model": "TruMH",
            "serial_number_1": "SN-1",
            "sales_price": 50000,
            "down_payment": 1000,
            "creditor_name": "21st Mortgage",
            "loan_term": "240",
            "apr": "8.5",
        }

        response = client.post(
            "/api/deals/deal-thin/generate-document",
            headers={"X-Admin-Token": token},
            json={
                "template_name": "TMHA_SalesContract.pdf",
                "overrides": full_overrides,
            },
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True
        assert body["filename"] == "overridden.pdf"
        # The engine should have been called and received our overrides.
        assert captured["template_name"] == "TMHA_SalesContract.pdf"
        for key, value in full_overrides.items():
            assert captured["data"].get(key) == value, f"{key} not propagated"

    def test_happy_path_full_deal_calls_engine(self, monkeypatch):
        """A complete Deal record should bypass validation and reach the engine."""
        deal_record = _complete_deal_dict()
        client, main_module, token = _build_test_client(monkeypatch, deal_record)

        engine_calls = {"count": 0}

        def _engine(template_name: str, data: dict, **kwargs):
            engine_calls["count"] += 1
            engine_calls["template_name"] = template_name
            engine_calls["deal_id"] = kwargs.get("deal_id")
            return {
                "success": True,
                "filename": "complete.pdf",
                "message": "generated",
            }

        monkeypatch.setattr(main_module, "engine_generate_document", _engine)

        response = client.post(
            "/api/deals/deal-complete/generate-document",
            headers={"X-Admin-Token": token},
            json={"template_name": "TMHA_SalesContract.pdf"},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True
        assert body["filename"] == "complete.pdf"
        assert body["download_url"].endswith("/complete.pdf")
        assert engine_calls["count"] == 1
        assert engine_calls["template_name"] == "TMHA_SalesContract.pdf"

    def test_unknown_deal_id_unchanged_behaviour(self, monkeypatch):
        """An unknown deal still returns the existing 'Deal not found' shape;
        validation must not change pre-existing not-found semantics."""
        client, _main, token = _build_test_client(monkeypatch, deal_record=None)

        response = client.post(
            "/api/deals/missing/generate-document",
            headers={"X-Admin-Token": token},
            json={"template_name": "TMHA_SalesContract.pdf"},
        )

        # Status 200 with success=False is the pre-existing behaviour for
        # missing deals — we are deliberately not changing it.
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is False
        assert "Deal not found" in body["error"]


class TestGeneratePacketEndpointValidation:
    """Deal-based packet generation should mirror single-document validation."""

    def test_missing_required_fields_returns_400_with_envelope(self, monkeypatch):
        deal_record = {"id": "deal-empty"}
        client, _main, token = _build_test_client(monkeypatch, deal_record)

        response = client.post(
            "/api/deals/deal-empty/generate-packet",
            headers={"X-Admin-Token": token},
            json={"packet_name": "standard_closing"},
        )

        assert response.status_code == 400, response.text
        body = response.json()
        assert body["error"] == "missing_required_fields"
        assert "buyer_identity" in body["missing"]
        assert "home_identity" in body["missing"]

    def test_overrides_can_satisfy_missing_fields_for_packet(self, monkeypatch):
        deal_record = {"id": "deal-thin"}
        client, main_module, token = _build_test_client(monkeypatch, deal_record)

        captured = {}

        def _engine(packet_name: str, data: dict, **kwargs):
            captured["packet_name"] = packet_name
            captured["data"] = data
            captured["deal_id"] = kwargs.get("deal_id")
            return {
                "success": True,
                "filename": "packet.pdf",
                "message": "ok",
                "page_count": 3,
                "documents_included": ["TMHA_SalesContract.pdf"],
            }

        monkeypatch.setattr(main_module, "engine_generate_packet", _engine)

        full_overrides = {
            "buyer_first_name": "Alice",
            "buyer_last_name": "Buyer",
            "buyer_address": "1 Test Way",
            "buyer_city": "Houston",
            "buyer_zip": "77001",
            "manufacturer": "Clayton",
            "manufacturer_address": "500 Factory Road",
            "manufacturer_city": "Fort Worth",
            "manufacturer_state": "TX",
            "manufacturer_zip": "76101",
            "model": "TruMH",
            "serial_number_1": "SN-1",
            "sales_price": 50000,
            "down_payment": 1000,
            "creditor_name": "21st Mortgage",
            "loan_term": "240",
            "apr": "8.5",
        }

        response = client.post(
            "/api/deals/deal-thin/generate-packet",
            headers={"X-Admin-Token": token},
            json={
                "packet_name": "standard_closing",
                "overrides": full_overrides,
            },
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True
        assert body["filename"] == "packet.pdf"
        assert captured["packet_name"] == "standard_closing"
        for key, value in full_overrides.items():
            assert captured["data"].get(key) == value, f"{key} not propagated"
