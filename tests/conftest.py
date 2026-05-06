"""Global test configuration for Project-Go-Forward."""
import hashlib
import os

# Ensure ADMIN_PIN_HASH is set before any test imports main.py
os.environ.setdefault("ADMIN_PIN_HASH", hashlib.sha256(b"4832").hexdigest())

# Local document tests generate synthetic PDFs. If developer credentials are
# present, the production upload helper can otherwise push those fixtures to
# the live document bucket. Live GCS checks must opt in explicitly.
os.environ.setdefault("THO_DISABLE_GCS_UPLOADS", "1")
