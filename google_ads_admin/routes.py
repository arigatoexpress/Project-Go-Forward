"""Authenticated durable review routes; no provider, job, or approval capability."""

from __future__ import annotations

import logging
from functools import lru_cache

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from database.google_ads_authority import FirestoreAuthorityLedger
from google_ads_admin.status import build_deployment_readiness, load_checked_in_contract
from scripts.google_ads_paused_worker import (
    ContractValidationError,
    DeploymentNotFound,
    DraftReviewControlPlane,
    InvalidStateTransition,
    LedgerConflict,
    StaticContractSource,
    deployment_id,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/google-ads", tags=["admin-google-ads"])


class ServerValidationRequest(BaseModel):
    """The complete client-controlled surface for deterministic offline validation."""

    model_config = ConfigDict(extra="forbid", strict=True)

    deployment_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}--[0-9a-f]{64}$")
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{16,128}$")


class DraftBootstrapRequest(BaseModel):
    """Bootstrap accepts no caller-controlled contract or authority fields."""

    model_config = ConfigDict(extra="forbid", strict=True)


@lru_cache(maxsize=1)
def get_authority_ledger() -> FirestoreAuthorityLedger:
    """Return one bounded, process-local adapter over the durable Firestore ledger."""
    return FirestoreAuthorityLedger()


def _projection(ledger, contract):
    record = ledger.get(deployment_id(contract))
    events = ledger.list_events(record.deployment_id)
    return build_deployment_readiness(record, events)


@router.get("/deployment-readiness")
async def deployment_readiness():
    """Read the allowlisted durable projection without mutating authority state."""
    try:
        contract = load_checked_in_contract()
        return await run_in_threadpool(_projection, get_authority_ledger(), contract)
    except DeploymentNotFound:
        raise HTTPException(404, "Paid Search deployment draft is not initialized.") from None
    except Exception:  # noqa: BLE001 - never expose Firestore or contract details
        logger.warning("Paid Search deployment readiness is unavailable")
        raise HTTPException(503, "Paid Search deployment readiness is unavailable.") from None


def _bootstrap_draft(ledger, contract):
    control = DraftReviewControlPlane(ledger, StaticContractSource(contract))
    record = control.ensure_internal_draft()
    events = ledger.list_events(record.deployment_id)
    return build_deployment_readiness(record, events)


@router.post("/draft")
async def bootstrap_draft(_request: DraftBootstrapRequest):
    """Idempotently create the reviewed draft through a CSRF-protected mutation."""
    try:
        contract = load_checked_in_contract()
        return await run_in_threadpool(
            _bootstrap_draft,
            get_authority_ledger(),
            contract,
        )
    except Exception:  # noqa: BLE001 - never expose Firestore or contract details
        logger.warning("Paid Search deployment draft is unavailable")
        raise HTTPException(503, "Paid Search deployment draft is unavailable.") from None


def _server_validate(ledger, contract, request: ServerValidationRequest):
    control = DraftReviewControlPlane(ledger, StaticContractSource(contract))
    record = control.server_validate(
        request.deployment_id,
        expected_version=request.expected_version,
        idempotency_key=request.idempotency_key,
    )
    events = ledger.list_events(record.deployment_id)
    return build_deployment_readiness(record, events)


@router.post("/server-validation")
async def server_validation(request: ServerValidationRequest):
    """Run contract-only validation and atomically record its durable result."""
    try:
        # Contract validity is established before the first Firestore read or write.
        contract = load_checked_in_contract()
        return await run_in_threadpool(
            _server_validate,
            get_authority_ledger(),
            contract,
            request,
        )
    except (ContractValidationError, InvalidStateTransition, LedgerConflict):
        raise HTTPException(
            409, "Paid Search deployment state changed. Refresh and retry."
        ) from None
    except Exception:  # noqa: BLE001 - never expose Firestore or contract details
        logger.warning("Paid Search offline server validation is unavailable")
        raise HTTPException(503, "Paid Search offline server validation is unavailable.") from None
