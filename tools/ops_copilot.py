"""Ops Copilot — in-app, admin/employee-only assistant for Texas Home Outlet staff.

Read-only by design (v1). It does two things for the team:

  1. Answers questions about live business data — leads, appointments,
     inventory, deals, feedback, installations — by aggregating the same
     managers the rest of the app uses (no PII beyond counts/status).
  2. Explains how to use the THO platform (the "how-to" layer): logging into
     the back office, where leads live, uploading home photos, etc.

It performs NO writes. This is the GCP-native (Vertex AI / Gemini) in-app
replacement for the external Telegram/Mira bot — staff get help right inside
the app instead of a separate chat tool.

Design notes:
  * The data tools are ``async`` (the managers are). genai's automatic
    function-calling invokes tools synchronously, so instead of wiring the
    model to async tools we gather a compact snapshot up front and inline it
    into the system instruction — one Gemini call, fully async-safe.
  * ``_call_model`` is the single external-model seam; tests monkeypatch it.
    The snapshot/help layers are pure and unit-tested without the model.
  * Everything is failure-tolerant: a Firestore hiccup degrades a single
    section to ``{"error": ...}``; the endpoint never 500s.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from fastapi.concurrency import run_in_threadpool

from database.firestore_timeouts import firestore_long_timeout
from structured_logging import logger

DEFAULT_PROJECT_ID = "tho-ai-agent"
DEFAULT_LOCATION = "us-central1"


# --------------------------------------------------------------------------- #
# Environment / config helpers (thin, monkeypatchable seams)
# --------------------------------------------------------------------------- #
def _project_id() -> str:
    return os.environ.get("GOOGLE_CLOUD_PROJECT", DEFAULT_PROJECT_ID)


def _location() -> str:
    return os.environ.get("GOOGLE_CLOUD_LOCATION", DEFAULT_LOCATION)


def _model_name() -> str:
    """Resolve the model from an env override or config.yaml — never hardcoded
    here (model-agnosticism charter: model identifiers live only in config/env)."""
    env_override = os.environ.get("COPILOT_MODEL")
    if env_override:
        return env_override
    from config_loader import get_model_config

    return get_model_config().get("model", "")


def _lead_manager(project_id: str):
    from lead_management import LeadManager

    return LeadManager(project_id=project_id)


def _appointment_manager(project_id: str):
    from appointment_manager import AppointmentManager

    return AppointmentManager(project_id=project_id)


def _get_db():
    from database.firestore_client import get_database

    return get_database().db


def _count_by_status(collection_name: str, status_field: str = "status") -> dict[str, int]:
    """Count documents in a Firestore collection by a status field.

    Mirrors mira_routes._count_collection_by_status but is decoupled from the
    partner-key router so the copilot owns its read path.
    """
    try:
        docs = _get_db().collection(collection_name).stream(timeout=firestore_long_timeout())
        return dict(Counter(doc.to_dict().get(status_field, "unknown") for doc in docs))
    except Exception as exc:  # pragma: no cover - defensive; surfaced as section error
        logger.error("ops_copilot count failed", collection=collection_name, error=str(exc))
        return {"error": "unavailable"}


# --------------------------------------------------------------------------- #
# Read-only data layer — the live "what's happening in the business" snapshot
# --------------------------------------------------------------------------- #
def _load_inventory_snapshot() -> dict[str, Any] | None:
    """Seam (monkeypatched in tests) for the legacy inventory snapshot."""
    from tools import legacy_site_crawler

    return legacy_site_crawler._load_snapshot_context()


def get_inventory_freshness() -> dict[str, Any]:
    """Read-only freshness of the public inventory the site shows.

    The public site serves homes from a legacy snapshot whose auto-refresh can
    silently freeze after the domain cutover (see legacy_site_crawler). Surfacing
    the snapshot's age lets staff ask the Copilot 'is our inventory up to date?'
    and get a real answer instead of assuming it's live.
    """
    try:
        from tools import legacy_site_crawler

        ctx = _load_inventory_snapshot()
        if not ctx:
            return {"available": False}
        retrieved_at = ctx.get("retrieved_at")
        age_days: int | None = None
        stale: bool | None = None
        if retrieved_at:
            try:
                ts = datetime.fromisoformat(str(retrieved_at).replace("Z", "+00:00"))
                age_days = (datetime.now(UTC) - ts).days
                stale = age_days >= legacy_site_crawler.SNAPSHOT_STALE_AFTER_DAYS
            except ValueError:
                pass
        return {
            "available": True,
            "source": ctx.get("source"),
            "last_refreshed": retrieved_at,
            "age_days": age_days,
            "stale": stale,
            "total_homes": len(ctx.get("homes") or []),
        }
    except Exception as exc:
        logger.error("ops_copilot inventory freshness failed", error=str(exc))
        return {"available": False}


def _command_center_enabled() -> bool:
    """True when the operator flipped on the Notion Command Center source."""
    try:
        from tools import notion_client

        return notion_client.is_command_center_enabled()
    except Exception:  # noqa: BLE001 — a config/import hiccup must not sink the snapshot
        return False


def _notion_status_counts(db_key: str) -> dict[str, int]:
    """PII-free count-by-status from a Notion Command Center DB ({} on any error)."""
    try:
        from tools import notion_client

        return notion_client.fetch_status_counts(db_key)
    except Exception as exc:  # noqa: BLE001 — graceful-degrade like every other section
        logger.error("ops_copilot notion read failed", db_key=db_key, error=str(exc))
        return {}


def _firestore_feedback() -> dict[str, Any]:
    """The Firestore feedback count — the default/fallback feedback section."""
    try:
        feedback_docs = list(
            _get_db().collection("feedback").stream(timeout=firestore_long_timeout())
        )
        return {"total": len(feedback_docs)}
    except Exception as exc:
        logger.error("ops_copilot feedback snapshot failed", error=str(exc))
        return {"error": "unavailable"}


async def get_business_snapshot() -> dict[str, Any]:
    """Aggregate a compact, PII-free snapshot of the live business state.

    Every section is independently fault-isolated: one failing collection
    degrades to ``{"error": ...}`` rather than sinking the whole snapshot.
    """
    project_id = _project_id()
    snapshot: dict[str, Any] = {}

    try:
        leads = await _lead_manager(project_id).list_leads(limit=10000)
        snapshot["leads"] = {
            "total": len(leads),
            "by_status": dict(Counter(getattr(lead, "status", "unknown") for lead in leads)),
        }
    except Exception as exc:
        logger.error("ops_copilot leads snapshot failed", error=str(exc))
        snapshot["leads"] = {"error": "unavailable"}

    try:
        appts = await _appointment_manager(project_id).list_appointments(limit=10000)
        snapshot["appointments"] = {
            "total": len(appts),
            "by_status": dict(Counter(getattr(appt, "status", "unknown") for appt in appts)),
        }
    except Exception as exc:
        logger.error("ops_copilot appointments snapshot failed", error=str(exc))
        snapshot["appointments"] = {"error": "unavailable"}

    snapshot["inventory"] = {"by_status": _count_by_status("inventory")}
    snapshot["deals"] = {"by_status": _count_by_status("deals")}
    # Installations / feedback / post-funding ops: the staff's system-of-record is
    # the Notion Command Center. When NOTION_COMMAND_CENTER is on, source these
    # PII-free counts from there (the functional Mira replacement — the Copilot can
    # answer "where is my delivery / title / payment"); otherwise keep the exact
    # Firestore behavior. One env var flips it; revert is instant.
    if _command_center_enabled():
        install_counts = _notion_status_counts("delivery_tracker")
        snapshot["installations"] = {
            "by_status": install_counts or _count_by_status("service_requests")
        }
        fb_counts = _notion_status_counts("cs_survey")
        snapshot["feedback"] = (
            {"total": sum(fb_counts.values()), "by_status": fb_counts}
            if fb_counts
            else _firestore_feedback()
        )
        snapshot["operations"] = {
            db_key: {"by_status": _notion_status_counts(db_key)}
            for db_key in ("service_warranty", "title", "collections", "insurance")
        }
    else:
        snapshot["installations"] = {"by_status": _count_by_status("service_requests")}
        snapshot["feedback"] = _firestore_feedback()

    snapshot["inventory_freshness"] = get_inventory_freshness()

    return snapshot


# --------------------------------------------------------------------------- #
# How-to layer — a small, curated, deterministic knowledge base for staff.
# Kept inline (not file IO at request time) so it is testable, reviewer-legible,
# and ships the "boomer-proof" guidance as something the copilot can answer from.
# --------------------------------------------------------------------------- #
HELP_TOPICS: list[dict[str, Any]] = [
    {
        "title": "Logging into the back office",
        "keywords": ["login", "log in", "sign in", "back office", "admin", "pin", "passkey", "lock", "access"],
        "body": (
            "Open the website and click the small lock icon (bottom of the page). "
            "Enter your team PIN, or use your saved passkey (Face ID / Touch ID / "
            "fingerprint) if you have one set up. Once you're in, the menu shows the "
            "admin tools: CRM, Appointments, Analytics, Document Center, and more. "
            "Never share your PIN; each person should use their own passkey."
        ),
    },
    {
        "title": "Finding and managing leads (CRM)",
        "keywords": ["lead", "leads", "crm", "customer", "contact", "inquiry", "new lead", "follow up"],
        "body": (
            "Leads are people who contacted us through the site. Open the CRM from the "
            "admin menu to see every lead, their status (new, contacted, qualified, "
            "etc.), and how they reached us. New leads show up automatically when "
            "someone submits the contact or quote form. Work them top to bottom and "
            "update each one's status as you go."
        ),
    },
    {
        "title": "Appointments and tours",
        "keywords": ["appointment", "appointments", "tour", "schedule", "calendar", "booking", "visit"],
        "body": (
            "The Appointments page lists tours and meetings customers have requested, "
            "with their status. Use it to see what's coming up and to confirm or follow "
            "up. Customers can request a tour from a home's page on the website."
        ),
    },
    {
        "title": "Uploading home photos",
        "keywords": ["photo", "photos", "picture", "image", "upload", "home", "listing", "inventory photo"],
        "body": (
            "Open the home in the admin tools and use the photo uploader. You can drag "
            "photos in or pick them from your phone or computer. Use clear, well-lit "
            "pictures; the first photo is the one customers see first. See "
            "docs/team/photo-upload-guide.md for the step-by-step guide."
        ),
    },
    {
        "title": "Inventory of homes",
        "keywords": ["inventory", "home", "homes", "floor plan", "listing", "model", "available"],
        "body": (
            "The inventory is the list of homes we offer. Browse it from the admin menu "
            "to check what's available and each home's status. Customers see the same "
            "homes on the public site under the inventory/homes pages."
        ),
    },
    {
        "title": "Generating documents and closing packets",
        "keywords": ["document", "documents", "packet", "closing", "contract", "paperwork", "pdf", "sign"],
        "body": (
            "Open the Document Center from the admin menu to create and manage customer "
            "paperwork, including closing packets. Fill in the customer's details and the "
            "system assembles the documents for you."
        ),
    },
    {
        "title": "Viewing analytics and how the business is doing",
        "keywords": ["analytics", "report", "reports", "numbers", "stats", "dashboard", "metrics", "traffic", "how many"],
        "body": (
            "The Analytics page shows how the site is performing: home views, quote and "
            "tour clicks, and how many leads are coming in. Use the time-range tabs "
            "(7 days, 30 days, 90 days, All Time) to compare periods. For a quick live "
            "count of leads, appointments, or inventory, just ask me directly."
        ),
    },
    {
        "title": "Customer feedback and surveys",
        "keywords": ["feedback", "survey", "review", "satisfaction", "rating", "comment"],
        "body": (
            "Customer feedback and post-install surveys are collected automatically. "
            "Ask me for the feedback count, or check the admin tools for the details."
        ),
    },
    {
        "title": "Where is my delivery / installation status",
        "keywords": ["delivery", "deliver", "install", "installation", "set", "tied down",
                     "trim", "white glove", "where is", "status of", "phase"],
        "body": (
            "Installation progress lives in the Notion Delivery Tracker, and the Copilot "
            "reads it directly — ask 'how many homes are in trim-out?' or 'what's pending "
            "delivery?' for a live count by stage. For one specific customer's home, open "
            "the Delivery Tracker in Notion."
        ),
    },
    {
        "title": "Title, collections, insurance, and service status",
        "keywords": ["title", "tdhca", "collections", "payment", "past due", "insurance",
                     "escrow", "renewal", "service", "warranty", "bill back"],
        "body": (
            "Title processing, collections, insurance/KIP, and service & warranty are "
            "tracked in Notion. The Copilot can give a live count by status for any of them "
            "— e.g. 'how many titles are pending?' or 'how many payments are past due?' — "
            "without exposing customer details. Open the matching Notion database for the "
            "per-customer specifics."
        ),
    },
    {
        "title": "What changed: the website's new name",
        "keywords": ["website", "url", "address", "name", "moved", "domain", "email", "secure", "changed"],
        "body": (
            "The website now lives at its real name, https://www.texashomeoutlet.com — "
            "it is live and secure. Your email address did NOT change. Old links still "
            "work and forward to the new address, so nothing you bookmarked is broken."
        ),
    },
    {
        "title": "Who to contact if something looks wrong",
        "keywords": ["help", "broken", "problem", "wrong", "support", "issue", "error", "stuck", "contact"],
        "body": (
            "If something looks wrong — the site won't load, a customer reports an error, "
            "or a number looks off — don't worry and don't try to fix infrastructure "
            "yourself. Take a screenshot and contact your team lead so it can be checked. "
            "Customer data is backed up daily."
        ),
    },
    {
        "title": "Are the home listings up to date?",
        "keywords": ["up to date", "current", "fresh", "stale", "outdated", "old", "refresh", "listings", "inventory updated", "new homes", "last updated"],
        "body": (
            "The homes shown on the site come from our listing feed. Ask me "
            "'is our inventory up to date?' and I'll tell you when it last "
            "refreshed. If it's been a while, the photos already on the site keep "
            "working, but a manager should check that the listing source is still "
            "connected so newly added homes show up."
        ),
    },
    {
        "title": "What this assistant can do",
        "keywords": ["you", "assistant", "copilot", "what can you do", "help me", "bot", "ai"],
        "body": (
            "I'm the Texas Home Outlet Ops Copilot — your in-app helper. I can tell you "
            "live numbers (how many new leads, upcoming appointments, homes in "
            "inventory, etc.) and explain how to do things on the platform. I'm "
            "read-only: I never change data. If you need to make a change, I'll tell you "
            "exactly where to do it in the admin tools."
        ),
    },
]


def _terms(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 2}


def search_platform_help(query: str, limit: int = 3) -> list[dict[str, str]]:
    """Return the most relevant how-to topics for a free-text query.

    Keyword phrase hits are weighted above incidental body-word overlap so a
    question like "how do I log in?" surfaces the login topic, not whatever
    topic happens to share the most common words.
    """
    q_terms = _terms(query)
    if not q_terms:
        return []

    scored: list[tuple[int, dict[str, Any]]] = []
    for topic in HELP_TOPICS:
        score = 0
        # Strong signal: a keyword phrase appears verbatim in the query.
        lowered = (query or "").lower()
        for kw in topic["keywords"]:
            if kw in lowered:
                score += 5
        # Weaker signal: vocabulary overlap with title + keywords + body.
        hay = _terms(topic["title"] + " " + " ".join(topic["keywords"]) + " " + topic["body"])
        score += len(q_terms & hay)
        if score > 0:
            scored.append((score, topic))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [{"title": t["title"], "body": t["body"]} for _, t in scored[:limit]]


# --------------------------------------------------------------------------- #
# Prompt assembly + model call
# --------------------------------------------------------------------------- #
SYSTEM_PREAMBLE = (
    "You are the Texas Home Outlet Ops Copilot, a friendly assistant used ONLY by "
    "Texas Home Outlet staff inside the company's own web app. You help employees "
    "with two things: (1) answering questions about the live business using the "
    "data snapshot below, and (2) explaining how to use the THO platform using the "
    "help topics below.\n\n"
    "Rules:\n"
    "- You are READ-ONLY. You can read and report data, but you cannot change "
    "anything. If a staffer asks you to create, edit, delete, send, or schedule "
    "something, do NOT claim you did it — instead explain, in plain steps, where in "
    "the admin tools they can do it themselves.\n"
    "- Ground every number in the LIVE DATA SNAPSHOT. Never invent counts. If a "
    "section shows an error or isn't in the snapshot, say the live number isn't "
    "available right now rather than guessing.\n"
    "- Keep answers short, warm, and plain-spoken — many teammates are not "
    "technical. Use simple words and, when giving steps, number them.\n"
    "- If asked whether the inventory/listings are up to date, use "
    "inventory_freshness (last_refreshed, age_days, stale, total_homes). If "
    "stale is true, gently say the listing feed hasn't refreshed in a while and "
    "a manager should check the inventory source — don't alarm; the photos "
    "already on the site still work.\n"
    "- Only discuss Texas Home Outlet and how to use this platform. If asked about "
    "something unrelated, gently steer back.\n"
)


def _build_system_instruction(snapshot: dict[str, Any], help_hits: list[dict[str, str]]) -> str:
    import json

    parts = [SYSTEM_PREAMBLE, "\n--- LIVE DATA SNAPSHOT (PII-free counts) ---\n"]
    parts.append(json.dumps(snapshot, indent=2, default=str))
    if help_hits:
        parts.append("\n\n--- RELEVANT HELP TOPICS ---\n")
        for hit in help_hits:
            parts.append(f"\n## {hit['title']}\n{hit['body']}\n")
    return "".join(parts)


def _build_contents(history: list[dict[str, Any]] | None, message: str) -> list[dict[str, Any]]:
    """Convert chat history + the new message into genai ``contents``.

    History items are ``{"role": "user"|"assistant", "content": str}``; the
    last 10 turns are kept so the prompt stays bounded.
    """
    contents: list[dict[str, Any]] = []
    for turn in (history or [])[-10:]:
        role = "model" if turn.get("role") in ("assistant", "model") else "user"
        text = str(turn.get("content", "")).strip()
        if text:
            contents.append({"role": role, "parts": [{"text": text}]})
    contents.append({"role": "user", "parts": [{"text": message}]})
    return contents


def _generate_sync(system_instruction: str, contents: list[dict[str, Any]]) -> str:
    """Blocking Gemini/Vertex call. Runs in a threadpool via ``_call_model``."""
    from google import genai
    from google.genai import types

    client = genai.Client(vertexai=True, project=_project_id(), location=_location())
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=0.3,
        max_output_tokens=1024,
    )
    response = client.models.generate_content(
        model=_model_name(), contents=contents, config=config
    )
    return (getattr(response, "text", "") or "").strip()


async def _call_model(system_instruction: str, contents: list[dict[str, Any]]) -> str:
    """Single external-model seam (monkeypatched in tests)."""
    return await run_in_threadpool(_generate_sync, system_instruction, contents)


async def run_copilot(message: str, history: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Answer a staff question. Never raises; always returns a reply dict."""
    message = (message or "").strip()
    if not message:
        return {
            "reply": "Hi! Ask me anything about Texas Home Outlet — like how many new "
            "leads we have, what's on the schedule, or how to upload home photos.",
            "error": False,
            "help_topics": [],
        }

    snapshot = await get_business_snapshot()
    help_hits = search_platform_help(message)
    system_instruction = _build_system_instruction(snapshot, help_hits)
    contents = _build_contents(history, message)

    try:
        reply = await _call_model(system_instruction, contents)
    except Exception as exc:
        logger.error("ops_copilot model call failed", error=str(exc))
        return {
            "reply": "Sorry — I had trouble reaching the assistant service just now. "
            "Please try again in a moment.",
            "error": True,
            "help_topics": [h["title"] for h in help_hits],
        }

    if not reply:
        reply = (
            "I'm not sure how to help with that yet. Try asking about leads, "
            "appointments, inventory, or how to use a part of the platform."
        )

    return {
        "reply": reply,
        "error": False,
        "help_topics": [h["title"] for h in help_hits],
    }
