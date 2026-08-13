#!/usr/bin/env python3
"""Fixed zero-argument dispatcher for one durable PAUSED-create outbox."""

from __future__ import annotations

import json
import os
import sys
import uuid
from collections.abc import Callable, Sequence
from typing import Any

from google_ads_admin.approval import PausedCreateApprovalRuntime
from google_ads_admin.dispatcher import DispatchError, FixedCloudRunJobDispatcher
from google_ads_admin.status import load_checked_in_contract
from scripts.google_ads_paused_worker import deployment_id


def run_dispatcher_job(
    *,
    ledger: Any,
    dispatcher: Any,
    contract: dict[str, Any],
    claimant_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
) -> dict[str, Any]:
    """Claim, invoke, and settle one deterministic outbox with injected seams."""
    target = deployment_id(contract)
    claimant = claimant_factory()
    if not ledger.claim_paused_create_outbox(target, claimant):
        outbox = ledger.get_paused_create_outbox(target)
        return {
            "schema_version": 1,
            "deployment_id": target,
            "outbox_state": outbox.state,
            "dispatch_attempted": False,
            "dispatch_succeeded": outbox.state == "DISPATCHED",
            "spend_enabled": False,
        }
    try:
        dispatcher.invoke()
    except DispatchError:
        ledger.release_paused_create_outbox(target, claimant)
        return {
            "schema_version": 1,
            "deployment_id": target,
            "outbox_state": "PENDING",
            "dispatch_attempted": True,
            "dispatch_succeeded": False,
            "error_code": "job_invocation_failed",
            "spend_enabled": False,
        }
    ledger.complete_paused_create_outbox(target, claimant)
    return {
        "schema_version": 1,
        "deployment_id": target,
        "outbox_state": "DISPATCHED",
        "dispatch_attempted": True,
        "dispatch_succeeded": True,
        "spend_enabled": False,
    }


def _run_production_job() -> dict[str, Any]:
    runtime = PausedCreateApprovalRuntime.from_env()
    if (
        not runtime.approval_available
        or os.environ.get("THO_GOOGLE_ADS_PAUSED_CREATE_DISPATCH_ENABLED") != "true"
        or runtime.project is None
        or runtime.region is None
        or runtime.job is None
    ):
        return {
            "schema_version": 1,
            "configured": False,
            "dispatch_attempted": False,
            "dispatch_succeeded": False,
            "spend_enabled": False,
        }
    from database.google_ads_authority import FirestoreAuthorityLedger

    ledger = FirestoreAuthorityLedger(project=os.environ.get("GOOGLE_CLOUD_PROJECT"))
    try:
        return run_dispatcher_job(
            ledger=ledger,
            dispatcher=FixedCloudRunJobDispatcher(
                project=runtime.project,
                region=runtime.region,
                job=runtime.job,
            ),
            contract=load_checked_in_contract(),
        )
    finally:
        ledger.close()


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "configured": False,
                    "dispatch_attempted": False,
                    "dispatch_succeeded": False,
                    "error_code": "runtime_overrides_rejected",
                    "spend_enabled": False,
                },
                sort_keys=True,
            )
        )
        return 2
    try:
        result = _run_production_job()
    except Exception:
        result = {
            "schema_version": 1,
            "dispatch_attempted": False,
            "dispatch_succeeded": False,
            "error_code": "dispatcher_unavailable",
            "spend_enabled": False,
        }
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("dispatch_succeeded") else 1


if __name__ == "__main__":
    raise SystemExit(main())
