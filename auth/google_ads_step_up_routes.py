"""Owner-only WebAuthn step-up evidence routes for future PAUSED creation.

These endpoints verify and persist a fresh, purpose-bound owner assertion.
They do not approve a deployment, dispatch work, contact Google Ads, create a
campaign, activate anything, or authorize spend.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from auth import routes as passkey_routes
from auth.csrf import require_cookie_csrf
from auth.google_ads_step_up import (
    MAX_NONCE_TTL_SECONDS,
    PAUSED_CREATE_PURPOSE,
    StepUpCaps,
    StepUpContext,
    StepUpEvidenceEnvelope,
    StepUpNonce,
    StepUpStore,
    StepUpStoreError,
    build_evidence_envelope,
    context_digest,
    default_step_up_store,
    email_hash,
    hash_value,
)
from auth.session import PASSKEY_COOKIE_NAME, SessionManager
from auth.store import CredentialStore
from google_ads_admin.routes import get_authority_ledger
from google_ads_admin.status import load_checked_in_contract
from scripts.google_ads_access_evidence import (
    AccessCheckKey,
    AccessEvidenceStatus,
    InvalidAccessEvidence,
    validate_access_evidence,
)
from scripts.google_ads_launch_draft import contract_sha256

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/admin/passkey/google-ads-step-up",
    tags=["admin-google-ads-step-up"],
)
STEP_UP_COOKIE_NAME = "tho_google_ads_step_up"
STEP_UP_FLOW = "google-ads-paused-create-step-up-v1"
_step_up_store: StepUpStore | None = None
_DEPLOYMENT_PATTERN = r"^[a-z0-9][a-z0-9-]{0,62}--[0-9a-f]{64}$"
_SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"


class StepUpRequestContext(BaseModel):
    """Browser-controlled fields; access evidence is intentionally absent."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    purpose: Literal["PAUSED_CREATE"] = PAUSED_CREATE_PURPOSE
    deployment_id: str = Field(pattern=_DEPLOYMENT_PATTERN)
    contract_hash: str = Field(pattern=_SHA256_PATTERN)
    caps: StepUpCaps


class StepUpCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    context: StepUpRequestContext
    credential: dict[str, Any]


class StepUpCompleteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    verified: Literal[True]
    approval_enabled: Literal[False]
    action_available: Literal[False]
    evidence: StepUpEvidenceEnvelope


def _utc_now() -> datetime:
    return datetime.now(UTC)


def get_session_manager() -> SessionManager:
    return passkey_routes.get_session_manager()


def get_credential_store() -> CredentialStore:
    return passkey_routes.get_credential_store()


def get_step_up_store(
    credential_store: CredentialStore = Depends(get_credential_store),
) -> StepUpStore:
    global _step_up_store
    try:
        if _step_up_store is None:
            _step_up_store = default_step_up_store(credential_store=credential_store)
        return _step_up_store
    except StepUpStoreError:
        raise HTTPException(503, "Owner step-up evidence store unavailable.") from None


def get_access_evidence_ledger():
    return get_authority_ledger()


def _configured_owner_emails() -> set[str]:
    try:
        return passkey_routes.google_ads_owner_emails()
    except ValueError:
        raise HTTPException(503, "Owner step-up is not configured.")


def _require_owner_passkey_session(request: Request, manager: SessionManager) -> str:
    payload = manager.verify_session(request.cookies.get(PASSKEY_COOKIE_NAME))
    if not payload:
        raise HTTPException(401, "A passkey session is required.")
    email = str(payload.get("email") or "").strip().lower()
    if (
        payload.get("user_id") != "admin"
        or payload.get("auth_method") != "passkey"
        or email not in _configured_owner_emails()
    ):
        raise HTTPException(403, "An allowlisted owner passkey session is required.")
    require_cookie_csrf(request)
    return email


def require_owner_step_up(
    request: Request,
    manager: SessionManager = Depends(get_session_manager),
) -> str:
    """Authenticate the exact configured owner through a passkey cookie and CSRF."""
    return _require_owner_passkey_session(request, manager)


