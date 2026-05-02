# WebAuthn Passkey Authentication — Design Document

**Status**: Scaffold (PIN remains primary; passkeys are additional path)  
**Author**: AI scaffold (review required by Ari)  
**Date**: 2026-05-02  
**PR**: `feat/passkey-auth-scaffold`

---

## 1. Overview

This document describes adding WebAuthn passkey authentication as a **parallel** path to the existing shared-PIN admin flow. The PIN is never removed — it becomes the recovery fallback when all passkeys are lost.

### Goals

| Goal | Notes |
|------|-------|
| Phishing-resistant login | Passkeys are domain-scoped; the credential only works on `sapphirealpha.xyz` |
| 1-2 tap daily login | TouchID / FaceID / Windows Hello / Proton Pass prompts automatically |
| Boomer-proof setup | 3 clicks: enter a label → click "Add" → approve biometric prompt |
| Multi-device | Each device registers its own passkey; all map to `user_id = "admin"` |
| Stateless Cloud Run | Tokens are the same HMAC-SHA256 JWT already in use; no session store |
| No lockout | PIN + Secret Manager rotation always available as fallback |

### Non-goals (this PR)

- Per-user identities (tracked as a future upgrade)
- Passkey sync via iCloud Keychain / Google Password Manager enforcement
- Revoking the PIN

---

## 2. Architecture

### 2.1 Backend (`auth/webauthn_routes.py`)

Four FastAPI routes mounted under `/api/auth/webauthn/`:

| Route | Auth required | Purpose |
|-------|--------------|---------|
| `POST /register/begin` | ✅ admin JWT | Return `PublicKeyCredentialCreationOptions` |
| `POST /register/complete` | ✅ admin JWT | Verify attestation, persist credential |
| `POST /login/begin` | ❌ public | Return `PublicKeyCredentialRequestOptions` |
| `POST /login/complete` | ❌ public | Verify assertion, return JWT token |
| `GET /credentials` | ✅ admin JWT | List registered passkeys |
| `DELETE /credentials/{id}` | ✅ admin JWT | Revoke a passkey |

**Library**: `webauthn>=2.1.0` (`py_webauthn`)  
**RP ID**: `WEBAUTHN_RP_ID` env var, defaults to `sapphirealpha.xyz`  
**RP Name**: `WEBAUTHN_RP_NAME` env var, defaults to `"Texas Home Outlet Admin"`  
**Allowed origins**: `WEBAUTHN_ORIGINS` env var (comma-separated); defaults to `https://{RP_ID}` or `http://localhost:5173,http://localhost:8080` when `RP_ID == "localhost"`

### 2.2 Firestore Collections

#### `webauthn_challenges` (ephemeral, 5-minute TTL)

```json
{
  "challenge_b64": "<base64url-encoded random bytes>",
  "user_id": "admin",
  "type": "registration" | "authentication",
  "label": "Mark's iPhone",
  "created_at": "2026-05-02T10:00:00Z",
  "expires_at": "2026-05-02T10:05:00Z",
  "used": false
}
```

Challenges are single-use (marked `used: true` immediately on consume) to prevent replay attacks.

#### `webauthn_credentials` (permanent)

```json
{
  "user_id": "admin",
  "credential_id": "<base64url-without-padding>",
  "public_key_bytes": "<base64-encoded COSE public key>",
  "sign_count": 42,
  "transports": ["internal", "hybrid"],
  "attestation_format": "none",
  "registered_at": "2026-05-02T10:00:00Z",
  "last_used_at": "2026-05-02T11:30:00Z",
  "label": "Mark's iPhone"
}
```

Doc IDs are the `credential_id` (base64url without padding) for O(1) lookup.

### 2.3 Token Shape

`/login/complete` returns the **identical** token format as `/api/admin/verify`:

```json
{ "success": true, "token": "<24-byte HMAC-signed base64url token>" }
```

The JWT secret is derived the same way: `SHA256("sapphire-jwt-{ADMIN_PIN_HASH[:16]}")`.  
All downstream code (`require_admin`, `adminFetch.js`) continues working without changes.

### 2.4 Frontend

**New components**:

- `PasskeyLogin.jsx` — "Sign in with passkey" button + PIN fallback link  
- `PasskeySettings.jsx` — List / add / revoke passkeys (shown after admin login)

**Modified**:

