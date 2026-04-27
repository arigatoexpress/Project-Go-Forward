"""Tests for the /api/v1/* partner integration surface.

Run: python -m pytest tests/test_api_v1.py -v
"""

from __future__ import annotations

import importlib
import os
import sys
import types
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import BaseModel


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class FakeStructuredLogger:
    """Collect structured log calls for assertions."""

    def __init__(self):
        self.entries: list[dict] = []

    def _record(self, level: str, message: str, **kwargs):
        entry = {"level": level, "message": message, **kwargs}
        self.entries.append(entry)

    def info(self, message: str, **kwargs):
        self._record("info", message, **kwargs)

    def warning(self, message: str, **kwargs):
        self._record("warning", message, **kwargs)

    def error(self, message: str, **kwargs):
        self._record("error", message, **kwargs)

    def request(self, request_id: str, user_id: str, session_id: str, message: str):
        self._record(
            "info",
            "Incoming request",
            request_id=request_id,
            user_id=user_id,
            session_id=session_id,
            message_preview=message,
        )

    def response(self, request_id: str, response_length: int, duration_ms: float):
        self._record(
            "info",
            "Response sent",
            request_id=request_id,
            response_length=response_length,
            duration_ms=duration_ms,
        )


@dataclass
class FakeLead:
    lead_id: str
    user_id: str
    session_id: str
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    budget_max: float | None = None
    home_type: str | None = None
    homes_viewed: list[str] = field(default_factory=list)
    appointment_requested: bool = False
    financing_discussed: bool = False
    source: str = "chat"
    status: str = "new"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "lead_id": self.lead_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "name": self.name,
            "email": self.email,
            "phone": self.phone,
            "bedrooms": self.bedrooms,
            "bathrooms": self.bathrooms,
            "budget_max": self.budget_max,
            "home_type": self.home_type,
            "homes_viewed": self.homes_viewed,
            "appointment_requested": self.appointment_requested,
            "financing_discussed": self.financing_discussed,
            "source": self.source,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class FakeAppointment:
    appointment_id: str = "appt-1"
    phone: str = "5551112222"
    date: str = "2026-04-22"
    time_slot: str = "10:00"


class FakeDocSnapshot:
    def __init__(self, doc_id: str, data: dict):
        self.id = doc_id
        self._data = data

    def to_dict(self) -> dict:
        return dict(self._data)


class FakeDocumentRef:
    def __init__(self, store: dict[str, dict], doc_id: str):
        self._store = store
        self.id = doc_id

    def set(self, data: dict, merge: bool = False):
        if merge and self.id in self._store:
            merged = dict(self._store[self.id])
            merged.update(data)
            self._store[self.id] = merged
        else:
            self._store[self.id] = dict(data)

    def get(self):
        data = self._store.get(self.id)
        if data is None:
            return types.SimpleNamespace(exists=False, id=self.id, to_dict=lambda: None)
        return types.SimpleNamespace(exists=True, id=self.id, to_dict=lambda: dict(data))


class FakeQuery:
    """Tiny subset of Firestore query chaining used by v1 endpoints."""

    def __init__(self, store: dict, filters: list[tuple[str, str, object]] | None = None, limit: int | None = None):
        self._store = store
        self._filters = filters or []
        self._limit = limit

    def where(self, field: str, op: str, value: object) -> "FakeQuery":
        return FakeQuery(self._store, self._filters + [(field, op, value)], self._limit)

    def limit(self, n: int) -> "FakeQuery":
        return FakeQuery(self._store, list(self._filters), n)

    @staticmethod
    def _resolve(doc: dict, field: str):
        cur: object = doc
        for part in field.split("."):
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
        return cur

    def get(self):
        results = []
        for doc_id, data in self._store.items():
            matched = all(
                (self._resolve(data, f) == v) if op == "==" else False
                for f, op, v in self._filters
            )
            if matched:
                results.append(FakeDocSnapshot(doc_id, data))
                if self._limit is not None and len(results) >= self._limit:
                    break
        return results


