#!/usr/bin/env python3
"""One-time migration: encrypt existing plaintext deal SSNs at rest.

Run AFTER provisioning the KMS key and setting ``SSN_KMS_KEY`` (+ granting the
running principal ``cloudkms.cryptoKeyEncrypterDecrypter``). The app already
encrypts SSNs on every new write and decrypts transparently on read, so this
only back-fills deals created before activation.

Safety:
* DRY-RUN by default — prints how many deals WOULD be encrypted and writes
  nothing. Pass ``--apply`` to perform the writes.
* Idempotent — values already carrying the ``kms1:`` prefix are skipped, so
  re-running is harmless.
* Never prints SSN values (only deal ids + field names).

Usage::

    SSN_KMS_KEY=projects/.../cryptoKeys/ssn python scripts/migrate_encrypt_deal_ssns.py
    SSN_KMS_KEY=projects/.../cryptoKeys/ssn python scripts/migrate_encrypt_deal_ssns.py --apply
"""

import os
import sys

from google.cloud import firestore

from tools import field_crypto


def main() -> int:
    apply = "--apply" in sys.argv[1:]
    if not field_crypto._kms_key():
        print("SSN_KMS_KEY is not set — nothing to do (feature inert).", file=sys.stderr)
        return 2

    db = firestore.Client(project=os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT"))
    scanned = to_encrypt = encrypted = 0

    for doc in db.collection("deals").stream():
        scanned += 1
        data = doc.to_dict() or {}
        updates = {}
        for field in field_crypto.SSN_FIELDS:
            value = data.get(field)
            if value and not field_crypto.is_encrypted(value):
                updates[field] = field_crypto.encrypt_field(value)
        if not updates:
            continue
        to_encrypt += 1
        print(f"deal {doc.id}: {'ENCRYPT' if apply else 'would encrypt'} {sorted(updates)}")
        if apply:
            db.collection("deals").document(doc.id).update(updates)
            encrypted += 1

    print(
        f"\nscanned={scanned} deals_with_plaintext_ssn={to_encrypt} "
        f"encrypted={encrypted} ({'APPLIED' if apply else 'DRY-RUN — re-run with --apply'})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
