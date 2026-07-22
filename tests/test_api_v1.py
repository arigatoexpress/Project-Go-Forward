"""Tests for the /api/v1/* partner integration surface.

Run: python -m pytest tests/test_api_v1.py -v
"""

from __future__ import annotations

import importlib
import os
import sys
import types
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path

import pytest
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
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    first_contacted_at: str | None = None
    first_contacted_by: str | None = None
    status_changed_at: str | None = None
    status_changed_by: str | None = None

    # Triage / routing fields
    priority: str | None = None
    assigned_to: str | None = None
    triage_notes: str | None = None
    triage_reason: str | None = None
    last_triage_at: str | None = None
    contact_consent_at: str | None = None
    contact_consent_source: str | None = None
    journey_id: str | None = None
    home_id: str | None = None
    home_model: str | None = None

    # Marketing attribution (first-party UTM carried on a reached-out lead; NOT visitor tracking)
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    utm_content: str | None = None
    utm_term: str | None = None
    referrer: str | None = None
    gclid: str | None = None
    gbraid: str | None = None
    wbraid: str | None = None

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
            "first_contacted_at": self.first_contacted_at,
            "first_contacted_by": self.first_contacted_by,
            "status_changed_at": self.status_changed_at,
            "status_changed_by": self.status_changed_by,
            "priority": self.priority,
            "assigned_to": self.assigned_to,
            "triage_notes": self.triage_notes,
            "triage_reason": self.triage_reason,
            "last_triage_at": self.last_triage_at,
            "contact_consent_at": self.contact_consent_at,
            "contact_consent_source": self.contact_consent_source,
            "journey_id": self.journey_id,
            "home_id": self.home_id,
            "home_model": self.home_model,
            "utm_source": self.utm_source,
            "utm_medium": self.utm_medium,
            "utm_campaign": self.utm_campaign,
            "utm_content": self.utm_content,
            "utm_term": self.utm_term,
            "referrer": self.referrer,
            "gclid": self.gclid,
            "gbraid": self.gbraid,
            "wbraid": self.wbraid,
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

    def update(self, data: dict):
        merged = dict(self._store.get(self.id, {}))
        merged.update(data)
        self._store[self.id] = merged

    def get(self):
        data = self._store.get(self.id)
        if data is None:
            return types.SimpleNamespace(exists=False, id=self.id, to_dict=lambda: None)
        return types.SimpleNamespace(exists=True, id=self.id, to_dict=lambda: dict(data))


class FakeQuery:
    """Tiny subset of Firestore query chaining used by v1 endpoints."""

    def __init__(
        self,
        store: dict,
        filters: list[tuple[str, str, object]] | None = None,
        limit: int | None = None,
    ):
        self._store = store
        self._filters = filters or []
        self._limit = limit

    def where(self, field: str, op: str, value: object) -> FakeQuery:
        return FakeQuery(self._store, self._filters + [(field, op, value)], self._limit)

    def limit(self, n: int) -> FakeQuery:
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
                (self._resolve(data, f) == v) if op == "==" else False for f, op, v in self._filters
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

    def add(self, data: dict):
        doc_id = f"{self._name}-{len(self._store) + 1}"
        self._store[doc_id] = dict(data)
        return FakeDocumentRef(self._store, doc_id)

    def where(self, field: str, op: str, value: object) -> FakeQuery:
        return FakeQuery(self._store).where(field, op, value)

    def limit(self, n: int) -> FakeLimitedCollection:
        return FakeLimitedCollection(self._store, n)