class FakeCollection:
    def __init__(self, collections: dict[str, dict[str, dict]], name: str):
        self._collections = collections
        self._name = name
        self._store = self._collections.setdefault(name, {})

    def stream(self):
        for doc_id, data in self._store.items():
            yield FakeDocSnapshot(doc_id, data)

    def document(self, doc_id: str | None = None):
        doc_id = doc_id or f"{self._name}-{len(self._store) + 1}"
        return FakeDocumentRef(self._store, doc_id)

    def where(self, field: str, op: str, value: object) -> FakeQuery:
        return FakeQuery(self._store).where(field, op, value)


class FakeFirestoreDB:
    def __init__(self, collections: dict[str, dict[str, dict]]):
        self.collections = collections

    def collection(self, name: str) -> FakeCollection:
        return FakeCollection(self.collections, name)


class FakeTHODatabase:
    """Small in-memory stand-in for THODatabase."""

    def __init__(self):
        self.collections: dict[str, dict[str, dict]] = {
            "customers": {
                "cust-1": {
                    "id": "cust-1",
                    "legacy_id": "legacy-1",
                    "legacy_source": "manual",
                    "full_name": "Alice Example",
                    "phone": "555-111-2222",
                    "email": "alice@example.com",
                    "status": "LEAD",
                    "city": "Austin",
                    "state": "TX",
                    "ssn_masked": "***-**-6789",
                    "ssn_hash": "hash-1",
                    "created_at": "2026-04-21T12:00:00",
                    "updated_at": "2026-04-21T12:00:00",
                }
            },
            "inventory": {
                "inv-1": {
                    "id": "inv-1",
                    "model_name": "Model One",
                    "manufacturer": "Champion",
                    "status": "AVAILABLE",
                    "msrp": 75000,
                    "bedrooms": 3,
                    "bathrooms": 2,
                    "sqft": 1200,
                },
                "inv-2": {
                    "id": "inv-2",
                    "model_name": "Model Two",
                    "manufacturer": "Clayton",
                    "status": "SOLD",
                    "msrp": 92000,
                    "bedrooms": 4,
                    "bathrooms": 2,
                    "sqft": 1500,
                },
            },
            "deals": {
                "deal-1": {"id": "deal-1", "status": "pending"},
                "deal-2": {"id": "deal-2", "status": "funded"},
                "deal-3": {"id": "deal-3", "status": "funded"},
            },
            "activities": {},
        }
        self.db = FakeFirestoreDB(self.collections)

    def get_customer(self, customer_id: str):
        direct = self.collections["customers"].get(customer_id)
        if direct:
            return dict(direct)
        for customer in self.collections["customers"].values():
            if customer.get("legacy_id") == customer_id:
                return dict(customer)
        return None

    def search_customers(self, query_text=None, status=None, limit=50):
        customers = [dict(customer) for customer in self.collections["customers"].values()]
        if status:
            customers = [customer for customer in customers if customer.get("status") == status.upper()]
        if query_text:
            needle = query_text.lower()
            customers = [
                customer
                for customer in customers
                if needle in " ".join(
                    [
                        customer.get("full_name", ""),
                        customer.get("email", ""),
                        customer.get("phone", ""),
                        customer.get("legacy_id", ""),
                    ]
                ).lower()
            ]
        return customers[:limit]

    def create_customer(self, data: dict, doc_id: str | None = None):
        doc_id = doc_id or f"cust-{len(self.collections['customers']) + 1}"
        record = dict(data)
        record["id"] = doc_id
        record.setdefault("created_at", "2026-04-22T00:00:00")
        record.setdefault("updated_at", "2026-04-22T00:00:00")
        self.collections["customers"][doc_id] = record
        return doc_id

    def search_inventory(self, status=None, manufacturer=None, limit=10, **_kwargs):
        inventory = [dict(item) for item in self.collections["inventory"].values()]
        if status:
            inventory = [item for item in inventory if item.get("status") == status]
        if manufacturer:
            inventory = [item for item in inventory if item.get("manufacturer") == manufacturer]
        return inventory[:limit]

    def count_customers(self):
        by_status: dict[str, int] = {}
        for customer in self.collections["customers"].values():
            current = customer.get("status", "UNKNOWN")
            by_status[current] = by_status.get(current, 0) + 1
        return {"total": len(self.collections["customers"]), "by_status": by_status}


