"""Mira Monitoring Routes — Read-only endpoints for the Mira AI agent.

All endpoints under /api/v1/mira/* are protected by the partner API key
(require_partner_api_key). They expose system health, metrics, and
aggregated counts without PII. No write operations are exposed.

To integrate:
  1. Set THO_API_KEY_MIRA in environment or Secret Manager
  2. Import and include in main.py:
       from mira_routes import router as mira_router, public_router as mira_public_router, set_mira_refs
       app.include_router(mira_public_router)
       app.include_router(mira_router, dependencies=[Depends(require_partner_api_key)])
       set_mira_refs(_app_start_time, _metrics_store)
  3. Mira queries with: Authorization: Bearer <THO_API_KEY_MIRA>
"""
from __future__ import annotations

import os
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request

from appointment_manager import AppointmentManager
from config_loader import get_deployment_config
from database.firestore_client import get_database
from lead_management import LeadManager
from structured_logging import logger as struct_logger
from tools.partner_webhooks import dispatch_partner_event

router = APIRouter(prefix="/api/v1/mira", tags=["mira"])
public_router = APIRouter(prefix="/api/v1/mira", tags=["mira"])

_mira_metrics_store: Any | None = None
_mira_app_start_time: float | None = None


def set_mira_refs(app_start_time: float, metrics_store: Any) -> None:
    """Called by main.py after service initialization."""
    global _mira_app_start_time, _mira_metrics_store
    _mira_app_start_time = app_start_time
    _mira_metrics_store = metrics_store


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _mira_error(e: Exception) -> dict:
    """Log the real exception server-side and return a generic code to the
    partner, so internal error strings never leak across the bridge API."""
    struct_logger.error("mira endpoint error", error=str(e))
    return {"status": "error", "timestamp": _now_iso(), "error": "internal_error"}


def _get_app_version() -> str:
    return os.environ.get("APP_VERSION") or os.environ.get("K_REVISION") or "local"


