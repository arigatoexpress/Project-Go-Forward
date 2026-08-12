"""Build a safe, local-only Paid Search deployment-readiness view.

This module reads the reviewed contract from disk. It deliberately has no
provider client, credential, persistence, worker, or network dependency.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from scripts.google_ads_launch_draft import contract_sha256, validate_draft

CONTRACT_PATH = Path(__file__).resolve().parents[1] / "config" / "google_ads_launch_draft.json"
_DEPLOYMENT_KEY = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_WORKFLOW = (
    ("INTERNAL_DRAFT", "current"),
    ("SERVER_VALIDATED", "not_started"),
    ("PAUSED_CREATE_APPROVED", "locked"),
    ("PAUSED_CREATED", "locked"),
)


def _load_contract(contract_path: Path) -> dict[str, Any]:
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("reviewed Google Ads contract is invalid")
    errors = validate_draft(payload)
    if errors:
        raise ValueError("reviewed Google Ads contract is invalid")
    return payload


def build_deployment_readiness(
    contract_path: Path = CONTRACT_PATH,
) -> dict[str, Any]:
    """Return the fail-closed admin projection of the checked-in contract.

    ``NO_EVIDENCE`` is intentional: this slice performs no provider or account
    probe and must not turn the presence of a local contract into a connection
    claim or spend authority.
    """
    payload = _load_contract(Path(contract_path))
    deployment = payload["deployment"]
    campaign = payload["campaign"]
    budget = campaign["budget"]
    bidding = campaign["bidding"]
    deployment_key = deployment["key"]
    if not _DEPLOYMENT_KEY.fullmatch(deployment_key):
        raise ValueError("reviewed Google Ads contract is invalid")

    digest = contract_sha256(payload)
    return {
        "schema_version": 1,
        "deployment_id": f"{deployment_key}--{digest}",
        "deployment_key": deployment_key,
        "contract_hash": f"sha256:{digest}",
        "state": "INTERNAL_DRAFT",
        "state_source": "CHECKED_IN_CONTRACT",
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
        "workflow": [{"state": state, "status": status} for state, status in _WORKFLOW],
        "actions": {
            "review": False,
            "approve_paused_create": False,
            "create_paused": False,
            "activate": False,
        },
    }