class FakeLeadManager:
    def __init__(self, project_id=None):
        self.project_id = project_id
        self.leads = [
            FakeLead(
                lead_id="lead-1",
                user_id="user-1",
                session_id="session-1",
                name="Lead Name",
                email="lead@example.com",
                phone="555-999-8888",
                bedrooms=3,
                bathrooms=2,
                budget_max=80000,
                home_type="singlewide",
                status="new",
            )
        ]

    async def create_lead(self, lead):
        self.leads.append(lead)
        return lead

    async def update_lead(self, lead):
        for idx, existing in enumerate(self.leads):
            if existing.lead_id == lead.lead_id:
                self.leads[idx] = lead
                return lead
        self.leads.append(lead)
        return lead

    async def get_lead(self, lead_id: str):
        for lead in self.leads:
            if lead.lead_id == lead_id:
                return lead
        return None

    async def get_lead_by_session(self, session_id: str):
        for lead in self.leads:
            if lead.session_id == session_id:
                return lead
        return None

    async def list_leads(self, status=None, limit=100):
        leads = list(self.leads)
        if status:
            leads = [lead for lead in leads if lead.status == status]
        return leads[:limit]


class FakeAppointmentManager:
    def __init__(self, project_id=None):
        self.project_id = project_id

    async def list_appointments(self, status=None, limit=100):
        return []

    async def get_appointment(self, appointment_id: str):
        return FakeAppointment(appointment_id=appointment_id)

    async def cancel_appointment(self, appointment_id: str):
        return FakeAppointment(appointment_id=appointment_id)


class FakeConversationMemory:
    def __init__(self, project_id=None):
        self.project_id = project_id

    def get_context_prompt(self, user_id: str):
        return ""

    def update_from_interaction(self, user_id: str, user_message: str, assistant_response: str):
        return None


class FakeChatHistory:
    def __init__(self, project_id=None):
        self.project_id = project_id

    def save_conversation_turn(self, session_id: str, user_message: str, assistant_response: str):
        return None

    def get_session_messages(self, session_id: str):
        return []

    def list_sessions(self, limit: int = 50):
        return []


class DummyBodyModel(BaseModel):
    pass


class FakeDeal:
    def __init__(self, **data):
        self._data = data

    def model_dump(self):
        return dict(self._data)