- `App.jsx` — PIN modal now shows `PasskeyLogin` by default; "Use PIN instead" toggle shows the existing form; admin nav gains a "Passkeys" settings link

**Library**: `@simplewebauthn/browser` — handles `navigator.credentials.create()` / `.get()` with proper CBOR/base64url encoding.

---

## 3. Sequence Diagrams

### 3.1 Registration (first-time setup)

```
Admin Browser          FastAPI                   Firestore
──────────────         ───────────────           ─────────────────
1. Login with PIN ──►  /api/admin/verify  ──►    —
                  ◄──  { token }

2. Click "Add passkey"
   Enter label "Mark's iPhone"
   Click "Add"
                  ──►  POST /api/auth/webauthn/register/begin
                       header: X-Admin-Token
                                    ──►  write webauthn_challenges/{uuid}
                                         { challenge_b64, type:"registration",
                                           label:"Mark's iPhone", expires_at }
                  ◄──  { challenge_id, options: PublicKeyCredentialCreationOptions }

3. Browser prompts TouchID/FaceID
   navigator.credentials.create(options)
   User approves biometric

4. POST /api/auth/webauthn/register/complete
   { challenge_id, credential: {id, rawId, response:{...}}, label }
                  ──►  read + mark-used webauthn_challenges/{challenge_id}
                       verify_registration_response(credential, challenge, origin)
                       write webauthn_credentials/{credential_id}
                  ◄──  { success: true, credential_id }

5. UI shows "Mark's iPhone registered!"
```

### 3.2 Daily Login (passkey)

```
Admin Browser          FastAPI                   Firestore
──────────────         ───────────────           ─────────────────
1. Open /crm or /?admin=true
   Click "Sign in with passkey"

2. POST /api/auth/webauthn/login/begin
   { user_id: "admin" }
                  ──►  list webauthn_credentials where user_id == "admin"
                       generate_authentication_options(allow_credentials=[...])
                       write webauthn_challenges/{uuid} { type:"authentication" }
                  ◄──  { challenge_id, options }

3. Browser prompts TouchID / Proton Pass / hardware key
   navigator.credentials.get(options)
   User taps fingerprint

4. POST /api/auth/webauthn/login/complete
   { challenge_id, credential: {id, rawId, response:{...}} }
                  ──►  consume webauthn_challenges/{challenge_id}
                       lookup webauthn_credentials/{credential.id}
                       verify_authentication_response(credential, challenge,
                         stored_public_key, stored_sign_count)
                       update sign_count + last_used_at
                  ◄──  { success: true, token }

5. Frontend stores token in sessionStorage
   Navigates to analytics dashboard
```

---

## 4. Threat Model

### 4.1 Threats PIN Prevents — Passkeys Also Prevent

| Threat | PIN | Passkey |
|--------|-----|---------|
| Brute-force (online) | Rate-limited (10 attempts / 5 min) | Challenge is single-use and domain-scoped |
| Token replay | HMAC + 2h TTL | HMAC + 2h TTL (identical) |

### 4.2 Threats PIN Doesn't Prevent — Passkeys Do

| Threat | How passkeys block it |
|--------|----------------------|
| **Phishing** | Passkeys are bound to `sapphirealpha.xyz`; a fake site gets no valid credential |
| **Credential stuffing** | No shared secret to leak; public key on server is useless to attacker |
| **Shared-secret leakage** | Private key never leaves the device's secure enclave |
| **Network interception** | Attestation/assertion are signed with device key; cannot be reused on different challenge |
| **PIN shoulder-surfing** | Nothing typed on screen during passkey login |

### 4.3 Threats That Remain

| Threat | Mitigation |
|--------|------------|
| Compromised admin device | Attacker can generate valid assertions; revoke the passkey from another device or fall back to PIN + reset |
| Lost all devices | Fall back to PIN, reset via Secret Manager PIN rotation runbook (`docs/PIN_ROTATION_RUNBOOK.md`) |
| Firestore compromise | Challenge and credential storage exposed; attacker cannot forge signatures without device's private key |
| Challenge replay within TTL | Challenges are single-use: marked `used: true` before verification completes |
| Side-channel on Cloud Run | Same risk as today; mitigated by HTTPS transport + Firestore auth |

---

## 5. Migration Plan

### Phase 0 — Scaffold (this PR)

- Routes deployed but no credentials registered
- Default: passkey button shown; auto-falls back to PIN if no credentials exist on server
- Zero disruption to current workflow

