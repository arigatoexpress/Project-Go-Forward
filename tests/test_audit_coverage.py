"""Audit-coverage tests for newly-instrumented sensitive/admin actions.

Backs the SECURITY.md §8 audit backlog: every sensitive admin / outward
action emits a structured audit-log entry, and NO sensitive value (recipient
email, message body, PII field values) ends up in the persisted entry.

These complement test_audit_log.py (which covers the audit_log helper itself)
by asserting the new call sites in main.py actually fire and stay PII-safe.

Run: python -m pytest tests/test_audit_coverage.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.test_api_v1 import load_app  # noqa: E402
from tests.test_audit_log import FakeFirestore  # noqa: E402


def _make_admin_client(monkeypatch):
    """Build the app with the audit-log Firestore client swapped for a fake.

    Returns (client, main, headers, audit_store) where audit_store is the
    in-memory FakeFirestore whose ``audit_log`` collection accumulates the
    entries emitted by the instrumented endpoints.
    """
    main, _fake_db, _fake_logger = load_app(monkeypatch, tho_api_key="tho-secret")
    main._crm_tasks.clear()

    fake = FakeFirestore()
    import audit_log

    # log_admin_action calls audit_log._get_db() lazily, so swapping the cache
    # + the accessor is enough to capture every write the endpoints make.
    audit_log._get_db = lambda: fake  # type: ignore[attr-defined]

    client = TestClient(main.app)
    token = main._create_admin_token()
    headers = {"Authorization": f"Bearer {token}"}
    return client, main, headers, fake


def _audit_entries(store: FakeFirestore) -> list[dict]:
    coll = store.collections.get("audit_log")
    return list(coll._docs) if coll else []


# ─── New actions are first-class in the allowlist (no drift warnings) ────────


def test_new_actions_in_allowlist():
    """The actions the new call sites emit must be in ALLOWED_ACTIONS so they
    do not trip the unknown-action drift warning on every legit request."""
    from audit_log import ALLOWED_ACTIONS, ALLOWED_TARGET_TYPES

    for action in (
        "document.esign_send",
        "inventory.bulk_import",
        "inventory.photos_upload",
        "inventory.photos_reorder",
        "inventory.photo_delete",
        "lead.update",
        "lead.lifecycle_transition",
        "crm_task.create",
        "crm_task.update",
        "email.send",
    ):
        assert action in ALLOWED_ACTIONS, f"{action} missing from ALLOWED_ACTIONS"

    for target in ("inventory", "lead", "crm_task", "email"):
        assert target in ALLOWED_TARGET_TYPES, f"{target} missing from ALLOWED_TARGET_TYPES"


# ─── Email send: emitted AND recipient/body redacted ────────────────────────


def test_email_send_emits_audit_without_recipient_or_body(monkeypatch):
    client, _main, headers, store = _make_admin_client(monkeypatch)

    secret_recipient = "private.buyer@example.com"
    secret_body = "Your SSN 123-45-6789 was approved, call 555-867-5309."
    secret_subject = "Confidential closing details for 123 Main St"

    resp = client.post(
        "/api/email/send",
        headers=headers,
        json={
            "to": secret_recipient,
            "customer_name": "Jane Buyer",
            "subject": secret_subject,
            "message": secret_body,
        },
    )
    assert resp.status_code == 200, resp.text

    entries = _audit_entries(store)
    sends = [e for e in entries if e.get("action") == "email.send"]
    assert len(sends) == 1, f"expected 1 email.send audit entry, got {entries}"
    entry = sends[0]

    # Schema essentials present.
    assert entry["target_type"] == "email"
    assert entry["details"]["subject_len"] == len(secret_subject)
    assert entry["details"]["message_len"] == len(secret_body)
    assert entry["details"]["recipient_fingerprint"]

    # REDACTION PROOF: no sensitive value anywhere in the serialized entry.
    flat = repr(entry)
    for sensitive in (
        secret_recipient,
        "private.buyer",
        secret_body,
        "123-45-6789",
        "555-867-5309",
        secret_subject,
        "123 Main St",
        "Jane Buyer",
    ):
        assert sensitive not in flat, f"sensitive value leaked into audit log: {sensitive!r}"

    # The fingerprint is a stable SHA256 prefix of the lowercased address — it
    # must NOT be reversible to the address and must differ from the raw value.
    import hashlib

    expected_fp = hashlib.sha256(secret_recipient.lower().encode("utf-8")).hexdigest()[:12]
    assert entry["details"]["recipient_fingerprint"] == expected_fp
    assert secret_recipient not in entry["details"]["recipient_fingerprint"]


# ─── Lead update: emitted, logs changed field NAMES only (not values) ───────


def test_lead_update_rejects_contact_and_lifecycle_fields_without_writing(monkeypatch):
    client, _main, headers, store = _make_admin_client(monkeypatch)

    before = _main.lead_manager.leads[0].to_dict()
    resp = client.put(
        "/api/leads/lead-1",
        headers=headers,
        json={
            "status": "qualified",
            "email": "new.secret@example.com",
            "phone": "555-111-2222",
        },
    )
    assert resp.status_code == 400, resp.text
    assert _main.lead_manager.leads[0].to_dict() == before
    assert _audit_entries(store) == []


def test_safe_lead_triage_update_emits_field_names_only(monkeypatch):
    client, _main, headers, store = _make_admin_client(monkeypatch)

    resp = client.put(
        "/api/leads/lead-1",
        headers=headers,
        json={"priority": "high", "assigned_to": "sales-desk"},
    )
    assert resp.status_code == 200, resp.text

    entries = _audit_entries(store)
    updates = [e for e in entries if e.get("action") == "lead.update"]
    assert len(updates) == 1, f"expected 1 lead.update entry, got {entries}"
    entry = updates[0]
    assert entry["target_type"] == "lead"
    assert entry["target_id"] == "lead-1"
    # Field NAMES are recorded (audit contract = field-name deltas)...
    assert set(entry["details"]["fields"]) == {"priority", "assigned_to"}

    # The assigned rep value is operational identity and still does not belong
    # in this field-name-only audit record.
    flat = repr(entry)
    assert "sales-desk" not in flat


def test_lead_lifecycle_transition_is_explicit_and_pii_safe(monkeypatch):
    client, _main, headers, store = _make_admin_client(monkeypatch)

    resp = client.patch(
        "/api/leads/lead-1/lifecycle",
        headers=headers,
        json={"status": "contacted"},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["success"] is True
    assert payload["changed"] is True
    assert payload["lead"]["status"] == "contacted"
    assert payload["lead"]["first_contacted_at"]

    entries = _audit_entries(store)
    transitions = [e for e in entries if e.get("action") == "lead.lifecycle_transition"]
    assert len(transitions) == 1
    assert transitions[0]["details"] == {
        "from_status": "new",
        "to_status": "contacted",
        "changed": True,
    }
    flat = repr(transitions[0])
    assert "lead@example.com" not in flat
    assert "555-999-8888" not in flat


def test_lead_lifecycle_rejects_mixed_or_invalid_payload_without_writing(monkeypatch):
    client, _main, headers, store = _make_admin_client(monkeypatch)
    before = _main.lead_manager.leads[0].to_dict()

    mixed = client.patch(
        "/api/leads/lead-1/lifecycle",
        headers=headers,
        json={"status": "contacted", "email": "attacker@example.com"},
    )
    invalid = client.patch(
        "/api/leads/lead-1/lifecycle",
        headers=headers,
        json={"status": "won-ish"},
    )

    assert mixed.status_code == 400, mixed.text
    assert invalid.status_code == 400, invalid.text
    assert _main.lead_manager.leads[0].to_dict() == before
    assert _audit_entries(store) == []


# ─── CRM task create: emitted, no free-text content persisted ───────────────


def test_crm_task_create_emits_audit_without_content(monkeypatch):
    client, _main, headers, store = _make_admin_client(monkeypatch)

    resp = client.post(
        "/api/crm/tasks",
        headers=headers,
        json={
            "title": "Call buyer about SSN on file 123-45-6789",
            "description": "Sensitive note: buyer phone 555-867-5309",
            "priority": "high",
            "related_lead": "lead-demo",
        },
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["task"]["task_id"]

    entries = _audit_entries(store)
    creates = [e for e in entries if e.get("action") == "crm_task.create"]
    assert len(creates) == 1, f"expected 1 crm_task.create entry, got {entries}"
    entry = creates[0]
    assert entry["target_type"] == "crm_task"
    assert entry["target_id"] == task_id
    assert entry["details"]["priority"] == "high"
    assert entry["details"]["related_lead"] == "lead-demo"

    # Free-text title/description (which may carry PII) is NOT in the entry.
    flat = repr(entry)
    assert "123-45-6789" not in flat
    assert "555-867-5309" not in flat
    assert "Sensitive note" not in flat