class FakeLimitedCollection:
    """Subset of FakeCollection that supports .limit(n).stream()."""

    def __init__(self, store: dict, limit: int):
        self._store = store
        self._limit = limit

    def stream(self):
        for i, (doc_id, data) in enumerate(self._store.items()):
            if i >= self._limit:
                break
            yield FakeDocSnapshot(doc_id, data)


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
            "service_requests": {"sr-1": {"id": "sr-1", "status": "open"}},
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
            customers = [
                customer for customer in customers if customer.get("status") == status.upper()
            ]
        if query_text:
            needle = query_text.lower()
            customers = [
                customer
                for customer in customers
                if needle
                in " ".join(
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

    def get_inventory_by_id(self, inventory_id: str):
        item = self.collections["inventory"].get(inventory_id)
        return dict(item) if item else None

    def create_inventory(self, data: dict) -> str:
        new_id = data.get("id") or f"inv-{len(self.collections['inventory']) + 1}"
        self.collections["inventory"][new_id] = {**data, "id": new_id}
        return new_id

    def update_inventory(self, inventory_id: str, data: dict) -> bool:
        existing = self.collections["inventory"].get(inventory_id, {})
        self.collections["inventory"][inventory_id] = {**existing, **data, "id": inventory_id}
        return True

    def delete_inventory(self, inventory_id: str) -> bool:
        self.collections["inventory"].pop(inventory_id, None)
        return True

    def count_customers(self):
        by_status: dict[str, int] = {}
        for customer in self.collections["customers"].values():
            current = customer.get("status", "UNKNOWN")
            by_status[current] = by_status.get(current, 0) + 1
        return {"total": len(self.collections["customers"]), "by_status": by_status}

    def update_service_request(self, request_id: str, data: dict) -> bool:
        if request_id not in self.collections.setdefault("service_requests", {}):
            return False
        self.collections["service_requests"][request_id].update(data)
        return True


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

    async def transition_lead_status(self, lead_id: str, new_status: str, *, actor: str):
        allowed = {"new", "contacted", "qualified", "converted", "archived"}
        if new_status not in allowed:
            raise ValueError(f"Invalid lead status: {new_status!r}")
        lead = await self.get_lead(lead_id)
        if lead is None:
            return None, None, False
        previous = lead.status
        if previous == new_status:
            return lead, previous, False
        now = datetime.now(UTC).isoformat()
        lead.status = new_status
        lead.status_changed_at = now
        lead.status_changed_by = actor
        if new_status in {"contacted", "qualified", "converted"} and not lead.first_contacted_at:
            lead.first_contacted_at = now
            lead.first_contacted_by = actor
        return lead, previous, True

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

    async def get_lead_by_phone(self, phone: str | None):
        wanted = "".join(character for character in str(phone or "") if character.isdigit())[-10:]
        for lead in self.leads:
            current = "".join(
                character for character in str(lead.phone or "") if character.isdigit()
            )[-10:]
            if wanted and current == wanted:
                return lead
        return None

    async def list_leads(self, status=None, limit=100):
        leads = list(self.leads)
        if status:
            leads = [lead for lead in leads if lead.status == status]
        return leads[:limit]

    async def list_leads_needing_triage(self, status="new", min_age_hours=None, limit=100):
        leads = await self.list_leads(status=status, limit=limit)
        if min_age_hours is None:
            return leads
        from datetime import UTC, datetime, timedelta

        cutoff = datetime.now(UTC) - timedelta(hours=min_age_hours)
        result = []
        for lead in leads:
            created = lead.created_at
            if not created:
                continue
            try:
                dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                if dt >= cutoff:
                    continue
            except Exception:
                continue
            result.append(lead)
        return result

    async def triage_lead(self, lead_id: str, update: dict):
        for lead in self.leads:
            if lead.lead_id == lead_id:
                allowed = {"status", "priority", "assigned_to", "triage_notes", "triage_reason"}
                for key, value in update.items():
                    if key in allowed:
                        setattr(lead, key, value)
                lead.last_triage_at = datetime.now(UTC).isoformat()
                return lead
        return None


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
    import hashlib

    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setenv("RATE_LIMIT_RPM", rate_limit_rpm)
    monkeypatch.setenv("ADMIN_PIN_HASH", hashlib.sha256(b"4832").hexdigest())
    if tho_api_key is None:
        monkeypatch.delenv("THO_API_KEY", raising=False)
        for name in list(os.environ):
            if name.startswith("THO_API_KEY_"):
                monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv("THO_API_KEY", tho_api_key)

    frontend_dist = REPO_ROOT / "frontend" / "dist"
    (frontend_dist / "assets").mkdir(parents=True, exist_ok=True)
    index_html = frontend_dist / "index.html"
    # Realistic Vite/React SPA shell so seo_routes._inject can replace
    # <title>, <meta name="description">, and inject before </head>/root.
    # Overwrite the old minimal stub so local test runs stay consistent.
    spa_shell = (
        "<!doctype html>"
        '<html lang="en">'
        "<head>"
        '<meta charset="UTF-8" />'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0" />'
        '<meta name="description" content="Texas Home Outlet" />'
        "<title>Texas Home Outlet</title>"
        '<script type="module" src="/assets/main.js"></script>'
        "</head>"
        '<body><div id="root"></div></body>'
        "</html>"
    )
    if not index_html.exists() or index_html.read_text() == "<html><body>test</body></html>":
        index_html.write_text(spa_shell)

    sys.modules.pop("database.models", None)
    from database.models import Inventory as RealInventory
    from database.models import InventoryWrite as RealInventoryWrite

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
        "mira_notify",
        "mira_routes",
        "github_mira_trigger",
        "obsidian_routes",
    ]
    # Use monkeypatch.delitem (NOT sys.modules.pop) so the REAL modules are
    # restored on teardown. A raw pop leaks: it leaves these names absent from
    # sys.modules after the test, so a later test's patch("appointment_manager.
    # _get_hours_for_date", ...) re-imports a FRESH module the other test's class
    # isn't bound to — the patch silently misses and the assertion fails. (This
    # caused test_closed_day_raises to fail only in the full-suite run.)
    for module_name in modules_to_clear:
        monkeypatch.delitem(sys.modules, module_name, raising=False)

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
    lead_management_module.normalize_phone = lambda phone: phone
    monkeypatch.setitem(sys.modules, "lead_management", lead_management_module)

    appointment_manager_module = types.ModuleType("appointment_manager")
    appointment_manager_module.AppointmentManager = FakeAppointmentManager
    appointment_manager_module.Appointment = FakeAppointment
    monkeypatch.setitem(sys.modules, "appointment_manager", appointment_manager_module)

    email_service_module = types.ModuleType("email_service")
    email_service_module.send_admin_login_code = lambda *args, **kwargs: {"success": True}
    email_service_module.send_appointment_confirmation = lambda *args, **kwargs: {"success": True}
    email_service_module.send_lead_welcome = lambda *args, **kwargs: {"success": True}
    email_service_module.send_deal_status_update = lambda *args, **kwargs: {"success": True}
    email_service_module.send_custom_email = lambda *args, **kwargs: {"success": True}
    email_service_module.send_document_email = lambda *args, **kwargs: {"success": True}
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
    database_models_module.Inventory = RealInventory
    database_models_module.InventoryWrite = RealInventoryWrite
    monkeypatch.setitem(sys.modules, "database.models", database_models_module)

    document_schemas_module = types.ModuleType("schemas.document_schemas")
    document_schemas_module.SalesContractForm = DummyBodyModel
    document_schemas_module.GenerateDocumentRequest = DummyBodyModel
    document_schemas_module.GeneratePacketRequest = DummyBodyModel
    monkeypatch.setitem(sys.modules, "schemas.document_schemas", document_schemas_module)

    document_tools_module = types.ModuleType("tools.document_tools")
    document_tools_module.OUTPUT_DIR = str(REPO_ROOT / "tmp_test_output")
    document_tools_module.DOCUMENTS_DIR = str(REPO_ROOT / "tho_documents")
    document_tools_module.generate_sales_contract_pdf = lambda *args, **kwargs: {
        "success": True,
        "filename": "ok.pdf",
    }
    document_tools_module.generate_work_order_pdf = lambda *args, **kwargs: {"success": True}
    document_tools_module.generate_service_ticket = lambda *args, **kwargs: {"success": True}
    document_tools_module.generate_customer_email = lambda *args, **kwargs: {"success": True}
    document_tools_module.download_from_gcs = lambda *args, **kwargs: False
    document_tools_module.list_gcs_documents = lambda *args, **kwargs: []
    document_tools_module.fill_pdf_form = lambda *args, **kwargs: {"success": True}
    document_tools_module.upload_to_gcs = lambda *args, **kwargs: True
    monkeypatch.setitem(sys.modules, "tools.document_tools", document_tools_module)

    document_engine_module = types.ModuleType("tools.document_engine")
    document_engine_module.generate_document = lambda *args, **kwargs: {
        "success": True,
        "file_path": "doc.pdf",
        "filename": "doc.pdf",
    }
    document_engine_module.generate_packet = lambda *args, **kwargs: {
        "success": True,
        "file_path": "packet.pdf",
        "filename": "packet.pdf",
    }
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
    crm_tools_module.get_current_datetime = lambda *args, **kwargs: datetime.now(UTC).isoformat()
    monkeypatch.setitem(sys.modules, "tools.crm_tools", crm_tools_module)

    marketing_tools_module = types.ModuleType("tools.marketing_tools")
    marketing_tools_module.generate_content_script = lambda **kwargs: {"success": True}
    marketing_tools_module.get_trending_content_ideas = lambda **kwargs: []
    marketing_tools_module.schedule_social_post = lambda **kwargs: {"success": True}
    marketing_tools_module.analyze_content_performance = lambda **kwargs: {}
    marketing_tools_module.generate_ad_image = lambda **kwargs: {"success": True}
    marketing_tools_module.get_gcp_ai_readiness = lambda **kwargs: {"success": True, "ready": True}
    marketing_tools_module.get_inventory_for_ads = lambda **kwargs: {
        "success": True,
        "homes": [],
        "total_inventory": 0,
    }
    marketing_tools_module.GENERATED_ADS_DIR = str(REPO_ROOT / "generated_ads")
    monkeypatch.setitem(sys.modules, "tools.marketing_tools", marketing_tools_module)

    legacy_site_crawler_module = types.ModuleType("tools.legacy_site_crawler")
    legacy_site_crawler_module.load_legacy_inventory_context = lambda **kwargs: {
        "success": False,
        "homes": [],
        "total_inventory": 0,
        "message": "stubbed legacy inventory unavailable",
    }
    legacy_site_crawler_module.load_legacy_floorplan_catalog_context = lambda **kwargs: {
        "success": False,
        "homes": [],
        "total_inventory": 0,
        "message": "stubbed legacy floorplan catalog unavailable",
    }
    monkeypatch.setitem(sys.modules, "tools.legacy_site_crawler", legacy_site_crawler_module)

    asset_scraper_module = types.ModuleType("tools.asset_scraper")
    asset_scraper_module.get_all_assets = lambda *args, **kwargs: []
    asset_scraper_module.get_assets_for_home = lambda *args, **kwargs: None
    asset_scraper_module.PROPERTY_ASSETS = {}
    asset_scraper_module.get_matterport_url = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "tools.asset_scraper", asset_scraper_module)

    video_generator_module = types.ModuleType("tools.video_generator")
    video_generator_module.generate_ad_video = lambda *args, **kwargs: {
        "success": True,
        "filename": "video.mp4",
    }
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


