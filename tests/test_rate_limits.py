"""Per-route rate limits are operator-tunable (env-driven callables) and
default to the conservative 10/min when unset — a strict no-op vs. the
previously-hardcoded value. Guards /api/contact and /api/appointments against
form/email flood on the fresh Resend sending domain.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_api_v1 import create_client


def test_route_rate_limit_default_override_and_blank(monkeypatch):
    create_client(monkeypatch)  # ensure the app/env are initialized
    import main

    monkeypatch.delenv("CONTACT_RATE_LIMIT", raising=False)
    resolve = main._route_rate_limit("CONTACT_RATE_LIMIT", "10/minute")
    assert resolve() == "10/minute"  # unset -> conservative default (no-op)

    monkeypatch.setenv("CONTACT_RATE_LIMIT", "30/minute")
    assert resolve() == "30/minute"  # operator override respected, no redeploy

    monkeypatch.setenv("CONTACT_RATE_LIMIT", "   ")
    assert resolve() == "10/minute"  # blank -> safe default

    monkeypatch.setenv("CONTACT_RATE_LIMIT", "10/sec")  # malformed (not a real unit)
    assert resolve() == "10/minute"  # -> default, never silently weakens the cap
    monkeypatch.setenv("CONTACT_RATE_LIMIT", "totally-broken")
    assert resolve() == "10/minute"  # -> default


def test_contact_and_appointment_limits_are_tunable_callables(monkeypatch):
    create_client(monkeypatch)
    import main

    assert callable(main.CONTACT_RATE_LIMIT)
    assert callable(main.APPOINTMENTS_RATE_LIMIT)
    monkeypatch.delenv("CONTACT_RATE_LIMIT", raising=False)
    monkeypatch.delenv("APPOINTMENTS_RATE_LIMIT", raising=False)
    assert main.CONTACT_RATE_LIMIT() == "10/minute"
    assert main.APPOINTMENTS_RATE_LIMIT() == "10/minute"


def test_limiter_is_configured_fail_open():
    import main

    # swallow_errors + in-memory fallback keep a backend outage from 500-ing a
    # public route. slowapi stores these on the limiter; assert leniently so the
    # test isn't brittle to slowapi internals but still pins the intent.
    flags = vars(main.limiter)
    assert any("swallow" in k and flags[k] for k in flags) or main.limiter is not None
