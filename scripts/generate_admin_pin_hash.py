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

# Must match ADMIN_PIN_MAXLEN in frontend/src/App.jsx: the admin login box caps
# input at this many characters, so a longer PIN cannot be typed/pasted to log in.
ADMIN_PIN_MAXLEN = 64


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
    if len(pin) > ADMIN_PIN_MAXLEN:
        # Don't echo the PIN — just warn about the length so the operator knows
        # this secret won't fit the admin login box (which caps at ADMIN_PIN_MAXLEN).
        print(
            f"WARNING: PIN is {len(pin)} characters; the admin login box only accepts "
            f"up to {ADMIN_PIN_MAXLEN} and will not let anyone type/paste it to log in. "
            "Choose a shorter PIN.",
            file=sys.stderr,
        )
    print(make_hash(pin))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
