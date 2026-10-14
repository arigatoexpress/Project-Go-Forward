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

    def set(self, data, timeout: float | None = None):
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


# ---------------------------------------------------------------------------
# Unit coverage for the remaining formatters / payload builders / gatekeeping
# ---------------------------------------------------------------------------


class TestVerifySignature:
    def test_rejects_non_sha256_prefix(self):
        assert github_mira_trigger._verify_github_signature(b"{}", "md5=abc", "secret") is False

    def test_rejects_empty_secret_or_signature(self):
        assert github_mira_trigger._verify_github_signature(b"{}", "sha256=x", "") is False
        assert github_mira_trigger._verify_github_signature(b"{}", "", "secret") is False


class TestFormatGithubEvent:
    def _repo(self):
        return {"repository": {"full_name": "tho/site", "html_url": "https://github.com/tho/site"}}

    def test_issues_event(self):
        payload = {
            **self._repo(),
            "action": "opened",
            "issue": {"number": 7, "title": "Blank field", "html_url": "https://example.com/i/7"},
        }
        message, url = github_mira_trigger._format_github_event("issues", payload)
        assert "tho/site" in message and "issue #7" in message and "`opened`" in message
        assert url == "https://example.com/i/7"

    def test_workflow_job_icons(self):
        base = {**self._repo(), "workflow_job": {"name": "build", "html_url": "https://example.com/j"}}
        ok_msg, _ = github_mira_trigger._format_github_event(
            "workflow_job", {**base, "workflow_job": {**base["workflow_job"], "status": "completed", "conclusion": "success"}}
        )
        assert ok_msg.startswith("🟢")
        fail_msg, _ = github_mira_trigger._format_github_event(
            "workflow_job",
            {**base, "workflow_job": {**base["workflow_job"], "status": "completed", "conclusion": "failure"}},
        )
        assert fail_msg.startswith("🔴")
        neutral_msg, _ = github_mira_trigger._format_github_event(
            "workflow_job", {**base, "workflow_job": {**base["workflow_job"], "status": "queued"}}
        )
        assert neutral_msg.startswith("⚙️") and "conclusion" not in neutral_msg

    def test_deployment_status_event(self):
        payload = {
            **self._repo(),
            "deployment": {"environment": "prod", "url": "https://example.com/deploy"},
            "deployment_status": {"state": "success", "target_url": "https://example.com/target"},
        }
        message, url = github_mira_trigger._format_github_event("deployment_status", payload)
        assert message.startswith("🟢")
        assert "prod" in message and "success" in message
        assert url == "https://example.com/target"

    def test_release_event(self):
        payload = {
            **self._repo(),
            "action": "published",
            "release": {
                "tag_name": "v1.2.3",
                "name": "Launch build",
                "html_url": "https://example.com/r",
            },
        }
        message, url = github_mira_trigger._format_github_event("release", payload)
        assert "v1.2.3" in message and "Launch build" in message
        assert url == "https://example.com/r"

    def test_unknown_event_falls_back_to_generic(self):
        payload = self._repo()
        message, url = github_mira_trigger._format_github_event("star", payload)
        assert "star" in message
        assert url == "https://github.com/tho/site"


class TestIsNotable:
    def test_unknown_event_type_is_not_notable(self):
        assert github_mira_trigger._is_notable("star", {"action": "created"}) is False

    def test_ignored_action_is_not_notable(self):
        assert github_mira_trigger._is_notable("issues", {"action": "labeled"}) is False

    def test_push_requires_main_branch(self):
        assert github_mira_trigger._is_notable("push", {"ref": "refs/heads/main"}) is True
        assert github_mira_trigger._is_notable("push", {"ref": "refs/heads/dev"}) is False

    def test_pr_without_action_is_notable(self):
        assert github_mira_trigger._is_notable("pull_request", {"pull_request": {"number": 1}}) is True


