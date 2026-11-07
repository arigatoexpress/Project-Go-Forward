"""Feature Cloud Logging lines stay specific and free of secrets.

PR 496 owns sign-in, passkey, page-view, and generic staff.action lines.
These tests cover the extra feature line emitted from the existing audit helper.
"""

from __future__ import annotations

import json
import logging

import pytest

from staff_feature_log import emit_staff_feature_event


class _Collection:
    def __init__(self):
        self._docs: list[dict] = []

    def add(self, entry: dict, timeout: float | None = None):
        self._docs.append(dict(entry))


class FakeFirestore:
    def __init__(self):
        self.collections: dict[str, _Collection] = {}

    def collection(self, name: str) -> _Collection:
        return self.collections.setdefault(name, _Collection())


_SECRET_PIN = "zz-pin-marker-9f3a"  # pragma: allowlist secret
_SECRET_CODE = "zz-code-marker-918273"  # pragma: allowlist secret
_SECRET_EMAIL = "buyer@example.com"  # pragma: allowlist secret
_SECRET_PHONE = "555-867-5309"  # pragma: allowlist secret
_SECRET_SSN = "123-45-6789"  # pragma: allowlist secret
_SECRET_BODY = "Please call me about lot 12 and send the packet."  # pragma: allowlist secret
_TRACEBACK = "Traceback (most recent call last): ValueError: boom buyer@example.com"


