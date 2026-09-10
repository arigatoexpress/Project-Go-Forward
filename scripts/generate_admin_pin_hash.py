#!/usr/bin/env python3
"""Generate a salted scrypt ADMIN_PIN_HASH for THO admin auth.

The app verifies BOTH this scrypt format and the legacy unsalted SHA-256 hex
hash, so you can roll forward to the stronger KDF with no downtime: generate a
new value here and set it as the ``ADMIN_PIN_HASH`` env var / Cloud Run secret.

Usage::

    # First create a private destination; see docs/PIN_ROTATION_RUNBOOK.md.
    python scripts/generate_admin_pin_hash.py > "$PIN_HASH_FILE"

The PIN is entered twice using hidden terminal input, never argv or environment.
The verifier is emitted only to redirected stdout, never an interactive terminal.
This script performs no cloud action and does not change any credential or session.

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
import warnings

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
    if len(sys.argv) != 1:
        print("PIN arguments are not accepted; use hidden terminal input.", file=sys.stderr)
        return 2
    if sys.stdout.isatty():
        print(
            "Redirect the verifier to a private file; see the PIN rotation runbook.",
            file=sys.stderr,
        )
        return 2
    try:
        with warnings.catch_warnings():
            # getpass otherwise falls back to echoing input when no secure
            # terminal is available. Stop before it can read that input.
            warnings.simplefilter("error", getpass.GetPassWarning)
            pin = getpass.getpass("New admin PIN: ")
            if not pin:
                print("PIN must not be empty", file=sys.stderr)
                return 1
            # HTML maxLength and JavaScript slice count UTF-16 code units.
            # Python len counts code points and would accept unusable emoji PINs.
            if len(pin.encode("utf-16-le")) // 2 > ADMIN_PIN_MAXLEN:
                print(
                    f"PIN exceeds the browser limit of {ADMIN_PIN_MAXLEN} UTF-16 units.",
                    file=sys.stderr,
                )
                return 1
            confirmation = getpass.getpass("Confirm admin PIN: ")
    except (getpass.GetPassWarning, EOFError, KeyboardInterrupt, UnicodeError):
        print(
            "Secure PIN input was unavailable or cancelled; no verifier generated.", file=sys.stderr
        )
        return 1
    if pin != confirmation:
        print("PIN confirmation does not match; no verifier generated.", file=sys.stderr)
        return 1
    print(make_hash(pin))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