### Phase 1 — Opt-in Enrollment (recommended: 2–4 weeks post-merge)

- Each admin logs in with PIN normally
- After login, prompted once to set up a passkey: "Add a passkey to log in faster"
- Multiple passkeys registered per person (work laptop + phone)
- Both paths active; passkey is default, PIN is one tap away

### Phase 2 — Passkey-primary (recommended: 8–12 weeks post-merge)

- Daily logins are passkey; PIN is hidden behind "Advanced / Recovery" link
- Add `PASSKEY_REQUIRED` feature flag: when true, PIN login fails unless issued by Secret Manager recovery flow
- **Decision for Ari**: timeline and whether to enforce passkey-only

### Phase 3 — PIN as recovery only (future)

- PIN is rotated to a random high-entropy value stored only in Secret Manager
- Only used for: new device enrollment when no other device is available
- Remove PIN UI from default modal

---

## 6. Recovery Story

**Scenario**: Admin loses all registered devices, cannot authenticate with any passkey.

**Recovery flow** (no code changes required):

1. Ari (or authorized admin) rotates `ADMIN_PIN_HASH` via Secret Manager:
   ```
   NEW_HASH=$(echo -n "NEW_6_DIGIT_PIN" | sha256sum | awk '{print $1}')
   gcloud secrets versions add admin-pin-hash \
     --project tho-ai-agent \
     --data-file=<(echo -n "$NEW_HASH")
   ```
2. Restart Cloud Run service (or wait for next cold start) to pick up new hash
3. Log in with new PIN
4. Re-enroll passkeys from new/recovered devices
5. (Optional) Delete orphaned passkeys from Firestore `webauthn_credentials`

**Documented in**: `docs/PIN_ROTATION_RUNBOOK.md` (existing)

---

## 7. Product Decisions Required (Ari)

The following items are **not decided** in this scaffold and need explicit sign-off before Phase 1:

| # | Decision | Options | Recommendation |
|---|----------|---------|----------------|
| 1 | **Per-user vs shared identity** | All admins share `user_id = "admin"` (current scaffold) vs each person gets their own (Mark, Celeste, Ben, Ari) | Per-user; needed for the audit log. But requires a user management UI. Start shared, migrate when audit log ships |
| 2 | **PIN sunset timeline** | Keep indefinitely / hide after 90 days / remove after 1 year | Keep indefinitely as recovery; hide from default UI after Phase 2 |
| 3 | **Passkey settings location** | Under CRM tab / separate `/admin/settings` page / modal from nav | Modal from admin nav — least disruptive |
| 4 | **Proton Pass integration testing** | Need to manually test registration + login via Proton Pass browser extension | Assign to Ben or Mark for smoke test on their machines |
| 5 | **Attestation level** | None (scaffold uses `AttestationConveyancePreference.NONE`) vs Direct (device certificate) | None is correct for most use cases; Direct adds complexity with no benefit here |
| 6 | **Biometric requirement** | `user_verification: PREFERRED` (allow PIN-only authenticators) vs `REQUIRED` (must use biometric) | `PREFERRED` — allows hardware keys and Windows Hello PIN as fallback |

---

## 8. Environment Variables

| Var | Default | Description |
|-----|---------|-------------|
| `WEBAUTHN_RP_ID` | `sapphirealpha.xyz` | Relying Party ID — must match the browser's origin hostname |
| `WEBAUTHN_RP_NAME` | `Texas Home Outlet Admin` | Human-readable RP name shown in browser prompts |
| `WEBAUTHN_ORIGINS` | `https://sapphirealpha.xyz` | Comma-separated allowed origins; `http://localhost:5173,http://localhost:8080` auto-detected when RP_ID is localhost |
| `ADMIN_TOKEN_TTL` | `7200` | JWT expiry in seconds (unchanged from current flow) |

For local development:
```bash
export WEBAUTHN_RP_ID=localhost
export ADMIN_PIN_HASH=<sha256 of your test PIN>
```

---

## 9. Dependencies

**Python** (`requirements.txt`):
```
webauthn>=2.1.0
```
This adds `py_webauthn` which transitively brings `cbor2`, `cryptography`, and `pydantic` (compatible with v2).

**npm** (`frontend/package.json`):
```json
"@simplewebauthn/browser": "^13.0.0"
```

No other new dependencies.