def _feature_lines(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        if '"feature":' not in line:
            continue
        rows.append(json.loads(line))
    return rows


def _bind_audit_db(monkeypatch) -> FakeFirestore:
    import audit_log

    fake = FakeFirestore()
    monkeypatch.setattr(audit_log, "_get_db", lambda: fake)
    return fake


def test_feature_line_names_photos_leads_documents_and_email(capsys):
    cases = (
        (
            "inventory.photos_upload",
            "photos",
            "43372",
            {"uploaded_count": 2, "error_count": 0},
        ),
        (
            "lead.lifecycle_transition",
            "leads",
            "lead-9",
            {"from_status": "new", "to_status": "contacted"},
        ),
        (
            "document.generate",
            "documents",
            "deal-4",
            {"template_name": "sales-contract", "deal_id": "deal-4"},
        ),
        (
            "email.send",
            "email",
            "should-not-appear",
            {"email_type": "custom", "delivered": True, "subject_len": 12},
        ),
    )
    for action, feature, target_id, details in cases:
        emit_staff_feature_event(
            action=action,
            actor="admin:abc123def456",
            target_type="inventory" if feature == "photos" else "deal",
            target_id=target_id,
            details=details,
            outcome="ok",
        )

    rows = _feature_lines(capsys.readouterr().out)
    assert [row["feature"] for row in rows] == ["photos", "leads", "documents", "email"]
    assert rows[0]["event"] == "inventory.photos_upload"
    assert rows[0]["target_id"] == "43372"
    assert rows[0]["lane"] == "pgf"
    assert rows[0]["service"] == "staff-admin"
    assert rows[0]["outcome"] == "ok"
    email = rows[3]
    assert email["email_type"] == "custom"
    assert "target_id" not in email
    assert "should-not-appear" not in json.dumps(email)


def test_field_names_with_underscores_stay_on_the_feature_line(capsys):
    emit_staff_feature_event(
        action="lead.update",
        actor="admin:abc123def456",
        target_type="lead",
        target_id="lead-9",
        details={"fields": ["assigned_to", "triage_notes", "priority"]},
    )
    rows = _feature_lines(capsys.readouterr().out)
    assert rows[0]["fields"] == ["assigned_to", "triage_notes", "priority"]


def test_sign_in_actions_do_not_emit_a_second_cloud_line(capsys):
    for action in ("admin.login", "admin.logout", "admin.login_code.request"):
        emit_staff_feature_event(
            action=action,
            actor="admin:abc123def456",
            target_type="session",
            target_id="admin:abc123def456",
            details={"method": "pin", "pin": _SECRET_PIN, "code": _SECRET_CODE},
        )
    assert _feature_lines(capsys.readouterr().out) == []


def test_allowlist_drops_secrets_bodies_exceptions_and_contact_values(capsys):
    emit_staff_feature_event(
        action="document.generate",
        actor="buyer@example.com",
        target_type="document",
        target_id="generated_docs/Jane_Doe_555-867-5309.pdf",
        details={
            "template_name": "sales-contract",
            "deal_id": "deal-4",
            "pin": _SECRET_PIN,
            "code": _SECRET_CODE,
            "email": _SECRET_EMAIL,
            "phone": _SECRET_PHONE,
            "ssn": _SECRET_SSN,
            "to": _SECRET_EMAIL,
            "subject": "Your approval",
            "message": _SECRET_BODY,
            "body": {"pin": _SECRET_PIN, "email": _SECRET_EMAIL, "message": _SECRET_BODY},
            "request_body": {"pin": _SECRET_PIN},
            "error": _TRACEBACK,
            "exception": RuntimeError(_TRACEBACK),
            "public_key": "zz-pubkey-marker",
            "credential_id": "zz-cred-id-marker",
            "token": "zz-session-token",
            "filename": "Jane_Doe_555-867-5309.jpg",
            "doc_filename": "Jane_Doe_contract.pdf",
        },
    )

    rows = _feature_lines(capsys.readouterr().out)
    assert len(rows) == 1
    blob = json.dumps(rows[0])
    assert rows[0]["feature"] == "documents"
    assert rows[0]["template_name"] == "sales-contract"
    assert rows[0]["deal_id"] == "deal-4"
    assert "target_id" not in rows[0]
    assert "actor_ref" not in rows[0]
    for secret in (
        _SECRET_PIN,
        _SECRET_CODE,
        _SECRET_EMAIL,
        _SECRET_PHONE,
        _SECRET_SSN,
        _SECRET_BODY,
        "Traceback",
        "zz-pubkey-marker",
        "zz-cred-id-marker",
        "zz-session-token",
        "Jane_Doe",
        "request_body",
    ):
        assert secret not in blob


def test_failed_delivery_sets_outcome_error_without_the_recipient(capsys):
    emit_staff_feature_event(
        action="email.send",
        actor="system:auto_ack",
        target_type="email",
        target_id=_SECRET_EMAIL,
        details={
            "kind": "reply_draft",
            "email_type": "custom",
            "delivered": False,
            "subject_len": 4,
            "message_len": 8,
            "recipient_fingerprint": "aabbccddeeff",
            "to": _SECRET_EMAIL,
        },
        outcome="ok",
    )
    rows = _feature_lines(capsys.readouterr().out)
    assert rows[0]["outcome"] == "error"
    assert rows[0]["severity"] == "WARNING"
    assert rows[0]["kind"] == "reply_draft"
    blob = json.dumps(rows[0])
    assert _SECRET_EMAIL not in blob
    assert "aabbccddeeff" not in blob


def test_log_admin_action_emits_feature_line_and_strips_firestore_pii(monkeypatch, capsys):
    from audit_log import log_admin_action

    fake = _bind_audit_db(monkeypatch)
    log_admin_action(
        actor="admin:abc123def456",
        action="inventory.photo_delete",
        target_type="inventory",
        target_id="43372",
        details={
            "filename": "Jane_Doe_555-867-5309.jpg",
            "email": _SECRET_EMAIL,
            "pin": _SECRET_PIN,
            "body": {"message": _SECRET_BODY, "phone": _SECRET_PHONE},
            "note": _TRACEBACK,
        },
    )

    docs = fake.collections["audit_log"]._docs
    assert len(docs) == 1
    stored = repr(docs[0])
    rows = _feature_lines(capsys.readouterr().out)
    assert rows[0]["event"] == "inventory.photo_delete"
    assert rows[0]["feature"] == "photos"
    assert rows[0]["target_id"] == "43372"
    blob = stored + json.dumps(rows[0])
    for secret in (
        _SECRET_EMAIL,
        _SECRET_PIN,
        _SECRET_PHONE,
        _SECRET_BODY,
        "Jane_Doe",
        "Traceback",
    ):
        assert secret not in blob
    assert "filename_sha256" in docs[0]["details"]
    assert "filename" not in docs[0]["details"]


def test_audit_write_failure_omits_exception_text_and_still_emits(monkeypatch, caplog, capsys):
    import audit_log

    class ExplodingCollection:
        def add(self, _entry, timeout=None):
            raise RuntimeError(f"firestore down {_SECRET_EMAIL} {_SECRET_PIN} {_TRACEBACK}")

    class ExplodingDB:
        def collection(self, _name):
            return ExplodingCollection()

    monkeypatch.setattr(audit_log, "_get_db", lambda: ExplodingDB())
    with caplog.at_level(logging.WARNING, logger="audit_log"):
        audit_log.log_admin_action(
            actor="admin:abc123def456",
            action="lead.update",
            target_type="lead",
            target_id="lead-9",
            details={"fields": ["priority"], "error": _TRACEBACK},
        )

    logged = " ".join(record.getMessage() for record in caplog.records)
    cloud = capsys.readouterr().out
    assert "lead.update" in logged
    assert "RuntimeError" in logged
    blob = logged + cloud
    for secret in (_SECRET_EMAIL, _SECRET_PIN, "Traceback", "firestore down"):
        assert secret not in blob
    rows = _feature_lines(cloud)
    assert rows[0]["feature"] == "leads"
    assert rows[0]["outcome"] == "ok"


def test_feature_emitter_failure_does_not_block_the_audit_write(monkeypatch, capsys):
    import audit_log

    fake = _bind_audit_db(monkeypatch)

    def _boom(**_kwargs):
        raise RuntimeError(_SECRET_EMAIL)

    monkeypatch.setattr(audit_log, "emit_staff_feature_event", _boom)
    audit_log.log_admin_action(
        actor="admin:abc123def456",
        action="deal.update",
        target_type="deal",
        target_id="deal-4",
        details={"fields": ["status"]},
    )
    assert len(fake.collections["audit_log"]._docs) == 1
    assert _SECRET_EMAIL not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("action", "feature"),
    (
        ("inventory.photos_reorder", "photos"),
        ("inventory.tour_set", "photos"),
        ("inventory.update", "inventory"),
        ("lead.update", "leads"),
        ("document.esign_send", "documents"),
        ("documents.share", "documents"),
        ("document.email_delivery", "email"),
        ("customer.update", "customers"),
        ("crm_task.create", "crm"),
        ("admin.staff_access.update", "team"),
    ),
)
def test_staff_tool_writes_keep_a_stable_feature(action, feature, capsys):
    emit_staff_feature_event(
        action=action, actor="system", target_type="document", target_id="deal-1"
    )
    rows = _feature_lines(capsys.readouterr().out)
    assert rows[0]["event"] == action
    assert rows[0]["feature"] == feature
    assert rows[0]["lane"] == "pgf"
