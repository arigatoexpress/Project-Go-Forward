"""Global test configuration for Project-Go-Forward."""

import hashlib
import os
from pathlib import Path

# Ensure ADMIN_PIN_HASH is set before any test imports main.py
os.environ.setdefault("ADMIN_PIN_HASH", hashlib.sha256(b"4832").hexdigest())
os.environ.setdefault(
    "ADMIN_SESSION_SECRET",
    "test-only-independent-session-signing-key",  # pragma: allowlist secret
)

# These are captured at MODULE-IMPORT time by github_mira_trigger.py (e.g.
# _GITHUB_WEBHOOK_SECRET), and main.py imports that module. Any test that does a
# top-level `import main` therefore freezes these values before an individual
# test file's own setdefaults can run — so they must be set HERE, in conftest,
# which executes before any test module is imported. (Previously only
# test_github_mira_trigger.py set them, which broke once another test imported
# main earlier in the collection order.)
os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "test-github-secret")
os.environ.setdefault("MIRA_GROUP_ID", "-123456789")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")

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

# main.py mounts StaticFiles("frontend/dist/assets") at import time, which raises
# if the dir is absent — true in a fresh checkout / worktree that never ran the
# Vite build. Any test that does `import main` (e.g. the /readyz probe tests)
# needs this present. Several test files already create it ad-hoc inside their
# own client builders (see test_smoke_empty_deal.py::_build_test_client); hoist
# it here so importing main is safe regardless of collection order. Runtime/prod
# is unaffected — conftest is pytest-only.
_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
(_FRONTEND_DIST / "assets").mkdir(parents=True, exist_ok=True)
_INDEX_HTML = _FRONTEND_DIST / "index.html"
if not _INDEX_HTML.exists():
    _INDEX_HTML.write_text('<html><body id="root">test</body></html>', encoding="utf-8")
