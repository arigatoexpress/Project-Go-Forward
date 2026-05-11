"""FastAPI WebAuthn passkey routes for THO admin authentication.

Mirrors the Sapphire analytics_dashboard auth blueprint, adapted for
FastAPI and THO's endpoint conventions.

Endpoints (mounted under /api/admin/passkey):
    POST /register/begin
    POST /register/complete
    POST /login/begin
    POST /login/complete
    GET  /credentials
    DELETE /credentials/{credential_id_b64}
    GET  /status
    POST /logout
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import struct
import time
from base64 import urlsafe_b64decode
from typing import Any

from fastapi import APIRouter, Body, Cookie, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .session import (
    PASSKEY_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    SessionManager,
)
from .store import CredentialStore, CredentialStoreUnavailable, default_store

# webauthn may not be installed in all environments (e.g. CI without it)
try:
    from webauthn import (
        generate_authentication_options,
        generate_registration_options,
        verify_authentication_response,
        verify_registration_response,
    )
    from webauthn.helpers import (
        base64url_to_bytes,
        options_to_json_dict,
        parse_authentication_credential_json,
        parse_registration_credential_json,
    )
    from webauthn.helpers.structs import (
        AttestationConveyancePreference,
        AuthenticatorSelectionCriteria,
        PublicKeyCredentialDescriptor,
        PublicKeyCredentialType,
        ResidentKeyRequirement,
        UserVerificationRequirement,
    )

    _HAS_WEBAUTHN = True
except Exception:
    _HAS_WEBAUTHN = False

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/passkey", tags=["passkey"])
_session_manager: SessionManager | None = None


# ---------------------------------------------------------------------------
# Helpers / deps
# ---------------------------------------------------------------------------


def get_session_manager() -> SessionManager:
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager


def get_credential_store() -> CredentialStore:
    try:
        return default_store()
    except CredentialStoreUnavailable as exc:
        log.warning("passkey credential store unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="Passkey credential store unavailable")


THO_ORIGIN = os.environ.get("WEBAUTHN_ORIGIN") or os.environ.get(
    "THO_ORIGIN", "https://tho.sapphirealpha.xyz"
)
RP_NAME = "THO Admin"
RP_ID = os.environ.get("WEBAUTHN_RP_ID") or os.environ.get("THO_RP_ID", "sapphirealpha.xyz")
DEFAULT_PASSKEY_STAFF_DOMAIN = "texashomeoutlet.com"
DEFAULT_OWNER_EMAILS = {
    "aribspector@gmail.com",
    "aristotlespec@gmail.com",
}
_CUTOVER_ORIGIN_RP_IDS = {
    "https://tho.sapphirealpha.xyz": "sapphirealpha.xyz",
    "https://tho.sapphire.xyz": "sapphire.xyz",
}


def _normalize_origin(origin: str | None) -> str:
    return str(origin or "").strip().rstrip("/").lower()


def _origin_rp_ids() -> dict[str, str]:
    """Return allowed WebAuthn origins and their RP IDs for domain cutovers."""
    pairs = dict(_CUTOVER_ORIGIN_RP_IDS)
    pairs[_normalize_origin(THO_ORIGIN)] = RP_ID
    raw_pairs = os.environ.get("WEBAUTHN_ORIGIN_RP_IDS", "")
    for item in raw_pairs.split(","):
        origin, sep, rp_id = item.partition("=")
        if sep and origin.strip() and rp_id.strip():
            pairs[_normalize_origin(origin)] = rp_id.strip().lower()
    return {origin: rp_id for origin, rp_id in pairs.items() if origin and rp_id}


def _request_origin(request: Request) -> str:
    origin = _normalize_origin(request.headers.get("origin"))
    if origin:
        return origin
    host = (
        (request.headers.get("x-forwarded-host") or request.headers.get("host") or "")
        .split(",")[0]
        .strip()
    )
    proto = (
        (request.headers.get("x-forwarded-proto") or request.url.scheme or "https")
        .split(",")[0]
        .strip()
    )
    if host:
        return _normalize_origin(f"{proto}://{host}")
    return _normalize_origin(THO_ORIGIN)


def _webauthn_context(request: Request) -> tuple[str, str]:
    origin = _request_origin(request)
    origin_rp_ids = _origin_rp_ids()
    if origin in origin_rp_ids:
        return origin, origin_rp_ids[origin]
    return _normalize_origin(THO_ORIGIN), RP_ID


def _split_env_values(*names: str) -> set[str]:
    values: set[str] = set()
    for name in names:
        for raw in os.environ.get(name, "").split(","):
            value = raw.strip().lower()
            if value:
                values.add(value)
    return values


def _normalize_email(value: Any) -> str:
    return str(value or "").strip().lower()


def _passkey_allowed_owner_emails() -> set[str]:
    configured = _split_env_values("THO_PASSKEY_OWNER_EMAILS", "THO_ADMIN_OWNER_EMAILS")
    return configured or set(DEFAULT_OWNER_EMAILS)


def _passkey_allowed_domains() -> set[str]:
    configured = _split_env_values("THO_PASSKEY_ALLOWED_DOMAINS")
    return configured or {DEFAULT_PASSKEY_STAFF_DOMAIN}


def _passkey_email_allowed(email: str | None) -> bool:
    normalized = _normalize_email(email)
    if not normalized or "@" not in normalized:
        return False
    if normalized in _passkey_allowed_owner_emails():
        return True
    _, _, domain = normalized.rpartition("@")
    return domain in _passkey_allowed_domains()


def _require_allowed_passkey_email(email: Any) -> str:
    normalized = _normalize_email(email)
    if not normalized:
        raise HTTPException(status_code=400, detail="Authorized email is required")
    if len(normalized.encode("utf-8")) > 200 or "@" not in normalized:
        raise HTTPException(status_code=400, detail="Enter a valid email address")
    if not _passkey_email_allowed(normalized):
        raise HTTPException(
            status_code=403,
            detail="Passkeys are limited to Ari or @texashomeoutlet.com staff.",
        )
    return normalized


def _credential_allowed(rec: Any) -> bool:
    return _passkey_email_allowed(getattr(rec, "user_id", ""))


def _credential_records(store: CredentialStore, *, allowed_only: bool = False) -> list[Any]:
    try:
        rows = store.list_all()
    except Exception as exc:
        log.warning("passkey credential lookup failed: %s", exc)
        raise HTTPException(status_code=503, detail="Passkey credential store unavailable")
    if allowed_only:
        rows = [rec for rec in rows if _credential_allowed(rec)]
    return rows


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


def _cookie_secure(request: Request) -> bool:
    return _webauthn_context(request)[0].startswith("https://")


def _admin_token_from_request(request: Request) -> str:
    token = request.cookies.get("tho_admin_token", "").strip()
    if token:
        return token
    token = request.headers.get("X-Admin-Token", "").strip()
    if token:
        return token
    authorization = request.headers.get("Authorization", "").strip()
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer" and value:
        return value.strip()
    return ""


def _verify_admin_pin_token(token: str) -> bool:
    admin_pin_hash = os.environ.get("ADMIN_PIN_HASH", "")
    if not token or not admin_pin_hash:
        return False
    try:
        padding = 4 - len(token) % 4
        if padding != 4:
            token += "=" * padding
        raw = urlsafe_b64decode(token)
        if len(raw) != 24:
            return False
        payload, sig = raw[:8], raw[8:]
        secret = hashlib.sha256(f"sapphire-jwt-{admin_pin_hash[:16]}".encode()).digest()
        expected_sig = hmac.new(secret, payload, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(sig, expected_sig):
            return False
        expires = struct.unpack(">Q", payload)[0]
        return time.time() < expires
    except Exception:
        return False


def _request_is_admin(request: Request, manager: SessionManager) -> bool:
    passkey_payload = manager.verify_session(request.cookies.get(PASSKEY_COOKIE_NAME))
    if passkey_payload and passkey_payload.get("user_id") == "admin":
        return True
    return _verify_admin_pin_token(_admin_token_from_request(request))


def _require_admin_request(request: Request, manager: SessionManager) -> None:
    if not _request_is_admin(request, manager):
        raise HTTPException(status_code=401, detail="Admin authentication required")


def _credential_descriptors(
    store: CredentialStore,
    *,
    allowed_only: bool = False,
) -> list[Any]:
    return [
        PublicKeyCredentialDescriptor(
            id=rec.credential_id,
            type=PublicKeyCredentialType.PUBLIC_KEY,
        )
        for rec in _credential_records(store, allowed_only=allowed_only)
    ]


def _discoverable_login_enabled() -> bool:
    """Let synced passkey providers present resident credentials for this RP.

    Login completion still verifies that the selected credential exists in the
    server-side store, but avoiding a browser allow-list makes Proton Pass and
    cross-device passkey recovery more reliable after a credential migration.
    """
    raw = os.environ.get("THO_PASSKEY_DISCOVERABLE_LOGIN", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _decode_credential_id(value: str) -> bytes:
    if not value:
        raise HTTPException(status_code=400, detail="Credential id is required")
    try:
        return base64url_to_bytes(value) if _HAS_WEBAUTHN else urlsafe_b64decode(value)
    except Exception:
        try:
            padding = "=" * (-len(value) % 4)
            return urlsafe_b64decode(value + padding)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid credential id")


def _serialize_credential(rec: Any) -> dict[str, Any]:
    def _dt(value: Any) -> str | None:
        return value.isoformat() if hasattr(value, "isoformat") else None

    return {
        "credential_id": rec.credential_id_b64,
        "user_id": rec.user_id,
        "authorized": _credential_allowed(rec),
        "aaguid": rec.aaguid or "",
        "created_at": _dt(rec.created_at),
        "last_used_at": _dt(rec.last_used_at),
        "sign_count": rec.sign_count,
    }


def _store_backend(store: CredentialStore) -> str:
    return str(getattr(store, "backend_name", store.__class__.__name__))


def _store_persistent(store: CredentialStore) -> bool:
    return bool(getattr(store, "persistent", False))


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


@router.post("/register/begin")
def register_begin(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    manager: SessionManager = Depends(get_session_manager),
    store: CredentialStore = Depends(get_credential_store),
) -> JSONResponse:
    """Begin passkey registration. Returns PublicKeyCredentialCreationOptions."""
    if not _HAS_WEBAUTHN:
        raise HTTPException(status_code=503, detail="WebAuthn library not available")
    _require_admin_request(request, manager)
    admin_email = _require_allowed_passkey_email((payload or {}).get("email"))

    challenge = manager.new_challenge_bytes()
    challenge_handle = manager.wrap_challenge(challenge, flow="register", email=admin_email)
    exclude_credentials = _credential_descriptors(store)
    _, rp_id = _webauthn_context(request)

    options = generate_registration_options(
        rp_name=RP_NAME,
        rp_id=rp_id,
        user_id=hashlib.sha256(f"tho-passkey:{admin_email}".encode()).digest(),
        user_name=admin_email,
        user_display_name=admin_email,
        challenge=challenge,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=exclude_credentials,
    )
    # Stash challenge handle in a short-lived cookie so the client
    # sends it back on the /complete call without needing to parse JSON.
    result = JSONResponse(options_to_json_dict(options))
    result.set_cookie(
        key="tho_passkey_register",
        value=challenge_handle,
        max_age=300,
        httponly=True,
        secure=_cookie_secure(request),
        samesite="Strict",
        path="/api/admin/passkey/register/complete",
    )
    return result


@router.post("/register/complete")
def register_complete(
    request: Request,
    credential: dict[str, Any],
    challenge_cookie: str | None = Cookie(default=None, alias="tho_passkey_register"),
    manager: SessionManager = Depends(get_session_manager),
    store: CredentialStore = Depends(get_credential_store),
) -> JSONResponse:
    """Complete passkey registration. Stores the credential."""
    if not _HAS_WEBAUTHN:
        raise HTTPException(status_code=503, detail="WebAuthn library not available")
    _require_admin_request(request, manager)

    challenge_payload = manager.unwrap_challenge_payload(challenge_cookie, flow="register")
    if challenge_payload is None:
        raise HTTPException(status_code=400, detail="Challenge expired or invalid")
    expected_challenge = challenge_payload["challenge"]
    admin_email = _require_allowed_passkey_email(challenge_payload.get("email"))
    expected_origin, expected_rp_id = _webauthn_context(request)

    try:
        reg_cred = parse_registration_credential_json(credential)
        verified = verify_registration_response(
            credential=reg_cred,
            expected_challenge=expected_challenge,
            expected_origin=expected_origin,
            expected_rp_id=expected_rp_id,
        )
    except Exception as exc:
        log.warning("passkey register verify failed: %s", exc)
        raise HTTPException(status_code=400, detail="Registration verification failed")

    from .store import CredentialRecord

    record = CredentialRecord(
        credential_id=verified.credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        user_id=admin_email,
        aaguid=str(verified.aaguid) if verified.aaguid else "",
    )
    try:
        store.add(record)
    except Exception as exc:
        log.warning("passkey credential persist failed: %s", exc)
        raise HTTPException(status_code=503, detail="Passkey credential store unavailable")

    # Issue session immediately after registration
    token = manager.issue_session("admin", email=admin_email, auth_method="passkey")
    result = JSONResponse({"success": True, "registered": True, "email": admin_email})
    result.delete_cookie(key="tho_passkey_register", path="/api/admin/passkey/register/complete")
    result.set_cookie(
        key=PASSKEY_COOKIE_NAME,
        value=token,
        max_age=manager.session_ttl,
        httponly=True,
        secure=_cookie_secure(request),
        samesite="Strict",
        path="/",
    )
    return result


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


@router.post("/login/begin")
def login_begin(
    request: Request,
    manager: SessionManager = Depends(get_session_manager),
    store: CredentialStore = Depends(get_credential_store),
) -> JSONResponse:
    """Begin passkey login. Returns PublicKeyCredentialRequestOptions."""
    if not _HAS_WEBAUTHN:
        raise HTTPException(status_code=503, detail="WebAuthn library not available")

    challenge = manager.new_challenge_bytes()
    challenge_handle = manager.wrap_challenge(challenge, flow="login")

    allow_credentials = _credential_descriptors(store, allowed_only=True)
    _, rp_id = _webauthn_context(request)

    option_kwargs = {
        "rp_id": rp_id,
        "challenge": challenge,
        "user_verification": UserVerificationRequirement.PREFERRED,
    }
    if not _discoverable_login_enabled():
        option_kwargs["allow_credentials"] = allow_credentials

    options = generate_authentication_options(**option_kwargs)
    result = JSONResponse(options_to_json_dict(options))
    result.set_cookie(
        key="tho_passkey_login",
        value=challenge_handle,
        max_age=300,
        httponly=True,
        secure=_cookie_secure(request),
        samesite="Strict",
        path="/api/admin/passkey/login/complete",
    )
    return result


@router.post("/login/complete")
def login_complete(
    request: Request,
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

    # Resolve credential from the id in the payload
    cred_id_b64 = credential.get("id", "")
    raw_id = _decode_credential_id(cred_id_b64)

    try:
        rec = store.get(raw_id)
    except Exception as exc:
        log.warning("passkey credential read failed: %s", exc)
        raise HTTPException(status_code=503, detail="Passkey credential store unavailable")
    if rec is None:
        raise HTTPException(status_code=400, detail="Unknown credential")
    if not _credential_allowed(rec):
        raise HTTPException(
            status_code=403,
            detail="This passkey is not tied to an approved THO admin email.",
        )
    expected_origin, expected_rp_id = _webauthn_context(request)

    try:
        auth_cred = parse_authentication_credential_json(credential)
        verified = verify_authentication_response(
            credential=auth_cred,
            expected_challenge=expected_challenge,
            expected_origin=expected_origin,
            expected_rp_id=expected_rp_id,
            credential_public_key=rec.public_key,
            credential_current_sign_count=rec.sign_count,
        )
    except Exception as exc:
        log.warning("passkey login verify failed: %s", exc)
        raise HTTPException(status_code=400, detail="Authentication verification failed")

    try:
        store.update_usage(verified.credential_id, sign_count=verified.new_sign_count)
    except Exception as exc:
        log.warning("passkey credential usage update failed: %s", exc)
        raise HTTPException(status_code=503, detail="Passkey credential store unavailable")

    token = manager.issue_session("admin", email=rec.user_id, auth_method="passkey")
    result = JSONResponse({"success": True, "authenticated": True, "email": rec.user_id})
    result.delete_cookie(key="tho_passkey_login", path="/api/admin/passkey/login/complete")
    result.set_cookie(
        key=PASSKEY_COOKIE_NAME,
        value=token,
        max_age=manager.session_ttl,
        httponly=True,
        secure=_cookie_secure(request),
        samesite="Strict",
        path="/",
    )
    return result


# ---------------------------------------------------------------------------
# Status / Whoami / Logout
# ---------------------------------------------------------------------------


@router.get("/credentials")
def list_credentials(
    request: Request,
    manager: SessionManager = Depends(get_session_manager),
    store: CredentialStore = Depends(get_credential_store),
) -> JSONResponse:
    """List registered admin passkeys without exposing public key material."""
    _require_admin_request(request, manager)
    try:
        records = store.list_all()
        rows = [_serialize_credential(rec) for rec in records]
    except Exception as exc:
        log.warning("passkey credential list failed: %s", exc)
        raise HTTPException(status_code=503, detail="Passkey credential store unavailable")
    authorized_count = sum(1 for rec in records if _credential_allowed(rec))
    return JSONResponse(
        {
            "success": True,
            "credentials": rows,
            "registered_keys": authorized_count,
            "total_registered_keys": len(rows),
            "unauthorized_keys": max(len(rows) - authorized_count, 0),
            "store_backend": _store_backend(store),
            "persistent": _store_persistent(store),
        }
    )


@router.delete("/credentials/{credential_id_b64}")
def delete_credential(
    credential_id_b64: str,
    request: Request,
    manager: SessionManager = Depends(get_session_manager),
    store: CredentialStore = Depends(get_credential_store),
) -> JSONResponse:
    """Revoke a lost or deprecated admin passkey by credential id."""
    _require_admin_request(request, manager)
    credential_id = _decode_credential_id(credential_id_b64)
    try:
        deleted = store.delete(credential_id)
        remaining_records = store.list_all()
    except Exception as exc:
        log.warning("passkey credential delete failed: %s", exc)
        raise HTTPException(status_code=503, detail="Passkey credential store unavailable")
    if not deleted:
        raise HTTPException(status_code=404, detail="Passkey credential not found")
    authorized_count = sum(1 for rec in remaining_records if _credential_allowed(rec))
    return JSONResponse(
        {
            "success": True,
            "deleted": True,
            "registered_keys": authorized_count,
            "total_registered_keys": len(remaining_records),
            "unauthorized_keys": max(len(remaining_records) - authorized_count, 0),
        }
    )


@router.get("/status")
def passkey_status(
    store: CredentialStore = Depends(get_credential_store),
) -> JSONResponse:
    """Return whether passkeys are configured and how many."""
    store_ready = True
    try:
        all_records = store.list_all()
    except Exception as exc:
        log.warning("passkey credential status failed: %s", exc)
        all_records = []
        store_ready = False
    authorized_count = sum(1 for rec in all_records if _credential_allowed(rec))
    return JSONResponse(
        {
            "enabled": _HAS_WEBAUTHN,
            "registered_keys": authorized_count,
            "total_registered_keys": len(all_records),
            "unauthorized_keys": max(len(all_records) - authorized_count, 0),
            "has_keys": authorized_count > 0,
            "store_backend": _store_backend(store),
            "persistent": _store_persistent(store),
            "store_ready": store_ready,
            "discoverable_login": _discoverable_login_enabled(),
            "allowed_domains": sorted(_passkey_allowed_domains()),
            "rp_id": RP_ID,
            "rp_ids": sorted(set(_origin_rp_ids().values())),
        }
    )


@router.get("/whoami")
def whoami(user: dict = Depends(_require_passkey_user)) -> JSONResponse:
    return JSONResponse(
        {"user_id": user.get("user_id"), "email": user.get("email"), "authed": True}
    )


@router.post("/logout")
def passkey_logout() -> JSONResponse:
    result = JSONResponse({"success": True})
    result.delete_cookie(key=PASSKEY_COOKIE_NAME, path="/")
    result.delete_cookie(key="tho_admin_session", path="/")
    return result
