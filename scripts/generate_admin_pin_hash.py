#!/usr/bin/env python3
"""Generate a salted scrypt ADMIN_PIN_HASH for THO admin auth.

The app verifies BOTH this scrypt format and the legacy unsalted SHA-256 hex
hash, so you can roll forward to the stronger KDF with no downtime: generate a
new value here and set it as the ``ADMIN_PIN_HASH`` env var / Cloud Run secret.

Usage::

    python scripts/generate_admin_pin_hash.py          # prompts (hidden input)
    python scripts/generate_admin_pin_hash.py 4832     # PIN as arg (shell history!)

Why scrypt: a 4-digit PIN has only 10,000 possibilities. Fast SHA-256 lets an
attacker who obtains the hash try all of them in microseconds; a salted,
memory-hard scrypt KDF makes that orders of magnitude slower and defeats
rainbow tables.
"""

import base64
import getpass
import hashlib
import os
import sys

# Interactive-tier scrypt parameters (~16 MB, tens of ms server-side).
N, R, P, DKLEN = 16384, 8, 1, 32


def make_hash(pin: str) -> str:
    """Return a ``scrypt$n$r$p$salt_b64$dk_b64`` hash string for ``pin``."""
    salt = os.urandom(16)
    dk = hashlib.scrypt(
        pin.encode(), salt=salt, n=N, r=R, p=P, dklen=DKLEN, maxmem=128 * N * R + (1 << 20)
    )
    salt_b64 = base64.b64encode(salt).decode()
    dk_b64 = base64.b64encode(dk).decode()
    return f"scrypt${N}${R}${P}${salt_b64}${dk_b64}"


def main() -> int:
    pin = sys.argv[1] if len(sys.argv) > 1 else getpass.getpass("Admin PIN: ")
    if not pin:
        print("PIN must not be empty", file=sys.stderr)
        return 1
    print(make_hash(pin))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
