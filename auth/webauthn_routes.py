"""
WebAuthn / Passkey authentication routes for THO admin.

Adds passkey login as a PARALLEL path alongside the existing PIN flow.
The PIN remains the fallback; /api/admin/verify is untouched.

Routes:
  POST /api/auth/webauthn/register/begin    — start passkey registration (admin-authed)
  POST /api/auth/webauthn/register/complete — finish passkey registration
  POST /api/auth/webauthn/login/begin       — start passkey login (public)
  POST /api/auth/webauthn/login/complete    — finish passkey login → same JWT as PIN flow
  GET  /api/auth/webauthn/credentials       — list registered passkeys (admin-authed)
  DELETE /api/auth/webauthn/credentials/{id} — revoke a passkey (admin-authed)

Firestore:
  webauthn_challenges   — ephemeral (5 min) challenge docs
  webauthn_credentials  — registered credential docs (keyed by credential_id base64url)
"""

import os
import json
import base64
import hashlib
import hmac
import struct
import time
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Request, Depends, HTTPException
from pydantic import BaseModel

import webauthn
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    ResidentKeyRequirement,
    UserVerificationRequirement,
    PublicKeyCredentialDescriptor,
    AttestationConveyancePreference,
    RegistrationCredential,
    AuthenticationCredential,
)
from webauthn.helpers.cose import COSEAlgorithmIdentifier

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth/webauthn", tags=["webauthn"])

# ─── Configuration ────────────────────────────────────────────────────────────

RP_ID: str = os.environ.get("WEBAUTHN_RP_ID", "sapphirealpha.xyz")
RP_NAME: str = os.environ.get("WEBAUTHN_RP_NAME", "Texas Home Outlet Admin")
CHALLENGE_TTL_SECONDS = 300  # 5 minutes


def _allowed_origins() -> list[str]:
    env = os.environ.get("WEBAUTHN_ORIGINS", "")
    if env:
        return [o.strip() for o in env.split(",") if o.strip()]
    if RP_ID == "localhost":
        return ["http://localhost:5173", "http://localhost:8080"]
    return [f"https://{RP_ID}"]


# ─── JWT token (same shape as main.py) ───────────────────────────────────────

ADMIN_TOKEN_TTL = int(os.environ.get("ADMIN_TOKEN_TTL", str(2 * 60 * 60)))


def _jwt_secret() -> bytes:
    """Derive JWT secret from ADMIN_PIN_HASH. Must stay in sync with main.py."""
    pin_hash = os.environ.get("ADMIN_PIN_HASH", "")
    if not pin_hash:
        pin_hash = hashlib.sha256(b"000000").hexdigest()
    return hashlib.sha256(f"sapphire-jwt-{pin_hash[:16]}".encode()).digest()


def _create_admin_token() -> str:
    expires = int(time.time()) + ADMIN_TOKEN_TTL
    payload = struct.pack(">Q", expires)
    sig = hmac.new(_jwt_secret(), payload, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(payload + sig).decode().rstrip("=")


def _verify_token(token: str) -> bool:
    try:
        padding = 4 - len(token) % 4
        if padding != 4:
            token += "=" * padding
        raw = base64.urlsafe_b64decode(token)
        if len(raw) != 24:
            return False
        payload, sig = raw[:8], raw[8:]
        expected = hmac.new(_jwt_secret(), payload, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(sig, expected):
            return False
        expires = struct.unpack(">Q", payload)[0]
        return time.time() < expires
    except Exception:
        return False


def _require_admin(request: Request):
    token = request.headers.get("X-Admin-Token", "").strip()
    if not token:
        auth = request.headers.get("Authorization", "").strip()
        scheme, _, value = auth.partition(" ")
        if scheme.lower() == "bearer" and value:
            token = value.strip()
    if not token or not _verify_token(token):
        raise HTTPException(status_code=401, detail="Admin authentication required")


# ─── Firestore helpers ────────────────────────────────────────────────────────

def _db():
    from database.firestore_client import get_database
    return get_database()


def _b64url_decode(s: str) -> bytes:
    s = s.rstrip("=")
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _store_challenge(challenge_bytes: bytes, challenge_type: str,
                     user_id: str = "admin", label: str = "") -> str:
    challenge_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    _db().db.collection("webauthn_challenges").document(challenge_id).set({
        "challenge_b64": _b64url_encode(challenge_bytes),
        "user_id": user_id,
        "type": challenge_type,
        "label": label,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=CHALLENGE_TTL_SECONDS)).isoformat(),
        "used": False,
    })
    return challenge_id


