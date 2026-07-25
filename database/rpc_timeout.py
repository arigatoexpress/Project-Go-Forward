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

# Wall-clock budget for a whole Firestore transaction. The
# ``@firestore.transactional`` helper performs its Begin/Commit/Rollback RPCs
# internally with no public per-RPC timeout hook, and it may retry the entire
# transaction — so a hung Commit cannot be bounded with ``timeout=`` above.
# Bound it at the call site instead: wrap the ``asyncio.to_thread(...)``
# running the transaction in ``asyncio.wait_for(..., FIRESTORE_TRANSACTION_TIMEOUT)``.
# Sized as a few RPC-timeout multiples to allow transactional retries.
FIRESTORE_TRANSACTION_TIMEOUT: float = FIRESTORE_RPC_TIMEOUT * 3
