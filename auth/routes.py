"""FastAPI WebAuthn passkey routes for THO admin authentication.

Mirrors the Sapphire analytics_dashboard auth blueprint, adapted for
FastAPI and THO's endpoint conventions.

Endpoints (mounted under /api/admin/passkey):
    POST /register/begin
    POST /register/complete
    POST /login/begin
    POST /login/complete
    GET  /credentials                 owner recovery list
    DELETE /credentials/{credential_id_b64}
    GET  /credentials/mine            signed-in user's own keys
    PATCH /credentials/mine/{credential_id_b64}
    DELETE /credentials/mine/{credential_id_b64}
    GET  /status
    GET  /whoami
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
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, Cookie, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .authentication_challenges import AuthenticationChallengeStoreUnavailable
from .csrf import create_csrf_token, require_cookie_csrf, require_request_csrf, set_csrf_cookie
from .session import (
    PASSKEY_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    SessionManager,
)
from .staff_directory import override_status
from .store import (
    CredentialAlreadyExists,
    CredentialRecord,
    CredentialStore,
    CredentialStoreUnavailable,
    MinimumCredentialError,
    default_store,
)

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


# Canonical storefront. A passkey is bound to this RP ID, so the default must
# be texashomeoutlet.com. The legacy sapphire hosts stay in the cutover map
# below and keep their own RP IDs; they are not the default.
THO_ORIGIN = os.environ.get("WEBAUTHN_ORIGIN") or os.environ.get(
    "THO_ORIGIN", "https://www.texashomeoutlet.com"
)
RP_NAME = "THO Admin"
RP_ID = os.environ.get("WEBAUTHN_RP_ID") or os.environ.get("THO_RP_ID", "texashomeoutlet.com")
DEFAULT_PASSKEY_STAFF_DOMAIN = "texashomeoutlet.com"
DEFAULT_OWNER_EMAILS = {
    "aribspector@gmail.com",
    "aristotlespec@gmail.com",
}
_CUTOVER_ORIGIN_RP_IDS = {
    "https://tho.sapphirealpha.xyz": "sapphirealpha.xyz",
    "https://tho.sapphire.xyz": "sapphire.xyz",
    "https://texashomeoutlet.com": "texashomeoutlet.com",
    "https://www.texashomeoutlet.com": "texashomeoutlet.com",
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


def _protected_owner_emails() -> set[str]:
    """Owner identities that shared PIN/staff sessions may never enroll for."""
    return _passkey_allowed_owner_emails() | _split_env_values("THO_GOOGLE_ADS_OWNER_EMAILS")


def google_ads_owner_emails() -> set[str]:
    """Return the exact Ads-owner set only when login authorization matches it."""
    ads_owners = _split_env_values("THO_GOOGLE_ADS_OWNER_EMAILS")
    if (
        not ads_owners
        or any("@" not in email or len(email.encode("utf-8")) > 200 for email in ads_owners)
        or ads_owners != _passkey_allowed_owner_emails()
    ):
        raise ValueError("Google Ads owner allowlist is not consistently configured")
    return ads_owners


def _passkey_allowed_domains() -> set[str]:
    configured = _split_env_values("THO_PASSKEY_ALLOWED_DOMAINS")
    return configured or {DEFAULT_PASSKEY_STAFF_DOMAIN}


def _passkey_email_allowed(email: str | None) -> bool:
    normalized = _normalize_email(email)
    if not normalized or "@" not in normalized:
        return False
    # Owners stay allowed even if a directory row says blocked, so the team
    # cannot lock out the last person who can manage access.
    if normalized in _passkey_allowed_owner_emails():
        return True
    status = override_status(normalized)
    if status == "blocked":
        return False
    if status == "allowed":
        return True
    _, _, domain = normalized.rpartition("@")
    return domain in _passkey_allowed_domains()


# Public alias so the email one-time-code login shares ONE allowlist with
# passkeys (owner emails + @texashomeoutlet.com staff, env-overridable via
# THO_PASSKEY_ALLOWED_DOMAINS / THO_PASSKEY_OWNER_EMAILS). Importers should use
# this name; the underscore-prefixed original stays for internal callers.
is_allowed_admin_email = _passkey_email_allowed


def allowed_owner_emails() -> set[str]:
    """Owner addresses that always remain allowed to sign in."""
    return set(_passkey_allowed_owner_emails())


def allowed_staff_domains() -> set[str]:
    """Email domains whose addresses can sign in unless an admin blocks them."""
    return set(_passkey_allowed_domains())


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
    # TODO(owner): fingerprint/face user verification stays preferred.
    # Requiring it is an owner decision; do not change this here.
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


def _pin_token_key(admin_pin_hash: str) -> bytes | None:
    """Return the PIN-session signing key, matching ``main._JWT_SECRET`` exactly.

    The key comes from the server-only ``ADMIN_SESSION_SECRET`` bound to the full
    PIN verifier, so this router and ``main.py`` accept exactly the same PIN
    sessions and rotating either value revokes them.
    """
    session_secret = os.environ.get("ADMIN_SESSION_SECRET", "")
    if not session_secret or not admin_pin_hash:
        return None
    return hmac.new(
        session_secret.encode("utf-8"),
        f"tho-pin-session-v2:{admin_pin_hash}".encode(),
        hashlib.sha256,
    ).digest()


_EMAIL_TOKEN_VERSION = 2


def _decode_staff_admin_token(token: str) -> tuple[int, str | None] | None:
    """Return ``(expires, email)`` for a staff cookie, matching ``main.py``.

    PIN sessions are the original 8-byte payload and carry no email. Email
    sign-in uses version 2 and embeds the address so a passkey can be saved
    for that person without asking them to type it again.
    """
    admin_pin_hash = os.environ.get("ADMIN_PIN_HASH", "")
    if not token or not admin_pin_hash:
        return None
    secret = _pin_token_key(admin_pin_hash)
    if secret is None:
        return None
    try:
        padding = "=" * (-len(token) % 4)
        raw = urlsafe_b64decode(token + padding)
        if len(raw) < 24:
            return None
        payload, sig = raw[:-16], raw[-16:]
        expected_sig = hmac.new(secret, payload, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(sig, expected_sig):
            return None
        if len(payload) == 8:
            return struct.unpack(">Q", payload)[0], None
        if len(payload) < 11 or payload[0] != _EMAIL_TOKEN_VERSION:
            return None
        expires = struct.unpack(">Q", payload[1:9])[0]
        email_len = struct.unpack(">H", payload[9:11])[0]
        if email_len <= 0 or email_len > 200 or len(payload) != 11 + email_len:
            return None
        email = payload[11:].decode("utf-8")
        if email != email.strip().lower() or "@" not in email:
            return None
        return expires, email
    except Exception:
        return None


def _verify_admin_pin_token(token: str) -> bool:
    decoded = _decode_staff_admin_token(token)
    if decoded is None:
        return False
    expires, email = decoded
    if time.time() >= expires:
        return False
    if email is not None and not _passkey_email_allowed(email):
        return False
    return True


def _request_is_admin(request: Request, manager: SessionManager) -> bool:
    passkey_payload = manager.verify_session(request.cookies.get(PASSKEY_COOKIE_NAME))
    if passkey_payload and passkey_payload.get("user_id") == "admin":
        return True
    return _verify_admin_pin_token(_admin_token_from_request(request))


def _require_admin_request(request: Request, manager: SessionManager) -> None:
    if not _request_is_admin(request, manager):
        raise HTTPException(status_code=401, detail="Admin authentication required")
    require_request_csrf(request)


def _require_same_owner_passkey_for_registration(
    request: Request,
    manager: SessionManager,
    email: str,
) -> None:
    if email not in _protected_owner_emails():
        return
    require_cookie_csrf(request)
    payload = manager.verify_session(request.cookies.get(PASSKEY_COOKIE_NAME))
    session_email = _normalize_email((payload or {}).get("email"))
    if (
        not payload
        or payload.get("user_id") != "admin"
        or payload.get("auth_method") != "passkey"
        or session_email != email
    ):
        raise HTTPException(
            status_code=403,
            detail="Owner passkeys can only be added from the same owner's passkey session.",
        )


def _session_profile(request: Request, manager: SessionManager) -> dict[str, Any] | None:
    """Identity of the signed-in staff member, when the session names one.

    A passkey cookie wins over a PIN or email cookie so owner rules stay tied
    to the passkey that proved the address. A shared PIN has no email.
    """
    payload = manager.verify_session(request.cookies.get(PASSKEY_COOKIE_NAME))
    if payload and payload.get("user_id") == "admin":
        email = _normalize_email(payload.get("email"))
        if email and not _passkey_email_allowed(email):
            email = ""
        return {
            "email": email,
            "auth_method": str(payload.get("auth_method") or "passkey"),
        }
    decoded = _decode_staff_admin_token(_admin_token_from_request(request))
    if decoded is None or time.time() >= decoded[0]:
        return None
    email = _normalize_email(decoded[1])
    if email and not _passkey_email_allowed(email):
        return None
    return {
        "email": email,
        "auth_method": "email" if email else "pin",
    }


def _session_email(request: Request, manager: SessionManager) -> str:
    profile = _session_profile(request, manager) or {}
    return _normalize_email(profile.get("email"))


def _registration_email(request: Request, manager: SessionManager, payload: dict | None) -> str:
    """Bind a new passkey to the signed-in email.

    Staff cannot type a different address from a shared PIN session. A body
    email that does not match the session is rejected. Owner addresses still
    go through the same-owner passkey rule after this returns.
    """
    requested = _normalize_email((payload or {}).get("email"))
    session_email = _session_email(request, manager)
    if session_email:
        if requested and requested != session_email:
            raise HTTPException(
                status_code=403,
                detail="This passkey is saved for the signed-in email.",
            )
        email = session_email
    elif requested:
        email = requested
    else:
        raise HTTPException(
            status_code=400,
            detail="Sign in with the email link before adding a passkey.",
        )
    email = _require_allowed_passkey_email(email)
    if not session_email and email not in _protected_owner_emails():
        raise HTTPException(
            status_code=400,
            detail="Sign in with the email link before adding a passkey.",
        )
    return email


def _require_staff_identity(
    request: Request,
    manager: SessionManager = Depends(get_session_manager),
) -> None:
    """A signed-in staff session: passkey cookie, email cookie, or shared PIN."""
    if not _request_is_admin(request, manager):
        raise HTTPException(status_code=401, detail="Admin authentication required")


def _require_named_staff(request: Request, manager: SessionManager) -> str:
    """Staff session that names an allowlisted email."""
    _require_admin_request(request, manager)
    email = _session_email(request, manager)
    if not email:
        raise HTTPException(
            status_code=400,
            detail="Sign in with the email link before managing passkeys.",
        )
    return _require_allowed_passkey_email(email)


def _require_recovery_owner(request: Request, manager: SessionManager) -> str:
    """Owner recovery list. A shared PIN is not an owner identity."""
    _require_admin_request(request, manager)
    email = _session_email(request, manager)
    if email not in _passkey_allowed_owner_emails():
        raise HTTPException(status_code=403, detail="Passkey recovery is limited to the owner.")
    return email


# Provider names for well-known authenticator AAGUIDs. Unknown ids stay
# "Passkey" so a staff member can rename the key on Your passkeys.
_AAGUID_PROVIDERS = {
    "ea9b8d66-4d01-1d21-3ce4-b6b48cb575d4": "Google Password Manager",
    "fbfc3007-154e-4ecc-8c0a-6ef5e7a3e3c4": "iCloud Keychain",
    "dd4ec289-e01d-41c9-bb89-70fa845d8bf2": "iCloud Keychain",
    "08987058-cadc-4b81-b6e1-30de50dcbe96": "Windows Hello",
    "9ddd1817-af5a-4672-a2b9-3e3dd95000a9": "Windows Hello",
    "6028b017-b1d4-4c02-b4b3-afcdafc96bb2": "Windows Hello",
    "bada5566-a7aa-401f-bd96-45619a55120d": "1Password",
    "d548826e-79b4-db40-a3d8-11116f7e8349": "Bitwarden",
    "531126d6-e717-415c-9320-3d9aa6981239": "Dashlane",
}


def _provider_name(aaguid: str) -> str:
    key = str(aaguid or "").strip().lower()
    if not key or key == "00000000-0000-0000-0000-000000000000":
        return "Passkey"
    return _AAGUID_PROVIDERS.get(key, "Passkey")


def _clean_label(value: Any) -> str:
    label = " ".join(str(value or "").split())
    if not label or len(label) > 60 or any(ord(char) < 32 for char in label):
        raise HTTPException(
            status_code=400,
            detail="Name the passkey in 60 characters or fewer.",
        )
    return label


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
        "label": getattr(rec, "label", "") or "",
        "provider": _provider_name(getattr(rec, "aaguid", "")),
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
    admin_email = _registration_email(request, manager, payload)
    _require_same_owner_passkey_for_registration(request, manager, admin_email)

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
            # Required resident key so the passkey can sync and be offered
            # by conditional UI. Attachment is left unset so a phone or
            # security key can still be used.
            resident_key=ResidentKeyRequirement.REQUIRED,
            # TODO(owner): fingerprint/face user verification stays preferred.
            # Requiring it is an owner decision; do not change this here.
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
    _require_same_owner_passkey_for_registration(request, manager, admin_email)
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

    aaguid = str(verified.aaguid) if verified.aaguid else ""
    record = CredentialRecord(
        credential_id=verified.credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        user_id=admin_email,
        aaguid=aaguid,
        label=_provider_name(aaguid),
    )
    try:
        store.add(record)
    except CredentialAlreadyExists:
        log.warning("passkey credential registration rejected an existing id")
        raise HTTPException(
            status_code=409,
            detail="Passkey credential is already registered.",
        ) from None
    except Exception as exc:
        log.warning("passkey credential persist failed: %s", exc)
        raise HTTPException(status_code=503, detail="Passkey credential store unavailable")

    # Issue session immediately after registration
    token = manager.issue_session("admin", email=admin_email, auth_method="passkey")
    csrf_token = create_csrf_token()
    result = JSONResponse(
        {
            "success": True,
            "registered": True,
            "email": admin_email,
            "csrf_token": csrf_token,
        }
    )
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
    set_csrf_cookie(
        result,
        csrf_token,
        secure=_cookie_secure(request),
        max_age=manager.session_ttl,
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
    try:
        challenge_handle = manager.wrap_challenge(challenge, flow="login")
    except AuthenticationChallengeStoreUnavailable:
        log.warning("passkey authentication challenge store unavailable")
        raise HTTPException(status_code=503, detail="Passkey challenge store unavailable") from None

    allow_credentials = _credential_descriptors(store, allowed_only=True)
    _, rp_id = _webauthn_context(request)

    option_kwargs = {
        "rp_id": rp_id,
        "challenge": challenge,
        # TODO(owner): fingerprint/face user verification stays preferred.
        # Requiring it is an owner decision; do not change this here.
        "user_verification": UserVerificationRequirement.PREFERRED,
    }
    if not _discoverable_login_enabled():
        option_kwargs["allow_credentials"] = allow_credentials

    options = generate_authentication_options(**option_kwargs)
    result = JSONResponse(options_to_json_dict(options))
    result.set_cookie(
        key="tho_passkey_login",
        value=challenge_handle,
        max_age=manager.challenge_ttl,
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

    # Burn the challenge before credential lookup or assertion checks so a
    # failed attempt cannot be replayed. Expiry deletes the server record too.
    try:
        expected_challenge = manager.consume_authentication_challenge(
            challenge_cookie, flow="login"
        )
    except AuthenticationChallengeStoreUnavailable:
        log.warning("passkey authentication challenge store unavailable")
        raise HTTPException(status_code=503, detail="Passkey challenge store unavailable") from None
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
        if verified.credential_id != raw_id:
            raise ValueError("credential identity mismatch")
    except Exception as exc:
        log.warning("passkey login verify failed: %s", exc)
        raise HTTPException(status_code=400, detail="Authentication verification failed")

    try:
        usage = store.prepare_usage_cas(rec, new_sign_count=verified.new_sign_count)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=409,
            detail="Passkey counter changed. Refresh and retry.",
        ) from None
    try:
        updated = store.compare_and_set_usage(usage, used_at=datetime.now(UTC))
    except Exception as exc:
        log.warning("passkey credential usage update failed: %s", exc)
        raise HTTPException(status_code=503, detail="Passkey credential store unavailable")
    if not updated:
        raise HTTPException(
            status_code=409,
            detail="Passkey counter changed. Refresh and retry.",
        )

    token = manager.issue_session("admin", email=rec.user_id, auth_method="passkey")
    csrf_token = create_csrf_token()
    result = JSONResponse(
        {
            "success": True,
            "authenticated": True,
            "email": rec.user_id,
            "csrf_token": csrf_token,
        }
    )
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
    set_csrf_cookie(
        result,
        csrf_token,
        secure=_cookie_secure(request),
        max_age=manager.session_ttl,
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
    _require_recovery_owner(request, manager)
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
    _require_recovery_owner(request, manager)
    credential_id = _decode_credential_id(credential_id_b64)
    try:
        target = store.get(credential_id)
        if target is None:
            raise HTTPException(status_code=404, detail="Passkey credential not found")
        target_email = _normalize_email(target.user_id)
        if target_email in _protected_owner_emails():
            _require_same_owner_passkey_for_registration(request, manager, target_email)
            deleted = store.delete_preserving_user_minimum(
                credential_id,
                user_id=target_email,
                minimum_remaining=1,
            )
        else:
            deleted = store.delete(credential_id)
        remaining_records = store.list_all()
    except HTTPException:
        raise
    except MinimumCredentialError:
        raise HTTPException(
            status_code=409,
            detail="Add a replacement owner passkey before revoking this recovery key.",
        ) from None
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


def _owned_credential(store: CredentialStore, credential_id: bytes, email: str) -> Any:
    try:
        record = store.get(credential_id)
    except Exception as exc:
        log.warning("passkey credential read failed: %s", exc)
        raise HTTPException(status_code=503, detail="Passkey credential store unavailable")
    if record is None or _normalize_email(record.user_id) != email:
        raise HTTPException(status_code=404, detail="Passkey credential not found")
    return record


@router.get("/credentials/mine", dependencies=[Depends(_require_staff_identity)])
def list_my_credentials(
    request: Request,
    manager: SessionManager = Depends(get_session_manager),
    store: CredentialStore = Depends(get_credential_store),
) -> JSONResponse:
    """List the signed-in user's passkeys. Does not include anyone else's."""
    email = _require_named_staff(request, manager)
    try:
        records = [
            rec for rec in store.list_for_user(email) if _normalize_email(rec.user_id) == email
        ]
    except Exception as exc:
        log.warning("passkey credential list failed: %s", exc)
        raise HTTPException(status_code=503, detail="Passkey credential store unavailable")
    return JSONResponse(
        {
            "success": True,
            "email": email,
            "credentials": [_serialize_credential(rec) for rec in records],
        }
    )


@router.patch(
    "/credentials/mine/{credential_id_b64}",
    dependencies=[Depends(_require_staff_identity)],
)
def rename_my_credential(
    credential_id_b64: str,
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    manager: SessionManager = Depends(get_session_manager),
    store: CredentialStore = Depends(get_credential_store),
) -> JSONResponse:
    """Rename one of the signed-in user's passkeys."""
    email = _require_named_staff(request, manager)
    label = _clean_label((payload or {}).get("label"))
    credential_id = _decode_credential_id(credential_id_b64)
    _owned_credential(store, credential_id, email)
    try:
        updated = store.update_label(credential_id, user_id=email, label=label)
        record = store.get(credential_id) if updated else None
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("passkey rename failed: %s", exc)
        raise HTTPException(status_code=503, detail="Passkey credential store unavailable")
    if not updated or record is None:
        raise HTTPException(status_code=404, detail="Passkey credential not found")
    return JSONResponse({"success": True, "credential": _serialize_credential(record)})


