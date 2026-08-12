"""Tests for CSRF protection on state-changing admin endpoints.

Run: python -m pytest tests/test_csrf_protection.py -v
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def admin_client(monkeypatch):
    """Create a test client with admin auth configured."""
    from tests.test_api_v1 import create_client

    client, main, _db, _logger = create_client(monkeypatch)
    return client, main


class TestCSRFTokens:
    """Test CSRF protection for admin endpoints."""

    def _login(self, client):
        """Log in as admin and return the CSRF token from the response body."""
        res = client.post(
            "/api/admin/verify",
            json={"pin": "4832"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert "csrf_token" in data
        return data["csrf_token"]

    @staticmethod
    def _set_passkey_session(client, main):
        token = main._get_passkey_session_manager().issue_session(
            "admin",
            email="mark@texashomeoutlet.com",
            auth_method="passkey",
        )
        client.cookies.set(main.PASSKEY_COOKIE_NAME, token)

    def test_login_sets_csrf_cookie(self, admin_client):
        client, _ = admin_client
        res = client.post(
            "/api/admin/verify",
            json={"pin": "4832"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert "csrf_token" in data
        # TestClient exposes cookies via response.cookies
        assert "tho_csrf_token" in res.cookies
        assert res.cookies["tho_csrf_token"] == data["csrf_token"]
        assert "tho_admin_token" in res.cookies

    def test_admin_post_without_csrf_returns_403(self, admin_client):
        client, _ = admin_client
        self._login(client)
        # POST without X-CSRF-Token header should be rejected
        res = client.post(
            "/api/crm/tasks",
            json={},
        )
        assert res.status_code == 403
        data = res.json()
        assert "CSRF" in data.get("message", data.get("detail", ""))

    def test_admin_post_with_csrf_succeeds(self, admin_client):
        client, _ = admin_client
        csrf = self._login(client)
        res = client.post(
            "/api/crm/tasks",
            json={"title": "test", "status": "pending"},
            headers={"X-CSRF-Token": csrf},
        )
        # 422 is acceptable if the body schema is invalid; just not 403
        assert res.status_code != 403
        assert res.status_code in (200, 201, 422)

    def test_admin_get_without_csrf_succeeds(self, admin_client):
        client, _ = admin_client
        self._login(client)
        res = client.get("/api/admin/check")
        assert res.status_code == 200
        data = res.json()
        assert data.get("valid") is True or data.get("success") is True

    def test_admin_post_with_wrong_csrf_returns_403(self, admin_client):
        client, _ = admin_client
        self._login(client)
        res = client.post(
            "/api/crm/tasks",
            json={"title": "test", "status": "pending"},
            headers={"X-CSRF-Token": "wrong-token"},
        )
        assert res.status_code == 403
        data = res.json()
        assert "CSRF" in data.get("message", data.get("detail", ""))

    def test_passkey_cookie_post_without_csrf_returns_403(self, admin_client):
        client, main = admin_client
        self._set_passkey_session(client, main)

        res = client.post(
            "/api/crm/tasks",
            json={"title": "test", "status": "pending"},
        )

        assert res.status_code == 403
        assert "CSRF" in res.json().get("message", res.json().get("detail", ""))

    def test_passkey_cookie_post_with_matching_csrf_succeeds(self, admin_client):
        client, main = admin_client
        self._set_passkey_session(client, main)
        client.cookies.set("tho_csrf_token", "passkey-csrf-token")

        res = client.post(
            "/api/crm/tasks",
            json={},
            headers={"X-CSRF-Token": "passkey-csrf-token"},
        )

        # Reaching handler validation proves CSRF passed without creating a task.
        assert res.status_code == 400
        assert res.json()["error"] == "Task title is required."

    def test_passkey_cookie_get_remains_csrf_exempt(self, admin_client):
        client, main = admin_client
        self._set_passkey_session(client, main)

        res = client.get("/api/crm/tasks")

        assert res.status_code == 200

    def test_admin_header_auth_skips_csrf(self, admin_client, monkeypatch):
        client, main = admin_client
        # Create a valid admin token via the helper
        token = main._create_admin_token()
        # POST with header auth and no CSRF should not be rejected for CSRF
        res = client.post(
            "/api/crm/tasks",
            json={"title": "test", "status": "pending"},
            headers={"X-Admin-Token": token},
        )
        assert res.status_code != 403
        assert "CSRF" not in res.json().get("message", res.json().get("detail", ""))