def _checked_in_context(
    context: StepUpRequestContext,
    access_ledger: Any,
) -> tuple[StepUpContext, Any]:
    try:
        contract = load_checked_in_contract()
        digest = contract_sha256(contract)
        budget = contract["campaign"]["budget"]
        bidding = contract["campaign"]["bidding"]
        expected = {
            "purpose": "PAUSED_CREATE",
            "deployment_id": f"{contract['deployment']['key']}--{digest}",
            "contract_hash": f"sha256:{digest}",
            "caps": {
                "average_daily_usd": budget["average_daily_usd"],
                "max_single_day_charge_usd": budget["max_single_day_charge_usd"],
                "monthly_charge_limit_usd": budget["monthly_charge_limit_usd"],
                "max_cpc_usd": bidding["max_cpc_usd"],
            },
        }
        if context.model_dump() != expected:
            raise HTTPException(409, "Owner step-up context changed. Refresh and retry.")
        evidence = access_ledger.get_access_evidence(
            context.deployment_id,
            AccessCheckKey.GOOGLE_ADS_ACCOUNT_ACCESS_GREEN,
        )
        evidence = validate_access_evidence(evidence, now=_utc_now())
        if (
            evidence.deployment_id != context.deployment_id
            or evidence.check_key is not AccessCheckKey.GOOGLE_ADS_ACCOUNT_ACCESS_GREEN
            or evidence.status is not AccessEvidenceStatus.PASSED
        ):
            raise InvalidAccessEvidence("current_access_evidence_not_green")
        expected_context = StepUpContext.model_validate(
            {**expected, "evidence_digest": evidence.evidence_digest}
        )
    except HTTPException:
        raise
    except Exception:
        logger.warning("Owner step-up contract or access evidence is unavailable")
        raise HTTPException(503, "Fresh owner step-up access evidence unavailable.") from None
    return expected_context, evidence


def _owner_credentials(store: CredentialStore, owner_email: str) -> list[Any]:
    try:
        records = store.list_for_user(owner_email)
    except Exception:
        logger.warning("Owner step-up credential lookup failed")
        raise HTTPException(503, "Owner passkey store unavailable.") from None
    if not records:
        raise HTTPException(409, "Register an owner passkey before step-up.")
    return records


@router.post("/begin")
def begin_step_up(
    request: Request,
    context: StepUpRequestContext,
    manager: SessionManager = Depends(get_session_manager),
    credential_store: CredentialStore = Depends(get_credential_store),
    step_up_store: StepUpStore = Depends(get_step_up_store),
    access_ledger: Any = Depends(get_access_evidence_ledger),
    owner_email: str = Depends(require_owner_step_up),
) -> JSONResponse:
    """Create one short nonce and require a fresh UV WebAuthn assertion."""
    if not passkey_routes._HAS_WEBAUTHN:
        raise HTTPException(503, "WebAuthn is unavailable.")
    context, _access_evidence = _checked_in_context(context, access_ledger)
    credentials = _owner_credentials(credential_store, owner_email)
    challenge = manager.new_challenge_bytes()
    issued_at = _utc_now()
    nonce = StepUpNonce(
        nonce_hash=hash_value(challenge),
        context_digest=context_digest(context),
        owner_email_hash=email_hash(owner_email),
        issued_at=issued_at,
        expires_at=issued_at + timedelta(seconds=MAX_NONCE_TTL_SECONDS),
    )
    try:
        step_up_store.create_nonce(nonce)
    except Exception:
        logger.warning("Owner step-up nonce persistence failed")
        raise HTTPException(503, "Owner step-up evidence store unavailable.") from None

    _, rp_id = passkey_routes._webauthn_context(request)
    options = passkey_routes.generate_authentication_options(
        rp_id=rp_id,
        challenge=challenge,
        user_verification=passkey_routes.UserVerificationRequirement.REQUIRED,
        allow_credentials=[
            passkey_routes.PublicKeyCredentialDescriptor(
                id=record.credential_id,
                type=passkey_routes.PublicKeyCredentialType.PUBLIC_KEY,
            )
            for record in credentials
        ],
    )
    challenge_handle = manager.wrap_challenge(
        challenge,
        flow=STEP_UP_FLOW,
        context_digest=nonce.context_digest,
        owner_email_hash=nonce.owner_email_hash,
    )
    response = JSONResponse(passkey_routes.options_to_json_dict(options))
    response.set_cookie(
        key=STEP_UP_COOKIE_NAME,
        value=challenge_handle,
        max_age=MAX_NONCE_TTL_SECONDS,
        httponly=True,
        secure=passkey_routes._cookie_secure(request),
        samesite="Strict",
        path=f"{router.prefix}/complete",
    )
    return response


