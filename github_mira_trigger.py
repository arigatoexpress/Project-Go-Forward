"""GitHub → Mira trigger bridge.

Receives GitHub webhook events at ``/api/github/mira/webhook``, validates the
``X-Hub-Signature-256`` HMAC, and forwards cutover-relevant events to:

1. The Mira Telegram group (via :func:`mira_notify.send_mira_notification`).
2. Any configured Mira partner webhook URL (via
   :func:`tools.partner_webhooks.dispatch_partner_event`) with an HMAC-SHA256
   signature in the ``X-THO-Signature`` header.
3. The Firestore ``activities/`` collection for auditability.

Partner-facing status/config endpoint lives at ``/api/v1/github/mira/status``
and uses the standard partner API key auth.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import mira_notify
from database.firestore_client import get_database
from structured_logging import logger as struct_logger
from tools.partner_webhooks import dispatch_partner_event

_GITHUB_WEBHOOK_SECRET = (os.environ.get("GITHUB_WEBHOOK_SECRET") or "").strip()

webhook_router = APIRouter(prefix="/api/github/mira", tags=["github-mira"])
status_router = APIRouter(prefix="/api/v1/github/mira", tags=["github-mira"])

_NOTIFY_EVENT_TYPES = {
    "pull_request",
    "pull_request_review",
    "issues",
    "workflow_run",
    "workflow_job",
    "deployment_status",
    "release",
    "push",
}

_IGNORED_ACTIONS = {
    "assigned",
    "unassigned",
    "labeled",
    "unlabeled",
    "milestoned",
    "demilestoned",
}

CUTOVER_PR_NUMBER = 156


class GitHubMiraStatus(BaseModel):
    configured: bool
    webhook_secret_set: bool
    mira_group_configured: bool
    partner_webhook_url_configured: bool
    cutover_pr_number: int


def _verify_github_signature(body: bytes, signature: str, secret: str) -> bool:
    """Verify a GitHub ``X-Hub-Signature-256`` HMAC-SHA256 signature."""
    if not secret or not signature:
        return False
    if not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


def _repo_basics(payload: dict[str, Any]) -> tuple[str, str | None]:
    """Return (full_name, html_url) for the repository in a payload."""
    repo = payload.get("repository") or {}
    return repo.get("full_name", "unknown/repo"), repo.get("html_url")


def _format_github_event(event_type: str, payload: dict[str, Any]) -> tuple[str, str | None]:
    """Return a Telegram-friendly (message, action_url) tuple for an event."""
    repo_name, repo_url = _repo_basics(payload)

    if event_type == "pull_request":
        pr = payload.get("pull_request") or {}
        number = pr.get("number")
        title = pr.get("title", "Untitled PR")
        action = payload.get("action", "updated")
        url = pr.get("html_url")
        if number == CUTOVER_PR_NUMBER:
            message = f"🚀 *CUTOVER PR #{number}* `{action}`\n_{title}_"
        else:
            message = f"🔀 *{repo_name}* PR #{number} `{action}`\n_{title}_"
        return message, url

    if event_type == "issues":
        issue = payload.get("issue") or {}
        number = issue.get("number")
        title = issue.get("title", "Untitled issue")
        action = payload.get("action", "updated")
        url = issue.get("html_url")
        return f"🐛 *{repo_name}* issue #{number} `{action}`\n_{title}_", url

    if event_type == "workflow_run":
        run = payload.get("workflow_run") or {}
        name = run.get("name", "Workflow")
        status = run.get("status", "unknown")
        conclusion = run.get("conclusion")
        branch = run.get("head_branch", "unknown")
        url = run.get("html_url")
        level_icon = "🟢" if conclusion == "success" else "🔴" if conclusion == "failure" else "⚙️"
        message = f"{level_icon} *{repo_name}* workflow `{name}` on `{branch}`\nstatus: {status}"
        if conclusion:
            message += f"\nconclusion: {conclusion}"
        return message, url

    if event_type == "workflow_job":
        job = payload.get("workflow_job") or {}
        name = job.get("name", "Job")
        status = job.get("status", "unknown")
        conclusion = job.get("conclusion")
        url = job.get("html_url")
        level_icon = "🟢" if conclusion == "success" else "🔴" if conclusion == "failure" else "⚙️"
        message = f"{level_icon} *{repo_name}* job `{name}`\nstatus: {status}"
        if conclusion:
            message += f"\nconclusion: {conclusion}"
        return message, url

    if event_type == "deployment_status":
        deployment = payload.get("deployment") or {}
        status = payload.get("deployment_status") or {}
        state = status.get("state", "unknown")
        env = deployment.get("environment", "unknown")
        url = status.get("target_url") or deployment.get("url")
        level_icon = "🟢" if state == "success" else "🔴" if state in {"failure", "error"} else "⚙️"
        return f"{level_icon} *{repo_name}* deploy to `{env}`: {state}", url

    if event_type == "release":
        release = payload.get("release") or {}
        tag = release.get("tag_name", "unknown")
        name = release.get("name", tag)
        action = payload.get("action", "published")
        url = release.get("html_url")
        return f"🏷️ *{repo_name}* release `{tag}` {action}\n_{name}_", url

    if event_type == "push":
        ref = payload.get("ref", "unknown")
        commits = payload.get("commits") or []
        pusher = (payload.get("pusher") or {}).get("name", "unknown")
        message = f"⬆️ *{repo_name}* push to `{ref}` by {pusher}\n{len(commits)} commit(s)"
        return message, repo_url

    return f"📡 *{repo_name}* `{event_type}` event received", repo_url


def _is_notable(event_type: str, payload: dict[str, Any]) -> bool:
    """Return True when an event should produce a Mira notification."""
    if event_type not in _NOTIFY_EVENT_TYPES:
        return False

    action = payload.get("action", "")
    if action in _IGNORED_ACTIONS:
        return False

    if event_type == "push":
        return payload.get("ref") == "refs/heads/main"

    return True


def _build_partner_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Build a PII-safe payload for the HMAC-signed outbound partner webhook."""
    repo_name, repo_url = _repo_basics(payload)
    base: dict[str, Any] = {
        "github_event_type": event_type,
        "repository": repo_name,
        "repository_url": repo_url,
        "action": payload.get("action"),
        "sender": (payload.get("sender") or {}).get("login"),
    }

    if event_type == "pull_request":
        pr = payload.get("pull_request") or {}
        base.update(
            {
                "pr_number": pr.get("number"),
                "pr_title": pr.get("title"),
                "pr_url": pr.get("html_url"),
                "pr_state": pr.get("state"),
                "pr_merged": pr.get("merged"),
                "base_branch": (pr.get("base") or {}).get("ref"),
                "head_branch": (pr.get("head") or {}).get("ref"),
                "is_cutover_pr": pr.get("number") == CUTOVER_PR_NUMBER,
            }
        )
    elif event_type == "issues":
        issue = payload.get("issue") or {}
        base.update(
            {
                "issue_number": issue.get("number"),
                "issue_title": issue.get("title"),
                "issue_url": issue.get("html_url"),
                "issue_state": issue.get("state"),
            }
        )
    elif event_type == "workflow_run":
        run = payload.get("workflow_run") or {}
        base.update(
            {
                "workflow_name": run.get("name"),
                "workflow_url": run.get("html_url"),
                "workflow_status": run.get("status"),
                "workflow_conclusion": run.get("conclusion"),
                "head_branch": run.get("head_branch"),
            }
        )
    elif event_type == "workflow_job":
        job = payload.get("workflow_job") or {}
        base.update(
            {
                "job_name": job.get("name"),
                "job_url": job.get("html_url"),
                "job_status": job.get("status"),
                "job_conclusion": job.get("conclusion"),
                "workflow_name": job.get("workflow_name"),
            }
        )
    elif event_type == "deployment_status":
        deployment = payload.get("deployment") or {}
        status = payload.get("deployment_status") or {}
        base.update(
            {
                "deployment_environment": deployment.get("environment"),
                "deployment_state": status.get("state"),
                "deployment_url": status.get("target_url") or deployment.get("url"),
            }
        )
    elif event_type == "release":
        release = payload.get("release") or {}
        base.update(
            {
                "release_tag": release.get("tag_name"),
                "release_name": release.get("name"),
                "release_url": release.get("html_url"),
                "release_action": payload.get("action"),
            }
        )
    elif event_type == "push":
        base.update(
            {
                "ref": payload.get("ref"),
                "commits_count": len(payload.get("commits") or []),
                "head_commit_message": (payload.get("head_commit") or {}).get("message"),
                "pusher": (payload.get("pusher") or {}).get("name"),
            }
        )

    return base


