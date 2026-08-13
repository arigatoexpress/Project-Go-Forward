"""Exact-owner PAUSED-create approval route; writes authority/outbox only."""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from auth.google_ads_step_up import (
    StepUpProofReference,
    email_hash,
    verify_proof_reference,
)
from auth.google_ads_step_up_routes import (
    get_session_manager,
    require_owner_step_up,
)
from auth.session import SessionManager
from database.google_ads_authority import FirestoreAuthorityLedger
from google_ads_admin.approval import PausedCreateApprovalRuntime
from google_ads_admin.routes import get_authority_ledger
from google_ads_admin.status import load_checked_in_contract
from scripts.google_ads_access_evidence import (
    AccessCheckKey,
    AccessEvidenceStatus,
)
from scripts.google_ads_launch_draft import contract_sha256
from scripts.google_ads_paused_worker import (
    InvalidStateTransition,
    LedgerConflict,
    LedgerWriteError,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/google-ads", tags=["admin-google-ads-owner-approval"])
_SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"
_DEPLOYMENT_PATTERN = r"^[a-z0-9][a-z0-9-]{0,62}--[0-9a-f]{64}$"


class PausedCreateApprovalRequest(BaseModel):
    """The entire browser-controlled approval surface."""

    model_config = ConfigDict(extra="forbid", strict=True)

    deployment_id: str = Field(pattern=_DEPLOYMENT_PATTERN)
    expected_version: int = Field(ge=1)
    proof_reference: str = Field(min_length=32, max_length=4096)
    proof_id: str = Field(pattern=_SHA256_PATTERN)
    access_evidence_id: str = Field(pattern=_SHA256_PATTERN)


class PausedCreateApprovalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    deployment_id: str = Field(pattern=_DEPLOYMENT_PATTERN)
    contract_hash: str = Field(pattern=_SHA256_PATTERN)
    state: str
    version: int = Field(ge=3)
    outbox_state: str
    replayed: bool
    paused_only: bool = True
    activation_authorized: bool = False
    spend_enabled: bool = False


class PausedCreateApprovalReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: int = 1
    deployment_id: str = Field(pattern=_DEPLOYMENT_PATTERN)
    contract_hash: str = Field(pattern=_SHA256_PATTERN)
    expected_version: int = Field(ge=1)
    state: str
    budget: dict[str, int]
    gates: dict[str, bool]
    access_evidence_id: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    access_evidence_fresh: bool
    action_available: bool
    dispatch_enabled: bool
    paused_only: bool = True
    activation_authorized: bool = False
    spend_enabled: bool = False
    remediation: list[str]


def get_approval_ledger() -> FirestoreAuthorityLedger:
    return get_authority_ledger()


def _checked_in_identity(request: PausedCreateApprovalRequest) -> tuple[str, dict[str, int]]:
    contract = load_checked_in_contract()
    digest = contract_sha256(contract)
    expected_deployment_id = f"{contract['deployment']['key']}--{digest}"
    if request.deployment_id != expected_deployment_id:
        raise InvalidStateTransition("reviewed contract changed")
    budget = contract["campaign"]["budget"]
    bidding = contract["campaign"]["bidding"]
    return f"sha256:{digest}", {
        "average_daily_usd": budget["average_daily_usd"],
        "max_single_day_charge_usd": budget["max_single_day_charge_usd"],
        "monthly_charge_limit_usd": budget["monthly_charge_limit_usd"],
        "max_cpc_usd": bidding["max_cpc_usd"],
    }


def _verify_request_proof(
    manager: SessionManager,
    request: PausedCreateApprovalRequest,
    owner_email: str,
) -> StepUpProofReference:
    proof = verify_proof_reference(manager, request.proof_reference)
    if (
        proof is None
        or proof.proof_id != request.proof_id
        or proof.access_evidence_id != request.access_evidence_id
        or proof.deployment_id != request.deployment_id
        or proof.owner_email_hash != email_hash(owner_email)
    ):
        raise InvalidStateTransition("owner proof changed")
    return proof


def _read_approval_readiness(ledger: FirestoreAuthorityLedger):
    runtime = PausedCreateApprovalRuntime.from_env()
    contract = load_checked_in_contract()
    digest = contract_sha256(contract)
    deployment_id = f"{contract['deployment']['key']}--{digest}"
    record = ledger.get(deployment_id)
    access_evidence_id = None
    access_evidence_fresh = False
    try:
        evidence = ledger.get_access_evidence(
            deployment_id,
            AccessCheckKey.GOOGLE_ADS_ACCOUNT_ACCESS_GREEN,
        )
        access_evidence_fresh = evidence.status is AccessEvidenceStatus.PASSED
        access_evidence_id = evidence.evidence_digest if access_evidence_fresh else None
    except Exception:
        pass
    dispatch_enabled = os.environ.get("THO_GOOGLE_ADS_PAUSED_CREATE_DISPATCH_ENABLED") == "true"
    action_available = (
        runtime.approval_available
        and record.state.value == "SERVER_VALIDATED"
        and record.version == 2
        and access_evidence_fresh
        and not dispatch_enabled
    )
    remediation = []
    if record.state.value != "SERVER_VALIDATED" or record.version != 2:
        remediation.append("Complete offline server validation.")
    if not access_evidence_fresh:
        remediation.append("Run the read-only Google Ads account-access evidence job.")
    if not runtime.approval_available:
        remediation.append("Verify current cloud/IAM readiness and enable owner approval config.")
    if dispatch_enabled:
        remediation.append("Disable PAUSED-create dispatch before owner approval.")
    budget = contract["campaign"]["budget"]
    return PausedCreateApprovalReadiness(
        deployment_id=deployment_id,
        contract_hash=f"sha256:{digest}",
        expected_version=record.version,
        state=record.state.value,
        budget={
            "average_daily_usd": budget["average_daily_usd"],
            "max_single_day_charge_usd": budget["max_single_day_charge_usd"],
            "monthly_charge_limit_usd": budget["monthly_charge_limit_usd"],
            "max_cpc_usd": contract["campaign"]["bidding"]["max_cpc_usd"],
        },
        gates=runtime.sanitized_gates(),
        access_evidence_id=access_evidence_id,
        access_evidence_fresh=access_evidence_fresh,
        action_available=action_available,
        dispatch_enabled=dispatch_enabled,
        paused_only=True,
        activation_authorized=False,
        spend_enabled=False,
        remediation=remediation,
    )


@router.get("/paused-create-approval-readiness", response_model=PausedCreateApprovalReadiness)
async def paused_create_approval_readiness(
    ledger: FirestoreAuthorityLedger = Depends(get_approval_ledger),
    _owner_email: str = Depends(require_owner_step_up),
):
    """Return sanitized owner-only prerequisites; never mutates authority state."""
    try:
        return await run_in_threadpool(_read_approval_readiness, ledger)
    except Exception:
        logger.warning("PAUSED-only approval readiness is unavailable")
        raise HTTPException(503, "PAUSED-only approval readiness is unavailable.") from None


@router.post("/paused-create-approval", response_model=PausedCreateApprovalResponse)
async def approve_paused_create(
    request: PausedCreateApprovalRequest,
    manager: SessionManager = Depends(get_session_manager),
    ledger: FirestoreAuthorityLedger = Depends(get_approval_ledger),
    owner_email: str = Depends(require_owner_step_up),
):
    """Consume one owner UV proof and atomically queue PAUSED-only work."""
    runtime = PausedCreateApprovalRuntime.from_env()
    if not runtime.approval_available:
        raise HTTPException(503, "PAUSED-only approval prerequisites are unavailable.")
    if os.environ.get("THO_GOOGLE_ADS_PAUSED_CREATE_DISPATCH_ENABLED") == "true":
        raise HTTPException(503, "Disable PAUSED-create dispatch before owner approval.")
    try:
        proof = _verify_request_proof(manager, request, owner_email)
        contract_hash, caps = _checked_in_identity(request)
        if proof.contract_hash != contract_hash:
            raise InvalidStateTransition("reviewed contract changed")
        result = await run_in_threadpool(
            ledger.approve_paused_create_with_proof,
            deployment_id=request.deployment_id,
            expected_version=request.expected_version,
            contract_hash=contract_hash,
            expected_caps=caps,
            proof=proof,
            access_evidence_id=request.access_evidence_id,
        )
        return PausedCreateApprovalResponse(
            **result,
            paused_only=True,
            activation_authorized=False,
            spend_enabled=False,
        )
    except (InvalidStateTransition, LedgerConflict):
        raise HTTPException(409, "PAUSED-only approval changed. Refresh and retry.") from None
    except LedgerWriteError:
        logger.warning("PAUSED-only approval evidence is unavailable")
        raise HTTPException(503, "PAUSED-only approval evidence is unavailable.") from None
    except HTTPException:
        raise
    except Exception:
        logger.warning("PAUSED-only approval is unavailable")
        raise HTTPException(503, "PAUSED-only approval is unavailable.") from None