@router.post("/complete", response_model=StepUpCompleteResponse)
def complete_step_up(
    request: Request,
    payload: StepUpCompleteRequest,
    challenge_cookie: str | None = Cookie(default=None, alias=STEP_UP_COOKIE_NAME),
    manager: SessionManager = Depends(get_session_manager),
    credential_store: CredentialStore = Depends(get_credential_store),
    step_up_store: StepUpStore = Depends(get_step_up_store),
    access_ledger: Any = Depends(get_access_evidence_ledger),
    owner_email: str = Depends(require_owner_step_up),
) -> JSONResponse:
    """Persist sanitized UV evidence; never grant or execute approval authority."""
    if not passkey_routes._HAS_WEBAUTHN:
        raise HTTPException(503, "WebAuthn is unavailable.")
    context, access_evidence = _checked_in_context(payload.context, access_ledger)
    challenge_payload = manager.unwrap_challenge_payload(
        challenge_cookie,
        flow=STEP_UP_FLOW,
    )
    if challenge_payload is None:
        raise HTTPException(409, "Owner step-up challenge expired or was already used.")
    challenge = challenge_payload["challenge"]
    nonce_hash = hash_value(challenge)
    digest = context_digest(context)
    if challenge_payload.get("context_digest") != digest or challenge_payload.get(
        "owner_email_hash"
    ) != email_hash(owner_email):
        raise HTTPException(409, "Owner step-up context changed. Refresh and retry.")

    raw_id = passkey_routes._decode_credential_id(payload.credential.get("id", ""))
    try:
        credential_record = credential_store.get(raw_id)
    except Exception:
        logger.warning("Owner step-up credential read failed")
        raise HTTPException(503, "Owner passkey store unavailable.") from None
    if (
        credential_record is None
        or str(credential_record.user_id).strip().lower() != owner_email
        or owner_email not in _configured_owner_emails()
    ):
        raise HTTPException(403, "This passkey is not registered to the current owner.")

    expected_origin, expected_rp_id = passkey_routes._webauthn_context(request)
    try:
        parsed = passkey_routes.parse_authentication_credential_json(payload.credential)
        verified = passkey_routes.verify_authentication_response(
            credential=parsed,
            expected_challenge=challenge,
            expected_origin=expected_origin,
            expected_rp_id=expected_rp_id,
            credential_public_key=credential_record.public_key,
            credential_current_sign_count=credential_record.sign_count,
            require_user_verification=True,
        )
        if verified.credential_id != raw_id:
            raise ValueError("credential identity mismatch")
    except Exception:
        logger.warning("Owner step-up assertion verification failed")
        raise HTTPException(400, "Owner passkey user verification failed.") from None

    try:
        stored_nonce = step_up_store.get_nonce(nonce_hash)
    except Exception:
        logger.warning("Owner step-up nonce read failed")
        raise HTTPException(503, "Owner step-up evidence store unavailable.") from None
    if stored_nonce is None:
        raise HTTPException(409, "Owner step-up challenge expired or was already used.")
    envelope = build_evidence_envelope(
        nonce=stored_nonce,
        context=context,
        credential_id_hash=hash_value(raw_id),
        verified_at=_utc_now(),
    )
    try:
        credential_usage = credential_store.prepare_usage_cas(
            credential_record,
            new_sign_count=verified.new_sign_count,
        )
    except (TypeError, ValueError):
        raise HTTPException(409, "Owner passkey counter changed. Refresh and retry.") from None
    try:
        consumed = step_up_store.consume_and_record(
            nonce_hash,
            expected_context_digest=digest,
            envelope=envelope,
            access_evidence=access_evidence,
            credential_store=credential_store,
            credential_usage=credential_usage,
        )
    except Exception:
        logger.warning("Owner step-up evidence persistence failed")
        raise HTTPException(503, "Owner step-up evidence store unavailable.") from None
    if not consumed:
        raise HTTPException(409, "Owner step-up challenge expired or was already used.")

    response = JSONResponse(
        StepUpCompleteResponse(
            verified=True,
            approval_enabled=False,
            action_available=False,
            evidence=envelope,
        ).model_dump(mode="json")
    )
    response.delete_cookie(
        key=STEP_UP_COOKIE_NAME,
        path=f"{router.prefix}/complete",
    )
    return response
