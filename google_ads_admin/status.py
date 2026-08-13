"""Allowlisted admin projection for the durable, offline Google Ads review state."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from scripts.google_ads_launch_draft import contract_sha256, validate_draft
from scripts.google_ads_paused_worker import DeploymentRecord, DeploymentState

CONTRACT_PATH = Path(__file__).resolve().parents[1] / "config" / "google_ads_launch_draft.json"
_DEPLOYMENT_KEY = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_VISIBLE_STATES = (DeploymentState.INTERNAL_DRAFT, DeploymentState.SERVER_VALIDATED)


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
    get = event.get if isinstance(event, dict) else lambda key: getattr(event, key)
    event_type = str(get("event_type"))
    semantics = {
        "INTERNAL_DRAFT_CREATED": (
            "00000000000000000001-internal-draft-created",
            1,
            None,
            "INTERNAL_DRAFT",
        ),
        "SERVER_VALIDATED": (
            "00000000000000000002-server-validated",
            2,
            "INTERNAL_DRAFT",
            "SERVER_VALIDATED",
        ),
    }.get(event_type)
    if (
        semantics is None
        or (
            str(get("event_id")),
            int(get("record_version")),
            get("from_state"),
            str(get("to_state")),
        )
        != semantics
        or get("deployment_id") != record.deployment_id
        or get("contract_hash") != record.contract_hash
        or get("error_code") is not None
        or get("worker_claim_hash") is not None
    ):
        raise ValueError("authority event is outside the offline review allowlist")
    return {
        "event_id": str(get("event_id")),
        "event_type": event_type,
        "record_version": int(get("record_version")),
        "from_state": str(get("from_state")) if get("from_state") is not None else None,
        "to_state": str(get("to_state")),
        "error_code": str(get("error_code")) if get("error_code") is not None else None,
        "occurred_at": _iso(get("occurred_at")),
    }


def build_deployment_readiness(
    record: DeploymentRecord,
    events: Iterable[Any],
    contract_path: Path = CONTRACT_PATH,
) -> dict[str, Any]:
    """Return only reviewed contract fields and sanitized durable authority evidence."""
    payload = load_checked_in_contract(contract_path)
    deployment = payload["deployment"]
    budget = payload["campaign"]["budget"]
    bidding = payload["campaign"]["bidding"]
    digest = contract_sha256(payload)
    expected_version = 1 if record.state is DeploymentState.INTERNAL_DRAFT else 2
    if (
        record.deployment_id != f"{deployment['key']}--{digest}"
        or record.contract_hash != f"sha256:{digest}"
        or record.deployment_key != deployment["key"]
        or record.state not in _VISIBLE_STATES
        or record.version != expected_version
        or record.updated_at is None
    ):
        raise ValueError("durable Google Ads record does not match the reviewed contract")

    projected_events = [_event_projection(event, record) for event in events]
    expected_event_types = (
        ["INTERNAL_DRAFT_CREATED"]
        if record.state is DeploymentState.INTERNAL_DRAFT
        else ["INTERNAL_DRAFT_CREATED", "SERVER_VALIDATED"]
    )
    if (
        len(projected_events) != record.version
        or [event["event_type"] for event in projected_events] != expected_event_types
    ):
        raise ValueError("authority event history does not match the durable review state")
    current_index = _VISIBLE_STATES.index(record.state)
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
        "events": {"count": len(projected_events), "items": projected_events},
    }