def _consume_challenge(challenge_id: str, expected_type: str) -> Optional[bytes]:
    """Retrieve and single-use consume a challenge. Returns bytes or None."""
    db = _db().db
    doc_ref = db.collection("webauthn_challenges").document(challenge_id)
    doc = doc_ref.get()
    if not doc.exists:
        return None
    data = doc.to_dict()
    if data.get("used") or data.get("type") != expected_type:
        return None
    expires_at = datetime.fromisoformat(data["expires_at"])
    if datetime.now(timezone.utc) > expires_at:
        doc_ref.delete()
        return None
    # Mark used before verification to prevent concurrent replay
    doc_ref.update({"used": True})
    return _b64url_decode(data["challenge_b64"])


def _list_credentials(user_id: str = "admin") -> list[dict]:
    docs = _db().db.collection("webauthn_credentials").where(
        "user_id", "==", user_id
    ).stream()
    result = []
    for doc in docs:
        d = doc.to_dict()
        d["credential_id"] = doc.id
        d.pop("public_key_bytes", None)  # never expose to UI
        result.append(d)
    return result


def _get_credential(credential_id: str) -> Optional[dict]:
    doc = _db().db.collection("webauthn_credentials").document(credential_id).get()
    return doc.to_dict() if doc.exists else None


def _save_credential(credential_id: str, data: dict):
    _db().db.collection("webauthn_credentials").document(credential_id).set(data)


def _update_sign_count(credential_id: str, new_count: int):
    _db().db.collection("webauthn_credentials").document(credential_id).update({
        "sign_count": new_count,
        "last_used_at": datetime.now(timezone.utc).isoformat(),
    })


def _delete_credential(credential_id: str):
    _db().db.collection("webauthn_credentials").document(credential_id).delete()


# ─── Parse helpers (isolated for testability) ────────────────────────────────

def _parse_registration_credential(body: dict) -> RegistrationCredential:
    return RegistrationCredential.parse_raw(json.dumps(body))


def _parse_authentication_credential(body: dict) -> AuthenticationCredential:
    return AuthenticationCredential.parse_raw(json.dumps(body))


# ─── Pydantic models ──────────────────────────────────────────────────────────

class RegisterBeginRequest(BaseModel):
    user_id: str = "admin"
    user_name: str = "admin"
    display_name: str = "THO Admin"
    label: str = "My passkey"


class LoginBeginRequest(BaseModel):
    user_id: str = "admin"


class CompleteRequest(BaseModel):
    challenge_id: str
    credential: dict
    label: str = "My passkey"


# ─── Routes ───────────────────────────────────────────────────────────────────

@router.post("/register/begin", dependencies=[Depends(_require_admin)])
async def register_begin(body: RegisterBeginRequest):
    """Return PublicKeyCredentialCreationOptions. Requires existing admin JWT."""
    # Exclude already-registered credentials so the device isn't double-enrolled
    existing = _list_credentials(body.user_id)
    exclude = []
    for cred in existing:
        try:
            exclude.append(
                PublicKeyCredentialDescriptor(id=_b64url_decode(cred["credential_id"]))
            )
        except Exception:
            pass

    options = webauthn.generate_registration_options(
        rp_id=RP_ID,
        rp_name=RP_NAME,
        user_id=body.user_id.encode(),
        user_name=body.user_name,
        user_display_name=body.display_name,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        supported_pub_key_algs=[
            COSEAlgorithmIdentifier.ECDSA_SHA_256,
            COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,
        ],
        timeout=60000,
        exclude_credentials=exclude,
    )

    challenge_id = _store_challenge(
        options.challenge, "registration", body.user_id, body.label
    )
    options_json = json.loads(webauthn.options_to_json(options))
    return {"challenge_id": challenge_id, "options": options_json}


