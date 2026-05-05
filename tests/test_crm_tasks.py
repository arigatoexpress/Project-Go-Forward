"""Regression tests for CRM task endpoints used by the employee dashboard."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.test_api_v1 import load_app  # noqa: E402


def _make_admin_client(monkeypatch):
    main, _fake_db, _fake_logger = load_app(monkeypatch, tho_api_key="tho-secret")
    main._crm_tasks.clear()
    client = TestClient(main.app)
    token = main._create_admin_token()
    return client, main, {"Authorization": f"Bearer {token}"}


def test_crm_tasks_require_admin(monkeypatch):
    client, _main, _headers = _make_admin_client(monkeypatch)

    response = client.get("/api/crm/tasks")

    assert response.status_code == 401


def test_crm_task_create_list_and_complete(monkeypatch):
    client, _main, headers = _make_admin_client(monkeypatch)

    create = client.post(
        "/api/crm/tasks",
        headers=headers,
        json={
            "title": "Follow up with synthetic buyer",
            "description": "Confirm paperwork packet.",
            "due_date": "2026-05-06",
            "priority": "high",
            "related_lead": "lead-demo",
        },
    )

    assert create.status_code == 200, create.text
    task = create.json()["task"]
    assert task["task_id"]
    assert task["status"] == "pending"
    assert task["priority"] == "high"

    listed = client.get("/api/crm/tasks?limit=10", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["success"] is True
    assert [item["task_id"] for item in listed.json()["tasks"]] == [task["task_id"]]

    updated = client.put(
        f"/api/crm/tasks/{task['task_id']}",
        headers=headers,
        json={"status": "completed"},
    )
    assert updated.status_code == 200
    assert updated.json()["task"]["status"] == "completed"

    completed = client.get("/api/crm/tasks?status=completed", headers=headers)
    assert completed.status_code == 200
    assert completed.json()["total"] == 1


def test_crm_task_validation(monkeypatch):
    client, _main, headers = _make_admin_client(monkeypatch)

    missing_title = client.post("/api/crm/tasks", headers=headers, json={"priority": "high"})
    assert missing_title.status_code == 400
    assert "title" in missing_title.json()["error"]

    created = client.post("/api/crm/tasks", headers=headers, json={"title": "Demo"})
    task_id = created.json()["task"]["task_id"]

    bad_status = client.put(
        f"/api/crm/tasks/{task_id}",
        headers=headers,
        json={"status": "lost"},
    )
    assert bad_status.status_code == 400
    assert "pending or completed" in bad_status.json()["error"]
