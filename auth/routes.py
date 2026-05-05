"""FastAPI WebAuthn passkey routes for THO admin authentication.

Mirrors the Sapphire analytics_dashboard auth blueprint, adapted for
FastAPI and THO's endpoint conventions.

Endpoints (mounted under /api/admin/passkey):
    POST /register/begin
    POST /register/complete
    POST /login/begin
    POST /login/complete
    GET  /status
    POST /logout
"""

from __future__ import annotations

import logging
import os
from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Any

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from .session import (
    PASSKEY_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    SessionManager,
)
from .store import CredentialStore, default_store

# webauthn may not be installed in all environments (e.g. CI without it)
try:
    from webauthn import generate_authentication_options, generate_registration_options, verify_authentication_response, verify_registration_response
    from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
    from webauthn.helpers.structs import (
        AttestationConveyancePreference,
        AuthenticatorSelectionCriteria,
        PublicKeyCredentialCreationOptions,
        PublicKeyCredentialRequestOptions,
        RegistrationCredential,
        AuthenticationCredential,
        UserVerificationRequirement,
        ResidentKeyRequirement,
        AuthenticatorAttachment,
    )
    _HAS_WEBAUTHN = True
except Exception:
    _HAS_WEBAUTHN = False

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/passkey", tags=["passkey"])


# ---------------------------------------------------------------------------
# Helpers / deps
# ---------------------------------------------------------------------------

def get_session_manager() -> SessionManager:
    return SessionManager()


def get_credential_store() -> CredentialStore:
    return default_store()


THO_ORIGIN = os.environ.get("THO_ORIGIN", "https://tho.sapphirealpha.xyz")
RP_NAME = "THO Admin"
RP_ID = os.environ.get("THO_RP_ID", "sapphirealpha.xyz")


def _current_user(
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    manager: SessionManager = Depends(get_session_manager),
) -> dict | None:
    return manager.verify_session(session)


def _require_passkey_user(
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    manager: SessionManager = Depends(get_session_manager),
) -> dict:
    payload = manager.verify_session(session)
    if not payload:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return payload


def _user_verification() -> UserVerificationRequirement:
    return UserVerificationRequirement.PREFERRED


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

@router.post("/register/begin")
def register_begin(
    request: Request,
    response: Response,
    manager: SessionManager = Depends(get_session_manager),
) -> JSONResponse:
    """Begin passkey registration. Returns PublicKeyCredentialCreationOptions."""
    if not _HAS_WEBAUTHN:
        raise HTTPException(status_code=503, detail="WebAuthn library not available")

    challenge = manager.new_challenge_bytes()
    challenge_handle = manager.wrap_challenge(challenge, flow="register")

    # Stash challenge handle in a short-lived cookie so the client
    # sends it back on the /complete call without needing to parse JSON.
    response.set_cookie(
        key="tho_passkey_register",
        value=challenge_handle,
        max_age=300,
        httponly=True,
        secure=True,
        samesite="Strict",
        path="/api/admin/passkey/register/complete",
    )

    options = generate_registration_options(
        rp_name=RP_NAME,
        rp_id=RP_ID,
        user_id=b"tho-admin",
        user_name="THO Admin",
        user_display_name="THO Admin",
        challenge=challenge,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )
    return JSONResponse(options)


@router.post("/register/complete")
def register_complete(
    request: Request,
    response: Response,
    credential: dict[str, Any],
    challenge_cookie: str | None = Cookie(default=None, alias="tho_passkey_register"),
    manager: SessionManager = Depends(get_session_manager),
    store: CredentialStore = Depends(get_credential_store),
) -> JSONResponse:
    """Complete passkey registration. Stores the credential."""
    if not _HAS_WEBAUTHN:
        raise HTTPException(status_code=503, detail="WebAuthn library not available")

    expected_challenge = manager.unwrap_challenge(challenge_cookie, flow="register")
    if expected_challenge is None:
        raise HTTPException(status_code=400, detail="Challenge expired or invalid")

    # Clear the temporary challenge cookie
    response.delete_cookie(key="tho_passkey_register", path="/api/admin/passkey/register/complete")

    try:
        reg_cred = RegistrationCredential.model_validate(credential)
        verified = verify_registration_response(
            credential=reg_cred,
            expected_challenge=expected_challenge,
            expected_origin=THO_ORIGIN,
            expected_rp_id=RP_ID,
        )
    except Exception as exc:
        log.warning("passkey register verify failed: %s", exc)
        raise HTTPException(status_code=400, detail="Registration verification failed")

    from .store import CredentialRecord

    record = CredentialRecord(
        credential_id=verified.credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        user_id="admin",
        aaguid=str(verified.aaguid) if verified.aaguid else "",
    )
    store.add(record)

    # Issue session immediately after registration
    token = manager.issue_session("admin")
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=manager.session_ttl,
        httponly=True,
        secure=True,
        samesite="Strict",
    )
    return JSONResponse({"success": True, "registered": True})


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