class TestPartnerPayloadBranches:
    def _base(self):
        return {
            "action": "opened",
            "repository": {"full_name": "tho/site", "html_url": "https://github.com/tho/site"},
            "sender": {"login": "octocat"},
        }

    def test_issues(self):
        payload = {**self._base(), "issue": {"number": 9, "title": "T", "html_url": "u", "state": "open"}}
        result = github_mira_trigger._build_partner_payload("issues", payload)
        assert result["issue_number"] == 9
        assert result["issue_state"] == "open"
        assert result["sender"] == "octocat"

    def test_workflow_job(self):
        payload = {
            **self._base(),
            "workflow_job": {
                "name": "build",
                "html_url": "u",
                "status": "completed",
                "conclusion": "success",
                "workflow_name": "CI",
            },
        }
        result = github_mira_trigger._build_partner_payload("workflow_job", payload)
        assert result["job_name"] == "build"
        assert result["workflow_name"] == "CI"

    def test_deployment_status(self):
        payload = {
            **self._base(),
            "deployment": {"environment": "prod", "url": "https://d"},
            "deployment_status": {"state": "success", "target_url": "https://t"},
        }
        result = github_mira_trigger._build_partner_payload("deployment_status", payload)
        assert result["deployment_environment"] == "prod"
        assert result["deployment_state"] == "success"
        assert result["deployment_url"] == "https://t"

    def test_release(self):
        payload = {
            **self._base(),
            "release": {"tag_name": "v1", "name": "N", "html_url": "u"},
        }
        result = github_mira_trigger._build_partner_payload("release", payload)
        assert result["release_tag"] == "v1"
        assert result["release_action"] == "opened"


class TestLogActivityFailure:
    def test_activity_log_failure_is_swallowed(self, monkeypatch):
        def boom():
            raise RuntimeError("firestore down")

        monkeypatch.setattr(github_mira_trigger, "get_database", boom)
        # Must not raise.
        github_mira_trigger._log_activity("push", {"repository": {"full_name": "tho/site"}}, True, [])


# ---------------------------------------------------------------------------
# Webhook-level failure paths
# ---------------------------------------------------------------------------


def _post_signed(client, event_type, body: bytes, secret="test-github-secret"):
    return client.post(
        "/api/github/mira/webhook",
        content=body,
        headers={
            "X-GitHub-Event": event_type,
            "X-Hub-Signature-256": _github_signature(body, secret),
            "Content-Type": "application/json",
        },
    )


def test_invalid_json_returns_400(client, monkeypatch):
    monkeypatch.setattr(github_mira_trigger, "_GITHUB_WEBHOOK_SECRET", "test-github-secret")
    body = b"{not valid json"
    response = _post_signed(client, "push", body)
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid JSON payload"


def test_telegram_failure_still_accepts_event(
    client, monkeypatch, fake_db, captured_partner_dispatch
):
    monkeypatch.setattr(github_mira_trigger, "_GITHUB_WEBHOOK_SECRET", "test-github-secret")

    def failing_send(payload):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(mira_notify, "send_mira_notification", failing_send)

    payload = {
        "action": "opened",
        "pull_request": {"number": 5, "title": "T", "html_url": "u"},
        "repository": {"full_name": "tho/site"},
    }
    body = json.dumps(payload).encode("utf-8")
    response = _post_signed(client, "pull_request", body)

    assert response.status_code == 200
    body_json = response.json()
    assert body_json["status"] == "ok"
    assert body_json["telegram_ok"] is False
    assert body_json["partner_ids"] == ["mira"]  # partner dispatch still ran
    # Activity recorded the failed telegram leg.
    (activity,) = fake_db.activities.values()
    assert activity["metadata"]["telegram_ok"] is False


def test_partner_dispatch_failure_still_accepts_event(
    client, monkeypatch, fake_db, captured_telegram
):
    monkeypatch.setattr(github_mira_trigger, "_GITHUB_WEBHOOK_SECRET", "test-github-secret")

    def failing_dispatch(event, payload, db=None, **kwargs):
        raise RuntimeError("partner down")

    monkeypatch.setattr(github_mira_trigger, "dispatch_partner_event", failing_dispatch)

    payload = {
        "action": "opened",
        "issue": {"number": 5, "title": "T", "html_url": "u"},
        "repository": {"full_name": "tho/site"},
    }
    body = json.dumps(payload).encode("utf-8")
    response = _post_signed(client, "issues", body)

    assert response.status_code == 200
    body_json = response.json()
    assert body_json["status"] == "ok"
    assert body_json["telegram_ok"] is True  # telegram leg succeeded
    assert body_json["partner_ids"] == []