class FakeDealStatus(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    CONTRACT = "contract"
    FUNDED = "funded"
    COMPLETE = "complete"
    DENIED = "denied"
    ARCHIVED = "archived"


def load_app(monkeypatch, tho_api_key: str | None = "tho-secret", rate_limit_rpm: str = "60"):
    """Import main.py with lightweight stubs for its eager imports."""
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setenv("RATE_LIMIT_RPM", rate_limit_rpm)
    if tho_api_key is None:
        monkeypatch.delenv("THO_API_KEY", raising=False)
    else:
        monkeypatch.setenv("THO_API_KEY", tho_api_key)

    frontend_dist = REPO_ROOT / "frontend" / "dist"
    (frontend_dist / "assets").mkdir(parents=True, exist_ok=True)
    index_html = frontend_dist / "index.html"
    if not index_html.exists():
        index_html.write_text("<html><body>test</body></html>")

    fake_logger = FakeStructuredLogger()
    fake_db = FakeTHODatabase()

    modules_to_clear = [
        "main",
        "structured_logging",
        "conversation_memory",
        "chat_history",
        "lead_management",
        "appointment_manager",
        "email_service",
        "database.firestore_client",
        "database.models",
        "schemas.document_schemas",
        "tools.document_tools",
        "tools.document_engine",
        "tools.inventory_tools",
        "tools.service_tools",
        "tools.crm_tools",
        "tools.marketing_tools",
        "tools.asset_scraper",
        "tools.video_generator",
        "pm_routes",
    ]
    for module_name in modules_to_clear:
        sys.modules.pop(module_name, None)

    structured_logging_module = types.ModuleType("structured_logging")
    structured_logging_module.logger = fake_logger
    monkeypatch.setitem(sys.modules, "structured_logging", structured_logging_module)

    conversation_memory_module = types.ModuleType("conversation_memory")
    conversation_memory_module.ConversationMemory = FakeConversationMemory
    monkeypatch.setitem(sys.modules, "conversation_memory", conversation_memory_module)

    chat_history_module = types.ModuleType("chat_history")
    chat_history_module.ChatHistory = FakeChatHistory
    monkeypatch.setitem(sys.modules, "chat_history", chat_history_module)

    lead_management_module = types.ModuleType("lead_management")
    lead_management_module.LeadManager = FakeLeadManager
    lead_management_module.Lead = FakeLead
    monkeypatch.setitem(sys.modules, "lead_management", lead_management_module)

    appointment_manager_module = types.ModuleType("appointment_manager")
    appointment_manager_module.AppointmentManager = FakeAppointmentManager
    appointment_manager_module.Appointment = FakeAppointment
    monkeypatch.setitem(sys.modules, "appointment_manager", appointment_manager_module)

    email_service_module = types.ModuleType("email_service")
    email_service_module.send_appointment_confirmation = lambda *args, **kwargs: {"success": True}
    email_service_module.send_lead_welcome = lambda *args, **kwargs: {"success": True}
    email_service_module.send_deal_status_update = lambda *args, **kwargs: {"success": True}
    email_service_module.send_custom_email = lambda *args, **kwargs: {"success": True}
    email_service_module.get_email_log = lambda *args, **kwargs: []
    email_service_module.notify_new_lead = lambda *args, **kwargs: {"success": True}
    email_service_module.notify_new_appointment = lambda *args, **kwargs: {"success": True}
    monkeypatch.setitem(sys.modules, "email_service", email_service_module)

    firestore_client_module = types.ModuleType("database.firestore_client")
    firestore_client_module.get_database = lambda: fake_db
    monkeypatch.setitem(sys.modules, "database.firestore_client", firestore_client_module)

    database_models_module = types.ModuleType("database.models")
    database_models_module.Deal = FakeDeal
    database_models_module.DealStatus = FakeDealStatus
    monkeypatch.setitem(sys.modules, "database.models", database_models_module)

    document_schemas_module = types.ModuleType("schemas.document_schemas")
    document_schemas_module.SalesContractForm = DummyBodyModel
    document_schemas_module.GenerateDocumentRequest = DummyBodyModel
    document_schemas_module.GeneratePacketRequest = DummyBodyModel
    monkeypatch.setitem(sys.modules, "schemas.document_schemas", document_schemas_module)

    document_tools_module = types.ModuleType("tools.document_tools")
    document_tools_module.OUTPUT_DIR = str(REPO_ROOT / "tmp_test_output")
    document_tools_module.generate_sales_contract_pdf = lambda *args, **kwargs: {"success": True, "filename": "ok.pdf"}
    document_tools_module.generate_work_order_pdf = lambda *args, **kwargs: {"success": True}
    document_tools_module.generate_service_ticket = lambda *args, **kwargs: {"success": True}
    document_tools_module.generate_customer_email = lambda *args, **kwargs: {"success": True}
    document_tools_module.download_from_gcs = lambda *args, **kwargs: False
    document_tools_module.list_gcs_documents = lambda *args, **kwargs: []
    monkeypatch.setitem(sys.modules, "tools.document_tools", document_tools_module)

    document_engine_module = types.ModuleType("tools.document_engine")
    document_engine_module.generate_document = lambda *args, **kwargs: {"success": True, "file_path": "doc.pdf", "filename": "doc.pdf"}
    document_engine_module.generate_packet = lambda *args, **kwargs: {"success": True, "file_path": "packet.pdf", "filename": "packet.pdf"}
    document_engine_module.generate_batch = lambda *args, **kwargs: {"success": True}
    document_engine_module.list_available_templates = lambda: []
    document_engine_module.list_available_packets = lambda: []
    document_engine_module.get_template_fields = lambda *args, **kwargs: []
    document_engine_module.get_all_field_definitions = lambda: []
    monkeypatch.setitem(sys.modules, "tools.document_engine", document_engine_module)

    inventory_tools_module = types.ModuleType("tools.inventory_tools")
    inventory_tools_module.search_inventory = lambda *args, **kwargs: []
    monkeypatch.setitem(sys.modules, "tools.inventory_tools", inventory_tools_module)

    service_tools_module = types.ModuleType("tools.service_tools")
    service_tools_module.check_warranty_status = lambda *args, **kwargs: {}
    service_tools_module.analyze_defect_image = lambda *args, **kwargs: {}
    service_tools_module.generate_invoice_pdf = lambda *args, **kwargs: {"success": True}
    monkeypatch.setitem(sys.modules, "tools.service_tools", service_tools_module)

    crm_tools_module = types.ModuleType("tools.crm_tools")
    crm_tools_module.book_appointment = lambda *args, **kwargs: {}
    crm_tools_module.get_business_hours = lambda *args, **kwargs: {}
    crm_tools_module.save_lead = lambda *args, **kwargs: {}
    crm_tools_module.check_available_slots = lambda *args, **kwargs: []
    crm_tools_module.cancel_appointment = lambda *args, **kwargs: {"success": True}
    crm_tools_module.get_current_datetime = lambda *args, **kwargs: datetime.now(timezone.utc).isoformat()
    monkeypatch.setitem(sys.modules, "tools.crm_tools", crm_tools_module)

    marketing_tools_module = types.ModuleType("tools.marketing_tools")
    marketing_tools_module.generate_content_script = lambda **kwargs: {"success": True}
    marketing_tools_module.get_trending_content_ideas = lambda **kwargs: []
    marketing_tools_module.schedule_social_post = lambda **kwargs: {"success": True}
    marketing_tools_module.analyze_content_performance = lambda **kwargs: {}
    marketing_tools_module.generate_ad_image = lambda **kwargs: {"success": True}
    marketing_tools_module.get_inventory_for_ads = lambda **kwargs: []
    marketing_tools_module.GENERATED_ADS_DIR = str(REPO_ROOT / "generated_ads")
    monkeypatch.setitem(sys.modules, "tools.marketing_tools", marketing_tools_module)

    asset_scraper_module = types.ModuleType("tools.asset_scraper")
    asset_scraper_module.get_all_assets = lambda *args, **kwargs: []
    asset_scraper_module.PROPERTY_ASSETS = {}
    asset_scraper_module.get_matterport_url = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "tools.asset_scraper", asset_scraper_module)

    video_generator_module = types.ModuleType("tools.video_generator")
    video_generator_module.generate_ad_video = lambda *args, **kwargs: {"success": True, "filename": "video.mp4"}
    video_generator_module.GENERATED_VIDEOS_DIR = str(REPO_ROOT / "generated_videos")
    monkeypatch.setitem(sys.modules, "tools.video_generator", video_generator_module)

    pm_routes_module = types.ModuleType("pm_routes")
    pm_routes_module.router = APIRouter()
    monkeypatch.setitem(sys.modules, "pm_routes", pm_routes_module)

    importlib.invalidate_caches()
    main = importlib.import_module("main")
    return main, fake_db, fake_logger


