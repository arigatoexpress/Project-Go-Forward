"""Shared Firestore RPC timeout (seconds) for request-path database calls.

Every Firestore/gRPC call in the serving path should pass
``timeout=FIRESTORE_RPC_TIMEOUT`` so a hung Firestore call raises
``DeadlineExceeded`` instead of blocking forever. Without a bound, a hanging
call inside an async endpoint can wedge a Cloud Run instance's event loop
(observed 2026-06-10; see docs/RUNBOOK.md §3.2 and LAUNCH_READINESS.md
"Known accepted risks" #2).

Override with the ``FIRESTORE_RPC_TIMEOUT_SECONDS`` env var; default 10s.
Invalid or non-positive values fall back to the default.
"""

import os

DEFAULT_RPC_TIMEOUT_SECONDS = 10.0


def _load_timeout() -> float:
    raw = os.getenv("FIRESTORE_RPC_TIMEOUT_SECONDS")
    if raw is None:
        return DEFAULT_RPC_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_RPC_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_RPC_TIMEOUT_SECONDS


FIRESTORE_RPC_TIMEOUT: float = _load_timeout()