def _log_activity(
    event_type: str,
    payload: dict[str, Any],
    telegram_ok: bool,
    partner_ids: list[str],
    error: str | None = None,
) -> None:
    """Persist the trigger outcome to Firestore ``activities/`` for audit."""
    try:
        db = get_database()
        repo_name, _ = _repo_basics(payload)
        activity = {
            "id": str(uuid.uuid4()),
            "activity_type": f"github_mira_trigger.{event_type}",
            "description": f"GitHub → Mira trigger: {event_type} from {repo_name}",
            "actor": "system:github-mira-trigger",
            "metadata": {
                "github_event_type": event_type,
                "repository": repo_name,
                "action": payload.get("action"),
                "telegram_ok": telegram_ok,
                "partner_ids": partner_ids,
                "error": error,
            },
            "created_at": datetime.now(UTC).isoformat(),
        }
        db.db.collection("activities").document(activity["id"]).set(activity)
    except Exception as e:
        struct_logger.warning("github_mira_trigger activity log failed", error=str(e))


@webhook_router.post("/webhook")
async def github_mira_webhook(request: Request) -> dict:
    """Receive a GitHub webhook and forward notable events to Mira."""
    if not _GITHUB_WEBHOOK_SECRET:
        return {"status": "ignored", "reason": "github_webhook_secret_not_configured"}

    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_github_signature(body, signature, _GITHUB_WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    event_type = request.headers.get("X-GitHub-Event", "")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from e

    if not _is_notable(event_type, payload):
        return {"status": "ignored", "event_type": event_type, "reason": "not_notable"}

    message, action_url = _format_github_event(event_type, payload)

    telegram_result: dict[str, Any] = {"ok": False, "error": "send_failed"}
    try:
        notify_payload = mira_notify.NotifyPayload(
            message=message,
            level="info",
            source="github",
            action_url=action_url,
        )
        telegram_result = mira_notify.send_mira_notification(notify_payload)
    except Exception as e:
        telegram_result = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
        struct_logger.warning("github_mira_trigger telegram send failed", error=str(e))

    partner_ids: list[str] = []
    try:
        partner_payload = _build_partner_payload(event_type, payload)
        partner_ids = dispatch_partner_event(
            f"github.{event_type}",
            partner_payload,
            db=get_database(),
        )
    except Exception as e:
        struct_logger.warning("github_mira_trigger partner dispatch failed", error=str(e))

    _log_activity(
        event_type=event_type,
        payload=payload,
        telegram_ok=telegram_result.get("ok", False),
        partner_ids=partner_ids,
        error=telegram_result.get("error"),
    )

    return {
        "status": "ok",
        "event_type": event_type,
        "telegram_ok": telegram_result.get("ok", False),
        "partner_ids": partner_ids,
    }


@status_router.get("/status")
async def github_mira_status() -> GitHubMiraStatus:
    """Return the trigger configuration status. Protected by partner API key."""
    mira_group = os.environ.get("MIRA_GROUP_ID") or os.environ.get("KIMI_RELAY_CHAT_ID")
    partner_url = (os.environ.get("PARTNER_WEBHOOK_URL_MIRA") or "").strip()
    return GitHubMiraStatus(
        configured=bool(_GITHUB_WEBHOOK_SECRET),
        webhook_secret_set=bool(_GITHUB_WEBHOOK_SECRET),
        mira_group_configured=bool(mira_group and mira_group.strip()),
        partner_webhook_url_configured=bool(partner_url),
        cutover_pr_number=CUTOVER_PR_NUMBER,
    )