@router.post("/register/complete", dependencies=[Depends(_require_admin)])
async def register_complete(body: CompleteRequest):
    """Verify attestation response and persist credential."""
    challenge_bytes = _consume_challenge(body.challenge_id, "registration")
    if challenge_bytes is None:
        raise HTTPException(status_code=400, detail="Challenge expired or invalid")

    verification = None
    last_err = None
    for origin in _allowed_origins():
        try:
            cred = _parse_registration_credential(body.credential)
            verification = webauthn.verify_registration_response(
                credential=cred,
                expected_challenge=challenge_bytes,
                expected_rp_id=RP_ID,
                expected_origin=origin,
            )
            break
        except Exception as exc:
            last_err = exc

    if verification is None:
        logger.warning("Passkey register/complete failed: %s", last_err)
        raise HTTPException(status_code=400, detail="Passkey verification failed")

    cred_id = _b64url_encode(verification.credential_id)
    _save_credential(cred_id, {
        "user_id": "admin",
        "credential_id": cred_id,
        "public_key_bytes": base64.b64encode(verification.credential_public_key).decode(),
        "sign_count": verification.sign_count,
        "transports": body.credential.get("response", {}).get("transports", []),
        "attestation_format": getattr(verification, "fmt", "none"),
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "last_used_at": None,
        "label": body.label,
    })

    logger.info("Passkey registered user=admin cred_id=%.12s", cred_id)
    return {"success": True, "credential_id": cred_id, "message": "Passkey registered"}


@router.post("/login/begin")
async def login_begin(body: LoginBeginRequest):
    """Return PublicKeyCredentialRequestOptions. Public — no auth required."""
    credentials = _list_credentials(body.user_id)
    allow = []
    for cred in credentials:
        try:
            allow.append(
                PublicKeyCredentialDescriptor(id=_b64url_decode(cred["credential_id"]))
            )
        except Exception:
            pass

    options = webauthn.generate_authentication_options(
        rp_id=RP_ID,
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.PREFERRED,
        timeout=60000,
    )

    challenge_id = _store_challenge(options.challenge, "authentication", body.user_id)
    options_json = json.loads(webauthn.options_to_json(options))
    return {"challenge_id": challenge_id, "options": options_json}


@router.post("/login/complete")
async def login_complete(body: CompleteRequest):
    """Verify assertion response. Returns same JWT shape as /api/admin/verify."""
    challenge_bytes = _consume_challenge(body.challenge_id, "authentication")
    if challenge_bytes is None:
        raise HTTPException(status_code=400, detail="Challenge expired or invalid")

    cred_id_raw = body.credential.get("id", "")
    if not cred_id_raw:
        raise HTTPException(status_code=400, detail="Missing credential id")

    # Credential ID may arrive with or without base64url padding
    cred_id = cred_id_raw.rstrip("=")
    stored = _get_credential(cred_id)
    if not stored:
        raise HTTPException(status_code=401, detail="Credential not registered")

    public_key = base64.b64decode(stored["public_key_bytes"])
    sign_count = stored.get("sign_count", 0)

    verification = None
    last_err = None
    for origin in _allowed_origins():
        try:
            cred = _parse_authentication_credential(body.credential)
            verification = webauthn.verify_authentication_response(
                credential=cred,
                expected_challenge=challenge_bytes,
                expected_rp_id=RP_ID,
                expected_origin=origin,
                credential_public_key=public_key,
                credential_current_sign_count=sign_count,
            )
            break
        except Exception as exc:
            last_err = exc

    if verification is None:
        logger.warning("Passkey login/complete failed: %s", last_err)
        raise HTTPException(status_code=401, detail="Passkey verification failed")

    _update_sign_count(cred_id, verification.new_sign_count)
    token = _create_admin_token()
    logger.info("Passkey login succeeded cred_id=%.12s", cred_id)
    return {"success": True, "token": token}


@router.get("/credentials", dependencies=[Depends(_require_admin)])
async def list_credentials_route():
    """List all passkeys registered for admin. Safe — public key bytes excluded."""
    creds = _list_credentials("admin")
    return {
        "success": True,
        "credentials": [
            {
                "credential_id": c["credential_id"],
                "label": c.get("label", "Unnamed passkey"),
                "registered_at": c.get("registered_at", ""),
                "last_used_at": c.get("last_used_at"),
                "transports": c.get("transports", []),
            }
            for c in creds
        ],
    }


@router.delete("/credentials/{credential_id}", dependencies=[Depends(_require_admin)])
async def revoke_credential(credential_id: str):
    """Revoke (delete) a registered passkey by its credential_id."""
    if not _get_credential(credential_id):
        raise HTTPException(status_code=404, detail="Credential not found")
    _delete_credential(credential_id)
    logger.info("Passkey revoked cred_id=%.12s", credential_id)
    return {"success": True, "message": "Passkey revoked"}
