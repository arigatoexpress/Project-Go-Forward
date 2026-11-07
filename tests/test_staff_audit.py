"""Stdout staff-admin audit lines stay free of secrets."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

import main
from auth.staff_audit import log_staff_action, redact_staff_path, staff_request_event


def _lines(text):
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_log_staff_action_drops_secrets_and_keeps_the_lane(capsys):
    log_staff_action(
        "session.sign_in",
        severity="WARNING",
        message="Staff sign-in",
        outcome="failure",
        method="email_code",
        role="anonymous",
        pin="zz-pin-marker-9f3a",
        code="zz-code-marker-918273",
        link_token="zz-link-token",
        public_key="zz-pubkey-marker",
        credential_id="zz-cred-id-marker",
        token="zz-session-token",
        cookie="zz-cookie",
        path="/api/admin/passkey/credentials/Y3JlZGVudGlhbC0x?code=zz-code-marker-918273",
    )

    rows = _lines(capsys.readouterr().out)
    assert len(rows) == 1
    row = rows[0]
    assert row["severity"] == "WARNING"
    assert row["lane"] == "pgf"
    assert row["service"] == "staff-admin"
    assert row["event"] == "session.sign_in"
    assert row["method"] == "email_code"
    assert row["path"] == "/api/admin/passkey/credentials/{redacted}"
    blob = json.dumps(row)
    assert "zz-pin-marker-9f3a" not in blob
    assert "zz-code-marker-918273" not in blob
    assert "zz-link-token" not in blob
    assert "zz-pubkey-marker" not in blob
    assert "zz-cred-id-marker" not in blob
    assert "zz-session-token" not in blob
    assert "zz-cookie" not in blob


def test_sign_in_failures_do_not_log_the_pin_or_email_code(capsys):
    client = TestClient(main.app)
    pin = "zz-pin-marker-9f3a"
    code = "zz-code-marker-918273"
    bad_pin = client.post("/api/admin/verify", json={"pin": pin, "public_key": "zz-pubkey-marker"})
    bad_email = client.post(
        "/api/admin/email-code/verify",
        json={
            "email": "nobody@example.com",
            "code": code,
            "link_token": "zz-link-token",
            "credential_id": "zz-cred-id-marker",
        },
    )

    assert bad_pin.status_code == 401
    assert bad_email.status_code == 401
    rows = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if '"service":"staff-admin"' in line
    ]
    blob = json.dumps(rows)
    assert any(row["event"] == "session.sign_in" and row["method"] == "pin" for row in rows)
    assert any(row["event"] == "session.sign_in" and row["method"] == "email_code" for row in rows)
    assert pin not in blob
    assert code not in blob
    assert "zz-link-token" not in blob
    assert "zz-pubkey-marker" not in blob
    assert "zz-cred-id-marker" not in blob
    assert "nobody@example.com" not in blob


def test_staff_request_classifier_hooks_pages_and_skips_auth_ceremonies():
    assert staff_request_event("GET", "/staff-home") == ("staff.page.view", "/staff-home")
    assert staff_request_event("GET", "/passkeys/") == ("staff.page.view", "/passkeys")
    assert staff_request_event("GET", "/api/admin/staff") == (
        "staff.feature.use",
        "/api/admin/staff",
    )
    assert staff_request_event("POST", "/api/admin/staff") == ("staff.action", "/api/admin/staff")
    assert staff_request_event("POST", "/api/deals/42") == ("staff.action", "/api/deals/42")
    assert staff_request_event("POST", "/api/admin/verify") is None
    assert staff_request_event("POST", "/api/admin/email-code/verify") is None
    assert staff_request_event("POST", "/api/admin/passkey/register/begin") is None
    assert staff_request_event("POST", "/api/contact") is None
    assert redact_staff_path("/api/admin/passkey/credentials/mine/Y3JlZGVudGlhbC0x") == (
        "/api/admin/passkey/credentials/mine/{redacted}"
    )
