"""Global test configuration for Project-Go-Forward."""
import hashlib
import os

# Ensure ADMIN_PIN_HASH is set before any test imports main.py
os.environ.setdefault("ADMIN_PIN_HASH", hashlib.sha256(b"4832").hexdigest())
