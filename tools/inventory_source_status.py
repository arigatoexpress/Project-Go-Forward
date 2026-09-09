"""PII-free provenance and freshness checks for public inventory sources.

The public inventory endpoint can route through a legacy snapshot or the
existing marketing inventory fallback chain. A non-empty result is not proof
that it came from Firestore, and a source label is not proof that it is fresh.
Keep those claims explicit so operators and automatic selection fail closed.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

VERIFIED_FIRESTORE_SOURCE = "firestore_inventory"
DEFAULT_STALE_AFTER_DAYS = 14
LEGACY_SNAPSHOT_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "legacy_site" / "legacy_inventory_context.json"
)


def load_legacy_inventory_snapshot_metadata() -> dict[str, Any]:
    """Read PII-free snapshot metadata without populating the inventory cache."""
    try:
        context = json.loads(LEGACY_SNAPSHOT_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        context = {}
    homes = context.get("homes") or []
    return {
        "success": bool(context),
        "source": "legacy_site_snapshot",
        "retrieved_at": context.get("retrieved_at"),
        "total_inventory": context.get("total_inventory", len(homes)),
    }


def stale_after_days() -> int:
    """Return the configured freshness ceiling with a safe positive default."""
    try:
        return max(
            1,
            int(os.getenv("LEGACY_SNAPSHOT_STALE_DAYS", str(DEFAULT_STALE_AFTER_DAYS))),
        )
    except ValueError:
        return DEFAULT_STALE_AFTER_DAYS


def source_status(
    context: Mapping[str, Any] | None,
    *,
    requested: str,
    selected_path: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return provenance/freshness without claiming more than the data proves."""
    context = context or {}
    retrieved_at = next(
        (
            context.get(field)
            for field in ("source_updated_at", "retrieved_at", "updated_at")
            if context.get(field)
        ),
        None,
    )
    threshold = stale_after_days()
    age_days: int | None = None
    freshness = "unknown"
    if retrieved_at:
        try:
            timestamp = datetime.fromisoformat(str(retrieved_at).replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            current = now or datetime.now(UTC)
            if current.tzinfo is None:
                current = current.replace(tzinfo=UTC)
            age_days = max(0, (current - timestamp).days)
            freshness = "stale" if age_days >= threshold else "fresh"
        except (TypeError, ValueError):
            freshness = "unknown"

    return {
        "requested": requested,
        "selected_path": selected_path,
        "reported_source": str(context.get("source") or "unreported"),
        "freshness": freshness,
        "retrieved_at": retrieved_at,
        "age_days": age_days,
        "stale_after_days": threshold,
    }


def automatic_firestore_eligible(context: Mapping[str, Any] | None, *, min_homes: int) -> bool:
    """Require explicit Firestore provenance, freshness, and a population floor.

    The current marketing loader has JSON/sample fallbacks, so its success and
    count alone cannot authorize an automatic source switch.
    """
    context = context or {}
    homes = context.get("homes") or []
    if not context.get("success") or len(homes) < max(1, min_homes):
        return False
    if context.get("source") != VERIFIED_FIRESTORE_SOURCE:
        return False
    status = source_status(context, requested="auto", selected_path="firestore")
    return status["freshness"] == "fresh"


def warning_code(status: Mapping[str, Any]) -> str | None:
    """Return a stable warning code for non-fresh source evidence."""
    if status.get("freshness") == "stale":
        return "inventory_source_stale"
    if status.get("freshness") == "unknown":
        return "inventory_source_freshness_unknown"
    return None
