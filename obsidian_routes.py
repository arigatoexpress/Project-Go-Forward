"""Obsidian Sovereign LLM Routes — Read-only monitoring + inbound notifications.

All endpoints under /api/v1/obsidian/* follow the same partner-API contract as
/api/v1/mira/*: protected by require_partner_api_key, PII-redacted by default,
and wired into the existing partner-webhook dispatcher for outbound events.
"""
from __future__ import annotations

import os
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from appointment_manager import AppointmentManager
from config_loader import get_deployment_config
from database.firestore_client import get_database
from database.firestore_timeouts import firestore_long_timeout, firestore_timeout
from lead_management import LeadManager

router = APIRouter(prefix="/api/v1/obsidian", tags=["obsidian"])
public_router = APIRouter(prefix="/api/v1/obsidian", tags=["obsidian"])

_obsidian_metrics_store: Any | None = None
_obsidian_app_start_time: float | None = None


def set_obsidian_refs(app_start_time: float, metrics_store: Any) -> None:
    global _obsidian_app_start_time, _obsidian_metrics_store
    _obsidian_app_start_time = app_start_time
    _obsidian_metrics_store = metrics_store


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _get_app_version() -> str:
    return os.environ.get("APP_VERSION") or os.environ.get("K_REVISION") or "local"


def _get_uptime_seconds() -> float | None:
    if _obsidian_app_start_time is None:
        return None
    return time.monotonic() - _obsidian_app_start_time


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def _count_collection_by_status(collection_name: str, status_field: str = "status") -> dict:
    try:
        db = get_database().db
        docs = db.collection(collection_name).stream(timeout=firestore_long_timeout())
        statuses = [doc.to_dict().get(status_field, "UNKNOWN") for doc in docs]
        return dict(Counter(statuses))
    except Exception:
        return {"UNKNOWN": 0}


@public_router.get("/health")
async def obsidian_health(request: Request) -> dict:
    uptime = _get_uptime_seconds()
    return {
        "status": "healthy",
        "version": _get_app_version(),
        "timestamp": _now_iso(),
        "uptime_seconds": uptime,
        "uptime_formatted": _format_duration(uptime) if uptime is not None else None,
    }


@router.get("/system")
async def obsidian_system(request: Request) -> dict:
    uptime = _get_uptime_seconds()
    deploy_cfg = get_deployment_config()
    env_keys = [
        "GOOGLE_GENAI_USE_VERTEXAI",
        "REDIS_HOST",
        "SENTRY_DSN",
        "K_SERVICE",
        "K_REVISION",
        "K_CONFIGURATION",
        "GOOGLE_CLOUD_PROJECT",
    ]
    env_status = {k: bool(os.environ.get(k)) for k in env_keys}
    return {
        "status": "healthy",
        "version": _get_app_version(),
        "uptime_seconds": round(uptime, 1) if uptime is not None else None,
        "uptime_formatted": _format_duration(uptime) if uptime is not None else None,
        "environment": os.environ.get("K_SERVICE") or "local",
        "platform": "cloud_run" if os.environ.get("K_SERVICE") else "local",
        "rate_limit_rpm": int(os.environ.get("RATE_LIMIT_RPM", "60")),
        "project_id": os.environ.get("GOOGLE_CLOUD_PROJECT") or deploy_cfg.get("project_id", "tho-ai-agent"),
        "env_status": env_status,
        "timestamp": _now_iso(),
    }


@router.get("/metrics")
async def obsidian_metrics(request: Request) -> dict:
    try:
        metrics = _obsidian_metrics_store.get_metrics() if _obsidian_metrics_store else {}
        return {"status": "healthy", "timestamp": _now_iso(), "metrics": metrics}
    except Exception as e:
        return {"status": "error", "timestamp": _now_iso(), "error": str(e), "metrics": {}}


@router.get("/leads/summary")
async def obsidian_leads_summary(request: Request) -> dict:
    try:
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "tho-ai-agent")
        lm = LeadManager(project_id=project_id)
        leads = await lm.list_leads(limit=10000)
        by_status = Counter(getattr(lead, "status", "unknown") for lead in leads)
        return {"status": "healthy", "timestamp": _now_iso(), "total": len(leads), "by_status": dict(by_status)}
    except Exception as e:
        return {"status": "error", "timestamp": _now_iso(), "error": str(e)}