def test_marketing_inventory_context_prefers_legacy_site_inventory(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")

    monkeypatch.setattr(
        main,
        "load_legacy_inventory_context",
        lambda **kwargs: {
            "success": True,
            "source": "legacy_site_live",
            "homes": [
                {
                    "id": "43372",
                    "legacy_inventory_id": "43372",
                    "model_name": "Premier / Creole 3256H32447",
                    "real_photos": ["https://example.com/creole-ext-1.jpg"],
                    "gallery_images": ["https://example.com/creole-ext-1.jpg"],
                    "floor_plan_url": "https://example.com/creole-floorplan.jpg",
                    "quote_url": "https://www.texashomeoutlet.com/quote/inventory/43372/dealer/3522/",
                }
            ],
            "total_inventory": 1,
            "website_homes": 1,
        },
    )
    monkeypatch.setattr(
        main,
        "get_inventory_for_ads",
        lambda **kwargs: {
            "success": True,
            "homes": [{"id": "stale-firestore"}],
            "total_inventory": 1,
        },
    )

    response = client.get("/api/marketing/inventory-context")

    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "legacy_site_live"
    assert data["total_inventory"] == 1
    assert [home["id"] for home in data["homes"]] == ["43372"]
    assert data["homes"][0]["quote_url"].endswith("/43372/dealer/3522/")


def test_marketing_inventory_context_appends_orderable_catalog_to_live_inventory(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")

    monkeypatch.setattr(
        main,
        "load_legacy_inventory_context",
        lambda **kwargs: {
            "success": True,
            "source": "legacy_site_live",
            "homes": [
                {
                    "id": "43372",
                    "legacy_inventory_id": "43372",
                    "model_name": "Premier / Creole 3256H32447",
                    "manufacturer": "Champion Homes",
                    "status": "Available",
                    "real_photos": ["https://example.com/creole-ext-1.jpg"],
                    "gallery_images": ["https://example.com/creole-ext-1.jpg"],
                }
            ],
            "total_inventory": 1,
        },
    )
    monkeypatch.setattr(
        main,
        "PROPERTY_ASSETS",
        {
            "the-orderable": {
                "name": "The Orderable",
                "manufacturer": "New Vision Manufacturing",
                "is_new": True,
                "beds": 3,
                "baths": 2,
                "sqft": 1200,
                "dims": "16x76",
                "images": ["https://example.com/orderable-kitchen-1.jpg"],
                "floor_plan": "https://example.com/orderable-floor-plans.jpg",
            },
            "used-sample": {
                "name": "Used Sample",
                "manufacturer": "Pre-Owned",
                "is_new": False,
            },
        },
    )

    response = client.get("/api/marketing/inventory-context")

    assert response.status_code == 200
    data = response.json()
    assert data["total_inventory"] == 2
    assert data["available_now"] == 1
    assert data["orderable_floorplans"] == 1
    assert [home["id"] for home in data["homes"]] == ["43372", "catalog-the-orderable"]
    assert data["homes"][0]["inventory_kind"] == "available_now"
    assert data["homes"][1]["status"] == "Orderable"
    assert data["homes"][1]["availability_label"] == "Orderable floorplan"
    assert data["homes"][1]["is_orderable"] is True


def _isolate_inventory_merge(monkeypatch, main):
    """Passthrough the floorplan merge / photo overlay so inventory-context
    source-selection tests assert which SOURCE wins, not merge internals."""
    monkeypatch.setattr(main, "merge_orderable_floorplan_catalog", lambda result, **k: result)
    monkeypatch.setattr(main, "_overlay_staff_photos", lambda homes: None)
    monkeypatch.setattr(
        main, "load_legacy_floorplan_catalog_context", lambda **k: {"success": False, "homes": []}
    )
    monkeypatch.setattr(main, "PROPERTY_ASSETS", {})


_LEGACY_CTX = {
    "success": True,
    "source": "legacy_site_live",
    "homes": [{"id": "legacy-1", "model_name": "Legacy Home", "real_photos": ["https://x/a.jpg"]}],
    "total_inventory": 1,
}
_FS_CTX = {
    "success": True,
    "homes": [{"id": "fs-1", "model_name": "FS Home", "real_photos": ["https://x/b.jpg"]}],
    "total_inventory": 1,
}


def test_inventory_context_defaults_to_legacy_source(monkeypatch):
    """No INVENTORY_SOURCE set -> legacy snapshot wins (behavior unchanged)."""
    client, main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    monkeypatch.delenv("INVENTORY_SOURCE", raising=False)
    _isolate_inventory_merge(monkeypatch, main)
    monkeypatch.setattr(main, "load_legacy_inventory_context", lambda **k: dict(_LEGACY_CTX))
    monkeypatch.setattr(main, "get_inventory_for_ads", lambda **k: dict(_FS_CTX))

    data = client.get("/api/marketing/inventory-context").json()
    assert [h["id"] for h in data["homes"]] == ["legacy-1"]


def test_inventory_context_firestore_source_serves_firestore(monkeypatch):
    """INVENTORY_SOURCE=firestore -> staff-managed Firestore inventory wins."""
    client, main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    monkeypatch.setenv("INVENTORY_SOURCE", "firestore")
    _isolate_inventory_merge(monkeypatch, main)
    monkeypatch.setattr(main, "load_legacy_inventory_context", lambda **k: dict(_LEGACY_CTX))
    monkeypatch.setattr(main, "get_inventory_for_ads", lambda **k: dict(_FS_CTX))

    data = client.get("/api/marketing/inventory-context").json()
    assert [h["id"] for h in data["homes"]] == ["fs-1"]


def test_inventory_context_auto_falls_back_to_legacy_when_firestore_empty(monkeypatch):
    """auto + empty Firestore -> legacy snapshot, NOT the website-asset catalog."""
    client, main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    monkeypatch.setenv("INVENTORY_SOURCE", "auto")
    _isolate_inventory_merge(monkeypatch, main)
    monkeypatch.setattr(main, "load_legacy_inventory_context", lambda **k: dict(_LEGACY_CTX))
    monkeypatch.setattr(main, "get_inventory_for_ads", lambda **k: {"success": True, "homes": []})

    data = client.get("/api/marketing/inventory-context").json()
    assert [h["id"] for h in data["homes"]] == ["legacy-1"]


def test_inventory_context_auto_prefers_firestore_when_populated(monkeypatch):
    """auto + >= min Firestore homes -> Firestore wins (the unfreeze)."""
    client, main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    monkeypatch.setenv("INVENTORY_SOURCE", "auto")
    _isolate_inventory_merge(monkeypatch, main)
    monkeypatch.setattr(main, "load_legacy_inventory_context", lambda **k: dict(_LEGACY_CTX))
    monkeypatch.setattr(main, "get_inventory_for_ads", lambda **k: dict(_FS_CTX))

    data = client.get("/api/marketing/inventory-context").json()
    assert [h["id"] for h in data["homes"]] == ["fs-1"]


def test_create_inventory_requires_admin(monkeypatch):
    client, _main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    resp = client.post("/api/inventory", json={"model_name": "The Nassau"})
    assert resp.status_code == 401


def test_create_inventory_item(monkeypatch):
    client, main, fake_db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    token = main._create_admin_token()
    resp = client.post(
        "/api/inventory",
        json={
            "model_name": "The Nassau",
            "manufacturer": "Jessup",
            "bedrooms": 3,
            "bathrooms": 2.0,
        },
        headers={"X-Admin-Token": token},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    stored = fake_db.collections["inventory"][body["id"]]
    assert stored["model_name"] == "The Nassau"
    assert stored["status"] == "AVAILABLE"  # defaulted
    assert stored["source"] == "staff_created"


def test_create_inventory_rejects_missing_model_name(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    token = main._create_admin_token()
    resp = client.post(
        "/api/inventory", json={"manufacturer": "Jessup"}, headers={"X-Admin-Token": token}
    )
    assert resp.status_code == 400
    assert resp.json()["success"] is False


def test_update_inventory_item_merges(monkeypatch):
    client, main, fake_db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    fake_db.collections["inventory"]["inv-1"] = {
        "id": "inv-1",
        "model_name": "Old",
        "status": "AVAILABLE",
    }
    token = main._create_admin_token()
    resp = client.put(
        "/api/inventory/inv-1",
        json={"sale_price": 89900, "model_name": "New Name"},
        headers={"X-Admin-Token": token},
    )
    assert resp.status_code == 200
    stored = fake_db.collections["inventory"]["inv-1"]
    assert stored["model_name"] == "New Name"
    assert stored["sale_price"] == 89900
    assert stored["status"] == "AVAILABLE"  # untouched field preserved


def test_retire_inventory_item_soft(monkeypatch):
    client, main, fake_db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    fake_db.collections["inventory"]["inv-1"] = {
        "id": "inv-1",
        "model_name": "X",
        "status": "AVAILABLE",
    }
    token = main._create_admin_token()
    resp = client.delete("/api/inventory/inv-1", headers={"X-Admin-Token": token})
    assert resp.status_code == 200
    assert resp.json()["retired"] is True
    # Soft retire keeps the record but drops it off the AVAILABLE list.
    assert fake_db.collections["inventory"]["inv-1"]["status"] == "RETIRED"


def test_delete_inventory_item_hard(monkeypatch):
    client, main, fake_db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    fake_db.collections["inventory"]["inv-1"] = {"id": "inv-1", "model_name": "X"}
    token = main._create_admin_token()
    resp = client.delete("/api/inventory/inv-1?hard=true", headers={"X-Admin-Token": token})
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    assert "inv-1" not in fake_db.collections["inventory"]


def test_marketing_readiness_routes_are_admin_protected(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    token = main._create_admin_token()

    denied = client.get("/api/marketing/gcp-readiness")
    assert denied.status_code == 401

    gcp = client.get("/api/marketing/gcp-readiness", headers={"X-Admin-Token": token})
    social = client.get("/api/marketing/social-readiness", headers={"X-Admin-Token": token})

    assert gcp.status_code == 200
    assert gcp.json()["ready"] is True
    assert social.status_code == 200
    assert "tiktok" in social.json()["platforms"]
    assert "instagram_reels" in social.json()["platforms"]


def test_admin_inventory_recovers_photo_ready_media_for_document_workflows(monkeypatch):
    client, main, db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    token = main._create_admin_token()
    floorplan = "https://example.com/floor-plans.jpg"
    recovered_photo = "https://example.com/recovered-ext-1.jpg"

    db.collections["inventory"].clear()
    db.collections["inventory"]["28102"] = {
        "id": "28102",
        "model_name": "TRU Single Section / TRU Single Section Delight",
        "manufacturer": "TRU",
        "status": "AVAILABLE",
        "image_url": floorplan,
        "real_photos": [floorplan],
        "gallery_images": [floorplan],
        "floorplan_url": floorplan,
    }
    db.collections["inventory"]["the-razor"] = {
        "id": "the-razor",
        "model_name": "The Razor",
        "manufacturer": "New Vision Manufacturing",
        "status": "AVAILABLE",
        "image_url": floorplan,
        "floorplan_url": floorplan,
    }
    db.collections["inventory"]["missing"] = {
        "id": "missing",
        "model_name": "No Exact Photo Home",
        "manufacturer": "Unknown",
        "status": "AVAILABLE",
    }
    monkeypatch.setattr(
        main,
        "load_legacy_inventory_context",
        lambda **kwargs: {
            "success": True,
            "homes": [
                {
                    "id": "28102",
                    "legacy_inventory_id": "28102",
                    "model_name": "TRU Single Section / TRU Single Section Delight",
                    "image_url": recovered_photo,
                    "real_photos": [
                        recovered_photo,
                        "https://example.com/recovered-kit-1.jpg",
                        "https://example.com/recovered-bed-1.jpg",
                    ],
                    "gallery_images": [recovered_photo],
                    "floorplan_url": floorplan,
                    "media_quality": {
                        "status": "photo_ready",
                        "has_real_photo": True,
                        "photo_count": 3,
                        "floorplan_count": 1,
                    },
                }
            ],
            "total_inventory": 1,
        },
    )
    sys.modules["tools.asset_scraper"].get_assets_for_home = lambda name: (
        {
            "name": "The Razor",
            "manufacturer": "New Vision Manufacturing",
            "images": ["https://example.com/razor.lvgrm.jpg"],
            "floor_plan": floorplan,
        }
        if name == "The Razor"
        else None
    )

    response = client.get("/api/inventory", headers={"X-Admin-Token": token})

    assert response.status_code == 200
    inventory = {item["id"]: item for item in response.json()["inventory"]}
    assert inventory["28102"]["image_url"] != floorplan
    assert inventory["28102"]["real_photos"]
    assert inventory["28102"]["floor_plan_url"] == floorplan
    assert inventory["28102"]["media_quality"]["status"] == "photo_ready"
    assert inventory["28102"]["media_recovery"]["source"] in {
        "exact_manufacturer_plan_cdn",
        "legacy_inventory_context",
    }
    assert inventory["the-razor"]["image_url"].endswith("razor.lvgrm.jpg")
    assert inventory["the-razor"]["media_recovery"]["source"] == "asset_catalog"
    assert inventory["missing"]["image_url"] == "/tex-icon.svg"
    assert inventory["missing"]["image_placeholder"] is True
    assert inventory["missing"]["media_quality"]["status"] == "missing_photos"


def test_admin_inventory_media_uses_snapshot_without_live_crawl(monkeypatch):
    client, main, db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    token = main._create_admin_token()
    floorplan = "https://example.com/floor-plans.jpg"
    recovered_photo = "https://example.com/snapshot-ext-1.jpg"
    calls = {"snapshot": 0}

    db.collections["inventory"].clear()
    db.collections["inventory"]["snapshot-home"] = {
        "id": "snapshot-home",
        "model_name": "Snapshot Only Home",
        "manufacturer": "Snapshot Homes",
        "status": "AVAILABLE",
        "image_url": floorplan,
        "real_photos": [floorplan],
        "gallery_images": [floorplan],
        "floorplan_url": floorplan,
    }

    def live_loader(**_kwargs):
        raise AssertionError("admin inventory should not block on a live legacy crawl")

    live_loader.__module__ = "tools.legacy_site_crawler"
    monkeypatch.setattr(main, "load_legacy_inventory_context", live_loader)

    legacy_module = sys.modules["tools.legacy_site_crawler"]

    def snapshot_loader():
        calls["snapshot"] += 1
        return {
            "success": True,
            "homes": [
                {
                    "id": "snapshot-home",
                    "legacy_inventory_id": "snapshot-home",
                    "model_name": "Snapshot Only Home",
                    "image_url": recovered_photo,
                    "real_photos": [recovered_photo],
                    "gallery_images": [recovered_photo],
                    "floorplan_url": floorplan,
                    "media_quality": {
                        "status": "photo_ready",
                        "has_real_photo": True,
                        "photo_count": 1,
                        "floorplan_count": 1,
                    },
                }
            ],
        }

    monkeypatch.setattr(legacy_module, "_load_snapshot_context", snapshot_loader, raising=False)
    main._INVENTORY_MEDIA_INDEX_CACHE["loaded_at"] = 0.0
    main._INVENTORY_MEDIA_INDEX_CACHE["index"] = {}

    first = client.get("/api/inventory", headers={"X-Admin-Token": token})
    second = client.get("/api/inventory", headers={"X-Admin-Token": token})

    assert first.status_code == 200
    assert second.status_code == 200
    first_inventory = {item["id"]: item for item in first.json()["inventory"]}
    assert first_inventory["snapshot-home"]["image_url"] == recovered_photo
    assert calls["snapshot"] == 1


def test_marketing_inventory_context_falls_back_to_firestore_when_legacy_unavailable(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")

    firestore_home = {
        "id": "28102",
        "model_name": "NEW YEAR CLEARANCE SALE / TRU Single Section Delight",
        "gallery_images": ["https://example.com/live-photo.jpg"],
        "real_photos": ["https://example.com/live-photo.jpg"],
        "floor_plan_url": "https://example.com/live-floorplan.jpg",
        "matterport_id": "SvVRKXdXUQq",
        "matterport_url": "https://my.matterport.com/show/?m=SvVRKXdXUQq&play=1",
    }

    monkeypatch.setattr(
        main,
        "load_legacy_inventory_context",
        lambda **kwargs: {
            "success": False,
            "homes": [],
            "error": "legacy unavailable",
        },
    )
    monkeypatch.setattr(
        main,
        "get_inventory_for_ads",
        lambda **kwargs: {
            "success": True,
            "homes": [firestore_home.copy()],
            "total_inventory": 1,
        },
    )
    monkeypatch.setattr(
        main,
        "PROPERTY_ASSETS",
        {
            "catalog-home": {
                "name": "Catalog Home",
                "manufacturer": "New Vision Manufacturing",
                "is_new": True,
                "images": ["https://example.com/catalog.jpg"],
                "floor_plan": "https://example.com/catalog-floorplan.jpg",
                "matterport_id": "staleTour",
            }
        },
    )

    response = client.get("/api/marketing/inventory-context")

    assert response.status_code == 200
    data = response.json()
    assert data["total_inventory"] == 2
    assert data["website_homes"] == 0
    assert [home["id"] for home in data["homes"]] == ["28102", "catalog-catalog-home"]
    assert data["homes"][0]["floor_plan_url"] == "https://example.com/live-floorplan.jpg"
    assert data["homes"][0]["matterport_id"] == "SvVRKXdXUQq"
    assert data["homes"][1]["inventory_kind"] == "orderable_floorplan"


def test_marketing_inventory_context_enriches_floorplan_only_firestore_home(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    floorplan = "https://example.com/floor-plans.jpg"
    photo = "https://example.com/exterior.jpg"

    firestore_home = {
        "id": "floorplan-only",
        "model_name": "Floorplan Only",
        "gallery_images": [floorplan],
        "real_photos": [floorplan],
        "image_url": floorplan,
        "floor_plan_url": "",
    }

    monkeypatch.setattr(
        main,
        "load_legacy_inventory_context",
        lambda **kwargs: {
            "success": False,
            "homes": [],
        },
    )
    monkeypatch.setattr(
        main,
        "get_inventory_for_ads",
        lambda **kwargs: {
            "success": True,
            "homes": [firestore_home.copy()],
            "total_inventory": 1,
        },
    )
    sys.modules["tools.asset_scraper"].get_assets_for_home = lambda _name: {
        "images": [photo],
        "floor_plan": floorplan,
    }

    response = client.get("/api/marketing/inventory-context")

    assert response.status_code == 200
    home = response.json()["homes"][0]
    assert home["image_url"] == photo
    assert home["real_photos"] == [photo]
    assert home["gallery_images"] == [photo]
    assert home["floor_plan_url"] == floorplan
    assert home["media_quality"]["has_real_photo"] is True


def test_marketing_inventory_context_falls_back_to_asset_catalog_when_firestore_empty(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")

    monkeypatch.setattr(
        main,
        "load_legacy_inventory_context",
        lambda **kwargs: {
            "success": False,
            "homes": [],
        },
    )
    monkeypatch.setattr(
        main,
        "get_inventory_for_ads",
        lambda **kwargs: {
            "success": False,
            "homes": [],
            "total_inventory": 0,
        },
    )
    monkeypatch.setattr(
        main,
        "PROPERTY_ASSETS",
        {
            "fallback-home": {
                "name": "Fallback Home",
                "manufacturer": "THO",
                "beds": 3,
                "baths": 2,
                "sqft": 1200,
                "dims": "16x76",
                "images": ["https://example.com/fallback.jpg"],
                "floor_plan": "https://example.com/fallback-floorplan.jpg",
                "matterport_id": "fallbackTour",
                "is_new": True,
            }
        },
    )
    monkeypatch.setattr(
        main,
        "get_matterport_url",
        lambda tour_id: f"https://my.matterport.com/show/?m={tour_id}&play=1",
    )

    response = client.get("/api/marketing/inventory-context")

    assert response.status_code == 200
    data = response.json()
    assert data["total_inventory"] == 1
    assert data["website_homes"] == 1
    assert data["homes"][0]["id"] == "fallback-home"
    assert data["homes"][0]["matterport_url"].endswith("fallbackTour&play=1")


def test_admin_crm_funnel_returns_stage_counts_and_conversions(monkeypatch):
    client, main, db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    token = main._create_admin_token()

    # Reset cache so prior tests don't pollute
    from caching import clear_local_cache

    clear_local_cache()

    # Seed customers: 3 LEAD, 2 ENROLLED, 1 SOLD
    db.collections["customers"].clear()
    for i in range(3):
        db.collections["customers"][f"lead-{i}"] = {
            "id": f"lead-{i}",
            "full_name": f"Lead {i}",
            "status": "LEAD",
            "created_at": "2026-04-01T00:00:00",
            "updated_at": "2026-04-01T00:00:00",
        }
    for i in range(2):
        db.collections["customers"][f"enr-{i}"] = {
            "id": f"enr-{i}",
            "full_name": f"Enrolled {i}",
            "status": "ENROLLED",
            "created_at": "2026-04-01T00:00:00",
            "updated_at": "2026-04-08T00:00:00",
        }
    db.collections["customers"]["sold-0"] = {
        "id": "sold-0",
        "full_name": "Sold Zero",
        "status": "SOLD",
        "created_at": "2026-04-01T00:00:00",
        "updated_at": "2026-04-15T00:00:00",
    }

    # Seed deals: 2 active (pending, contract), 1 closed (funded), 1 denied
    db.collections["deals"].clear()
    db.collections["deals"]["d-pending"] = {"id": "d-pending", "status": "pending"}
    db.collections["deals"]["d-contract"] = {"id": "d-contract", "status": "contract"}
    db.collections["deals"]["d-funded"] = {
        "id": "d-funded",
        "status": "funded",
        "created_at": "2026-04-01T00:00:00",
        "updated_at": "2026-04-21T00:00:00",
    }
    db.collections["deals"]["d-denied"] = {"id": "d-denied", "status": "denied"}

    response = client.get("/api/admin/crm/funnel", headers={"X-Admin-Token": token})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["success"] is True

    stages = {s["key"]: s for s in data["stages"]}
    # Top of funnel = 3+2+1 = 6
    assert stages["LEAD"]["count"] == 6
    assert stages["LEAD"]["conversion_pct"] == 100.0
    # Enrolled = 2 + 1 (sold) = 3
    assert stages["ENROLLED"]["count"] == 3
    # Deal active = 2 active + 1 closed = 3
    assert stages["DEAL"]["count"] == 3
    # Closed = 1 funded + 1 sold customer = 2
    assert stages["CLOSED"]["count"] == 2

    totals = data["totals"]
    assert totals["customers_total"] == 6
    assert totals["deals_active"] == 2
    assert totals["deals_closed"] == 1
    assert totals["deals_denied"] == 1

    # Median time: enrolled = 7 days, sold = 14 days → median 7
    assert data["median_days_in_stage"]["LEAD_to_ENROLLED"] == 7.0
    # Deal closed: 20 days from 04-01 to 04-21
    assert data["median_days_in_stage"]["DEAL_to_CLOSED"] == 20.0


def test_admin_crm_funnel_requires_auth(monkeypatch):
    client, _main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    response = client.get("/api/admin/crm/funnel")
    assert response.status_code == 401


def test_admin_lead_sources_categorizes_and_attributes(monkeypatch):
    client, main, db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    token = main._create_admin_token()

    from caching import clear_local_cache

    clear_local_cache()

    # Replace the in-memory leads with a controlled mix of sources
    now = datetime.now(UTC)
    main.lead_manager.leads = [
        FakeLead(
            lead_id="L1",
            user_id="u1",
            session_id="s1",
            source="chat",
            email="alice@example.com",
            created_at=now.isoformat(),
        ),
        FakeLead(
            lead_id="L2",
            user_id="u2",
            session_id="s2",
            source="contact_form",
            phone="555-111-2222",
            created_at=now.isoformat(),
        ),
        FakeLead(
            lead_id="L3",
            user_id="u3",
            session_id="s3",
            source="contact_form",
            created_at=now.isoformat(),
        ),
        FakeLead(
            lead_id="L4",
            user_id="u4",
            session_id="s4",
            source="appointment",
            # Outside the 30-day window
            created_at=(now - timedelta(days=120)).isoformat(),
        ),
    ]

    # Funded deal that should attribute to L1 (email match)
    db.collections["deals"].clear()
    db.collections["deals"]["d-1"] = {
        "id": "d-1",
        "status": "funded",
        "buyer_email": "alice@example.com",
        "sale_price": 75000,
    }
    # Funded deal that should attribute to L2 (phone match, last 10 digits)
    db.collections["deals"]["d-2"] = {
        "id": "d-2",
        "status": "complete",
        "buyer_phone": "+1 (555) 111-2222",
        "sale_price": 90000,
    }
    # Pending deal — should NOT attribute revenue
    db.collections["deals"]["d-pending"] = {
        "id": "d-pending",
        "status": "pending",
        "buyer_email": "alice@example.com",
        "sale_price": 999999,
    }

    response = client.get("/api/admin/crm/lead-sources?days=30", headers={"X-Admin-Token": token})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["success"] is True
    assert data["window_days"] == 30
    assert data["total_leads"] == 3  # L4 outside window

    cats = {c["category"]: c for c in data["categories"]}
    assert cats["chat"]["count"] == 1
    assert cats["contact_form"]["count"] == 2
    assert "appointment" not in cats  # outside window

    # Attribution
    assert cats["chat"]["attributed_deals"] == 1
    assert cats["chat"]["attributed_revenue"] == 75000.0
    assert cats["contact_form"]["attributed_deals"] == 1
    assert cats["contact_form"]["attributed_revenue"] == 90000.0

    # pct sums approximately to 100 (within rounding)
    total_pct = sum(c["pct"] for c in data["categories"])
    assert 99.0 <= total_pct <= 101.0


def test_admin_lead_sources_clamps_days_argument(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    token = main._create_admin_token()

    from caching import clear_local_cache

    clear_local_cache()

    response = client.get("/api/admin/crm/lead-sources?days=9999", headers={"X-Admin-Token": token})
    assert response.status_code == 200
    assert response.json()["window_days"] == 365

    response = client.get("/api/admin/crm/lead-sources?days=0", headers={"X-Admin-Token": token})
    assert response.status_code == 200
    assert response.json()["window_days"] == 1


def test_admin_lead_sources_requires_auth(monkeypatch):
    client, _main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    response = client.get("/api/admin/crm/lead-sources")
    assert response.status_code == 401


def test_admin_inventory_analytics_returns_full_report(monkeypatch):
    client, main, db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    token = main._create_admin_token()

    from caching import clear_local_cache

    clear_local_cache()

    now = datetime.now(UTC)

    db.collections["inventory"].clear()
    # Available home, listed 30 days ago
    db.collections["inventory"]["a-1"] = {
        "id": "a-1",
        "model_name": "Avail A",
        "manufacturer": "Champion",
        "status": "AVAILABLE",
        "msrp": 80000,
        "sale_price": 75000,
        "date_added": (now - timedelta(days=30)).isoformat(),
    }
    # Available home, listed 10 days ago
    db.collections["inventory"]["a-2"] = {
        "id": "a-2",
        "model_name": "Avail B",
        "manufacturer": "Clayton",
        "status": "AVAILABLE",
        "msrp": 95000,
        "date_added": (now - timedelta(days=10)).isoformat(),
    }
    # Recently sold (within 30 days)
    db.collections["inventory"]["s-1"] = {
        "id": "s-1",
        "model_name": "Sold A",
        "manufacturer": "Champion",
        "status": "SOLD",
        "sale_price": 70000,
        "date_added": (now - timedelta(days=45)).isoformat(),
        "updated_at": (now - timedelta(days=5)).isoformat(),
    }
    # Sold long ago (outside 30d window)
    db.collections["inventory"]["s-2"] = {
        "id": "s-2",
        "model_name": "Sold B",
        "manufacturer": "Clayton",
        "status": "SOLD",
        "sale_price": 100000,
        "date_added": (now - timedelta(days=200)).isoformat(),
        "updated_at": (now - timedelta(days=100)).isoformat(),
    }
    # Reserved
    db.collections["inventory"]["r-1"] = {
        "id": "r-1",
        "model_name": "Reserved",
        "manufacturer": "Champion",
        "status": "RESERVED",
        "sale_price": 90000,
    }

    response = client.get("/api/admin/inventory/analytics", headers={"X-Admin-Token": token})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["success"] is True

    totals = data["totals"]
    assert totals["total"] == 5
    assert totals["available"] == 2
    assert totals["sold_total"] == 2
    assert totals["sold_last_30d"] == 1  # only s-1
    assert totals["reserved"] == 1

    # Median sale price: [70000, 75000, 90000, 95000, 100000] → 90000
    assert data["median_sale_price"] == 90000

    mfr = {m["manufacturer"]: m for m in data["by_manufacturer"]}
    assert mfr["Champion"]["count"] == 3
    assert mfr["Champion"]["available"] == 1
    assert mfr["Champion"]["sold"] == 1
    assert mfr["Clayton"]["count"] == 2

    # Time-on-lot only computed for SOLD + AVAILABLE entries
    assert data["median_time_on_lot_days"] is not None


def test_admin_inventory_analytics_requires_auth(monkeypatch):
    client, _main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    response = client.get("/api/admin/inventory/analytics")
    assert response.status_code == 401


def test_admin_token_accepts_supported_employee_headers(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    token = main._create_admin_token()

    x_header = client.get("/api/admin/check", headers={"X-Admin-Token": token})
    bearer_header = client.get("/api/admin/check", headers={"Authorization": f"Bearer {token}"})
    protected_route = client.get(
        "/api/documents/templates", headers={"Authorization": f"Bearer {token}"}
    )
    legacy_templates_route = client.get(
        "/api/document-templates", headers={"Authorization": f"Bearer {token}"}
    )

    assert x_header.status_code == 200
    assert bearer_header.status_code == 200
    assert protected_route.status_code == 200
    assert legacy_templates_route.status_code == 200
    assert legacy_templates_route.json().keys() >= {"templates", "packets"}


def test_admin_check_returns_false_without_auth(monkeypatch):
    client, _main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")

    response = client.get("/api/admin/check")

    assert response.status_code == 200
    assert response.json() == {"valid": False}


def test_cloud_run_admin_auth_fails_closed_without_pin_hash(monkeypatch):
    """App refuses to start in Cloud Run without ADMIN_PIN_HASH set."""
    monkeypatch.setenv("K_SERVICE", "project-go-forward")
    monkeypatch.delenv("ADMIN_PIN_HASH", raising=False)

    # Clear cached main module so re-import sees the new env. Use
    # monkeypatch.delitem (NOT sys.modules.pop) so the real modules are RESTORED
    # on teardown — a raw pop leaves them absent and silently breaks later tests'
    # patch("appointment_manager...") targets (see the create_client note above).
    for module_name in (
        "main",
        "structured_logging",
        "conversation_memory",
        "chat_history",
        "lead_management",
        "appointment_manager",
        "email_service",
        "database.firestore_client",
        "database.models",
    ):
        monkeypatch.delitem(sys.modules, module_name, raising=False)

    # Inject lightweight stubs so import doesn't need live Firestore / GCS
    fake_logger = FakeStructuredLogger()
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
    lead_management_module.normalize_phone = lambda phone: phone
    monkeypatch.setitem(sys.modules, "lead_management", lead_management_module)

    appointment_manager_module = types.ModuleType("appointment_manager")
    appointment_manager_module.AppointmentManager = FakeAppointmentManager
    appointment_manager_module.Appointment = FakeAppointment
    monkeypatch.setitem(sys.modules, "appointment_manager", appointment_manager_module)

    email_service_module = types.ModuleType("email_service")
    email_service_module.send_admin_login_code = lambda *a, **k: {"success": True}
    email_service_module.send_appointment_confirmation = lambda *a, **k: {"success": True}
    email_service_module.send_lead_welcome = lambda *a, **k: {"success": True}
    email_service_module.send_deal_status_update = lambda *a, **k: {"success": True}
    email_service_module.send_custom_email = lambda *a, **k: {"success": True}
    email_service_module.send_document_email = lambda *a, **k: {"success": True}
    email_service_module.get_email_log = lambda *a, **k: []
    email_service_module.notify_new_lead = lambda *a, **k: {"success": True}
    email_service_module.notify_new_appointment = lambda *a, **k: {"success": True}
    monkeypatch.setitem(sys.modules, "email_service", email_service_module)

    firestore_client_module = types.ModuleType("database.firestore_client")
    firestore_client_module.get_database = lambda: FakeTHODatabase()
    monkeypatch.setitem(sys.modules, "database.firestore_client", firestore_client_module)

    with pytest.raises(RuntimeError, match="ADMIN_PIN_HASH is mandatory"):
        import main  # noqa: F401


def test_admin_lead_analytics_handles_string_and_datetime_created_at(monkeypatch):
    client, main, _db, logger = create_client(monkeypatch, tho_api_key="tho-secret")
    token = main._create_admin_token()
    now = datetime.now(UTC)
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
        entry
        for entry in logger.entries
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
        lambda: [
            {
                "filename": "cloud.pdf",
                "size_bytes": 2048,
                "created_at": "2026-05-16T00:00:00+00:00",
                "download_url": "/api/documents/download/cloud.pdf",
            }
        ],
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


def test_document_history_hides_synthetic_artifacts_by_default(monkeypatch, tmp_path):
    client, main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    token = main._create_admin_token()
    headers = {"X-Admin-Token": token}

    (tmp_path / "TMHA_SalesContract_Client_Ready_20260505.pdf").write_bytes(b"%PDF-1.4\n%EOF\n")
    (tmp_path / "TMHA_SalesContract_Joe_Testbuyer_20260515.pdf").write_bytes(b"%PDF-1.4\n%EOF\n")
    (tmp_path / "Documents_Another_Test_20260514_180634.pdf").write_bytes(b"%PDF-1.4\n%EOF\n")
    (tmp_path / "TMHA_SalesContract_Prod_Smoke20260515_1921_20260515.pdf").write_bytes(
        b"%PDF-1.4\n%EOF\n"
    )
    (tmp_path / "TMHA_SalesContract_Test_Buyer_20260505.pdf").write_bytes(b"%PDF-1.4\n%EOF\n")
    (tmp_path / "_batch_TMHA_SalesContract_Smoke_Buyer_20260506052403.pdf").write_bytes(
        b"%PDF-1.4\n%EOF\n"
    )
    (tmp_path / "TMHA_SalesContract_Quality_Buyer_20260515.pdf").write_bytes(b"%PDF-1.4\n%EOF\n")
    (tmp_path / "Documents_Garett_T_Floyd_20260515_225013.pdf").write_bytes(b"%PDF-1.4\n%EOF\n")
    (tmp_path / "Documents_UiBurnin20260515215603_Buyer_20260515_215609.pdf").write_bytes(
        b"%PDF-1.4\n%EOF\n"
    )
    (tmp_path / "Documents_Ui_Burnin_Buyer_20260515151251_Final_20260515_211301.pdf").write_bytes(
        b"%PDF-1.4\n%EOF\n"
    )
    (tmp_path / "Documents_Jane_Doe_20260515_202638.pdf").write_bytes(b"%PDF-1.4\n%EOF\n")
    (tmp_path / "TMHA-TwoPartyContract191220_Real_Buyer_20260515.pdf").write_bytes(
        b"%PDF-1.4\n%EOF\n"
    )
    (tmp_path / "TMHA_SalesContract_E2E_Batch_Tester_20260513.pdf").write_bytes(b"%PDF-1.4\n%EOF\n")
    (tmp_path / "_batch_TMHA_SalesContract_QA_Browser_20260511173103.pdf").write_bytes(
        b"%PDF-1.4\n%EOF\n"
    )
    (tmp_path / "Documents_Browser_Smoke_Buyer_20260522.pdf").write_bytes(b"%PDF-1.4\n%EOF\n")
    (tmp_path / "Documents_Document_UI_Smoke_Buyer_20260522224722_20260522.pdf").write_bytes(
        b"%PDF-1.4\n%EOF\n"
    )
    monkeypatch.setattr(main, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(
        main,
        "list_gcs_documents",
        lambda: [
            {
                "filename": "TDHCA_1038_Consumer_Disclosure_Real_Buyer_20260505.pdf",
                "size_bytes": 4096,
                "created_at": "2026-05-16T00:00:00+00:00",
                "download_url": (
                    "/api/documents/download/"
                    "TDHCA_1038_Consumer_Disclosure_Real_Buyer_20260505.pdf"
                ),
            },
            {
                "filename": "TDHCA_1038_Consumer_Disclosure_Smoke_Buyer_20260505.pdf",
                "size_bytes": 2048,
                "created_at": "2026-05-05T23:00:00+00:00",
                "download_url": (
                    "/api/documents/download/"
                    "TDHCA_1038_Consumer_Disclosure_Smoke_Buyer_20260505.pdf"
                ),
            },
        ],
    )

    default_response = client.get("/api/documents/history", headers=headers)
    include_test_response = client.get("/api/documents/history?include_test=true", headers=headers)

    assert default_response.status_code == 200
    body = default_response.json()
    default_filenames = [doc["filename"] for doc in body["documents"]]
    assert default_filenames == [
        "TMHA_SalesContract_Client_Ready_20260505.pdf",
        "TDHCA_1038_Consumer_Disclosure_Real_Buyer_20260505.pdf",
    ]
    assert body["total"] == 2
    assert body["total_including_test"] == 18
    assert body["hidden_test_document_count"] == 16
    assert body["hidden_document_count"] == 16
    assert body["hidden_quality_document_count"] == 2
    assert body["hidden_legacy_document_count"] == 1

    assert include_test_response.status_code == 200
    include_test_body = include_test_response.json()
    assert include_test_body["total"] == 18
    assert any(doc["synthetic"] for doc in include_test_body["documents"])
    assert any(doc["quality_blocked"] for doc in include_test_body["documents"])
    assert any(doc["legacy_unverified"] for doc in include_test_body["documents"])


def test_admin_create_customer_manual_payload_sanitizes_nested_sensitive_fields(monkeypatch):
    client, main, db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    token = main._create_admin_token()

    response = client.post(
        "/api/customers",
        headers={"X-Admin-Token": token},
        json={
            "legacy_source": "manual",
            "full_name": "Manual Buyer",
            "email": "Manual.Buyer@Example.com",
            "phone": "(555) 222-3333",
            "status": "lead",
            "address": "<b>123 Test Way</b>",
            "city": "Huffman",
            "state": "TX",
            "zip_code": "77336",
            "marital_status": "Single",
            "employer": "THO Test Employer",
            "occupation": "Operator",
            "salesrep": "Ari",
            "notes": "<script>alert(1)</script> synthetic manual customer",
            "buyer_ssn": "123-45-6789",
            "ssn_hash": "raw-hash-should-not-store",
            "co_buyer": {
                "full_name": "Co Buyer",
                "phone": "555-333-4444",
                "ssn": "999-88-7777",
                "ssn_hash": "nested-hash-should-not-store",
                "ssn_masked": "***-**-7777",
            },
            "references": [{"name": "Reference One", "ssn": "111-22-3333"}],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    customer = body["customer"]
    assert customer["email"] == "manual.buyer@example.com"
    assert customer["status"] == "LEAD"
    assert customer["address"] == "123 Test Way"
    assert customer["notes"] == "alert(1) synthetic manual customer"
    assert customer["ssn_masked"] == "***-**-6789"
    assert "ssn_hash" not in customer
    assert "ssn" not in customer["co_buyer"]
    assert "ssn_hash" not in customer["co_buyer"]
    assert "ssn" not in customer["references"][0]

    stored = db.collections["customers"][customer["id"]]
    assert stored["_name_lower"] == "manual buyer"
    assert stored["co_buyer"]["ssn_masked"] == "***-**-7777"
    assert "ssn" not in stored["co_buyer"]
    assert "ssn_hash" not in stored["co_buyer"]

    search_response = client.get(
        "/api/customers/search?q=Manual&limit=5",
        headers={"X-Admin-Token": token},
    )
    assert search_response.status_code == 200
    assert any(c["id"] == customer["id"] for c in search_response.json()["customers"])


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
        a for a in fake_db.collections["activities"].values() if a.get("deal_id") == "deal-3"
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
    partner_logs = [
        entry for entry in fake_logger.entries if entry["message"] == "Partner API request"
    ]
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
        tho_api_key="primary-fixture",  # pragma: allowlist secret
    )
    monkeypatch.setenv("THO_API_KEY_ETAI", "etai-secret")
    monkeypatch.setenv("THO_API_KEY_N8N", "n8n-secret")

    # Primary
    r1 = client.get("/api/v1/stats", headers={"Authorization": "Bearer primary-fixture"})
    # Etai's key
    r2 = client.get("/api/v1/stats", headers={"Authorization": "Bearer etai-secret"})
    # n8n's key
    r3 = client.get("/api/v1/stats", headers={"X-API-Key": "n8n-secret"})

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 200

    # Audit log should record which partner slot matched
    partner_logs = [
        e
        for e in fake_logger.entries
        if e["message"] == "Partner API request" and e["auth_status"] == "accepted"
    ]
    matched_slots = {e.get("partner_id") for e in partner_logs[-3:]}
    assert matched_slots == {"THO_API_KEY", "THO_API_KEY_ETAI", "THO_API_KEY_N8N"}


def test_revoking_one_partner_key_does_not_affect_others(monkeypatch):
    """Simulate removing THO_API_KEY_ETAI — primary key keeps working."""
    client, _main, _db, _logger = create_client(
        monkeypatch,
        tho_api_key="primary-fixture",  # pragma: allowlist secret
    )
    monkeypatch.setenv("THO_API_KEY_ETAI", "etai-secret")

    # Both work while configured
    assert (
        client.get("/api/v1/stats", headers={"Authorization": "Bearer etai-secret"}).status_code
        == 200
    )

    # Revoke Etai's slot
    monkeypatch.delenv("THO_API_KEY_ETAI")

    # Etai's key now invalid, primary still works
    assert (
        client.get("/api/v1/stats", headers={"Authorization": "Bearer etai-secret"}).status_code
        == 401
    )
    assert (
        client.get("/api/v1/stats", headers={"Authorization": "Bearer primary-fixture"}).status_code
        == 200
    )


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

    assert (
        client.get(
            "/api/v1/stats", headers={"Authorization": "Bearer etai-only-secret"}
        ).status_code
        == 200
    )
    assert client.get("/api/v1/stats", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_v1_service_request_resolve_success(monkeypatch):
    """Partner can resolve a service request by ID."""
    client, _main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")

    response = client.post(
        "/api/v1/service-requests/sr-1/resolve", headers={"Authorization": "Bearer tho-secret"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "resolved"
    assert response.json()["id"] == "sr-1"

    assert _db.collections["service_requests"]["sr-1"]["status"] == "resolved"


def test_v1_service_request_resolve_not_found(monkeypatch):
    """Partner gets 404 for unknown service request."""
    client, _main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")

    response = client.post(
        "/api/v1/service-requests/unknown-123/resolve",
        headers={"Authorization": "Bearer tho-secret"},
    )
    assert response.status_code == 404


# ─── Mira lead-triage bridge ────────────────────────────────────────────────


def test_mira_leads_recent_returns_recent_leads(monkeypatch):
    """The /leads/recent bridge endpoint must not 404 after the timedelta fix."""
    client, _main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    headers = {"Authorization": "Bearer tho-secret"}

    response = client.get("/api/v1/mira/leads/recent?hours=24&limit=10", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "leads" in data
    assert data["count"] == len(data["leads"])


def test_mira_leads_triage_surfaces_new_leads(monkeypatch):
    """Mira can fetch the cohort of leads awaiting triage."""
    client, _main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    headers = {"Authorization": "Bearer tho-secret"}

    response = client.get("/api/v1/mira/leads/triage?status=new&limit=50", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["filter"] == {"status": "new", "min_age_hours": None}
    assert data["count"] >= 1
    lead = data["leads"][0]
    assert "lead_id" in lead
    assert "name" not in lead
    assert "email" not in lead
    assert "phone" not in lead
    assert "priority" in lead


def test_mira_leads_triage_respects_min_age_hours(monkeypatch):
    """Only leads older than min_age_hours are returned."""
    client, _main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    headers = {"Authorization": "Bearer tho-secret"}
    mira_routes = sys.modules["mira_routes"]

    class _AgeFilteredManager(FakeLeadManager):
        def __init__(self, project_id=None):
            super().__init__(project_id=project_id)
            self.leads = [
                FakeLead(
                    lead_id="fresh-lead",
                    user_id="u1",
                    session_id="s1",
                    status="new",
                    created_at=datetime.now(UTC).isoformat(),
                ),
                FakeLead(
                    lead_id="old-lead",
                    user_id="u2",
                    session_id="s2",
                    status="new",
                    created_at=(datetime.now(UTC) - timedelta(hours=72)).isoformat(),
                ),
            ]

    monkeypatch.setattr(mira_routes, "LeadManager", _AgeFilteredManager)

    response = client.get("/api/v1/mira/leads/triage?status=new&min_age_hours=48", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    ids = {lead["lead_id"] for lead in data["leads"]}
    assert "old-lead" in ids
    assert "fresh-lead" not in ids


def test_mira_update_lead_triage_updates_and_dispatches_webhook(monkeypatch):
    """Triage update mutates the lead and emits a signed partner webhook."""
    client, _main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    headers = {"Authorization": "Bearer tho-secret"}
    mira_routes = sys.modules["mira_routes"]

    dispatched: list[dict] = []

    def _fake_dispatch(event, payload, db=None, **kwargs):
        dispatched.append({"event": event, "payload": payload, "db": db is not None})
        return ["mira"]

    monkeypatch.setattr(mira_routes, "dispatch_partner_event", _fake_dispatch)

    response = client.post(
        "/api/v1/mira/leads/lead-1/triage",
        headers=headers,
        json={
            "status": "qualified",
            "priority": "high",
            "assigned_to": "sales-rep-jordan",
            "triage_reason": "hot_lead",
            "triage_notes": "Called within 5 minutes",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    lead = data["lead"]
    assert lead["status"] == "qualified"
    assert lead["priority"] == "high"
    assert lead["assigned_to"] == "sales-rep-jordan"
    assert lead["triage_reason"] == "hot_lead"
    assert lead["last_triage_at"] is not None

    assert len(dispatched) == 1
    assert dispatched[0]["event"] == "lead.triage_updated"
    assert dispatched[0]["payload"]["lead_id"] == "lead-1"
    assert dispatched[0]["payload"]["old_status"] == "new"
    assert dispatched[0]["payload"]["new_status"] == "qualified"
    assert dispatched[0]["payload"]["assigned_to"] == "sales-rep-jordan"
    assert "name" not in dispatched[0]["payload"]
    assert "email" not in dispatched[0]["payload"]


def test_mira_update_lead_triage_rejects_invalid_status(monkeypatch):
    """Unknown lead statuses are rejected before touching Firestore."""
    client, _main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    headers = {"Authorization": "Bearer tho-secret"}

    response = client.post(
        "/api/v1/mira/leads/lead-1/triage",
        headers=headers,
        json={"status": "bogus_status"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert "Invalid status" in data["error"]


def test_mira_update_lead_triage_not_found(monkeypatch):
    """Triage update for a missing lead returns a structured error."""
    client, _main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    headers = {"Authorization": "Bearer tho-secret"}

    response = client.post(
        "/api/v1/mira/leads/missing-lead/triage",
        headers=headers,
        json={"priority": "medium"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert "not found" in data["error"].lower()


def test_create_inventory_strips_dealer_cost(monkeypatch):
    """Dealer COST must never be persisted into the public-served inventory store."""
    client, main, fake_db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    token = main._create_admin_token()
    resp = client.post(
        "/api/inventory",
        json={
            "model_name": "The Nassau",
            "invoice_amount": 60187.0,
            "invoice_date": "2026-01-01",
            "cost": 50000,
        },
        headers={"X-Admin-Token": token},
    )
    assert resp.status_code == 200
    stored = fake_db.collections["inventory"][resp.json()["id"]]
    for forbidden in ("invoice_amount", "invoice_date", "cost"):
        assert forbidden not in stored
    assert stored["model_name"] == "The Nassau"


def test_ops_snapshot_requires_admin(monkeypatch):
    client, _main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    assert client.get("/api/admin/ops-snapshot").status_code == 401


def test_ops_snapshot_returns_counts(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    from tools import ops_copilot

    async def _fake_snapshot():
        return {"leads": {"total": 3}, "operations": {"title": {"by_status": {"Title Issued": 5}}}}

    monkeypatch.setattr(ops_copilot, "get_business_snapshot", _fake_snapshot)
    token = main._create_admin_token()
    resp = client.get("/api/admin/ops-snapshot", headers={"X-Admin-Token": token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["snapshot"]["operations"]["title"]["by_status"] == {"Title Issued": 5}