@router.post("/login/begin")
def login_begin(
    request: Request,
    response: Response,
    manager: SessionManager = Depends(get_session_manager),
    store: CredentialStore = Depends(get_credential_store),
) -> JSONResponse:
    """Begin passkey login. Returns PublicKeyCredentialRequestOptions."""
    if not _HAS_WEBAUTHN:
        raise HTTPException(status_code=503, detail="WebAuthn library not available")

    challenge = manager.new_challenge_bytes()
    challenge_handle = manager.wrap_challenge(challenge, flow="login")

    # Stash challenge in cookie for the complete step
    response.set_cookie(
        key="tho_passkey_login",
        value=challenge_handle,
        max_age=300,
        httponly=True,
        secure=True,
        samesite="Strict",
        path="/api/admin/passkey/login/complete",
    )

    allow_credentials = []
    for rec in store.list_for_user("admin"):
        allow_credentials.append(
            {
                "id": rec.credential_id,
                "type": "public-key",
            }
        )

    options = generate_authentication_options(
        rp_id=RP_ID,
        challenge=challenge,
        allow_credentials=allow_credentials,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    return JSONResponse(options)


@router.post("/login/complete")
def login_complete(
    request: Request,
    response: Response,
    credential: dict[str, Any],
    challenge_cookie: str | None = Cookie(default=None, alias="tho_passkey_login"),
    manager: SessionManager = Depends(get_session_manager),
    store: CredentialStore = Depends(get_credential_store),
) -> JSONResponse:
    """Complete passkey login. Issues session cookie on success."""
    if not _HAS_WEBAUTHN:
        raise HTTPException(status_code=503, detail="WebAuthn library not available")

    expected_challenge = manager.unwrap_challenge(challenge_cookie, flow="login")
    if expected_challenge is None:
        raise HTTPException(status_code=400, detail="Challenge expired or invalid")

    # Clear temp cookie
    response.delete_cookie(key="tho_passkey_login", path="/api/admin/passkey/login/complete")

    # Resolve credential from the id in the payload
    cred_id_b64 = credential.get("id", "")
    try:
        raw_id = base64url_to_bytes(cred_id_b64) if _HAS_WEBAUTHN else urlsafe_b64decode(cred_id_b64)
    except Exception:
        raw_id = urlsafe_b64decode(cred_id_b64)

    rec = store.get(raw_id)
    if rec is None:
        raise HTTPException(status_code=400, detail="Unknown credential")

    try:
        auth_cred = AuthenticationCredential.model_validate(credential)
        verified = verify_authentication_response(
            credential=auth_cred,
            expected_challenge=expected_challenge,
            expected_origin=THO_ORIGIN,
            expected_rp_id=RP_ID,
            credential_public_key=rec.public_key,
            credential_current_sign_count=rec.sign_count,
        )
    except Exception as exc:
        log.warning("passkey login verify failed: %s", exc)
        raise HTTPException(status_code=400, detail="Authentication verification failed")

    store.update_usage(verified.credential_id, sign_count=verified.new_sign_count)

    token = manager.issue_session("admin")
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=manager.session_ttl,
        httponly=True,
        secure=True,
        samesite="Strict",
    )
    return JSONResponse({"success": True, "authenticated": True})


# ---------------------------------------------------------------------------
# Status / Whoami / Logout
# ---------------------------------------------------------------------------

@router.get("/status")
def passkey_status(
    store: CredentialStore = Depends(get_credential_store),
) -> JSONResponse:
    """Return whether passkeys are configured and how many."""
    count = store.count()
    return JSONResponse(
        {
            "enabled": _HAS_WEBAUTHN,
            "registered_keys": count,
            "has_keys": count > 0,
        }
    )


@router.get("/whoami")
def whoami(user: dict = Depends(_require_passkey_user)) -> JSONResponse:
    return JSONResponse({"user_id": user.get("user_id"), "authed": True})


@router.post("/logout")
def passkey_logout(response: Response) -> JSONResponse:
    response.delete_cookie(key=SESSION_COOKIE_NAME)
    return JSONResponse({"success": True})