def create_client(monkeypatch, tho_api_key: str | None = "tho-secret", rate_limit_rpm: str = "60"):
    main, fake_db, fake_logger = load_app(
        monkeypatch,
        tho_api_key=tho_api_key,
        rate_limit_rpm=rate_limit_rpm,
    )
    client = TestClient(main.app)
    return client, main, fake_db, fake_logger


def test_admin_token_accepts_supported_employee_headers(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    token = main._create_admin_token()

    x_header = client.get("/api/admin/check", headers={"X-Admin-Token": token})
    bearer_header = client.get("/api/admin/check", headers={"Authorization": f"Bearer {token}"})
    protected_route = client.get("/api/documents/templates", headers={"Authorization": f"Bearer {token}"})

    assert x_header.status_code == 200
    assert bearer_header.status_code == 200
    assert protected_route.status_code == 200


def test_admin_lead_analytics_handles_string_and_datetime_created_at(monkeypatch):
    client, main, _db, logger = create_client(monkeypatch, tho_api_key="tho-secret")
    token = main._create_admin_token()
    now = datetime.now(timezone.utc)
    main.lead_manager.leads = [
        FakeLead(
            lead_id="lead-string-date",
            user_id="user-1",
            session_id="session-1",
            name="String Date",
            email="string@example.test",
            status="new",
            created_at=now.isoformat(),
        ),
        FakeLead(
            lead_id="lead-datetime",
            user_id="user-2",
            session_id="session-2",
            name="Datetime Date",
            phone="555-000-1111",
            status="contacted",
            appointment_requested=True,
            created_at=now,
        ),
    ]

    response = client.get("/api/analytics/leads?range=30d", headers={"X-Admin-Token": token})

    assert response.status_code == 200
    body = response.json()
    assert "error" not in body
    assert body["total"] == 2
    assert body["by_status"] == {"new": 1, "contacted": 1}
    assert body["with_contact_info"] == 2
    assert body["appointment_requested"] == 1
    assert body["new_this_week"] == 2
    assert len(body["time_series"]) == 30
    assert not [
        entry for entry in logger.entries
        if entry["level"] == "error" and entry["message"] == "Lead analytics failed"
    ]


def test_document_readiness_reports_safe_counts(monkeypatch, tmp_path):
    client, main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    token = main._create_admin_token()
    headers = {"X-Admin-Token": token}

    (tmp_path / "local.pdf").write_bytes(b"%PDF-1.4\n%EOF\n")
    monkeypatch.setattr(main, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(
        main,
        "engine_list_templates",
        lambda: [
            {"template_name": "TMHA_Test.pdf", "category": "TMHA"},
            {"template_name": "State_Test.pdf", "category": "State"},
        ],
    )
    monkeypatch.setattr(
        main,
        "engine_list_packets",
        lambda: [{"packet_name": "standard_closing", "templates": ["TMHA_Test.pdf"]}],
    )
    monkeypatch.setattr(
        main,
        "list_gcs_documents",
        lambda: [{
            "filename": "cloud.pdf",
            "size_bytes": 2048,
            "created_at": "2026-04-26T10:00:00+00:00",
            "download_url": "/api/documents/download/cloud.pdf",
        }],
    )

    unauthenticated = client.get("/api/documents/readiness")
    response = client.get("/api/documents/readiness", headers=headers)

    assert unauthenticated.status_code == 401
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["template_count"] == 2
    assert body["packet_count"] == 1
    assert body["generated_document_count"] == 2
    assert body["local_document_count"] == 1
    assert body["gcs_document_count"] == 1
    assert body["category_counts"] == {"TMHA": 1, "State": 1}


def test_returns_503_when_tho_api_key_is_unset(monkeypatch):
    client, _main, _db, _logger = create_client(monkeypatch, tho_api_key=None)

    response = client.get("/api/v1/customers")

    assert response.status_code == 503
    assert response.json() == {"detail": "API key auth not configured"}


def test_returns_401_when_header_is_missing_or_wrong(monkeypatch):
    client, _main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")

    missing = client.get("/api/v1/customers")
    wrong = client.get("/api/v1/customers", headers={"Authorization": "Bearer wrong-secret"})

    assert missing.status_code == 401
    assert missing.json() == {"detail": "Missing API key"}
    assert wrong.status_code == 401
    assert wrong.json() == {"detail": "Invalid API key"}


def test_valid_key_returns_redacted_customer_data(monkeypatch):
    client, _main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")

    response = client.get("/api/v1/customers", headers={"Authorization": "Bearer tho-secret"})

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    customer = body["customers"][0]
    assert customer["id"] == "cust-1"
    assert customer["status"] == "LEAD"
    assert customer["full_name"] == "Alice Example"
    assert "phone" not in customer
    assert "email" not in customer
    assert "ssn_hash" not in customer
    assert "ssn_masked" not in customer


def test_single_customer_accepts_x_api_key_and_stays_redacted(monkeypatch):
    client, _main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")

    response = client.get("/api/v1/customers/cust-1", headers={"X-API-Key": "tho-secret"})

    assert response.status_code == 200
    customer = response.json()["customer"]
    assert customer["id"] == "cust-1"
    assert "phone" not in customer
    assert "email" not in customer


def test_create_customer_returns_201_and_id(monkeypatch):
    client, _main, fake_db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")

    response = client.post(
        "/api/v1/customers",
        headers={"Authorization": "Bearer tho-secret"},
        json={
            "full_name": "Created Customer",
            "email": "created@example.com",
            "phone": "5551239999",
            "status": "ENROLLED",
            "city": "Houston",
            "state": "TX",
        },
    )

    assert response.status_code == 201
    created_id = response.json()["id"]
    stored_customer = fake_db.get_customer(created_id)
    assert stored_customer is not None
    assert stored_customer["full_name"] == "Created Customer"
    assert stored_customer["phone"] == "5551239999"


def test_create_customer_drops_ssn_fields_from_partner_input(monkeypatch):
    """Partner API must never persist SSN data, even if clients try to send it."""
    client, _main, fake_db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")

    response = client.post(
        "/api/v1/customers",
        headers={"Authorization": "Bearer tho-secret"},
        json={
            "full_name": "No SSN Here",
            "status": "LEAD",
            "ssn_masked": "***-**-1111",
            "ssn": "123-45-6789",
            "co_buyer": {
                "name": "Co",
                "ssn": "987-65-4321",
                "ssn_masked": "***-**-4321",
                "phone": "5550001111",
            },
            "references": [
                {"name": "Ref1", "ssn_last4": "6789"},
            ],
        },
    )

    assert response.status_code == 201
    stored = fake_db.get_customer(response.json()["id"])
    assert "ssn_masked" not in stored
    assert "ssn" not in stored
    # Nested co_buyer / references also stripped of any ssn-shaped fields
    co_buyer = stored.get("co_buyer") or {}
    assert all("ssn" not in k.lower() for k in co_buyer.keys())
    for ref in stored.get("references") or []:
        if isinstance(ref, dict):
            assert all("ssn" not in k.lower() for k in ref.keys())


def test_inventory_leads_and_stats_use_partner_safe_shapes(monkeypatch):
    client, _main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    headers = {"Authorization": "Bearer tho-secret"}

    inventory_response = client.get("/api/v1/inventory?manufacturer=Champion", headers=headers)
    leads_response = client.get("/api/v1/leads", headers=headers)
    stats_response = client.get("/api/v1/stats", headers=headers)

    assert inventory_response.status_code == 200
    inventory = inventory_response.json()["inventory"]
    assert len(inventory) == 1
    assert inventory[0]["manufacturer"] == "Champion"

    assert leads_response.status_code == 200
    lead = leads_response.json()["leads"][0]
    assert lead["lead_id"] == "lead-1"
    assert "name" not in lead
    assert "email" not in lead
    assert "phone" not in lead
    assert "session_id" not in lead

    assert stats_response.status_code == 200
    stats = stats_response.json()
    assert stats["customers"]["by_status"]["LEAD"] == 1
    assert stats["deals"]["by_status"]["funded"] == 2
    assert stats["inventory"]["by_status"]["AVAILABLE"] == 1


def test_webhook_notify_records_activity(monkeypatch):
    client, _main, fake_db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")

    response = client.post(
        "/api/v1/webhooks/notify",
        headers={"Authorization": "Bearer tho-secret"},
        json={
            "event": "deal.status_changed",
            "deal_id": "deal-2",
            "payload": {"from": "approved", "to": "funded"},
        },
    )

    assert response.status_code == 200
    activity_id = response.json()["id"]
    stored = fake_db.collections["activities"][activity_id]
    assert stored["deal_id"] == "deal-2"
    assert stored["metadata"]["event"] == "deal.status_changed"
    assert stored["metadata"]["payload"] == {"from": "approved", "to": "funded"}
    assert response.json().get("idempotent_replay") is False


def test_webhook_notify_is_idempotent_when_key_repeated(monkeypatch):
    """Same idempotency_key from the same API key must not create duplicates."""
    client, _main, fake_db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    headers = {"Authorization": "Bearer tho-secret"}

    body = {
        "event": "deal.funded",
        "deal_id": "deal-3",
        "payload": {"funded_at": "2026-04-23T00:00:00Z"},
        "idempotency_key": "notion-run-abc123",
    }

    first = client.post("/api/v1/webhooks/notify", headers=headers, json=body)
    second = client.post("/api/v1/webhooks/notify", headers=headers, json=body)

    assert first.status_code == 200
    assert second.status_code == 200
    # Same activity id returned both times
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["idempotent_replay"] is False
    assert second.json()["idempotent_replay"] is True
    # Only one activities record was written
    matching = [
        a for a in fake_db.collections["activities"].values()
        if a.get("deal_id") == "deal-3"
    ]
    assert len(matching) == 1


def test_rate_limiting_still_applies(monkeypatch):
    client, _main, _db, _logger = create_client(
        monkeypatch,
        tho_api_key="tho-secret",
        rate_limit_rpm="2",
    )
    headers = {"Authorization": "Bearer tho-secret"}

    first = client.get("/api/v1/stats", headers=headers)
    second = client.get("/api/v1/stats", headers=headers)
    third = client.get("/api/v1/stats", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert third.json() == {"error": "Rate limit exceeded. Please try again shortly."}


def test_fingerprint_logging_is_present_and_never_logs_the_raw_key(monkeypatch):
    raw_key = "tho-secret"
    client, _main, _db, fake_logger = create_client(monkeypatch, tho_api_key=raw_key)

    response = client.get("/api/v1/customers", headers={"Authorization": f"Bearer {raw_key}"})

    assert response.status_code == 200
    partner_logs = [entry for entry in fake_logger.entries if entry["message"] == "Partner API request"]
    assert partner_logs, "expected at least one partner API audit log entry"
    latest = partner_logs[-1]
    assert latest["api_key_fingerprint"]
    assert latest["api_key_fingerprint"] != raw_key
    assert latest["endpoint"] == "/api/v1/customers"
    assert latest["method"] == "GET"
    assert latest["auth_status"] == "accepted"
    assert raw_key not in repr(latest)


# ─── Multi-key (partner-scoped) auth ────────────────────────────────────────


def test_multiple_partner_keys_each_accepted(monkeypatch):
    """THO_API_KEY_* env vars should each authenticate independently."""
    client, _main, _db, fake_logger = create_client(
        monkeypatch,
        tho_api_key="primary-secret",
    )
    monkeypatch.setenv("THO_API_KEY_ETAI", "etai-secret")
    monkeypatch.setenv("THO_API_KEY_N8N", "n8n-secret")

    # Primary
    r1 = client.get("/api/v1/stats", headers={"Authorization": "Bearer primary-secret"})
    # Etai's key
    r2 = client.get("/api/v1/stats", headers={"Authorization": "Bearer etai-secret"})
    # n8n's key
    r3 = client.get("/api/v1/stats", headers={"X-API-Key": "n8n-secret"})

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 200

    # Audit log should record which partner slot matched
    partner_logs = [
        e for e in fake_logger.entries
        if e["message"] == "Partner API request" and e["auth_status"] == "accepted"
    ]
    matched_slots = {e.get("partner_id") for e in partner_logs[-3:]}
    assert matched_slots == {"THO_API_KEY", "THO_API_KEY_ETAI", "THO_API_KEY_N8N"}


def test_revoking_one_partner_key_does_not_affect_others(monkeypatch):
    """Simulate removing THO_API_KEY_ETAI — primary key keeps working."""
    client, _main, _db, _logger = create_client(
        monkeypatch,
        tho_api_key="primary-secret",
    )
    monkeypatch.setenv("THO_API_KEY_ETAI", "etai-secret")

    # Both work while configured
    assert client.get("/api/v1/stats", headers={"Authorization": "Bearer etai-secret"}).status_code == 200

    # Revoke Etai's slot
    monkeypatch.delenv("THO_API_KEY_ETAI")

    # Etai's key now invalid, primary still works
    assert client.get("/api/v1/stats", headers={"Authorization": "Bearer etai-secret"}).status_code == 401
    assert client.get("/api/v1/stats", headers={"Authorization": "Bearer primary-secret"}).status_code == 200


def test_503_when_no_partner_keys_configured(monkeypatch):
    """All partner keys removed → fail-closed."""
    client, _main, _db, _logger = create_client(monkeypatch, tho_api_key=None)
    # Also strip any THO_API_KEY_* the test env might have inherited
    for name in list(os.environ):
        if name.startswith("THO_API_KEY_"):
            monkeypatch.delenv(name, raising=False)

    response = client.get("/api/v1/stats", headers={"Authorization": "Bearer anything"})
    assert response.status_code == 503
    assert response.json() == {"detail": "API key auth not configured"}


def test_only_partner_scoped_key_configured_still_authenticates(monkeypatch):
    """If only THO_API_KEY_ETAI is set (no primary), partner can still auth."""
    client, _main, _db, _logger = create_client(monkeypatch, tho_api_key=None)
    monkeypatch.setenv("THO_API_KEY_ETAI", "etai-only-secret")

    assert client.get("/api/v1/stats", headers={"Authorization": "Bearer etai-only-secret"}).status_code == 200
    assert client.get("/api/v1/stats", headers={"Authorization": "Bearer wrong"}).status_code == 401
