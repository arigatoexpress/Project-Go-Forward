"""Allowlisted admin projection for the durable, offline Google Ads review state."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from database.models import GoogleAdsAuthorityEventRecord
from scripts.google_ads_launch_draft import contract_sha256, validate_draft
from scripts.google_ads_paused_worker import DeploymentRecord, DeploymentState

CONTRACT_PATH = Path(__file__).resolve().parents[1] / "config" / "google_ads_launch_draft.json"
_DEPLOYMENT_KEY = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_VISIBLE_STATES = (
    DeploymentState.INTERNAL_DRAFT,
    DeploymentState.SERVER_VALIDATED,
    DeploymentState.PAUSED_CREATE_APPROVED,
    DeploymentState.PAUSED_CREATED,
)


def load_checked_in_contract(contract_path: Path = CONTRACT_PATH) -> dict[str, Any]:
    """Load and fully validate the immutable server-owned contract."""
    payload = json.loads(Path(contract_path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or validate_draft(payload):
        raise ValueError("reviewed Google Ads contract is invalid")
    deployment_key = payload.get("deployment", {}).get("key")
    if not isinstance(deployment_key, str) or not _DEPLOYMENT_KEY.fullmatch(deployment_key):
        raise ValueError("reviewed Google Ads contract is invalid")
    return payload


def _iso(value: datetime | str) -> str:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    return value


def _event_projection(event: Any, record: DeploymentRecord) -> dict[str, Any]:
    raw = event if isinstance(event, dict) else event.model_dump(mode="python")
    try:
        model = GoogleAdsAuthorityEventRecord.model_validate(raw)
    except Exception:
        raise ValueError("authority event is outside the offline review allowlist")
    if model.deployment_id != record.deployment_id or model.contract_hash != record.contract_hash:
        raise ValueError("authority event is outside the offline review allowlist")
    return {
        "event_id": model.event_id,
        "event_type": model.event_type,
        "record_version": model.record_version,
        "from_state": model.from_state,
        "to_state": model.to_state,
        "error_code": model.error_code,
        "occurred_at": _iso(model.occurred_at),
    }


def build_deployment_readiness(
    record: DeploymentRecord,
    events: Iterable[Any],
    contract_path: Path = CONTRACT_PATH,
    *,
    outbox_state: str | None = None,
) -> dict[str, Any]:
    """Return only reviewed contract fields and sanitized durable authority evidence."""
    payload = load_checked_in_contract(contract_path)
    deployment = payload["deployment"]
    budget = payload["campaign"]["budget"]
    bidding = payload["campaign"]["bidding"]
    digest = contract_sha256(payload)
    if (
        record.deployment_id != f"{deployment['key']}--{digest}"
        or record.contract_hash != f"sha256:{digest}"
        or record.deployment_key != deployment["key"]
        or record.state not in _VISIBLE_STATES
        or record.updated_at is None
    ):
        raise ValueError("durable Google Ads record does not match the reviewed contract")

    projected_events = [_event_projection(event, record) for event in events]
    expected_evidence_count = min(record.version, 100)
    first_projected_version = record.version - expected_evidence_count + 1
    if (
        len(projected_events) != expected_evidence_count
        or [event["record_version"] for event in projected_events]
        != list(range(first_projected_version, record.version + 1))
        or (
            first_projected_version == 1
            and [event["event_type"] for event in projected_events[:3]]
            != [
                "INTERNAL_DRAFT_CREATED",
                "SERVER_VALIDATED",
                "PAUSED_CREATE_APPROVED",
            ][: min(record.version, 3)]
        )
        or projected_events[-1]["to_state"] != record.state.value
    ):
        raise ValueError("authority event history does not match the durable review state")
    current_index = _VISIBLE_STATES.index(record.state)
    if record.state in {
        DeploymentState.INTERNAL_DRAFT,
        DeploymentState.SERVER_VALIDATED,
    }:
        if outbox_state is not None:
            raise ValueError("pre-approval deployment cannot have an outbox state")
    elif outbox_state not in {"PENDING", "DISPATCHING", "DISPATCHED", "FAILED"}:
        raise ValueError("approved deployment requires a sanitized outbox state")
    if record.state is DeploymentState.PAUSED_CREATED and outbox_state != "DISPATCHED":
        raise ValueError("paused-created deployment requires dispatched outbox evidence")
    workflow = []
    for index, state in enumerate(_VISIBLE_STATES):
        status = (
            "complete"
            if index < current_index
            else "current"
            if index == current_index
            else "not_started"
        )
        workflow.append({"state": state.value, "status": status})

    return {
        "schema_version": 2,
        "deployment_id": record.deployment_id,
        "deployment_key": record.deployment_key,
        "contract_hash": record.contract_hash,
        "state": record.state.value,
        "state_source": "FIRESTORE_AUTHORITY_LEDGER",
        "version": record.version,
        "updated_at": _iso(record.updated_at),
        "connection": {"state": "NO_EVIDENCE", "verified_at": None},
        "feature_enabled": False,
        "ready": False,
        "spend_enabled": False,
        "budget": {
            "average_daily_usd": budget["average_daily_usd"],
            "max_single_day_charge_usd": budget["max_single_day_charge_usd"],
            "monthly_charge_limit_usd": budget["monthly_charge_limit_usd"],
            "max_cpc_usd": bidding["max_cpc_usd"],
        },
        "workflow": workflow,
        "actions": {"server_validation": record.state is DeploymentState.INTERNAL_DRAFT},
        "paused_create": {
            "outbox_state": outbox_state,
            "activation_authorized": False,
            "spend_enabled": False,
        },
        "events": {
            "count": record.version,
            "first_version": first_projected_version,
            "items": projected_events,
        },
    }
