"""Tests for the GitHub → Mira trigger bridge.

Run: python -m pytest tests/test_github_mira_trigger.py -v
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("ADMIN_PIN_HASH", hashlib.sha256(b"4832").hexdigest())
os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "test-github-secret")
os.environ.setdefault("MIRA_GROUP_ID", "-123456789")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")

import github_mira_trigger  # noqa: E402
import mira_notify  # noqa: E402


def _github_signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class FakeDocRef:
    def __init__(self, store: dict, doc_id: str):
        self._store = store
        self._doc_id = doc_id

    def set(self, data):
        self._store[self._doc_id] = dict(data)


class FakeCollection:
    def __init__(self, store: dict):
        self._store = store

    def document(self, doc_id: str):
        return FakeDocRef(self._store, doc_id)


class FakeDB:
    def __init__(self):
        self.activities: dict = {}

    def collection(self, name: str):
        assert name == "activities", f"unexpected collection {name}"
        return FakeCollection(self.activities)


class FakeFirestoreClient:
    def __init__(self):
        self.db = FakeDB()


@pytest.fixture
def fake_db(monkeypatch):
    client = FakeFirestoreClient()
    monkeypatch.setattr(github_mira_trigger, "get_database", lambda: client)
    return client.db


@pytest.fixture
def captured_telegram(monkeypatch):
    calls = []

    def _fake_send(payload):
        calls.append({"message": payload.message, "level": payload.level, "source": payload.source, "action_url": payload.action_url})
        return {"ok": True, "telegram": {"message_id": 42}}

    monkeypatch.setattr(mira_notify, "send_mira_notification", _fake_send)
    return calls


@pytest.fixture
def captured_partner_dispatch(monkeypatch):
    calls = []

    def _fake_dispatch(event, payload, db=None, *, timeout_seconds=8.0, blocking=False):
        calls.append({"event": event, "payload": payload, "db": db})
        return ["mira"]

    monkeypatch.setattr(github_mira_trigger, "dispatch_partner_event", _fake_dispatch)
    return calls


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(github_mira_trigger.webhook_router)
    app.include_router(github_mira_trigger.status_router)
    return TestClient(app)


def test_invalid_signature_returns_401(client, monkeypatch):
    monkeypatch.setattr(github_mira_trigger, "_GITHUB_WEBHOOK_SECRET", "test-github-secret")
    payload = {"action": "opened", "pull_request": {"number": 1}}
    body = json.dumps(payload).encode("utf-8")

    response = client.post(
        "/api/github/mira/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": "sha256=invalid",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid webhook signature"


def test_missing_signature_returns_401(client, monkeypatch):
    monkeypatch.setattr(github_mira_trigger, "_GITHUB_WEBHOOK_SECRET", "test-github-secret")
    body = b'{}'

    response = client.post(
        "/api/github/mira/webhook",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "Content-Type": "application/json"},
    )

    assert response.status_code == 401


def test_unconfigured_secret_returns_ignored(client, monkeypatch):
    monkeypatch.setattr(github_mira_trigger, "_GITHUB_WEBHOOK_SECRET", "")
    body = b'{}'

    response = client.post(
        "/api/github/mira/webhook",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "Content-Type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert response.json()["reason"] == "github_webhook_secret_not_configured"


def test_non_notable_event_is_ignored(client, monkeypatch, fake_db, captured_telegram, captured_partner_dispatch):
    monkeypatch.setattr(github_mira_trigger, "_GITHUB_WEBHOOK_SECRET", "test-github-secret")
    payload = {"action": "labeled", "pull_request": {"number": 1}}
    body = json.dumps(payload).encode("utf-8")

    response = client.post(
        "/api/github/mira/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _github_signature(body, "test-github-secret"),
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert response.json()["reason"] == "not_notable"
    assert captured_telegram == []
    assert captured_partner_dispatch == []
    assert fake_db.activities == {}


def test_pull_request_open_forwards_to_telegram_and_partner(client, monkeypatch, fake_db, captured_telegram, captured_partner_dispatch):
    monkeypatch.setattr(github_mira_trigger, "_GITHUB_WEBHOOK_SECRET", "test-github-secret")
    payload = {
        "action": "opened",
        "repository": {"full_name": "tho/project", "html_url": "https://github.com/tho/project"},
        "sender": {"login": "dev-a"},
        "pull_request": {
            "number": 99,
            "title": "Add feature X",
            "html_url": "https://github.com/tho/project/pull/99",
            "state": "open",
            "merged": False,
            "base": {"ref": "main"},
            "head": {"ref": "feat/x"},
        },
    }
    body = json.dumps(payload).encode("utf-8")

    response = client.post(
        "/api/github/mira/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _github_signature(body, "test-github-secret"),
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["event_type"] == "pull_request"
    assert data["telegram_ok"] is True
    assert data["partner_ids"] == ["mira"]

    assert len(captured_telegram) == 1
    telegram = captured_telegram[0]
    assert "PR #99" in telegram["message"]
    assert "Add feature X" in telegram["message"]
    assert telegram["source"] == "github"
    assert telegram["action_url"] == "https://github.com/tho/project/pull/99"

    assert len(captured_partner_dispatch) == 1
    dispatch = captured_partner_dispatch[0]
    assert dispatch["event"] == "github.pull_request"
    assert dispatch["payload"]["pr_number"] == 99
    assert dispatch["payload"]["pr_title"] == "Add feature X"
    assert dispatch["payload"]["is_cutover_pr"] is False
    assert dispatch["payload"]["repository"] == "tho/project"
    assert dispatch["db"].db is fake_db

    assert len(fake_db.activities) == 1
    activity = next(iter(fake_db.activities.values()))
    assert activity["activity_type"] == "github_mira_trigger.pull_request"
    assert activity["metadata"]["telegram_ok"] is True
    assert activity["metadata"]["partner_ids"] == ["mira"]


def test_cutover_pr_gets_special_formatting(client, monkeypatch, fake_db, captured_telegram, captured_partner_dispatch):
    monkeypatch.setattr(github_mira_trigger, "_GITHUB_WEBHOOK_SECRET", "test-github-secret")
    payload = {
        "action": "closed",
        "repository": {"full_name": "tho/project", "html_url": "https://github.com/tho/project"},
        "sender": {"login": "dev-a"},
        "pull_request": {
            "number": 156,
            "title": "Cutover DNS prep",
            "html_url": "https://github.com/tho/project/pull/156",
            "state": "closed",
            "merged": True,
            "base": {"ref": "main"},
            "head": {"ref": "cutover/20260613-dns-prep"},
        },
    }
    body = json.dumps(payload).encode("utf-8")

    response = client.post(
        "/api/github/mira/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _github_signature(body, "test-github-secret"),
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    telegram = captured_telegram[0]
    assert "CUTOVER PR #156" in telegram["message"]
    dispatch = captured_partner_dispatch[0]
    assert dispatch["payload"]["is_cutover_pr"] is True


def test_workflow_run_failure_formats_red_icon(client, monkeypatch, fake_db, captured_telegram, captured_partner_dispatch):
    monkeypatch.setattr(github_mira_trigger, "_GITHUB_WEBHOOK_SECRET", "test-github-secret")
    payload = {
        "action": "completed",
        "repository": {"full_name": "tho/project"},
        "sender": {"login": "ci-bot"},
        "workflow_run": {
            "name": "Deploy to Cloud Run",
            "status": "completed",
            "conclusion": "failure",
            "head_branch": "main",
            "html_url": "https://github.com/tho/project/actions/runs/1",
        },
    }
    body = json.dumps(payload).encode("utf-8")

    response = client.post(
        "/api/github/mira/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "workflow_run",
            "X-Hub-Signature-256": _github_signature(body, "test-github-secret"),
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    telegram = captured_telegram[0]
    assert "🔴" in telegram["message"]
    assert "Deploy to Cloud Run" in telegram["message"]
    assert "failure" in telegram["message"]
    dispatch = captured_partner_dispatch[0]
    assert dispatch["event"] == "github.workflow_run"
    assert dispatch["payload"]["workflow_conclusion"] == "failure"


def test_push_to_main_is_notable(client, monkeypatch, fake_db, captured_telegram, captured_partner_dispatch):
    monkeypatch.setattr(github_mira_trigger, "_GITHUB_WEBHOOK_SECRET", "test-github-secret")
    payload = {
        "ref": "refs/heads/main",
        "repository": {"full_name": "tho/project", "html_url": "https://github.com/tho/project"},
        "pusher": {"name": "dev-a"},
        "commits": [{"id": "abc123", "message": "Fix bug"}],
        "head_commit": {"id": "abc123", "message": "Fix bug"},
    }
    body = json.dumps(payload).encode("utf-8")

    response = client.post(
        "/api/github/mira/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": _github_signature(body, "test-github-secret"),
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    assert response.json()["telegram_ok"] is True
    telegram = captured_telegram[0]
    assert "refs/heads/main" in telegram["message"]
    assert "1 commit(s)" in telegram["message"]


def test_push_to_feature_branch_is_ignored(client, monkeypatch, fake_db, captured_telegram, captured_partner_dispatch):
    monkeypatch.setattr(github_mira_trigger, "_GITHUB_WEBHOOK_SECRET", "test-github-secret")
    payload = {
        "ref": "refs/heads/feat/x",
        "repository": {"full_name": "tho/project"},
        "pusher": {"name": "dev-a"},
        "commits": [{"id": "abc123", "message": "WIP"}],
    }
    body = json.dumps(payload).encode("utf-8")

    response = client.post(
        "/api/github/mira/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": _github_signature(body, "test-github-secret"),
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert captured_telegram == []


def test_status_endpoint(client):
    response = client.get("/api/v1/github/mira/status")

    assert response.status_code == 200
    data = response.json()
    assert data["configured"] is True
    assert data["webhook_secret_set"] is True
    assert data["mira_group_configured"] is True
    assert data["partner_webhook_url_configured"] is False
    assert data["cutover_pr_number"] == 156
