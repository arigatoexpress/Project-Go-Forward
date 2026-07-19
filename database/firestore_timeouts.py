"""Central timeout configuration for Firestore SDK calls.

Why this exists
---------------
A hanging Firestore/gRPC call can wedge a request (or an entire async
event loop) indefinitely — see ``docs/RUNBOOK.md`` §3.2 and
``LAUNCH_READINESS.md`` known risk #2. The google-cloud-firestore sync
client accepts a ``timeout`` kwarg (seconds, float) on every blocking
call (``get``/``stream``/``set``/``update``/``delete``/``add``/``create``/
``commit``); the async client's coroutines accept it too. Passing an
explicit bound on every call converts an unbounded hang into a
``google.api_core.exceptions.DeadlineExceeded`` error that normal error
handling can surface.

Configuration
-------------
Values are read from the environment *at call time* (not import time) so
operators can tune them without code changes and tests can monkeypatch
``os.environ``:

* ``THO_FIRESTORE_TIMEOUT_SECONDS`` — normal request-path reads/writes.
  Default ``10.0``.
* ``THO_FIRESTORE_LONG_TIMEOUT_SECONDS`` — bulk/batch work (batch
  commits, bulk imports, full-collection scans/migrations).
  Default ``60.0``.

Unset, unparsable, or non-positive values degrade to the defaults.
"""

from __future__ import annotations

import os

ENV_TIMEOUT = "THO_FIRESTORE_TIMEOUT_SECONDS"
ENV_LONG_TIMEOUT = "THO_FIRESTORE_LONG_TIMEOUT_SECONDS"

DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_LONG_TIMEOUT_SECONDS = 60.0


def _read_env_seconds(env_var: str, default: float) -> float:
    """Parse a positive float seconds value from ``env_var`` or fall back to ``default``."""
    raw = os.getenv(env_var)
    if raw is None:
        return default
    try:
        value = float(raw.strip())
    except (TypeError, ValueError, AttributeError):
        return default
    if value <= 0:
        return default
    return value


def firestore_timeout() -> float:
    """Timeout in seconds for normal request-path Firestore calls."""
    return _read_env_seconds(ENV_TIMEOUT, DEFAULT_TIMEOUT_SECONDS)


def firestore_long_timeout() -> float:
    """Timeout in seconds for bulk/batch Firestore calls (imports, migrations, full scans)."""
    return _read_env_seconds(ENV_LONG_TIMEOUT, DEFAULT_LONG_TIMEOUT_SECONDS)