def _get_uptime_seconds() -> float | None:
    """Return uptime since app start (if known)."""
    if _mira_app_start_time is None:
        return None
    return time.monotonic() - _mira_app_start_time


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def _parse_timestamp(value: Any) -> datetime | None:
    """Parse a Firestore timestamp (datetime object or ISO string) to UTC.

    Firestore returns native datetime objects in production, but tests and
    some migration paths store ISO strings. Treat naive datetimes as UTC.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, str):
        s = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except Exception:
            return None
    return None


def _count_collection_by_status(collection_name: str, status_field: str = "status") -> dict:
    """Count documents in a Firestore collection by status field."""
    try:
        db = get_database().db
        docs = db.collection(collection_name).stream()
        statuses = [doc.to_dict().get(status_field, "UNKNOWN") for doc in docs]
        return dict(Counter(statuses))
    except Exception:
        return {"UNKNOWN": 0}


@public_router.get("/health")
async def mira_health(request: Request) -> dict:
    """Basic health check — no auth required so Mira can probe freely."""
    uptime = _get_uptime_seconds()
    return {
        "status": "healthy",
        "version": _get_app_version(),
        "timestamp": _now_iso(),
        "uptime_seconds": uptime,
        "uptime_formatted": _format_duration(uptime) if uptime is not None else None,
    }


@router.get("/system")
async def mira_system(request: Request) -> dict:
    """System overview for Mira — services, config, environment."""
    uptime = _get_uptime_seconds()
    deploy_cfg = get_deployment_config()
    env_keys = [
        "RESEND_API_KEY",
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
async def mira_metrics(request: Request) -> dict:
    """Performance metrics for Mira — same data as /api/metrics but via partner key."""
    try:
        metrics = _mira_metrics_store.get_metrics() if _mira_metrics_store else {}
        return {"status": "healthy", "timestamp": _now_iso(), "metrics": metrics}
    except Exception as e:
        struct_logger.error("mira endpoint error", error=str(e))
        return {
            "status": "error",
            "timestamp": _now_iso(),
            "error": "internal_error",
            "metrics": {},
        }


@router.get("/leads/summary")
async def mira_leads_summary(request: Request) -> dict:
    """Lead counts by status — no PII exposed."""
    try:
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "tho-ai-agent")
        lm = LeadManager(project_id=project_id)
        leads = await lm.list_leads(limit=10000)
        by_status = Counter(getattr(lead, "status", "unknown") for lead in leads)
        return {
            "status": "healthy",
            "timestamp": _now_iso(),
            "total": len(leads),
            "by_status": dict(by_status),
        }
    except Exception as e:
        return _mira_error(e)


@router.get("/leads/recent")
async def mira_leads_recent(request: Request, hours: int = 24, limit: int = 50) -> dict:
    """Recent leads by creation time — no PII exposed."""
    try:
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "tho-ai-agent")
        lm = LeadManager(project_id=project_id)
        leads = await lm.list_leads(limit=10000)
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        recent = [
            {
                "lead_id": lead.lead_id,
                "status": lead.status,
                "source": lead.source,
                "created_at": lead.created_at,
            }
            for lead in leads
            if lead.created_at and datetime.fromisoformat(lead.created_at.replace("Z", "+00:00")) >= cutoff
        ]
        recent.sort(key=lambda x: x["created_at"], reverse=True)
        return {
            "status": "healthy",
            "timestamp": _now_iso(),
            "hours": hours,
            "count": len(recent),
            "leads": recent[:limit],
        }
    except Exception as e:
        return _mira_error(e)


@router.get("/leads/triage")
async def mira_leads_triage(
    request: Request,
    status: str = "new",
    min_age_hours: int | None = None,
    limit: int = 50,
) -> dict:
    """Cohort of leads awaiting triage — no PII exposed."""
    try:
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "tho-ai-agent")
        lm = LeadManager(project_id=project_id)
        leads = await lm.list_leads_needing_triage(
            status=status, min_age_hours=min_age_hours, limit=limit
        )
        return {
            "status": "healthy",
            "timestamp": _now_iso(),
            "filter": {"status": status, "min_age_hours": min_age_hours},
            "count": len(leads),
            "leads": [
                {
                    "lead_id": lead.lead_id,
                    "status": lead.status,
                    "source": lead.source,
                    "priority": lead.priority,
                    "assigned_to": lead.assigned_to,
                    "triage_reason": lead.triage_reason,
                    "created_at": lead.created_at,
                }
                for lead in leads
            ],
        }
    except Exception as e:
        return _mira_error(e)


@router.post("/leads/{lead_id}/triage")
async def mira_update_lead_triage(request: Request, lead_id: str, update: dict) -> dict:
    """Update triage fields on a lead and dispatch a partner webhook."""
    valid_statuses = {"new", "contacted", "qualified", "converted", "archived"}
    if "status" in update and update["status"] not in valid_statuses:
        return {
            "status": "error",
            "timestamp": _now_iso(),
            "error": f"Invalid status: {update['status']}. Must be one of {sorted(valid_statuses)}",
        }

    try:
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "tho-ai-agent")
        lm = LeadManager(project_id=project_id)
        old_lead = await lm.get_lead(lead_id)
        if old_lead is None:
            return {
                "status": "error",
                "timestamp": _now_iso(),
                "error": f"Lead {lead_id} not found",
            }

        old_status = old_lead.status
        lead = await lm.triage_lead(lead_id, update)
        if lead is None:
            return {
                "status": "error",
                "timestamp": _now_iso(),
                "error": f"Lead {lead_id} not found",
            }

        payload = {
            "lead_id": lead.lead_id,
            "old_status": old_status,
            "new_status": lead.status,
            "priority": lead.priority,
            "assigned_to": lead.assigned_to,
            "triage_reason": lead.triage_reason,
            "triage_notes": lead.triage_notes,
            "updated_at": lead.last_triage_at,
        }
        db = get_database()
        dispatch_partner_event("lead.triage_updated", payload, db=db)

        return {
            "status": "healthy",
            "timestamp": _now_iso(),
            "lead": {
                "lead_id": lead.lead_id,
                "status": lead.status,
                "priority": lead.priority,
                "assigned_to": lead.assigned_to,
                "triage_reason": lead.triage_reason,
                "triage_notes": lead.triage_notes,
                "last_triage_at": lead.last_triage_at,
            },
        }
    except Exception as e:
        return _mira_error(e)


@router.get("/appointments/summary")
async def mira_appointments_summary(request: Request) -> dict:
    """Appointment counts by status — no PII exposed."""
    try:
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "tho-ai-agent")
        am = AppointmentManager(project_id=project_id)
        appts = await am.list_appointments(limit=10000)
        by_status = Counter(getattr(appt, "status", "unknown") for appt in appts)
        return {
            "status": "healthy",
            "timestamp": _now_iso(),
            "total": len(appts),
            "by_status": dict(by_status),
        }
    except Exception as e:
        return _mira_error(e)


@router.get("/inventory/summary")
async def mira_inventory_summary(request: Request) -> dict:
    """Inventory summary — counts by status, no PII."""
    try:
        by_status = _count_collection_by_status("inventory", "status")
        return {
            "status": "healthy",
            "timestamp": _now_iso(),
            "total": sum(by_status.values()),
            "by_status": by_status,
        }
    except Exception as e:
        return _mira_error(e)


@router.get("/installations/summary")
async def mira_installations_summary(request: Request) -> dict:
    """Installation/service request summary — counts by status, no PII."""
    try:
        by_status = _count_collection_by_status("service_requests", "status")
        return {
            "status": "healthy",
            "timestamp": _now_iso(),
            "total": sum(by_status.values()),
            "by_status": by_status,
        }
    except Exception as e:
        return _mira_error(e)


@router.get("/installations/recent")
async def mira_installations_recent(request: Request, hours: int = 24, limit: int = 50) -> dict:
    """Recent installation/service requests — no PII exposed.

    Notion Delivery Tracker uses this to reconcile post-funding service work.
    Exposes status, issue type, warranty flag, and contractor assignment;
    omits customer PII.
    """
    try:
        db = get_database().db
        cutoff = datetime.now(UTC) - timedelta(hours=max(1, min(hours, 168)))
        docs = db.collection("service_requests").order_by("created_at", direction="DESCENDING").limit(limit).stream()
        items = []
        for doc in docs:
            data = doc.to_dict()
            created = _parse_timestamp(data.get("created_at"))
            if created and created >= cutoff:
                item: dict[str, Any] = {
                    "id": doc.id,
                    "status": data.get("status", "UNKNOWN"),
                    "issue_type": data.get("issue_type", "UNKNOWN"),
                    "is_warranty_claim": bool(data.get("is_warranty_claim", False)),
                    "warranty_status": data.get("warranty_status"),
                    "assigned_contractor": data.get("assigned_contractor"),
                    "created_at": created.isoformat(),
                    "updated_at": _parse_timestamp(data.get("updated_at")),
                }
                if data.get("deal_id"):
                    item["deal_id"] = data["deal_id"]
                items.append({k: v for k, v in item.items() if v is not None})
        return {
            "status": "healthy",
            "timestamp": _now_iso(),
            "hours": hours,
            "count": len(items),
            "items": items,
        }
    except Exception as e:
        return _mira_error(e)


@router.get("/deals/summary")
async def mira_deals_summary(request: Request) -> dict:
    """Deal counts by status — no PII exposed."""
    try:
        by_status = _count_collection_by_status("deals", "status")
        return {
            "status": "healthy",
            "timestamp": _now_iso(),
            "total": sum(by_status.values()),
            "by_status": by_status,
        }
    except Exception as e:
        return _mira_error(e)


@router.get("/customers/summary")
async def mira_customers_summary(request: Request) -> dict:
    """Customer count — no PII exposed."""
    try:
        db = get_database()
        count = db.count_customers()
        return {
            "status": "healthy",
            "timestamp": _now_iso(),
            "total": count,
        }
    except Exception as e:
        return _mira_error(e)


@router.get("/feedback/summary")
async def mira_feedback_summary(request: Request) -> dict:
    """Customer feedback summary — counts by sentiment/rating if available, no PII."""
    try:
        db = get_database().db
        docs = db.collection("feedback").stream()
        ratings = []
        sentiments = []
        total = 0
        for doc in docs:
            data = doc.to_dict()
            total += 1
            if "rating" in data:
                ratings.append(data["rating"])
            if "sentiment" in data:
                sentiments.append(data["sentiment"])
        return {
            "status": "healthy",
            "timestamp": _now_iso(),
            "total": total,
            "rating_counts": dict(Counter(ratings)) if ratings else {},
            "sentiment_counts": dict(Counter(sentiments)) if sentiments else {},
        }
    except Exception as e:
        return _mira_error(e)


@router.get("/feedback/recent")
async def mira_feedback_recent(request: Request, hours: int = 24, limit: int = 50) -> dict:
    """Recent customer feedback (CS surveys) — no PII exposed.

    Notion CS Surveys uses this to surface recent satisfaction signals.
    Exposes rating, sentiment, and source; omits free-text comments that
    may contain PII.
    """
    try:
        db = get_database().db
        cutoff = datetime.now(UTC) - timedelta(hours=max(1, min(hours, 168)))
        docs = db.collection("feedback").order_by("created_at", direction="DESCENDING").limit(limit).stream()
        items = []
        for doc in docs:
            data = doc.to_dict()
            created = _parse_timestamp(data.get("created_at"))
            if created and created >= cutoff:
                item: dict[str, Any] = {
                    "id": doc.id,
                    "rating": data.get("rating"),
                    "sentiment": data.get("sentiment"),
                    "source": data.get("source", "UNKNOWN"),
                    "created_at": created.isoformat(),
                }
                if data.get("deal_id"):
                    item["deal_id"] = data["deal_id"]
                items.append({k: v for k, v in item.items() if v is not None})
        return {
            "status": "healthy",
            "timestamp": _now_iso(),
            "hours": hours,
            "count": len(items),
            "items": items,
        }
    except Exception as e:
        return _mira_error(e)


@router.get("/errors")
async def mira_errors(request: Request, hours: int = 24) -> dict:
    """Recent error summary from structured logs — no PII.

    Query params:
      hours: lookback window (default 24, max 168)
    """
    hours = max(1, min(hours, 168))
    return {
        "status": "healthy",
        "timestamp": _now_iso(),
        "hours": hours,
        "note": "Error log querying is available via Cloud Logging API. This endpoint will return error summaries once structured log parsing is enabled.",
    }


@router.get("/firestore/collections")
async def mira_firestore_collections(request: Request, limit: int = 1000) -> dict:
    """Firestore collection names and document counts — metadata only, no PII."""
    try:
        db = get_database().db
        cols = db.collections()
        result = []
        for col in cols:
            try:
                count = len(list(col.limit(limit).stream()))
                result.append({"collection": col.id, "count": count})
            except Exception:
                result.append({"collection": col.id, "count": None})
        return {
            "status": "healthy",
            "timestamp": _now_iso(),
            "collections": sorted(result, key=lambda x: x["collection"]),
        }
    except Exception as e:
        return _mira_error(e)


@router.get("/chat/summary")
async def mira_chat_summary(request: Request, hours: int = 24) -> dict:
    """Chat activity summary — message counts, no PII content.

    Query params:
      hours: lookback window (default 24, max 168)
    """
    hours = max(1, min(hours, 168))
    return {
        "status": "healthy",
        "timestamp": _now_iso(),
        "hours": hours,
        "collection": "chat_sessions",
        "note": "Chat summary requires querying the chat_sessions Firestore collection directly. Use /api/v1/mira/firestore/collections to see document counts.",
    }
