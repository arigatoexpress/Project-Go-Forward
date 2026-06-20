"""Application-level envelope encryption for sensitive at-rest fields (SSN).

Why this exists
---------------
Firestore is already encrypted at rest with Google-managed keys, and the API
layer strips ``buyer_ssn`` / ``co_buyer_ssn`` from every response (see
``_strip_ssn_from_customer`` / the deal SSN strip in ``main.py``). This module
adds *defense in depth*: deal SSNs are additionally encrypted with a Cloud KMS
key before they are written to Firestore, so a Firestore-level compromise or an
over-broad IAM read cannot expose plaintext SSNs — only a principal with KMS
*decrypt* permission (the running service) can read them, and only at
document-generation time.

Ships INERT and BACKWARD-COMPATIBLE
-----------------------------------
* If ``SSN_KMS_KEY`` is unset, ``encrypt_field`` / ``decrypt_field`` are exact
  identity pass-throughs — behaviour is byte-for-byte unchanged. The feature is
  therefore a no-op until an operator provisions a key (gated activation), the
  same pattern as the inert inbound-email webhook.
* ``decrypt_field`` passes legacy plaintext (values without the ``kms1:``
  prefix) straight through, so existing Firestore deals keep working during and
  after a one-time migration — encrypted and plaintext rows coexist safely.
* ``encrypt_field`` is idempotent: an already-encrypted value is returned as-is,
  so re-saving a deal never double-encrypts.

Activation runbook lives in memory ``tho-prod-ops-state``.
"""

from __future__ import annotations

import base64
import functools
import os

# Marks a value as KMS-ciphertext. Versioned so the scheme can evolve without
# ambiguity against legacy plaintext (which never carries this prefix).
_PREFIX = "kms1:"

# Sensitive deal fields encrypted at rest. Keep in sync with the SSN strip set
# in main.py.
SSN_FIELDS = ("buyer_ssn", "co_buyer_ssn")


def _kms_key() -> str | None:
    """Full KMS crypto-key resource name, or None when the feature is inert.

    Format: ``projects/<p>/locations/<l>/keyRings/<r>/cryptoKeys/<k>``.
    """
    return os.environ.get("SSN_KMS_KEY") or None


@functools.lru_cache(maxsize=1)
def _client():
    # Lazy import so inert deployments never need the google-cloud-kms package
    # loaded, and so a missing dependency surfaces only at activation time.
    from google.cloud import kms

    return kms.KeyManagementServiceClient()


def is_encrypted(value: object) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


def encrypt_field(plaintext: str | None) -> str | None:
    """Encrypt one field value. Inert + idempotent.

    Fails CLOSED: if a key is configured but encryption errors, the exception
    propagates rather than silently persisting plaintext. An operator catches
    this during activation testing, before any real SSN is written.
    """
    if not plaintext or not isinstance(plaintext, str):
        return plaintext
    if is_encrypted(plaintext):  # already ciphertext — never double-encrypt
        return plaintext
    key = _kms_key()
    if not key:
        return plaintext  # inert: feature not activated
    resp = _client().encrypt(request={"name": key, "plaintext": plaintext.encode("utf-8")})
    return _PREFIX + base64.b64encode(resp.ciphertext).decode("ascii")


def decrypt_field(stored: str | None) -> str | None:
    """Decrypt one field value. Legacy plaintext passes through untouched.

    If the value is ciphertext but no key is configured, the ciphertext is
    returned unchanged (it still gets stripped before egress; doc-gen would show
    a missing SSN rather than leaking anything).
    """
    if not is_encrypted(stored):
        return stored  # legacy plaintext, empty, or non-str — passthrough
    key = _kms_key()
    if not key:
        return stored  # cannot decrypt without the key; never raise on read
    ciphertext = base64.b64decode(stored[len(_PREFIX):])
    resp = _client().decrypt(request={"name": key, "ciphertext": ciphertext})
    return resp.plaintext.decode("utf-8")


def encrypt_ssn_fields(data: dict) -> dict:
    """Encrypt SSN fields in a deal dict in place (returns the same dict)."""
    for field in SSN_FIELDS:
        if data.get(field):
            data[field] = encrypt_field(data[field])
    return data


def decrypt_ssn_fields(data: dict) -> dict:
    """Decrypt SSN fields in a deal dict in place (returns the same dict)."""
    for field in SSN_FIELDS:
        if data.get(field):
            data[field] = decrypt_field(data[field])
    return data