@router.delete(
    "/credentials/mine/{credential_id_b64}",
    dependencies=[Depends(_require_staff_identity)],
)
def delete_my_credential(
    credential_id_b64: str,
    request: Request,
    manager: SessionManager = Depends(get_session_manager),
    store: CredentialStore = Depends(get_credential_store),
) -> JSONResponse:
    """Remove one of the signed-in user's passkeys.

    An owner's last recovery key stays, and removing an owner key still
    requires that owner's passkey session.
    """
    email = _require_named_staff(request, manager)
    credential_id = _decode_credential_id(credential_id_b64)
    _owned_credential(store, credential_id, email)
    try:
        if email in _protected_owner_emails():
            _require_same_owner_passkey_for_registration(request, manager, email)
            deleted = store.delete_preserving_user_minimum(
                credential_id,
                user_id=email,
                minimum_remaining=1,
            )
        else:
            deleted = store.delete(credential_id)
    except HTTPException:
        raise
    except MinimumCredentialError:
        raise HTTPException(
            status_code=409,
            detail="Add a replacement owner passkey before revoking this recovery key.",
        ) from None
    except Exception as exc:
        log.warning("passkey credential delete failed: %s", exc)
        raise HTTPException(status_code=503, detail="Passkey credential store unavailable")
    if not deleted:
        raise HTTPException(status_code=404, detail="Passkey credential not found")
    return JSONResponse({"success": True, "deleted": True})


@router.get("/whoami", dependencies=[Depends(_require_staff_identity)])
def whoami(
    request: Request,
    manager: SessionManager = Depends(get_session_manager),
) -> JSONResponse:
    if not _request_is_admin(request, manager):
        raise HTTPException(status_code=401, detail="Unauthorized")
    profile = _session_profile(request, manager) or {}
    email = _normalize_email(profile.get("email"))
    owners = _passkey_allowed_owner_emails()
    is_owner = bool(email) and email in owners
    body: dict[str, Any] = {
        "user_id": "admin",
        "email": email or None,
        "authed": True,
        "owner": is_owner,
        "auth_method": profile.get("auth_method") or "",
    }
    if is_owner:
        body["owner_emails"] = sorted(owners)
    return JSONResponse(body)


@router.post("/logout")
def passkey_logout() -> JSONResponse:
    result = JSONResponse({"success": True})
    result.delete_cookie(key=PASSKEY_COOKIE_NAME, path="/")
    result.delete_cookie(key="tho_admin_session", path="/")
    return result
