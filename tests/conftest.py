"""Global test configuration for Project-Go-Forward."""

import hashlib
import os

# Ensure ADMIN_PIN_HASH is set before any test imports main.py
os.environ.setdefault("ADMIN_PIN_HASH", hashlib.sha256(b"4832").hexdigest())

# Local document tests generate synthetic PDFs. If developer credentials are
# present, the production upload helper can otherwise push those fixtures to
# the live document bucket. Live GCS checks must opt in explicitly.
os.environ.setdefault("THO_DISABLE_GCS_UPLOADS", "1")

# Hermeticity: NEVER let the test suite touch production Firestore. A developer's
# ADC defaults to the prod project (tho-ai-agent), so an unmocked test write
# lands in LIVE customer data — it has (see memory tho-prod-ops-state, where
# `pytest tests/` created junk leads in prod). Point Firestore at a local
# emulator host (absent by default) + a throwaway project so any unmocked write
# fails fast LOCALLY instead of polluting prod. CI already has no ADC, so this
# is a no-op there; runtime/prod is unaffected (conftest is pytest-only). To run
# against a real emulator, start it and set FIRESTORE_EMULATOR_HOST yourself.
os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "localhost:8085")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "tho-test-local")
os.environ.setdefault("GCP_PROJECT_ID", "tho-test-local")