@router.get("/leads/recent")
async def obsidian_leads_recent(request: Request, hours: int = 24, limit: int = 50) -> dict:
    try:
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "tho-ai-agent")
        lm = LeadManager(project_id=project_id)
        leads = await lm.list_leads(limit=10000)
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        recent = [
            {"lead_id": lead.lead_id, "status": lead.status, "source": lead.source, "created_at": lead.created_at}
            for lead in leads
            if lead.created_at and datetime.fromisoformat(str(lead.created_at).replace("Z", "+00:00")) >= cutoff
        ]
        recent.sort(key=lambda x: x["created_at"], reverse=True)
        return {"status": "healthy", "timestamp": _now_iso(), "hours": hours, "count": len(recent), "leads": recent[:limit]}
    except Exception as e:
        return {"status": "error", "timestamp": _now_iso(), "error": str(e)}


@router.get("/appointments/summary")
async def obsidian_appointments_summary(request: Request) -> dict:
    try:
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "tho-ai-agent")
        am = AppointmentManager(project_id=project_id)
        appts = await am.list_appointments(limit=10000)
        by_status = Counter(getattr(appt, "status", "unknown") for appt in appts)
        return {"status": "healthy", "timestamp": _now_iso(), "total": len(appts), "by_status": dict(by_status)}
    except Exception as e:
        return {"status": "error", "timestamp": _now_iso(), "error": str(e)}


@router.get("/inventory/summary")
async def obsidian_inventory_summary(request: Request) -> dict:
    try:
        by_status = _count_collection_by_status("inventory", "status")
        return {"status": "healthy", "timestamp": _now_iso(), "total": sum(by_status.values()), "by_status": by_status}
    except Exception as e:
        return {"status": "error", "timestamp": _now_iso(), "error": str(e)}


@router.get("/deals/summary")
async def obsidian_deals_summary(request: Request) -> dict:
    try:
        by_status = _count_collection_by_status("deals", "status")
        return {"status": "healthy", "timestamp": _now_iso(), "total": sum(by_status.values()), "by_status": by_status}
    except Exception as e:
        return {"status": "error", "timestamp": _now_iso(), "error": str(e)}


@router.get("/customers/summary")
async def obsidian_customers_summary(request: Request) -> dict:
    try:
        db = get_database()
        count = db.count_customers()
        return {"status": "healthy", "timestamp": _now_iso(), "total": count}
    except Exception as e:
        return {"status": "error", "timestamp": _now_iso(), "error": str(e)}


@router.get("/feedback/summary")
async def obsidian_feedback_summary(request: Request) -> dict:
    try:
        db = get_database().db
        docs = db.collection("feedback").stream(timeout=firestore_long_timeout())
        ratings = []
        sentiments = []
        for doc in docs:
            data = doc.to_dict()
            if "rating" in data:
                ratings.append(data["rating"])
            if "sentiment" in data:
                sentiments.append(data["sentiment"])
        return {
            "status": "healthy",
            "timestamp": _now_iso(),
            "total": len(ratings) + len(sentiments),
            "rating_counts": dict(Counter(ratings)) if ratings else {},
            "sentiment_counts": dict(Counter(sentiments)) if sentiments else {},
        }
    except Exception as e:
        return {"status": "error", "timestamp": _now_iso(), "error": str(e)}


@router.get("/firestore/collections")
async def obsidian_firestore_collections(request: Request, limit: int = 1000) -> dict:
    try:
        db = get_database().db
        cols = db.collections(timeout=firestore_timeout())
        result = []
        for col in cols:
            try:
                count = len(list(col.limit(limit).stream(timeout=firestore_long_timeout())))
                result.append({"collection": col.id, "count": count})
            except Exception:
                result.append({"collection": col.id, "count": None})
        return {"status": "healthy", "timestamp": _now_iso(), "collections": sorted(result, key=lambda x: x["collection"])}
    except Exception as e:
        return {"status": "error", "timestamp": _now_iso(), "error": str(e)}


class ObsidianNotifyPayload(BaseModel):
    message: str = Field(..., description="Alert text (supports Markdown)")
    level: str = Field(default="info", pattern=r"^(info|warn|error|critical)$", description="Severity level")
    source: str = Field(default="tho", description="Source system")
    action_url: str | None = Field(default=None, description="Optional action link")
    event: str = Field(default="obsidian.notify", description="Partner webhook event name")


@router.post("/notify")
async def obsidian_notify(payload: ObsidianNotifyPayload, request: Request) -> dict:
    from tools.partner_webhooks import dispatch_partner_event
    try:
        partners = dispatch_partner_event(
            payload.event,
            {"message": payload.message, "level": payload.level, "source": payload.source, "action_url": payload.action_url},
            db=get_database(),
            blocking=True,
            partner_ids=["obsidian"],
        )
        return {"ok": bool(partners), "timestamp": _now_iso(), "delivered_to": partners, "event": payload.event}
    except Exception as e:
        return {"ok": False, "timestamp": _now_iso(), "error": str(e), "event": payload.event}
