# Per-User Admin Identity — Design Document

**Status**: Scaffold merged, migration pending  
**Branch**: `feat/per-user-identity-scaffold`  
**Related PRs**: #36 (audit log), #63 (passkey auth)

---

## Problem

The current admin auth model is a single shared PIN that issues a token carrying no
identity. Two in-flight features require per-person attribution:

| Feature | What it needs |
|---|---|
| Passkey auth (#63) | Each hardware key should map to a specific user, not `"admin"` |
| Audit log (#36) | Every mutation entry must name *who* acted, not just *that* an admin acted |

Without per-user identity:
- Audit log can only say "an admin did X", not "Mark did X".
- Passkey revocation is all-or-nothing (delete every key or none).
- Revoking one team member's access requires rotating the shared PIN for everyone.

---

## Identity Sources

### Today (shared PIN)

```
POST /api/admin/verify  { pin }
  → HMAC-signed binary token (no user_id embedded)
  → require_admin sets request.state.user_id = "admin"
```

### This PR (email-optional PIN)

```
POST /api/admin/verify  { pin, email? }
  if email supplied:
    → look up or auto-create User record
    → issue JWT-format token carrying sub = user_id (UUID)
    → require_admin sets request.state.user_id = <UUID>
  if no email:
    → legacy binary token (unchanged)
    → require_admin sets request.state.user_id = "admin"
```

### Future (passkey)

```
POST /api/auth/webauthn/login/complete
  → authenticates credential keyed to webauthn_credentials.user_id
  → issues same JWT-format token carrying sub = user_id
```

---

## User Model

Stored in Firestore collection `users`, document ID = `user.id` (UUID4).

```python
class Role(str, Enum):
    ADMIN = "admin"
    STAFF = "staff"

class User(BaseModel):
    id: str                    # UUID4
    email: str                 # unique — used as login identifier
    display_name: str
    role: Role = Role.STAFF
    created_at: datetime
    last_login_at: Optional[datetime] = None
    disabled: bool = False
```

**Firestore path**: `users/{user_id}`  
**Indexes needed**: `email` (unique lookup), `disabled` (list active users)

---

## Admin Bootstrap

On first deploy, there are no `User` records. Bootstrap happens lazily:

1. Operator sets `ADMIN_USER_EMAILS=mark@tho.com,celeste@tho.com` as a Cloud Run
   environment variable (or lists emails in `admin_users.yaml`).
2. When someone authenticates at `POST /api/admin/verify` with a recognised email +
   valid PIN, `get_or_create_user` fires and writes their `User` doc to Firestore.
3. The first bootstrap user always gets `role=admin`.

Legacy clients that omit `email` fall through to `user_id="admin"` — no breakage.

```yaml
# admin_users.yaml (optional, checked into secrets or mounted as a volume)
admin_emails:
  - mark@texashomeoutlet.com
  - celeste@texashomeoutlet.com
```

---

## Passkey-to-User Mapping

PR #63 scaffolds the `webauthn_credentials` Firestore collection. Each document
currently hard-codes `user_id = "admin"`. Once per-user identity ships:

```
webauthn_credentials/{credential_id}
  user_id: "<UUID from users collection>"   # ← updated from "admin"
  label:   "Mark's iPhone"
  created_at: ...
```

The registration flow (`POST /api/auth/webauthn/register/begin`) should accept a
`user_id` (resolved from email) so the credential is bound to a specific person.
Revocation then becomes "delete this credential for this user" instead of "wipe all
credentials".

---

## Token Formats

### Legacy binary token (no user identity)

```
base64url( [8-byte big-endian expiry] + [16-byte HMAC-SHA256 truncated] )
```

Detected by: no `.` characters in the token string.  
Decoded as: `user_id = "admin"`.

### JWT-format token (carries user identity) — introduced this PR

```
<header_b64>.<payload_b64>.<sig_b64>
```

```json
// payload
{ "exp": 1735689600, "sub": "a3f2c1d0-..." }
```

HMAC-SHA256 key: same `_JWT_SECRET` derived from `ADMIN_PIN_HASH`.  
Detected by: exactly two `.` characters.  
Decoded as: `user_id = payload["sub"]`.

Both formats are verified in `_decode_admin_token(token)`. Old tokens continue to
work indefinitely — legacy clients are not broken.

---

## Audit Log Integration

PR #36 adds `_audit_actor(request)` which hashes the token to produce a short
correlation ID (`admin:<hex12>`). Once per-user tokens land, the actor can be:

```python
def _audit_actor(request: Request) -> str:
    user_id = getattr(request.state, "user_id", "admin")
    if user_id and user_id != "admin":
        return f"user:{user_id[:12]}"          # UUID prefix — correlatable
    # Fall back to token hash for legacy sessions
    token = _admin_token_from_request(request)
    return f"admin:{hashlib.sha256(token.encode()).hexdigest()[:12]}"
```

This change belongs in the audit log PR (#36) once it rebases onto this scaffold.

---

## Migration Plan

| Phase | What ships | Risk |
|---|---|---|
| **This PR** | User model, auth/users.py, email-optional PIN, `/api/admin/me` | Zero — legacy path unchanged |
| **Follow-up 1** | Frontend Settings panel sends `email` on PIN verify; shows "Logged in as \<name\>" | Low — additive UI change |
| **Follow-up 2** | Audit log (#36) reads `request.state.user_id` instead of hashing the token | Low — internal refactor |
| **Follow-up 3** | Passkey (#63) registration resolves `user_id` from email; revocation is per-user | Medium — changes passkey schema |
| **Follow-up 4** | Deprecate `user_id="admin"` fallback; require email on all PIN logins | High — coordinate with all team members |

---

## Security Notes

- The shared PIN is not weakened — it still controls access. Per-user identity adds
  attribution, not additional authentication factors.
- `get_or_create_user` will only auto-create a user for emails that appear in
  `ADMIN_USER_EMAILS` (or `admin_users.yaml`). Unknown emails are rejected even with
  a valid PIN.
- `User.disabled = True` immediately prevents `/api/admin/me` from returning a
  valid identity. Route-level enforcement (blocking disabled users at `require_admin`)
  is a follow-up task once per-user JWTs are universal.
- PII: `email` and `display_name` are stored in Firestore. They are not logged via
  `struct_logger` and are stripped from LLM calls per existing PII guard policy.
